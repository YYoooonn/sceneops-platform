"""AlignedEpisode's temporal context: the result states the origin instance the
source EpisodeManifest issues for the alignment clock, whatever the source was,
so independently derived Episodes can be told apart without their manifests.

Alignment adds context, not behaviour: the frozen digests below were computed
from the alignment output of the code before ``clock_origin`` / ``clock_scope``
existed, over everything an AlignedEpisode held then.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from pydantic import ValidationError

from sceneops_core.artifacts.logical_uri import LogicalNamespace, format_logical_uri
from sceneops_core.episodes.alignment import (
    ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION,
    ALIGNMENT_SEMANTICS_VERSION,
    AlignedEpisode,
    AlignedEpisodeArtifact,
    ClockMismatchError,
    EpisodeSourceRevision,
    InvalidEpisodeBoundsError,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    UnsupportedAlignedArtifactSchemaError,
    UnsupportedEpisodeContentError,
    align_episode,
    load_aligned_episode_artifact,
)
from sceneops_core.episodes.schemas import EpisodeManifest, EpisodeStreamRole
from sceneops_core.episodes.testing import (
    DEFAULT_CLOCK,
    action,
    episode_manifest,
    observation,
    state,
)
from sceneops_core.provenance import ContentUnitSource, ProducerInfo
from sceneops_core.scenes.testing import build_scene_manifest, recording_source
from sceneops_core.sources import content_id_for
from sceneops_core.temporal import (
    ClockInstance,
    ClockOrigin,
    IncomparableClocksError,
    frame_time_ns,
    require_comparable,
)

S = 1_000_000_000
CHECKSUM = "sha256:" + "c" * 64
CONTENT_ID = content_id_for(CHECKSUM)
NATIVE_CLOCK = "lerobot.frame_time"
VIDEO_URI = format_logical_uri(
    LogicalNamespace.SOURCES, f"contents/{CONTENT_ID}/files/videos/cam.mp4"
)
RECORDING_CONFIG = TemporalAlignmentConfig(
    target_frequency_hz=1.0, tolerance_us=500_000, max_gap_us=2_000_000
)
NATIVE_CONFIG = TemporalAlignmentConfig(
    target_frequency_hz=10.0, tolerance_us=50_000, max_gap_us=200_000
)


def _native_manifest(
    unit_key: str = "0",
    *,
    checksum: str = CHECKSUM,
    frames: int = 3,
    fps: int = 10,
    origin: str = "unit",
) -> EpisodeManifest:
    """A LeRobot-style Episode on a frame grid: frame 0 is the unit's origin,
    and no ``next.*`` feature was built."""
    source = ContentUnitSource(
        content_id=content_id_for(checksum),
        content_checksum=checksum,
        source_clock=NATIVE_CLOCK,
        start_timestamp_ns=0,
        end_timestamp_ns=frame_time_ns(frames - 1, fps) + 1,
        unit_key=unit_key,
    )
    producer = ProducerInfo.create(
        producer_id="sceneops.lerobot_episode_builder",
        semantics_version=1,
        build_config={"features": []},
        source=source.source_revision(),
    )

    def occurrence(topic: str, k: int, values: dict[str, Any], **extra: Any) -> dict:
        return {
            "occurrence_id": f"{topic}-{k:08d}",
            "topic": topic,
            "timestamp_ns": frame_time_ns(k, fps),
            "values": values,
            "native_ref": str(k),
            **extra,
        }

    return EpisodeManifest.model_validate(
        {
            "schema_version": "sceneops.episode_manifest/v4",
            "lineage": {
                "source": source.model_dump(mode="json"),
                "producer": producer.model_dump(mode="json"),
            },
            "clock_domains": [
                {"clock": NATIVE_CLOCK, "origin": origin, "resolution_ns": 1}
            ],
            # A frame grid's frame 0 is the unit's own origin.
            "frame_timeline": (
                {"clock": NATIVE_CLOCK, "fps": fps, "frame_count": frames}
                if origin == "unit"
                else None
            ),
            "streams": [
                {
                    "topic": "observation.image",
                    "role": "observation",
                    "schema_name": "video",
                    "source_clock": NATIVE_CLOCK,
                    "has_payload": True,
                },
                {
                    "topic": "observation.state",
                    "role": "state",
                    "schema_name": "float32",
                    "source_clock": NATIVE_CLOCK,
                    "fields": [{"name": "value", "path": "observation.state"}],
                },
            ],
            "observations": [
                occurrence(
                    "observation.image",
                    k,
                    {},
                    payload={
                        "uri": VIDEO_URI,
                        "checksum": "sha256:" + "e" * 64,
                        "size_bytes": 1000,
                        "media_type": "video/mp4",
                        "selector": {
                            "kind": "video_frame",
                            "timestamp_ns": frame_time_ns(k, fps),
                        },
                    },
                )
                for k in range(frames)
            ],
            "states": [
                occurrence("observation.state", k, {"value": [float(k), -float(k)]})
                for k in range(frames)
            ],
        }
    )


def _recording_manifest(**kwargs: Any) -> EpisodeManifest:
    return episode_manifest(
        [
            observation("/cam", 0),
            observation("/cam", S),
            state("/odom", 0, x=0.0),
            state("/odom", 400, x=9.0),
            # Floor and round disagree here: the quantization shows in the result.
            state("/odom", 2 * S + 700, x=2.0),
            state("/odom", 4 * S, x=4.0),
            action("/control", 0, steering=0.1),
            action("/control", 2 * S, steering=0.2),
        ],
        window=(0, 5 * S),
        **kwargs,
    )


def _align_recording(**kwargs: Any) -> AlignedEpisode:
    clock = kwargs.get("clock", DEFAULT_CLOCK)
    return align_episode(
        _recording_manifest(**kwargs),
        RECORDING_CONFIG,
        TemporalSourceContext(source_clock=clock),
        episode_id="ep-1",
    )


def _align_native(
    unit_key: str = "0", *, episode_id: str | None = None, **kwargs: Any
) -> AlignedEpisode:
    return align_episode(
        _native_manifest(unit_key, **kwargs),
        NATIVE_CONFIG,
        TemporalSourceContext(source_clock=NATIVE_CLOCK),
        episode_id=episode_id or f"ep-{unit_key}",
    )


def _comparable(a: ClockInstance, b: ClockInstance) -> bool:
    try:
        require_comparable(a, b)
    except IncomparableClocksError:
        return False
    return True


def _artifact(aligned: AlignedEpisode) -> AlignedEpisodeArtifact:
    return AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id=aligned.episode_id,
            episode_manifest_uri="mem://source.json",
            source_artifact_id="art-1",
            source_manifest_sha256="a" * 64,
        ),
        aligned_episode=aligned,
    )


def _bytes(artifact: AlignedEpisodeArtifact) -> bytes:
    return json.dumps(
        artifact.to_artifact_dict(), sort_keys=True, separators=(",", ":")
    ).encode()


def _document(**aligned_overrides: Any) -> dict[str, Any]:
    document = _artifact(_align_recording()).to_artifact_dict()
    document["alignedEpisode"].update(aligned_overrides)
    return document


# --- the engine propagates what the manifest issues ------------------------------


@pytest.mark.parametrize(
    ("build_manifest", "clock", "config"),
    [
        (_recording_manifest, DEFAULT_CLOCK, RECORDING_CONFIG),
        (_native_manifest, NATIVE_CLOCK, NATIVE_CONFIG),
    ],
    ids=["recording", "native"],
)
def test_the_result_holds_the_instance_the_source_manifest_issues(
    build_manifest, clock, config
):
    manifest = build_manifest()
    aligned = align_episode(
        manifest, config, TemporalSourceContext(source_clock=clock), episode_id="ep"
    )
    assert aligned.clock_instance == manifest.clock_instance(clock)
    assert aligned.source_clock == clock
    assert aligned.clock_origin == manifest.clock_domain(clock).origin
    assert aligned.clock_scope == manifest.clock_instance(clock).scope


def test_a_stream_clock_other_than_the_window_clock_keeps_its_own_instance():
    manifest = episode_manifest(
        [
            state("/odom", 0, x=0.0, clock="sensor.other_stamp"),
            state("/odom", S, x=1.0, clock="sensor.other_stamp"),
        ],
        clock="sensor.other_stamp",
        window=(0, S),
        window_clock="mcap_log_time",
    )
    aligned = align_episode(
        manifest,
        RECORDING_CONFIG,
        TemporalSourceContext(source_clock="sensor.other_stamp"),
        episode_id="ep-1",
    )
    assert aligned.clock_instance == manifest.clock_instance("sensor.other_stamp")
    assert aligned.clock_instance != manifest.clock_instance("mcap_log_time")


# --- comparability of the results ----------------------------------------------


def test_independent_robot_runs_on_one_clock_id_stay_incomparable():
    a = _align_recording(robot_run_id="run-001")
    b = _align_recording(robot_run_id="run-002")
    assert a.source_clock == b.source_clock
    assert [s.timestamp_us for s in a.steps] == [s.timestamp_us for s in b.steps]
    assert not _comparable(a.clock_instance, b.clock_instance)


def test_two_segments_of_one_robot_run_stay_comparable():
    a = _align_recording(unit_key="segment-0000")
    b = _align_recording(unit_key="segment-0001")
    assert _comparable(a.clock_instance, b.clock_instance)


def test_a_scene_and_an_episode_of_one_robot_run_stay_comparable_on_one_clock():
    aligned = _align_recording(robot_run_id="run-001", clock="mcap_log_time")
    scene = build_scene_manifest(source=recording_source(robot_run_id="run-001"))
    assert _comparable(aligned.clock_instance, scene.clock_instance("mcap_log_time"))
    other = build_scene_manifest(source=recording_source(robot_run_id="run-002"))
    assert not _comparable(
        aligned.clock_instance, other.clock_instance("mcap_log_time")
    )


def test_different_native_episodes_stay_incomparable_on_identical_frame_times():
    a = _align_native("0")
    b = _align_native("1")
    assert [s.timestamp_us for s in a.steps] == [s.timestamp_us for s in b.steps]
    assert not _comparable(a.clock_instance, b.clock_instance)


def test_a_native_episode_preserves_its_unit_origin_scope():
    manifest = _native_manifest("7")
    aligned = _align_native("7")
    assert aligned.clock_origin == ClockOrigin.UNIT
    assert aligned.clock_scope == manifest.clock_instance(NATIVE_CLOCK).scope
    assert len(aligned.clock_scope) > 0


@pytest.mark.parametrize(
    "other",
    [
        {"source_clock": "another.clock"},
        {"clock_origin": ClockOrigin.UNIT},
        {"clock_scope": ("another", "scope")},
    ],
    ids=["clock", "origin", "scope"],
)
def test_changing_one_part_of_the_triple_changes_comparability(other):
    aligned = _align_recording()
    changed = AlignedEpisode.model_validate(aligned.model_dump() | other)
    assert _comparable(aligned.clock_instance, aligned.clock_instance)
    assert not _comparable(aligned.clock_instance, changed.clock_instance)


def test_equal_unix_epoch_instances_are_comparable_across_contents():
    a = _align_native("0", origin="unix_epoch")
    b = _align_native("1", origin="unix_epoch", checksum="sha256:" + "d" * 64)
    assert a.clock_scope == b.clock_scope == ()
    assert _comparable(a.clock_instance, b.clock_instance)


# --- validation ------------------------------------------------------------------


@pytest.mark.parametrize("field", ["clockOrigin", "clockScope"])
def test_missing_context_is_refused_and_never_defaulted(field):
    document = _document()
    del document["alignedEpisode"][field]
    with pytest.raises(ValidationError, match=field):
        AlignedEpisodeArtifact.model_validate(document)


@pytest.mark.parametrize(
    "overrides",
    [
        {"clockOrigin": "bogus"},
        {"clockOrigin": None},
        {"clockScope": None},
        {"clockScope": "recording"},
        {"clockScope": []},
        {"clockScope": ["recording", ""]},
        {"clockScope": ["recording", 1]},
        {"clockOrigin": "unix_epoch"},
        {"clockOrigin": "unix_epoch", "clockScope": ["recording"]},
        {"sourceClock": "NOT A CLOCK"},
    ],
    ids=lambda overrides: json.dumps(overrides),
)
def test_a_malformed_triple_is_refused_when_deserialized(overrides):
    with pytest.raises(ValidationError):
        AlignedEpisodeArtifact.model_validate(_document(**overrides))


def test_a_scope_is_never_built_from_an_unordered_collection():
    fields = _align_recording().model_dump()
    with pytest.raises(ValidationError, match="ordered sequence"):
        AlignedEpisode.model_validate(fields | {"clock_scope": {"a", "b"}})


def test_a_structurally_valid_scope_is_not_a_claim_of_source_ownership():
    # Nothing here can tell that this scope names another source: tying a scope
    # to the Episode it describes takes the Episode's canonical manifest.
    aligned = _align_recording(robot_run_id="run-001")
    moved = AlignedEpisode.model_validate(
        aligned.model_dump() | {"clock_scope": ("content", "someone-else", "0")}
    )
    assert moved.clock_instance.scope == ("content", "someone-else", "0")


# --- serialization -----------------------------------------------------------------


@pytest.mark.parametrize(
    "aligned",
    [_align_recording(), _align_native()],
    ids=["recording", "native"],
)
def test_the_artifact_round_trips_the_whole_instance_byte_identically(aligned):
    artifact = _artifact(aligned)
    data = _bytes(artifact)
    document = json.loads(data)
    assert document["schemaVersion"] == ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION == "v2"
    assert document["alignedEpisode"]["sourceClock"] == aligned.source_clock
    assert document["alignedEpisode"]["clockOrigin"] == aligned.clock_origin.value
    assert document["alignedEpisode"]["clockScope"] == list(aligned.clock_scope)

    reread = load_aligned_episode_artifact(data)
    assert reread == artifact
    assert reread.aligned_episode.clock_instance == aligned.clock_instance
    assert _bytes(reread) == data


def test_repeated_alignment_serializes_to_identical_bytes():
    assert _bytes(_artifact(_align_native())) == _bytes(_artifact(_align_native()))
    assert _bytes(_artifact(_align_recording())) == _bytes(
        _artifact(_align_recording())
    )


# --- old artifacts are refused, not read with missing context ----------------------


def _v1_document() -> dict[str, Any]:
    """The shape of an artifact persisted under schema v1."""
    document = _document()
    document["schemaVersion"] = "v1"
    del document["alignedEpisode"]["clockOrigin"]
    del document["alignedEpisode"]["clockScope"]
    return document


def test_a_v1_artifact_is_refused_explicitly_by_the_loader():
    with pytest.raises(UnsupportedAlignedArtifactSchemaError, match="'v1'.*'v2'"):
        load_aligned_episode_artifact(json.dumps(_v1_document()).encode())


@pytest.mark.parametrize("version", ["v1", "v3", "v999", None, 2])
def test_a_v2_shaped_document_of_another_version_is_refused(version):
    document = _document()
    document["schemaVersion"] = version
    with pytest.raises(UnsupportedAlignedArtifactSchemaError):
        load_aligned_episode_artifact(json.dumps(document).encode())


def test_a_document_stating_no_version_is_never_assumed_to_be_current():
    document = _document()
    del document["schemaVersion"]
    with pytest.raises(UnsupportedAlignedArtifactSchemaError):
        load_aligned_episode_artifact(json.dumps(document).encode())


@pytest.mark.parametrize("data", [b"[]", b'"v2"', b"null"])
def test_a_document_that_is_no_artifact_is_refused(data):
    with pytest.raises(UnsupportedAlignedArtifactSchemaError):
        load_aligned_episode_artifact(data)


def test_the_envelope_model_refuses_another_version_on_any_path():
    with pytest.raises(ValidationError, match="not supported"):
        AlignedEpisodeArtifact.model_validate(_v1_document())
    with pytest.raises(ValidationError, match="not supported"):
        AlignedEpisodeArtifact(
            schema_version="v1",
            source_revision=_artifact(_align_recording()).source_revision,
            aligned_episode=_align_recording(),
        )


# --- alignment behaviour is unchanged ----------------------------------------------


def _semantic_digest(aligned: AlignedEpisode) -> str:
    body = {
        k: v
        for k, v in aligned.model_dump(mode="json").items()
        if k not in {"clock_origin", "clock_scope"}
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_a_recording_alignment_is_what_it_was_before_the_context_existed():
    assert ALIGNMENT_SEMANTICS_VERSION == "v3"
    assert (
        _semantic_digest(_align_recording())
        == "7e39633440a386982f081f817d1d750b3cf90f076934ce4539ae7e87946a07e6"
    )


def test_a_native_alignment_is_what_it_was_before_the_context_existed():
    aligned = _align_native(episode_id="ep-native")
    assert (
        _semantic_digest(aligned)
        == "2bbffbaf5c2a2af73832e1ee1b5efdd611b93e1166165dfc878311bf000f13ad"
    )
    # The frame reference is carried whole, selector included.
    signal = aligned.steps[1].observations["observation.image"]
    assert signal.value.reference_uri == VIDEO_URI
    assert signal.value.reference_metadata["selector"] == {
        "kind": "video_frame",
        "timestamp_ns": frame_time_ns(1, 10),
    }


def test_the_failures_of_alignment_keep_their_precedence():
    """Resolving the instance adds no failure: each refusal below comes first,
    including for a clock the manifest does not declare (no ``KeyError``)."""
    manifest = _recording_manifest()
    with pytest.raises(InvalidEpisodeBoundsError, match="no alignable occurrence"):
        align_episode(
            episode_manifest([observation("/cam", 0)], extra_streams=()).model_copy(
                update={"observations": []}
            ),
            RECORDING_CONFIG,
            TemporalSourceContext(source_clock=DEFAULT_CLOCK),
            episode_id="ep-1",
        )
    with pytest.raises(ClockMismatchError):
        align_episode(
            manifest,
            RECORDING_CONFIG,
            TemporalSourceContext(source_clock="not.declared"),
            episode_id="ep-1",
        )
    transition_stream = manifest.streams[0].model_copy(
        update={"topic": "next.done", "role": EpisodeStreamRole.TRANSITION}
    )
    with pytest.raises(UnsupportedEpisodeContentError):
        align_episode(
            manifest.model_copy(
                update={"streams": [*manifest.streams, transition_stream]}
            ),
            RECORDING_CONFIG,
            TemporalSourceContext(source_clock="not.declared"),
            episode_id="ep-1",
        )
