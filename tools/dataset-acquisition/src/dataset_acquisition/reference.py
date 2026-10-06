"""Reference corpus: a versioned set of source fixtures, their locked
fingerprints and a verified local cache of batch recordings.

A *corpus* (``corpus.json``, hand-written) names the source and the fixtures,
and states one acquisition / replay definition per fixture. The *lock*
(``corpus.lock.json``, generated only by ``--update-lock``) records what that
definition produced:

    definition_sha256   the resolved fixture definition
    source              fingerprint and counts of every input the fixture reads
    recording           sha256, size, message count and topic counts of the
                        batch MCAP
    labels              sha256, size and counts of the reference label artifact
                        (the source's ground truth, with no runtime identity)

A fixture is the sensor recording plus its source-derived labels. A cached
recording or label artifact is reused only when the definition, the source fingerprint,
the tool identity and the recording itself all agree with the lock. Every
disagreement is reported and fails; nothing is regenerated, rewritten or
deleted unless ``--update-lock`` is given, and the lock is never edited by
hand. Only preparation reads the source: ``resolve`` and ``render-labels``
work from the lock and the cache alone.

The corpus knows nothing about Scenes, Episodes or any SceneOps build
configuration: it selects and describes input only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from . import __version__
from .events import AcquisitionError
from .labels import (
    label_counts,
    parse_reference_labels,
    reference_labels,
    render_label_document,
    serialize_reference_labels,
)
from .mcap_sink import RecordingSummary, write_mcap
from .nuscenes import CHANNEL_GROUPS, NuScenesAdapter, NuScenesSelection

CORPUS_SCHEMA = "sceneops.reference_corpus/1"
LOCK_SCHEMA = "sceneops.reference_corpus_lock/1"
CORPUS_FILE = "corpus.json"
LOCK_FILE = "corpus.lock.json"
ALL_FIXTURES = "*"

# The packages whose versions decide the bytes of a recording: serialization
# (rosbags), container (mcap + its zstd), array formatting (numpy), source
# reading (nuscenes-devkit) and the tool itself.
IDENTITY_PACKAGES = (
    "mcap",
    "nuscenes-devkit",
    "numpy",
    "rosbags",
    "sceneops-dataset-acquisition",
    "zstandard",
)


class ReferenceDataError(AcquisitionError):
    """The corpus, its lock or its cache is invalid or does not agree."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(value)).hexdigest()}"


# -- corpus ---------------------------------------------------------------


