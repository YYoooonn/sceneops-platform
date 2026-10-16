"""Canonical unit identity and unit revision contracts (ADR-015 §3, §6.1,
§6.4; MS-6, MS-7, MS-14).

    unit       unit_id = f(domain, source_kind, source_id, unit_key)
    revision   (unit_id, manifest_checksum); manifest_artifact_id derived

Expected ids are recomputed here from the documented formulas with
``hashlib`` and ``json`` directly, never taken from the functions under test.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, replace

import pytest

from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.episodes.schemas import episode_id_for, project_episode_record
from sceneops_core.episodes.testing import action, episode_manifest, state
from sceneops_core.provenance import (
    InconsistentRevisionsError,
    NonDeterministicProducerError,
    PinnedContent,
    PinnedRecording,
    RecordingSourceRevision,
    RevisionDecision,
    RevisionPinError,
    canonical_unit_id,
    check_revision_pin,
    decide_revision_registration,
    manifest_artifact_id_for,
    unit_id_domain,
)
from sceneops_core.scenes.schemas import project_scene_record, scene_id_for
from sceneops_core.scenes.testing import build_scene_manifest, recording_source
from sceneops_core.sources import (
    SnapshotDeclaration,
    SnapshotSelection,
    SourceContentInventory,
    SourceFileEntry,
    SourceSnapshotDescriptor,
)

SCENE_TOKEN = "cc8c0bf57f984915a77078b10eb33198"


def _sha(document: dict) -> str:
    """The documented canonical JSON form, written out independently."""
    data = json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(data).hexdigest()


def _expected_unit_id(domain, source_kind, source_id, unit_key) -> str:
    digest = _sha(
        {
            "unit_id_schema": "sceneops.unit_id/v2",
            "domain": domain,
            "source_kind": source_kind,
            "source_id": source_id,
            "unit_key": unit_key,
        }
    )
    return f"{domain}-{digest[:32]}"


def _expected_manifest_artifact_id(unit_id, manifest_checksum) -> str:
    digest = _sha(
        {
            "manifest_artifact_id_schema": "sceneops.unit_manifest_artifact_id/v1",
            "unit_id": unit_id,
            "manifest_checksum": manifest_checksum,
        }
    )
    return f"{unit_id.split('-', 1)[0]}-manifest-{digest[:32]}"


def _inventory(*files: tuple[str, str]) -> SourceContentInventory:
    files = files or (("v1.0-mini/scene.json", "1"), ("samples/a.jpg", "2"))
    return SourceContentInventory.create(
        [
            SourceFileEntry(path=path, size_bytes=10, sha256="sha256:" + digit * 64)
            for path, digit in files
        ]
    )


def _snapshot(revision: str, inventory: SourceContentInventory, **annotations):
    return SourceSnapshotDescriptor(
        declaration=SnapshotDeclaration(
            format="nuscenes",
            upstream="nuscenes",
            upstream_revision=revision,
            selection=SnapshotSelection(include=("samples/**", "v1.0-mini/**")),
        ),
        content_id=inventory.content_id,
        annotations=annotations,
    )


def _content_source(inventory=None, *, unit_key=SCENE_TOKEN, start=10, end=20):
    return PinnedContent(inventory=inventory or _inventory()).unit_source(
        source_clock="nuscenes.timestamp",
        start_timestamp_ns=start,
        end_timestamp_ns=end,
        unit_key=unit_key,
    )


# --- unit identity: the documented formula ------------------------------------------


@pytest.mark.parametrize("domain", ["scene", "episode"])
def test_recording_unit_id_is_the_documented_formula(domain):
    source = recording_source(robot_run_id="run-1", unit_key="segment-0003")
    assert canonical_unit_id(domain=domain, source=source) == _expected_unit_id(
        domain, "recording", "run-1", "segment-0003"
    )


@pytest.mark.parametrize("domain", ["scene", "episode"])
def test_content_unit_id_is_the_documented_formula(domain):
    inventory = _inventory()
    source = _content_source(inventory)
    assert canonical_unit_id(domain=domain, source=source) == _expected_unit_id(
        domain, "content", inventory.content_id, SCENE_TOKEN
    )


def test_domain_helpers_are_the_canonical_unit_id_of_their_domain():
    source = recording_source()
    assert scene_id_for(source) == canonical_unit_id(domain="scene", source=source)
    assert episode_id_for(source) == canonical_unit_id(domain="episode", source=source)
    assert scene_id_for(source) != episode_id_for(source)
    content = _content_source()
    assert scene_id_for(content) == canonical_unit_id(domain="scene", source=content)


@pytest.mark.parametrize(
    "function",
    [canonical_unit_id, scene_id_for, episode_id_for, manifest_artifact_id_for],
)
def test_identity_takes_no_dataset_version_snapshot_or_execution_input(function):
    parameters = set(inspect.signature(function).parameters)
    forbidden = ("dataset", "version", "snapshot", "job", "uri", "fingerprint")
    assert not [name for name in parameters if any(f in name for f in forbidden)]


# --- unit identity: stability -------------------------------------------------------


def test_recording_unit_id_is_stable_across_revisions_and_windows():
    """A rebuild of the same unit key of the same RobotRun is the same unit,
    whatever its recording checksum, window, clock or producer."""
    base = recording_source(robot_run_id="run-1", unit_key="segment-0000")
    rebuilt = recording_source(
        robot_run_id="run-1",
        unit_key="segment-0000",
        recording_checksum="sha256:" + "9" * 64,
        start_timestamp_ns=5,
        end_timestamp_ns=6,
        source_clock="sensor.header_stamp",
    )
    assert scene_id_for(base) == scene_id_for(rebuilt)
    assert scene_id_for(base) == scene_id_for(base.model_copy())


def test_pinned_recording_bindings_give_the_ids_of_their_unit_keys():
    pinned = PinnedRecording(
        robot_run_id="run-1",
        recording_checksum="sha256:" + "a" * 64,
        recording_clock="mcap_log_time",
    )
    source = pinned.segment_source(
        source_clock="mcap_log_time",
        start_timestamp_ns=0,
        end_timestamp_ns=1,
        unit_key="segment-0000",
    )
    assert scene_id_for(source) == _expected_unit_id(
        "scene", "recording", "run-1", "segment-0000"
    )


def test_identical_content_in_different_snapshots_is_the_same_unit():
    """Units follow bytes, snapshots follow declarations (ADR-015 §3, R6):
    two upstream revisions declaring identical bytes build the same units."""
    inventory = _inventory()
    first = _snapshot("v1.0-mini", inventory)
    second = _snapshot("v1.0-mini-r2", inventory, licence="CC BY-NC-SA 4.0")
    assert first.snapshot_id != second.snapshot_id
    assert first.content_id == second.content_id

    def unit_id(descriptor: SourceSnapshotDescriptor) -> str:
        # A build resolves the snapshot to its content once and builds from
        # the content alone; nothing of the descriptor reaches the binding.
        assert descriptor.content_id == inventory.content_id
        return scene_id_for(_content_source(inventory))

    assert unit_id(first) == unit_id(second)
    for value in (first.snapshot_id, second.snapshot_id):
        assert value not in unit_id(first)


def test_content_unit_id_is_stable_across_windows_and_clocks():
    inventory = _inventory()
    a = _content_source(inventory, start=10, end=20)
    b = PinnedContent(inventory=inventory).unit_source(
        source_clock="lerobot.frame_time",
        start_timestamp_ns=0,
        end_timestamp_ns=99,
        unit_key=SCENE_TOKEN,
    )
    assert episode_id_for(a) == episode_id_for(b)


# --- unit identity: distinct sources ------------------------------------------------


def test_distinct_sources_and_keys_are_distinct_units():
    inventory = _inventory()
    other_bytes = _inventory(("v1.0-mini/scene.json", "1"), ("samples/a.jpg", "3"))
    assert other_bytes.content_id != inventory.content_id
    ids = [
        scene_id_for(recording_source(robot_run_id="run-1", unit_key="segment-0000")),
        scene_id_for(recording_source(robot_run_id="run-2", unit_key="segment-0000")),
        scene_id_for(recording_source(robot_run_id="run-1", unit_key="segment-0001")),
        scene_id_for(_content_source(inventory)),
        scene_id_for(_content_source(other_bytes)),
        scene_id_for(_content_source(inventory, unit_key="other-token")),
        episode_id_for(_content_source(inventory)),
    ]
    assert len(set(ids)) == len(ids)


def test_source_kind_separates_a_run_id_spelled_like_a_content_id():
    inventory = _inventory()
    run_id = inventory.content_id  # a syntactically valid RobotRun id
    recording = recording_source(robot_run_id=run_id, unit_key=SCENE_TOKEN)
    content = _content_source(inventory)
    assert recording.source_id == content.source_id
    assert scene_id_for(recording) != scene_id_for(content)


def test_source_id_and_unit_key_cannot_be_reshuffled_into_a_collision():
    a = recording_source(robot_run_id="run-a", unit_key="b")
    b = recording_source(robot_run_id="run", unit_key="a-b")
    assert scene_id_for(a) != scene_id_for(b)


def test_unit_identity_rejects_what_is_not_a_domain_or_a_binding():
    with pytest.raises(ValueError, match="domain"):
        canonical_unit_id(domain="dataset", source=recording_source())  # type: ignore[arg-type]
    revision = RecordingSourceRevision(
        robot_run_id="run-1", recording_checksum="sha256:" + "1" * 64
    )
    with pytest.raises(TypeError, match="source binding"):
        canonical_unit_id(domain="scene", source=revision)  # type: ignore[arg-type]


def test_unit_id_domain_reads_the_domain_and_rejects_other_ids():
    source = recording_source()
    assert unit_id_domain(scene_id_for(source)) == "scene"
    assert unit_id_domain(episode_id_for(source)) == "episode"
    for value in (
        "scene-missing",
        "dataset-" + "0" * 32,
        "scene-" + "0" * 31,
        "scene-" + "A" * 32,
        robot_run_recording_artifact_id("run-1"),
        42,
    ):
        with pytest.raises(ValueError, match="canonical unit id"):
            unit_id_domain(value)  # type: ignore[arg-type]


# --- revision identity --------------------------------------------------------------


def test_manifest_artifact_id_is_the_documented_function_of_the_revision():
    manifest = build_scene_manifest()
    unit_id = scene_id_for(manifest.lineage.source)
    checksum = manifest.checksum()
    assert manifest_artifact_id_for(
        unit_id=unit_id, manifest_checksum=checksum
    ) == _expected_manifest_artifact_id(unit_id, checksum)

    episode = episode_manifest([state("/odom", 0, x=1.0)])
    episode_unit = episode_id_for(episode.lineage.source)
    artifact_id = manifest_artifact_id_for(
        unit_id=episode_unit, manifest_checksum=episode.checksum()
    )
    assert artifact_id.startswith("episode-manifest-")


def test_unit_and_revision_are_separate_identities():
    """A changed build configuration is a new revision of the same unit: the
    unit id stays, the checksum and manifest artifact id change."""
    a = build_scene_manifest(build_config={"channels": ["CAM_FRONT", "LIDAR_TOP"]})
    b = build_scene_manifest(build_config={"channels": ["CAM_FRONT"]})
    unit_a, unit_b = scene_id_for(a.lineage.source), scene_id_for(b.lineage.source)
    assert unit_a == unit_b
    assert a.checksum() != b.checksum()
    assert manifest_artifact_id_for(
        unit_id=unit_a, manifest_checksum=a.checksum()
    ) != manifest_artifact_id_for(unit_id=unit_b, manifest_checksum=b.checksum())


def test_a_revision_is_the_same_whatever_dataset_version_records_it():
    manifest = build_scene_manifest()
    records = [
        project_scene_record(
            dataset_id=dataset_id,
            dataset_version=version,
            manifest=manifest,
            manifest_artifact_id=manifest_artifact_id_for(
                unit_id=scene_id_for(manifest.lineage.source),
                manifest_checksum=manifest.checksum(),
            ),
            manifest_checksum=manifest.checksum(),
        )
        for dataset_id, version in (("d", "v1"), ("d", "v2"), ("other", "v1"))
    ]
    assert len({r.scene_id for r in records}) == 1
    assert len({(r.manifest_artifact_id, r.manifest_checksum) for r in records}) == 1


def test_the_same_checksum_under_two_units_is_two_revisions():
    checksum = "sha256:" + "c" * 64
    one = scene_id_for(recording_source(unit_key="segment-0000"))
    two = scene_id_for(recording_source(unit_key="segment-0001"))
    assert manifest_artifact_id_for(
        unit_id=one, manifest_checksum=checksum
    ) != manifest_artifact_id_for(unit_id=two, manifest_checksum=checksum)


@pytest.mark.parametrize(
    ("unit_id", "checksum"),
    [
        ("scene-missing", "sha256:" + "0" * 64),
        ("art-robotrun-run-1", "sha256:" + "0" * 64),
        (None, "sha256:" + "0" * 64),
        ("SCENE", "sha256:" + "0" * 64),
        ("valid", "0" * 64),
        ("valid", "sha256:" + "0" * 63),
        ("valid", None),
    ],
)
def test_manifest_artifact_id_rejects_malformed_inputs(unit_id, checksum):
    if unit_id in ("valid", "SCENE"):
        unit_id = scene_id_for(recording_source()) if unit_id == "valid" else unit_id
    with pytest.raises(ValueError):
        manifest_artifact_id_for(unit_id=unit_id, manifest_checksum=checksum)


def test_check_revision_pin_accepts_only_the_derived_artifact_id():
    unit_id = scene_id_for(recording_source())
    other_unit = scene_id_for(recording_source(unit_key="segment-0009"))
    checksum = "sha256:" + "d" * 64
    pinned = manifest_artifact_id_for(unit_id=unit_id, manifest_checksum=checksum)
    check_revision_pin(
        unit_id=unit_id, manifest_artifact_id=pinned, manifest_checksum=checksum
    )
    for artifact_id, unit, digest in (
        ("art-1", unit_id, checksum),
        (pinned, other_unit, checksum),
        (pinned, unit_id, "sha256:" + "e" * 64),
    ):
        with pytest.raises(RevisionPinError):
            check_revision_pin(
                unit_id=unit, manifest_artifact_id=artifact_id, manifest_checksum=digest
            )


# --- revision registration decision -------------------------------------------------


@dataclass(frozen=True)
class Facts:
    manifest_artifact_id: str
    manifest_checksum: str
    producer_fingerprint: str


UNIT = scene_id_for(recording_source(robot_run_id="run-1", unit_key="segment-0000"))


def _facts(checksum_digit: str, fingerprint_digit: str, unit_id: str = UNIT) -> Facts:
    checksum = "sha256:" + checksum_digit * 64
    return Facts(
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=unit_id, manifest_checksum=checksum
        ),
        manifest_checksum=checksum,
        producer_fingerprint="sha256:" + fingerprint_digit * 64,
    )


def _decide(candidate: Facts, *registered: Facts) -> RevisionDecision:
    return decide_revision_registration(
        unit_id=UNIT, candidate=candidate, registered=registered
    )


def test_decision_new_unit_when_nothing_is_registered():
    assert _decide(_facts("1", "a")) is RevisionDecision.NEW_UNIT


def test_decision_identical_revision_is_a_no_op():
    revision = _facts("1", "a")
    assert _decide(revision, revision) is RevisionDecision.EXISTING_REVISION
    assert (
        _decide(revision, _facts("2", "b"), revision)
        is RevisionDecision.EXISTING_REVISION
    )


def test_decision_new_fingerprint_is_a_new_revision_beside_the_others():
    assert _decide(_facts("2", "b"), _facts("1", "a")) is RevisionDecision.NEW_REVISION
    assert (
        _decide(_facts("3", "c"), _facts("1", "a"), _facts("2", "b"))
        is RevisionDecision.NEW_REVISION
    )


def test_decision_same_fingerprint_with_another_checksum_fails_loudly():
    with pytest.raises(NonDeterministicProducerError, match="not deterministic"):
        _decide(_facts("2", "a"), _facts("1", "a"))


def test_decision_converges_on_retry_and_is_order_independent():
    """A retried registration sees its own earlier write and becomes a
    no-op; the order the registered revisions are read in never matters."""
    registered = [_facts("1", "a"), _facts("2", "b")]
    candidate = _facts("3", "c")
    assert _decide(candidate, *registered) is RevisionDecision.NEW_REVISION
    after = [*registered, candidate]
    for order in (after, list(reversed(after))):
        assert _decide(candidate, *order) is RevisionDecision.EXISTING_REVISION
    with pytest.raises(NonDeterministicProducerError):
        _decide(_facts("4", "c"), *reversed(after))


@pytest.mark.parametrize(
    "registered",
    [
        # one fingerprint, two checksums
        (_facts("1", "a"), _facts("2", "a")),
        # one checksum, two fingerprints
        (
            _facts("1", "a"),
            replace(_facts("1", "a"), producer_fingerprint="sha256:" + "b" * 64),
        ),
    ],
)
def test_decision_refuses_registered_revisions_that_already_conflict(registered):
    with pytest.raises(InconsistentRevisionsError):
        _decide(_facts("9", "f"), *registered)


def test_decision_refuses_a_candidate_contradicting_its_registered_checksum():
    registered = _facts("1", "a")
    claim = replace(registered, producer_fingerprint="sha256:" + "b" * 64)
    with pytest.raises(InconsistentRevisionsError, match="claims"):
        _decide(claim, registered)


def test_decision_refuses_revisions_of_another_unit():
    other_unit = scene_id_for(recording_source(robot_run_id="run-2"))
    with pytest.raises(RevisionPinError):
        _decide(_facts("1", "a"), _facts("2", "b", unit_id=other_unit))
    with pytest.raises(RevisionPinError):
        _decide(_facts("1", "a", unit_id=other_unit))
    with pytest.raises(RevisionPinError):
        _decide(replace(_facts("1", "a"), manifest_artifact_id="scene-manifest-x"))


def test_decision_rejects_a_malformed_fingerprint():
    with pytest.raises(ValueError, match="producer_fingerprint"):
        _decide(replace(_facts("1", "a"), producer_fingerprint="abc"))


# --- revision rules over real manifests and domain records ---------------------------


def _scene_record(manifest):
    unit_id = scene_id_for(manifest.lineage.source)
    return project_scene_record(
        dataset_id="d",
        dataset_version="v1",
        manifest=manifest,
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=unit_id, manifest_checksum=manifest.checksum()
        ),
        manifest_checksum=manifest.checksum(),
    )


def test_scene_records_follow_the_revision_rules():
    base = _scene_record(build_scene_manifest())
    rebuilt = _scene_record(build_scene_manifest())
    reconfigured = _scene_record(
        build_scene_manifest(build_config={"channels": ["CAM_FRONT"]})
    )
    unit_id = base.scene_id

    def decide(candidate, *registered):
        return decide_revision_registration(
            unit_id=unit_id, candidate=candidate, registered=registered
        )

    assert decide(base) is RevisionDecision.NEW_UNIT
    assert decide(rebuilt, base) is RevisionDecision.EXISTING_REVISION
    assert decide(reconfigured, base) is RevisionDecision.NEW_REVISION

    # Same producer fingerprint, different bytes: only a non-deterministic
    # producer can do that, so it is refused rather than added.
    drifted = base.model_copy(
        update={
            "manifest_checksum": "sha256:" + "7" * 64,
            "manifest_artifact_id": manifest_artifact_id_for(
                unit_id=unit_id, manifest_checksum="sha256:" + "7" * 64
            ),
        }
    )
    with pytest.raises(NonDeterministicProducerError):
        decide(drifted, base)


def test_episode_records_follow_the_revision_rules():
    def record(**config):
        manifest = episode_manifest(
            [state("/odom", 0, x=1.0), action("/control", 5, u=0.1)],
            build_config=config or None,
        )
        unit_id = episode_id_for(manifest.lineage.source)
        return project_episode_record(
            dataset_id="ds",
            dataset_version="v1",
            manifest=manifest,
            manifest_artifact_id=manifest_artifact_id_for(
                unit_id=unit_id, manifest_checksum=manifest.checksum()
            ),
            manifest_checksum=manifest.checksum(),
        )

    first, again = record(), record()
    other = record(topics=["/odom"])
    assert first.episode_id == again.episode_id == other.episode_id
    assert first.producer_fingerprint != other.producer_fingerprint

    def decide(candidate, *registered):
        return decide_revision_registration(
            unit_id=first.episode_id, candidate=candidate, registered=registered
        )

    assert decide(again, first) is RevisionDecision.EXISTING_REVISION
    assert decide(other, first) is RevisionDecision.NEW_REVISION
