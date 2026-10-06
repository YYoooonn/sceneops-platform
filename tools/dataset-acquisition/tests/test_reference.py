"""Reference corpus: definition, fingerprints, write-once cache and the lock.

Runs on the synthetic nuScenes dataroot, so it proves the mechanism (what
agrees, what fails loudly, what is never rewritten) and nothing about the
real corpus, which ``make reference-data-verify`` checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from synthetic_nuscenes import UNIT, VERSION

from dataset_acquisition import reference
from dataset_acquisition.cli import main as cli_main
from dataset_acquisition.events import AcquisitionError

CORPUS_ID = "synthetic-v1"


def corpus_document(**overrides) -> dict:
    document = {
        "schema": reference.CORPUS_SCHEMA,
        "corpus_id": CORPUS_ID,
        "source": {"format": "nuscenes", "version": VERSION},
        "defaults": {
            "acquisition": {
                "channel_groups": ["camera", "lidar", "pose", "can", "mission"]
            },
            "replay": {"rate": 2.0, "wait_subscribers_seconds": 60},
        },
        "fixtures": [
            {"fixture_id": "unit-one", "source_unit": UNIT},
            {
                "fixture_id": "unit-two",
                "source_unit": "scene-0002",
                "acquisition": {"channel_groups": ["camera"]},
            },
        ],
        "scopes": {"smoke": ["unit-one"], "all": ["*"]},
    }
    document.update(overrides)
    return document


@pytest.fixture()
def corpus_dir(tmp_path: Path) -> Path:
    path = tmp_path / "config" / CORPUS_ID
    path.mkdir(parents=True)
    write_corpus(path, corpus_document())
    return path


def write_corpus(path: Path, document: dict) -> None:
    (path / reference.CORPUS_FILE).write_text(json.dumps(document))


@pytest.fixture()
def cache_root(tmp_path: Path) -> Path:
    return tmp_path / "cache"


def run(
    capsys, command: str, corpus_dir: Path, dataroot: Path, cache_root: Path, *args: str
):
    code = cli_main(
        [
            "reference",
            command,
            "--corpus",
            str(corpus_dir),
            "--dataroot",
            str(dataroot),
            "--cache-root",
            str(cache_root),
            *args,
        ]
    )
    out = capsys.readouterr().out
    return code, [json.loads(line) for line in out.splitlines() if line.strip()]


def lock_bytes(corpus_dir: Path) -> bytes:
    return (corpus_dir / reference.LOCK_FILE).read_bytes()


def cached(cache_root: Path) -> list[Path]:
    return sorted((cache_root / CORPUS_ID / "recordings").glob("*.mcap"))


# -- corpus definition -------------------------------------------------------------


def test_scopes_expand_and_fixtures_resolve_defaults_with_overrides(
    corpus_dir: Path,
) -> None:
    corpus = reference.load_corpus(corpus_dir)
    assert corpus.scopes == {"smoke": ["unit-one"], "all": ["unit-one", "unit-two"]}
    one, two = corpus.resolve("unit-one"), corpus.resolve("unit-two")
    assert one["acquisition"]["channel_groups"] == [
        "camera",
        "lidar",
        "pose",
        "can",
        "mission",
    ]
    assert two["acquisition"]["channel_groups"] == ["camera"]
    assert (
        one["replay"] == two["replay"] == {"rate": 2.0, "wait_subscribers_seconds": 60}
    )


def test_definition_hash_follows_the_resolved_fixture_only(corpus_dir: Path) -> None:
    before = reference.load_corpus(corpus_dir).definition_sha256("unit-one")
    document = corpus_document()
    # Same meaning, other spelling: group order is normalized.
    document["defaults"]["acquisition"]["channel_groups"] = [
        "mission",
        "can",
        "pose",
        "lidar",
        "camera",
    ]
    write_corpus(corpus_dir, document)
    assert reference.load_corpus(corpus_dir).definition_sha256("unit-one") == before
    document["defaults"]["replay"]["rate"] = 1.0
    write_corpus(corpus_dir, document)
    assert reference.load_corpus(corpus_dir).definition_sha256("unit-one") != before


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.update(extra=1), "unknown keys"),
        (lambda d: d.update(schema="other/1"), "schema"),
        (
            lambda d: d["fixtures"].append(dict(d["fixtures"][0])),
            "duplicate fixture_id",
        ),
        (lambda d: d["scopes"].update(bad=["nope"]), "scopes.bad"),
        (
            lambda d: d["defaults"]["acquisition"].update(channel_groups=["radar"]),
            "channel_groups",
        ),
        (lambda d: d["defaults"]["replay"].update(rate=-1), "rate"),
        (lambda d: d["source"].update(format="waymo"), "not supported"),
        (lambda d: d["fixtures"][1].update(source_unit=UNIT), "same source_unit"),
        (lambda d: d.update(corpus_id="other-v1"), "directory name"),
    ],
)
def test_an_invalid_corpus_is_refused(corpus_dir: Path, mutate, message: str) -> None:
    document = corpus_document()
    mutate(document)
    write_corpus(corpus_dir, document)
    with pytest.raises(AcquisitionError, match=message):
        reference.load_corpus(corpus_dir)


def test_selection_needs_a_known_scope_or_fixture(corpus_dir: Path) -> None:
    corpus = reference.load_corpus(corpus_dir)
    assert corpus.select("smoke", ["unit-two"]) == ["unit-one", "unit-two"]
    for scope, fixtures in ((None, []), ("missing", []), (None, ["missing"])):
        with pytest.raises(AcquisitionError):
            corpus.select(scope, fixtures)


# -- inspect / prepare / verify ---------------------------------------------------------


def test_inspect_reports_definition_and_source_without_writing(
    capsys, corpus_dir: Path, dataroot: Path, cache_root: Path
) -> None:
    code, (record,) = run(
        capsys, "inspect", corpus_dir, dataroot, cache_root, "--scope", "smoke"
    )
    assert code == 0
    assert record["source"]["counts"] == {
        "samples": 2,
        "camera_frames": 5,
        "lidar_sweeps": 3,
        "ego_poses": 9,
        "can_messages": {"pose": 6, "ms_imu": 11, "vehicle_monitor": 2},
    }
    assert record["source"]["fingerprint_sha256"].startswith("sha256:")
    assert record["plan_topic_counts"]["/mission/status"] == 2
    assert not cache_root.exists()
    assert not (corpus_dir / reference.LOCK_FILE).exists()


def test_prepare_without_a_lock_converts_nothing_and_writes_no_lock(
    capsys, corpus_dir: Path, dataroot: Path, cache_root: Path
) -> None:
    code, (record,) = run(
        capsys, "prepare", corpus_dir, dataroot, cache_root, "--scope", "smoke"
    )
    assert code == 1
    assert record["status"] == "failed"
    assert "not in corpus.lock.json" in record["problems"][0]
    assert not cache_root.exists()
    assert not (corpus_dir / reference.LOCK_FILE).exists()


def test_update_lock_materializes_locks_and_verify_agrees(
    capsys, corpus_dir: Path, dataroot: Path, cache_root: Path
) -> None:
    code, (record,) = run(
        capsys,
        "prepare",
        corpus_dir,
        dataroot,
        cache_root,
        "--scope",
        "smoke",
        "--update-lock",
    )
    assert code == 0 and record["status"] == "materialized"
    lock = json.loads(lock_bytes(corpus_dir))
    entry = lock["fixtures"]["unit-one"]
    assert entry["recording"]["sha256"] == record["recording"]["sha256"]
    assert entry["recording"]["topic_counts"]["/lidar/top/points"] == 3
    assert entry["definition_sha256"].startswith("sha256:")
    assert lock["tool"]["identity_sha256"].startswith("sha256:")
    assert len(cached(cache_root)) == 1

    code, (verified,) = run(
        capsys, "verify", corpus_dir, dataroot, cache_root, "--scope", "smoke"
    )
    assert code == 0 and verified["status"] == "ok" and verified["problems"] == []


def test_a_verified_cache_is_reused_not_regenerated(
    capsys, corpus_dir: Path, dataroot: Path, cache_root: Path
) -> None:
    run(
        capsys,
        "prepare",
        corpus_dir,
        dataroot,
        cache_root,
        "--scope",
        "smoke",
        "--update-lock",
    )
    (path,) = cached(cache_root)
    inode = path.stat().st_ino
    code, (record,) = run(
        capsys, "prepare", corpus_dir, dataroot, cache_root, "--scope", "smoke"
    )
    assert (code, record["status"]) == (0, "reused")
    assert record["conversion_seconds"] == 0
    assert cached(cache_root) == [path] and path.stat().st_ino == inode


def test_update_lock_adds_fixtures_and_keeps_the_others(
    capsys, corpus_dir: Path, dataroot: Path, cache_root: Path
) -> None:
    run(
        capsys,
        "prepare",
        corpus_dir,
        dataroot,
        cache_root,
        "--scope",
        "smoke",
        "--update-lock",
    )
    first = json.loads(lock_bytes(corpus_dir))["fixtures"]["unit-one"]
    code, records = run(
        capsys,
        "prepare",
        corpus_dir,
        dataroot,
        cache_root,
        "--scope",
        "all",
        "--update-lock",
    )
    assert code == 0
    assert [r["status"] for r in records] == ["reused", "materialized"]
    lock = json.loads(lock_bytes(corpus_dir))
    assert set(lock["fixtures"]) == {"unit-one", "unit-two"}
    assert lock["fixtures"]["unit-one"] == first


def test_verify_fails_for_a_fixture_that_is_not_prepared(
    capsys, corpus_dir: Path, dataroot: Path, cache_root: Path
) -> None:
    run(
        capsys,
        "prepare",
        corpus_dir,
        dataroot,
        cache_root,
        "--scope",
        "smoke",
        "--update-lock",
    )
    (cached(cache_root)[0]).unlink()
    code, (record,) = run(
        capsys, "verify", corpus_dir, dataroot, cache_root, "--scope", "smoke"
    )
    assert code == 1 and "not prepared" in record["problems"][0]


def test_a_changed_source_fails_before_any_conversion_and_changes_nothing(
    capsys, corpus_dir: Path, dataroot: Path, cache_root: Path
) -> None:
    run(
        capsys,
        "prepare",
        corpus_dir,
        dataroot,
        cache_root,
        "--scope",
        "smoke",
        "--update-lock",
    )
    lock_before, files_before = lock_bytes(corpus_dir), cached(cache_root)
    blob = dataroot / "samples" / "CAM_FRONT" / "sd-cf-1.jpg"
    data = bytearray(blob.read_bytes())
    data[10] ^= 0xFF  # same size, other content
    blob.write_bytes(bytes(data))

    for command in ("verify", "prepare"):
        code, (record,) = run(
            capsys, command, corpus_dir, dataroot, cache_root, "--scope", "smoke"
        )
        assert code == 1 and record["status"] == "failed"
        assert any("source fingerprint" in p for p in record["problems"])
    assert lock_bytes(corpus_dir) == lock_before
    assert cached(cache_root) == files_before  # no new key, nothing rewritten


def test_a_changed_definition_fails_against_the_lock(
    capsys, corpus_dir: Path, dataroot: Path, cache_root: Path
) -> None:
    run(
        capsys,
        "prepare",
        corpus_dir,
        dataroot,
        cache_root,
        "--scope",
        "smoke",
        "--update-lock",
    )
    document = corpus_document()
    document["fixtures"][0]["replay"] = {"rate": 4.0}
    write_corpus(corpus_dir, document)
    code, (record,) = run(
        capsys, "verify", corpus_dir, dataroot, cache_root, "--scope", "smoke"
    )
    assert code == 1
    assert any(p.startswith("fixture definition") for p in record["problems"])


def test_a_corrupt_cached_recording_is_reported_never_replaced(
    capsys, corpus_dir: Path, dataroot: Path, cache_root: Path
) -> None:
    run(
        capsys,
        "prepare",
        corpus_dir,
        dataroot,
        cache_root,
        "--scope",
        "smoke",
        "--update-lock",
    )
    (path,) = cached(cache_root)
    lock_before = lock_bytes(corpus_dir)
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))
    corrupt = path.read_bytes()

    for args in (("verify",), ("prepare",), ("prepare", "--update-lock")):
        code, (record,) = run(
            capsys,
            args[0],
            corpus_dir,
            dataroot,
            cache_root,
            "--scope",
            "smoke",
            *args[1:],
        )
        assert code == 1 and record["status"] == "failed", args
        assert any("recording sha256" in p for p in record["problems"]), args
        assert path.read_bytes() == corrupt and cached(cache_root) == [path]
        assert lock_bytes(corpus_dir) == lock_before


def test_the_same_pinned_environment_gives_byte_identical_recordings(
    capsys, corpus_dir: Path, dataroot: Path, tmp_path: Path
) -> None:
    shas = []
    for name in ("first", "second"):
        _, (record,) = run(
            capsys,
            "prepare",
            corpus_dir,
            dataroot,
            tmp_path / name,
            "--scope",
            "smoke",
            "--update-lock",
        )
        shas.append(record["recording"]["sha256"])
    assert shas[0] == shas[1]


def test_a_changed_tool_identity_invalidates_the_lock_and_needs_the_full_corpus(
    capsys, monkeypatch, corpus_dir: Path, dataroot: Path, cache_root: Path
) -> None:
    run(
        capsys,
        "prepare",
        corpus_dir,
        dataroot,
        cache_root,
        "--scope",
        "all",
        "--update-lock",
    )
    lock_before = lock_bytes(corpus_dir)
    real = reference.tool_identity()
    changed = {**real, "version": "9.9.9", "identity_sha256": "sha256:" + "0" * 64}
    monkeypatch.setattr(reference, "tool_identity", lambda: changed)

    code, (record,) = run(
        capsys, "verify", corpus_dir, dataroot, cache_root, "--scope", "smoke"
    )
    assert code == 1 and any("tool identity" in p for p in record["problems"])

    with pytest.raises(AcquisitionError, match="must cover every fixture"):
        reference.prepare(
            corpus_dir,
            ["unit-one"],
            dataroot=dataroot,
            cache_root=cache_root,
            update_lock=True,
        )
    assert lock_bytes(corpus_dir) == lock_before

    code, records = run(
        capsys,
        "prepare",
        corpus_dir,
        dataroot,
        cache_root,
        "--scope",
        "all",
        "--update-lock",
    )
    assert code == 0
    assert (
        json.loads(lock_bytes(corpus_dir))["tool"]["identity_sha256"]
        == changed["identity_sha256"]
    )


def test_update_lock_is_only_a_prepare_option(
    capsys, corpus_dir: Path, dataroot: Path, cache_root: Path
) -> None:
    code = cli_main(
        [
            "reference",
            "verify",
            "--corpus",
            str(corpus_dir),
            "--dataroot",
            str(dataroot),
            "--cache-root",
            str(cache_root),
            "--scope",
            "smoke",
            "--update-lock",
        ]
    )
    assert code == 1
    assert "--update-lock applies to" in capsys.readouterr().err


# -- resolve: the consumer's view, no source ------------------------------------------


def resolve(capsys, corpus_dir: Path, cache_root: Path, *args: str):
    code = cli_main(
        [
            "reference",
            "resolve",
            "--corpus",
            str(corpus_dir),
            "--cache-root",
            str(cache_root),
            *args,
        ]
    )
    out = capsys.readouterr().out
    return code, [json.loads(line) for line in out.splitlines() if line.strip()]


@pytest.fixture()
def prepared(capsys, corpus_dir: Path, dataroot: Path, cache_root: Path) -> Path:
    code, _ = run(
        capsys,
        "prepare",
        corpus_dir,
        dataroot,
        cache_root,
        "--scope",
        "all",
        "--update-lock",
    )
    assert code == 0
    return cache_root


def test_resolve_returns_locked_facts_and_the_recording_path_without_the_source(
    capsys, corpus_dir: Path, prepared: Path
) -> None:
    code, records = resolve(capsys, corpus_dir, prepared, "--scope", "all")
    assert code == 0
    assert [r["fixture_id"] for r in records] == ["unit-one", "unit-two"]
    lock = json.loads(lock_bytes(corpus_dir))
    for record in records:
        assert record["status"] == "ok" and record["problems"] == []
        assert (
            record["recording"] == lock["fixtures"][record["fixture_id"]]["recording"]
        )
        assert Path(record["path"]) in cached(prepared)
    assert records[0]["source_unit"] == UNIT


def test_resolve_lock_only_reads_no_recording(
    capsys, corpus_dir: Path, tmp_path: Path, prepared: Path
) -> None:
    code, (record,) = resolve(
        capsys,
        corpus_dir,
        tmp_path / "no-cache",
        "--fixture",
        "unit-one",
        "--lock-only",
    )
    assert code == 0 and record["status"] == "ok" and "path" not in record


def test_resolve_fails_loudly_for_missing_or_corrupt_recordings_and_changes_nothing(
    capsys, corpus_dir: Path, prepared: Path
) -> None:
    one, two = cached(prepared)
    two.unlink()
    data = bytearray(one.read_bytes())
    data[len(data) // 2] ^= 0xFF
    one.write_bytes(bytes(data))
    corrupt, lock_before = one.read_bytes(), lock_bytes(corpus_dir)

    code, records = resolve(capsys, corpus_dir, prepared, "--scope", "all")
    assert code == 1
    by_id = {r["fixture_id"]: r for r in records}
    assert any("recording sha256" in p for p in by_id["unit-one"]["problems"])
    assert any("not prepared" in p for p in by_id["unit-two"]["problems"])
    assert one.read_bytes() == corrupt and cached(prepared) == [one]
    assert lock_bytes(corpus_dir) == lock_before


def test_resolve_refuses_an_unlocked_fixture_and_a_changed_definition(
    capsys, corpus_dir: Path, tmp_path: Path, dataroot: Path, cache_root: Path
) -> None:
    run(
        capsys,
        "prepare",
        corpus_dir,
        dataroot,
        cache_root,
        "--scope",
        "smoke",
        "--update-lock",
    )
    code, (_, unlocked) = resolve(
        capsys, corpus_dir, cache_root, "--scope", "all", "--lock-only"
    )
    assert code == 1 and "not in corpus.lock.json" in unlocked["problems"][0]

    document = corpus_document()
    document["fixtures"][0]["replay"] = {"rate": 4.0}
    write_corpus(corpus_dir, document)
    code, (record,) = resolve(
        capsys, corpus_dir, cache_root, "--scope", "smoke", "--lock-only"
    )
    assert code == 1 and record["problems"][0].startswith("fixture definition")


def test_resolve_checks_the_tool_identity_unless_lock_only(
    capsys, monkeypatch, corpus_dir: Path, prepared: Path
) -> None:
    real = reference.tool_identity()
    monkeypatch.setattr(
        reference,
        "tool_identity",
        lambda: {**real, "identity_sha256": "sha256:" + "0" * 64},
    )
    code, (record,) = resolve(capsys, corpus_dir, prepared, "--fixture", "unit-one")
    assert code == 1 and "tool identity" in record["problems"][0]
    code, _ = resolve(
        capsys, corpus_dir, prepared, "--fixture", "unit-one", "--lock-only"
    )
    assert code == 0


def test_lock_only_is_a_resolve_option(
    capsys, corpus_dir: Path, dataroot: Path, cache_root: Path
) -> None:
    code = cli_main(
        [
            "reference",
            "verify",
            "--corpus",
            str(corpus_dir),
            "--dataroot",
            str(dataroot),
            "--cache-root",
            str(cache_root),
            "--scope",
            "smoke",
            "--lock-only",
        ]
    )
    assert code == 1 and "--lock-only applies to" in capsys.readouterr().err