def _object(value: Any, where: str, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ReferenceDataError(f"{where}: expected an object")
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown or missing:
        raise ReferenceDataError(
            f"{where}: unknown keys {sorted(unknown)}, missing keys {sorted(missing)}"
        )
    return value


@dataclass(frozen=True)
class Corpus:
    corpus_id: str
    source: dict[str, str]
    defaults: dict[str, dict[str, Any]]
    fixtures: dict[str, dict[str, Any]]  # fixture_id -> raw entry, in file order
    scopes: dict[str, list[str]]  # scope -> fixture ids (``*`` expanded)

    def resolve(self, fixture_id: str) -> dict[str, Any]:
        """The fixture's definition: shared defaults overridden per key."""
        entry = self.fixtures.get(fixture_id)
        if entry is None:
            raise ReferenceDataError(f"unknown fixture {fixture_id!r}")
        resolved: dict[str, Any] = {
            "fixture_id": fixture_id,
            "source_unit": entry["source_unit"],
        }
        for section in ("acquisition", "replay"):
            resolved[section] = {
                **self.defaults[section],
                **entry.get(section, {}),
            }
        resolved["acquisition"]["channel_groups"] = [
            g for g in CHANNEL_GROUPS if g in resolved["acquisition"]["channel_groups"]
        ]
        return resolved

    def definition_sha256(self, fixture_id: str) -> str:
        return _digest(
            {
                "schema": CORPUS_SCHEMA,
                "corpus_id": self.corpus_id,
                "source": self.source,
                "fixture": self.resolve(fixture_id),
            }
        )

    def select(self, scope: str | None, fixtures: Iterable[str] = ()) -> list[str]:
        chosen = list(fixtures)
        if scope is not None:
            if scope not in self.scopes:
                raise ReferenceDataError(
                    f"unknown scope {scope!r}; the corpus defines {sorted(self.scopes)}"
                )
            chosen = [*self.scopes[scope], *chosen]
        if not chosen:
            raise ReferenceDataError("select a scope or at least one fixture")
        for fixture_id in chosen:
            if fixture_id not in self.fixtures:
                raise ReferenceDataError(f"unknown fixture {fixture_id!r}")
        return list(dict.fromkeys(chosen))


def _validate_defaults(defaults: Any) -> dict[str, dict[str, Any]]:
    _object(defaults, "defaults", {"acquisition", "replay"}, {"acquisition", "replay"})
    acquisition = _object(
        defaults["acquisition"],
        "defaults.acquisition",
        {"channel_groups"},
        {"channel_groups"},
    )
    _channel_groups(
        acquisition["channel_groups"], "defaults.acquisition.channel_groups"
    )
    replay = _object(
        defaults["replay"],
        "defaults.replay",
        {"rate", "wait_subscribers_seconds"},
        {"rate", "wait_subscribers_seconds"},
    )
    _replay(replay, "defaults.replay")
    return {"acquisition": acquisition, "replay": replay}


def _channel_groups(value: Any, where: str) -> None:
    if (
        not isinstance(value, list)
        or not value
        or len(set(value)) != len(value)
        or not set(value) <= set(CHANNEL_GROUPS)
    ):
        raise ReferenceDataError(
            f"{where}: expected a non-empty list of distinct groups from {list(CHANNEL_GROUPS)}"
        )


def _replay(replay: Mapping[str, Any], where: str) -> None:
    for key, minimum in (("rate", 0), ("wait_subscribers_seconds", 0)):
        if key in replay:
            value = replay[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < minimum
            ):
                raise ReferenceDataError(
                    f"{where}.{key}: expected a number >= {minimum}"
                )


def load_corpus(corpus_dir: Path) -> Corpus:
    path = Path(corpus_dir) / CORPUS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReferenceDataError(f"cannot read {path}: {exc}") from exc
    _object(
        raw,
        CORPUS_FILE,
        {"schema", "corpus_id", "source", "defaults", "fixtures", "scopes"},
        {"schema", "corpus_id", "source", "defaults", "fixtures", "scopes"},
    )
    if raw["schema"] != CORPUS_SCHEMA:
        raise ReferenceDataError(f"{CORPUS_FILE}: schema must be {CORPUS_SCHEMA!r}")
    corpus_id = raw["corpus_id"]
    if (
        not isinstance(corpus_id, str)
        or not corpus_id
        or Path(corpus_id).name != corpus_id
    ):
        raise ReferenceDataError(f"{CORPUS_FILE}: corpus_id must be a plain name")
    if Path(corpus_dir).name != corpus_id:
        raise ReferenceDataError(
            f"corpus_id {corpus_id!r} must equal its directory name {Path(corpus_dir).name!r}"
        )
    source = _object(
        raw["source"], "source", {"format", "version"}, {"format", "version"}
    )
    if source["format"] != "nuscenes":
        raise ReferenceDataError(f"source.format {source['format']!r} is not supported")
    defaults = _validate_defaults(raw["defaults"])

    fixtures: dict[str, dict[str, Any]] = {}
    if not isinstance(raw["fixtures"], list) or not raw["fixtures"]:
        raise ReferenceDataError("fixtures: expected a non-empty list")
    for index, entry in enumerate(raw["fixtures"]):
        where = f"fixtures[{index}]"
        _object(
            entry,
            where,
            {"fixture_id", "source_unit", "acquisition", "replay"},
            {"fixture_id", "source_unit"},
        )
        fixture_id = entry["fixture_id"]
        if (
            not isinstance(fixture_id, str)
            or not fixture_id
            or Path(fixture_id).name != fixture_id
        ):
            raise ReferenceDataError(f"{where}.fixture_id must be a plain name")
        if fixture_id in fixtures:
            raise ReferenceDataError(f"duplicate fixture_id {fixture_id!r}")
        if not isinstance(entry["source_unit"], str) or not entry["source_unit"]:
            raise ReferenceDataError(f"{where}.source_unit must be a non-empty string")
        if "acquisition" in entry:
            override = _object(
                entry["acquisition"], f"{where}.acquisition", {"channel_groups"}, set()
            )
            if "channel_groups" in override:
                _channel_groups(
                    override["channel_groups"], f"{where}.acquisition.channel_groups"
                )
        if "replay" in entry:
            _replay(
                _object(
                    entry["replay"],
                    f"{where}.replay",
                    {"rate", "wait_subscribers_seconds"},
                    set(),
                ),
                f"{where}.replay",
            )
        fixtures[fixture_id] = entry
    units = [f["source_unit"] for f in fixtures.values()]
    if len(set(units)) != len(units):
        raise ReferenceDataError("two fixtures select the same source_unit")

    scopes: dict[str, list[str]] = {}
    if not isinstance(raw["scopes"], dict) or not raw["scopes"]:
        raise ReferenceDataError("scopes: expected a non-empty object")
    for scope, members in raw["scopes"].items():
        if members == [ALL_FIXTURES]:
            members = list(fixtures)
        if (
            not isinstance(members, list)
            or not members
            or len(set(members)) != len(members)
            or not set(members) <= set(fixtures)
        ):
            raise ReferenceDataError(
                f"scopes.{scope}: expected distinct fixture ids of this corpus, or [{ALL_FIXTURES!r}]"
            )
        scopes[scope] = members
    return Corpus(corpus_id, source, defaults, fixtures, scopes)


# -- tool identity ------------------------------------------------------------


def tool_identity() -> dict[str, Any]:
    """The pinned environment a recording is produced in. The container image
    installs it from uv.lock with --frozen, so equal identity means the same
    locked packages."""
    packages = {}
    for name in IDENTITY_PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError as exc:
            raise ReferenceDataError(
                f"tool identity: package {name!r} is not installed"
            ) from exc
    body = {
        "name": "sceneops-dataset-acquisition",
        "version": __version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "packages": packages,
    }
    return {**body, "identity_sha256": _digest(body)}


# -- lock ---------------------------------------------------------------------


def read_lock(corpus_dir: Path, corpus: Corpus) -> dict[str, Any] | None:
    path = Path(corpus_dir) / LOCK_FILE
    if not path.exists():
        return None
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ReferenceDataError(f"{path}: not valid JSON: {exc}") from exc
    _object(
        lock,
        LOCK_FILE,
        {"schema", "corpus_id", "tool", "fixtures"},
        {"schema", "corpus_id", "tool", "fixtures"},
    )
    if lock["schema"] != LOCK_SCHEMA or lock["corpus_id"] != corpus.corpus_id:
        raise ReferenceDataError(
            f"{LOCK_FILE} is not the lock of {corpus.corpus_id!r} ({LOCK_SCHEMA})"
        )
    unknown = set(lock["fixtures"]) - set(corpus.fixtures)
    if unknown:
        raise ReferenceDataError(
            f"{LOCK_FILE} locks fixtures the corpus does not define: {sorted(unknown)}"
        )
    return lock


def write_lock(corpus_dir: Path, lock: dict[str, Any]) -> None:
    path = Path(corpus_dir) / LOCK_FILE
    temporary = path.with_name(path.name + ".tmp")
    payload = json.dumps(lock, indent=2, sort_keys=True) + "\n"
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


# -- facts of one fixture ---------------------------------------------------------


def _selection(
    corpus: Corpus, resolved: Mapping[str, Any], dataroot: Path
) -> NuScenesSelection:
    return NuScenesSelection(
        dataroot=dataroot,
        version=corpus.source["version"],
        source_unit=resolved["source_unit"],
        channel_groups=frozenset(resolved["acquisition"]["channel_groups"]),
    )


def _recording_key(
    corpus: Corpus, resolved: Mapping[str, Any], tool_sha256: str, source_sha256: str
) -> str:
    return _digest(
        {
            "source": corpus.source,
            "source_unit": resolved["source_unit"],
            "acquisition": resolved["acquisition"],
            "tool": tool_sha256,
            "source_fingerprint": source_sha256,
        }
    ).removeprefix("sha256:")[:12]


def recordings_dir(cache_root: Path, corpus: Corpus) -> Path:
    return Path(cache_root) / corpus.corpus_id / "recordings"


def recording_path(cache_root: Path, corpus: Corpus, fixture_id: str, key: str) -> Path:
    return recordings_dir(cache_root, corpus) / f"{fixture_id}-{key}.mcap"


def read_recording_facts(path: Path) -> RecordingSummary:
    """sha256 and size of the bytes; message and topic counts from the MCAP's
    own statistics."""
    from mcap.reader import make_reader

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    with path.open("rb") as stream:
        summary = make_reader(stream).get_summary()
    if summary is None or summary.statistics is None:
        raise ReferenceDataError(f"{path}: no MCAP summary statistics (not finalized?)")
    counts = {
        summary.channels[channel_id].topic: count
        for channel_id, count in summary.statistics.channel_message_counts.items()
    }
    return RecordingSummary(
        path=path,
        sha256=f"sha256:{digest.hexdigest()}",
        size_bytes=path.stat().st_size,
        message_count=sum(counts.values()),
        first_log_time_ns=summary.statistics.message_start_time,
        last_log_time_ns=summary.statistics.message_end_time,
        topic_counts=counts,
    )


def _recording_entry(summary: RecordingSummary) -> dict[str, Any]:
    return {
        "sha256": summary.sha256,
        "size_bytes": summary.size_bytes,
        "message_count": summary.message_count,
        "topic_counts": dict(sorted(summary.topic_counts.items())),
    }


# -- reference labels -----------------------------------------------------------
#
# The label artifact is a function of the source's annotation tables and the
# tool (``tool_identity`` covers the devkit and this package), so the lock
# holds its content hash: any change of source or extractor shows up as a
# different hash. The cache file name carries that hash, so a changed artifact
# is never found under an old name.


def labels_dir(cache_root: Path, corpus: Corpus) -> Path:
    return Path(cache_root) / corpus.corpus_id / "labels"


def labels_path(cache_root: Path, corpus: Corpus, fixture_id: str, sha256: str) -> Path:
    short = sha256.removeprefix("sha256:")[:12]
    return labels_dir(cache_root, corpus) / f"{fixture_id}-{short}.labels.json"


def _labels_entry(data: bytes, artifact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": artifact["schema"],
        "sha256": f"sha256:{hashlib.sha256(data).hexdigest()}",
        "size_bytes": len(data),
        **label_counts(dict(artifact)),
    }


def compute_labels(
    corpus: Corpus, fixture_id: str, *, dataroot: Path, nusc: Any
) -> tuple[bytes, dict[str, Any]]:
    """The reference label artifact of a fixture, read from the source, and
    its lock entry."""
    adapter = NuScenesAdapter(
        _selection(corpus, corpus.resolve(fixture_id), dataroot), nusc=nusc
    )
    data = serialize_reference_labels(reference_labels(adapter))
    return data, _labels_entry(data, parse_reference_labels(data))


def _check_label_file(
    path: Path, expected: Mapping[str, Any]
) -> tuple[list[str], dict[str, Any] | None]:
    """Read a cached label artifact and compare it with ``expected`` (a lock
    entry). Returns the disagreements and, when the bytes are the expected
    ones, the parsed artifact."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"labels unreadable: {path}: {exc}"], None
    problems: list[str] = []
    actual = f"sha256:{hashlib.sha256(data).hexdigest()}"
    _compare(problems, "labels sha256", expected["sha256"], actual)
    _compare(problems, "labels size_bytes", expected["size_bytes"], len(data))
    if problems:
        return problems, None
    try:
        artifact = parse_reference_labels(data)
    except AcquisitionError as exc:
        return [str(exc)], None
    _compare(
        problems,
        "labels counts",
        {k: expected[k] for k in ("sample_count", "label_count")},
        label_counts(artifact),
    )
    return problems, None if problems else artifact


def _labels_differences(
    locked: Mapping[str, Any], actual: Mapping[str, Any]
) -> list[str]:
    problems: list[str] = []
    for field in ("schema", "sha256", "size_bytes", "sample_count", "label_count"):
        _compare(problems, f"labels {field}", locked.get(field), actual[field])
    return problems


def _write_once(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path``: temporary file, fsync, atomic rename. An
    existing ``path`` is never overwritten and a leftover ``.partial`` is
    reported, not adopted."""
    partial = path.with_name(path.name + ".partial")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stream = partial.open("xb")
    except FileExistsError as exc:
        raise ReferenceDataError(
            f"{partial} exists (another or an interrupted run); remove it to retry"
        ) from exc
    try:
        with stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(partial, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _prepare_labels(
    corpus: Corpus,
    fixture_id: str,
    *,
    dataroot: Path,
    nusc: Any,
    cache_root: Path,
    locked: Mapping[str, Any] | None,
    update_lock: bool,
) -> tuple[dict[str, Any], str, list[str]]:
    """Materialize a fixture's label artifact into the cache (write-once).
    Without ``update_lock`` the artifact the source produces must be the locked
    one. An existing cache file is verified, never replaced."""
    data, entry = compute_labels(corpus, fixture_id, dataroot=dataroot, nusc=nusc)
    if not update_lock:
        if locked is None:
            return (
                entry,
                "failed",
                [
                    f"labels are not in {LOCK_FILE} (run with --update-lock to lock them)"
                ],
            )
        differences = _labels_differences(locked, entry)
        if differences:
            return entry, "failed", differences
    path = labels_path(cache_root, corpus, fixture_id, entry["sha256"])
    if path.exists():
        problems, _ = _check_label_file(path, entry)
        return entry, "failed" if problems else "reused", problems
    _write_once(path, data)
    return entry, "materialized", []


def _locate_labels(
    corpus: Corpus, locked: Mapping[str, Any], fixture_id: str, cache_root: Path
) -> tuple[Path | None, dict[str, Any] | None, list[str]]:
    """The cached label artifact a lock entry describes, verified. Needs no
    source."""
    entry = locked.get("labels")
    if entry is None:
        return (
            None,
            None,
            [
                f"labels are not in {LOCK_FILE} (run reference-data-bootstrap "
                "with UPDATE_LOCK=1 to lock them)"
            ],
        )
    path = labels_path(cache_root, corpus, fixture_id, entry["sha256"])
    if not path.is_file():
        return (
            path,
            None,
            [f"labels not prepared: {path} (run reference-data-bootstrap)"],
        )
    problems, artifact = _check_label_file(path, entry)
    return path, artifact, problems


@dataclass
class FixtureFacts:
    fixture_id: str
    definition_sha256: str
    source: dict[str, Any]
    key: str
    path: Path
    plan_topic_counts: dict[str, int]
    source_seconds: float


def compute_facts(
    corpus: Corpus,
    fixture_id: str,
    *,
    dataroot: Path,
    cache_root: Path,
    tool: Mapping[str, Any],
    nusc: Any,
) -> FixtureFacts:
    resolved = corpus.resolve(fixture_id)
    started = time.monotonic()
    adapter = NuScenesAdapter(_selection(corpus, resolved, dataroot), nusc=nusc)
    fingerprint = adapter.source_fingerprint()
    source_seconds = time.monotonic() - started
    key = _recording_key(corpus, resolved, tool["identity_sha256"], fingerprint.sha256)
    return FixtureFacts(
        fixture_id=fixture_id,
        definition_sha256=corpus.definition_sha256(fixture_id),
        source={
            "fingerprint_sha256": fingerprint.sha256,
            "counts": fingerprint.counts,
            "blob_bytes": fingerprint.blob_bytes,
        },
        key=key,
        path=recording_path(cache_root, corpus, fixture_id, key),
        plan_topic_counts=adapter.plan_topic_counts(),
        source_seconds=source_seconds,
    )


def _compare(problems: list[str], what: str, locked: Any, actual: Any) -> None:
    if locked != actual:
        problems.append(f"{what}: locked {locked!r}, actual {actual!r}")


def check_against_lock(
    facts: FixtureFacts,
    locked: Mapping[str, Any] | None,
    lock_tool: Mapping[str, Any] | None,
    tool: Mapping[str, Any],
) -> list[str]:
    """Everything that disagrees with the lock before any recording is read."""
    if locked is None or lock_tool is None:
        return [
            f"{facts.fixture_id} is not in {LOCK_FILE} (run with --update-lock to lock it)"
        ]
    problems: list[str] = []
    _compare(
        problems, "tool identity", lock_tool["identity_sha256"], tool["identity_sha256"]
    )
    _compare(
        problems,
        "fixture definition",
        locked["definition_sha256"],
        facts.definition_sha256,
    )
    _compare(
        problems,
        "source fingerprint",
        locked["source"]["fingerprint_sha256"],
        facts.source["fingerprint_sha256"],
    )
    _compare(
        problems, "source counts", locked["source"]["counts"], facts.source["counts"]
    )
    _compare(
        problems,
        "source blob_bytes",
        locked["source"]["blob_bytes"],
        facts.source["blob_bytes"],
    )
    _compare(
        problems,
        "replay plan topic counts vs recording topic counts",
        locked["recording"]["topic_counts"],
        facts.plan_topic_counts,
    )
    return problems


def check_recording(locked: Mapping[str, Any], summary: RecordingSummary) -> list[str]:
    problems: list[str] = []
    entry = _recording_entry(summary)
    for field in ("sha256", "size_bytes", "message_count", "topic_counts"):
        _compare(
            problems, f"recording {field}", locked["recording"][field], entry[field]
        )
    return problems


# -- commands ------------------------------------------------------------------


def _new_nusc(corpus: Corpus, dataroot: Path) -> Any:
    from nuscenes.nuscenes import NuScenes

    return NuScenes(
        version=corpus.source["version"], dataroot=str(dataroot), verbose=False
    )


def _emit(record: Mapping[str, Any]) -> None:
    print(json.dumps(record, sort_keys=True), flush=True)


def inspect(
    corpus_dir: Path, fixtures: list[str], *, dataroot: Path, cache_root: Path
) -> int:
    """Print each fixture's definition and the facts of the live source. Reads
    only; compares nothing."""
    corpus = load_corpus(corpus_dir)
    tool = tool_identity()
    nusc = _new_nusc(corpus, dataroot)
    for fixture_id in fixtures:
        facts = compute_facts(
            corpus,
            fixture_id,
            dataroot=dataroot,
            cache_root=cache_root,
            tool=tool,
            nusc=nusc,
        )
        _emit(
            {
                "fixture_id": fixture_id,
                "definition": corpus.resolve(fixture_id),
                "definition_sha256": facts.definition_sha256,
                "source": facts.source,
                "plan_topic_counts": facts.plan_topic_counts,
                "recording_path": str(facts.path),
                "tool_identity_sha256": tool["identity_sha256"],
                "source_seconds": round(facts.source_seconds, 3),
            }
        )
    return 0


def verify(
    corpus_dir: Path, fixtures: list[str], *, dataroot: Path, cache_root: Path
) -> int:
    """Read-only: source, definition, tool identity and cached recording of
    every fixture against the lock. Exit 1 if any disagrees."""
    corpus = load_corpus(corpus_dir)
    lock = read_lock(corpus_dir, corpus)
    tool = tool_identity()
    nusc = _new_nusc(corpus, dataroot)
    failed = 0
    for fixture_id in fixtures:
        facts = compute_facts(
            corpus,
            fixture_id,
            dataroot=dataroot,
            cache_root=cache_root,
            tool=tool,
            nusc=nusc,
        )
        locked = lock["fixtures"].get(fixture_id) if lock else None
        problems = check_against_lock(
            facts, locked, lock["tool"] if lock else None, tool
        )
        status = "ok"
        record: dict[str, Any] = {}
        if not problems:
            if not facts.path.is_file():
                problems.append(
                    f"recording not prepared: {facts.path} (run reference-data-bootstrap)"
                )
            else:
                summary = read_recording_facts(facts.path)
                problems.extend(check_recording(locked, summary))
                record = _recording_entry(summary)
        labels_record: dict[str, Any] = {}
        if not problems:
            # The source's annotations must still produce the locked artifact,
            # and the cached file must be that artifact.
            if locked.get("labels") is None:
                problems.append(
                    f"labels are not in {LOCK_FILE} (run with --update-lock to lock them)"
                )
            else:
                _, labels_record = compute_labels(
                    corpus, fixture_id, dataroot=dataroot, nusc=nusc
                )
                problems.extend(_labels_differences(locked["labels"], labels_record))
                _, _, label_problems = _locate_labels(
                    corpus, locked, fixture_id, cache_root
                )
                problems.extend(label_problems)
        if problems:
            status, failed = "failed", failed + 1
        _emit(
            {
                "fixture_id": fixture_id,
                "status": status,
                "problems": problems,
                "path": str(facts.path),
                "source_seconds": round(facts.source_seconds, 3),
                **({"recording": record} if record else {}),
                **({"labels": labels_record} if labels_record else {}),
            }
        )
    return 1 if failed else 0


def resolve(
    corpus_dir: Path,
    fixtures: list[str],
    *,
    cache_root: Path,
    check_recordings: bool,
    check_labels: bool = False,
) -> int:
    """The locked facts of each fixture, without reading the source. The
    consumer's entry point: it needs the recordings, not the dataset.

    Always checks that the fixture is locked and that its resolved definition
    is the locked one. With ``check_recordings`` it also requires the tool
    identity to be the locked one, finds the cached recording by the key the
    lock implies (the lock holds the source fingerprint, so no source access
    is needed) and verifies its bytes, size and counts. It never converts,
    writes or deletes anything. With ``check_labels`` it also finds and verifies
    the fixture's cached reference label artifact. Exit 1 if any fixture
    disagrees."""
    corpus = load_corpus(corpus_dir)
    lock = read_lock(corpus_dir, corpus)
    tool = tool_identity() if check_recordings else None
    failed = 0
    for fixture_id in fixtures:
        locked = lock["fixtures"].get(fixture_id) if lock else None
        problems: list[str] = []
        path: Path | None = None
        labels_file: Path | None = None
        if locked is None:
            problems.append(
                f"{fixture_id} is not in {LOCK_FILE} (run reference-data-bootstrap "
                "with UPDATE_LOCK=1 to lock it)"
            )
        else:
            _compare(
                problems,
                "fixture definition",
                locked["definition_sha256"],
                corpus.definition_sha256(fixture_id),
            )
        if not problems and tool is not None:
            _compare(
                problems,
                "tool identity",
                lock["tool"]["identity_sha256"],
                tool["identity_sha256"],
            )
        if not problems and tool is not None:
            key = _recording_key(
                corpus,
                corpus.resolve(fixture_id),
                lock["tool"]["identity_sha256"],
                locked["source"]["fingerprint_sha256"],
            )
            path = recording_path(cache_root, corpus, fixture_id, key)
            if not path.is_file():
                problems.append(
                    f"recording not prepared: {path} (run reference-data-bootstrap)"
                )
            else:
                problems.extend(check_recording(locked, read_recording_facts(path)))
        if not problems and check_labels and locked is not None:
            labels_file, _, label_problems = _locate_labels(
                corpus, locked, fixture_id, cache_root
            )
            problems.extend(label_problems)
        if problems:
            failed += 1
        _emit(
            {
                "fixture_id": fixture_id,
                "source_unit": corpus.fixtures[fixture_id]["source_unit"],
                "status": "failed" if problems else "ok",
                "problems": problems,
                **({"path": str(path)} if path else {}),
                **({"recording": locked["recording"]} if locked else {}),
                **(
                    {"labels": {**locked["labels"], "path": str(labels_file)}}
                    if labels_file and not problems
                    else {}
                ),
            }
        )
    return 1 if failed else 0


def prepare(
    corpus_dir: Path,
    fixtures: list[str],
    *,
    dataroot: Path,
    cache_root: Path,
    update_lock: bool,
) -> int:
    """Materialize each fixture's batch MCAP into the cache (write-once) and
    verify it against the lock. A fixture that is already cached is verified,
    never regenerated. With ``update_lock`` the lock records what the current
    source and tool produce; without it any disagreement fails."""
    corpus = load_corpus(corpus_dir)
    lock = read_lock(corpus_dir, corpus)
    tool = tool_identity()
    if update_lock:
        if (
            lock is not None
            and lock["tool"]["identity_sha256"] != tool["identity_sha256"]
        ):
            missing = sorted(set(corpus.fixtures) - set(fixtures))
            if missing:
                raise ReferenceDataError(
                    "the tool identity changed: --update-lock must cover every fixture of "
                    f"the corpus (not covered: {missing})"
                )
            lock = None
        lock = lock or {
            "schema": LOCK_SCHEMA,
            "corpus_id": corpus.corpus_id,
            "tool": tool,
            "fixtures": {},
        }
    nusc = _new_nusc(corpus, dataroot)
    failed = 0
    for fixture_id in fixtures:
        facts = compute_facts(
            corpus,
            fixture_id,
            dataroot=dataroot,
            cache_root=cache_root,
            tool=tool,
            nusc=nusc,
        )
        locked = lock["fixtures"].get(fixture_id) if lock else None
        problems = (
            []
            if update_lock
            else check_against_lock(facts, locked, lock["tool"] if lock else None, tool)
        )
        record: dict[str, Any] = {"fixture_id": fixture_id, "path": str(facts.path)}
        convert_seconds = 0.0
        action = "reused"
        summary: RecordingSummary | None = None
        labels_entry: dict[str, Any] | None = None
        labels_action = ""
        if not problems:
            if facts.path.exists():
                summary = read_recording_facts(facts.path)
            else:
                action = "materialized"
                started = time.monotonic()
                adapter = NuScenesAdapter(
                    _selection(corpus, corpus.resolve(fixture_id), dataroot), nusc=nusc
                )
                summary = write_mcap(
                    adapter.events(), facts.path, origin=adapter.origin()
                )
                convert_seconds = time.monotonic() - started
            if update_lock:
                if action == "reused" and locked is not None:
                    # A cached file is adopted into the lock only if it is the
                    # recording the lock already describes.
                    problems.extend(check_recording(locked, summary))
                if not problems and facts.plan_topic_counts != dict(
                    sorted(summary.topic_counts.items())
                ):
                    problems.append(
                        "replay plan topic counts differ from the recording's topic counts"
                    )
            else:
                problems.extend(check_recording(locked, summary))
            if not problems:
                labels_entry, labels_action, label_problems = _prepare_labels(
                    corpus,
                    fixture_id,
                    dataroot=dataroot,
                    nusc=nusc,
                    cache_root=cache_root,
                    locked=(locked or {}).get("labels"),
                    update_lock=update_lock,
                )
                problems.extend(label_problems)
            if update_lock and not problems:
                lock["fixtures"][fixture_id] = {
                    "definition_sha256": facts.definition_sha256,
                    "source": facts.source,
                    "recording": _recording_entry(summary),
                    "labels": labels_entry,
                }
        if problems:
            failed += 1
        _emit(
            {
                **record,
                "status": "failed" if problems else action,
                "problems": problems,
                "conversion_seconds": round(convert_seconds, 3),
                "source_seconds": round(facts.source_seconds, 3),
                **({"recording": _recording_entry(summary)} if summary else {}),
                **(
                    {"labels": labels_entry, "labels_status": labels_action}
                    if labels_entry
                    else {}
                ),
            }
        )
    if update_lock and not failed:
        write_lock(corpus_dir, lock)
    return 1 if failed else 0


def render_labels(
    corpus_dir: Path,
    fixture_id: str,
    *,
    cache_root: Path,
    robot_run_id: str,
    label_set_id: str,
    output: Path,
) -> int:
    """Render a fixture's locked reference label artifact for one RobotRun as
    a ``sceneops.label_set/v1`` document. Reads no source: the lock and the
    cached artifact are the only inputs, and the artifact is verified against
    the lock first. The document is a deterministic function of (artifact,
    run id, label set id); it is written to ``output`` atomically and
    replaces an earlier rendering."""
    corpus = load_corpus(corpus_dir)
    lock = read_lock(corpus_dir, corpus)
    locked = lock["fixtures"].get(fixture_id) if lock else None
    problems: list[str] = []
    artifact: dict[str, Any] | None = None
    if locked is None:
        problems.append(
            f"{fixture_id} is not in {LOCK_FILE} (run reference-data-bootstrap "
            "with UPDATE_LOCK=1 to lock it)"
        )
    else:
        _compare(
            problems,
            "fixture definition",
            locked["definition_sha256"],
            corpus.definition_sha256(fixture_id),
        )
        if not problems:
            _, artifact, problems = _locate_labels(
                corpus, locked, fixture_id, cache_root
            )
    if problems or artifact is None:
        _emit({"fixture_id": fixture_id, "status": "failed", "problems": problems})
        return 1
    document = render_label_document(
        artifact, robot_run_id=robot_run_id, label_set_id=label_set_id
    )
    payload = json.dumps(document, sort_keys=True).encode()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output)
    _emit(
        {
            "fixture_id": fixture_id,
            "status": "ok",
            "problems": [],
            "path": str(output),
            "label_set_id": label_set_id,
            "robot_run_id": robot_run_id,
            "coverage_count": len(document["coverage"]),
            "label_count": len(document["labels"]),
            "sha256": f"sha256:{hashlib.sha256(payload).hexdigest()}",
            "labels_sha256": locked["labels"]["sha256"],
        }
    )
    return 0
