from __future__ import annotations

from sceneops_core.episodes.alignment import (
    ALIGNMENT_SEMANTICS_VERSION,
    AlignedEpisodeArtifact,
    AlignedSignalStatus,
    AlignedValueKind,
    AssociationPolicy,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
    alignment_config_hash,
)
from sceneops_core.episodes.alignment.schemas import (
    AlignedEpisode,
    AlignedSignal,
    AlignedValue,
    LearningStep,
)

from sceneops_analytics.learning_tables import (
    LEARNING_EPISODES_SCHEMA,
    LEARNING_SIGNALS_SCHEMA,
    LEARNING_STEPS_SCHEMA,
    build_learning_episodes_table,
    build_learning_signals_table,
    build_learning_steps_table,
)

DATASET_ID = "ds1"
DATASET_VERSION = "v1"
EXPORT_ID = "export-abc123"

_CONFIG = TemporalAlignmentConfig(target_frequency_hz=10.0)


def _episode(episode_id: str, steps: list[LearningStep]) -> AlignedEpisode:
    return AlignedEpisode(
        episode_id=episode_id,
        source_start_timestamp_us=0,
        source_end_timestamp_us=len(steps) * 100_000,
        source_clock="mcap_log_time",
        alignment_semantics_version=ALIGNMENT_SEMANTICS_VERSION,
        alignment_config=_CONFIG,
        target_frequency_hz=10.0,
        achieved_frequency_hz=10.0,
        dt_us=100_000,
        step_count=len(steps),
        duplicate_discarded_count=0,
        steps=steps,
    )


def _artifact(episode: AlignedEpisode) -> AlignedEpisodeArtifact:
    return AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id=episode.episode_id,
            episode_manifest_uri=f"mem://episodes/{episode.episode_id}.json",
            source_artifact_id="src-artifact-1",
            source_manifest_sha256="s" * 64,
        ),
        aligned_episode=episode,
    )


def _resolved_scalar(
    channel: str,
    *,
    value: float = 1.0,
    policy: AssociationPolicy = AssociationPolicy.NEAREST,
) -> AlignedSignal:
    return AlignedSignal(
        channel=channel,
        policy=policy,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_SCALAR, scalar=value),
        source_timestamp_us=0,
        time_delta_us=0,
    )


def _missing(channel: str) -> AlignedSignal:
    return AlignedSignal(
        channel=channel,
        policy=AssociationPolicy.LINEAR_INTERPOLATION,
        status=AlignedSignalStatus.MISSING,
        value=None,
    )


# ---------------------------------------------------------------------------
# learning_episodes
# ---------------------------------------------------------------------------


def test_learning_episodes_one_row_per_entry() -> None:
    ep1 = _episode("ep-1", [LearningStep(timestamp_us=0)])
    ep2 = _episode("ep-2", [LearningStep(timestamp_us=0)])
    entries = [("checksum-1", _artifact(ep1)), ("checksum-2", _artifact(ep2))]

    df = build_learning_episodes_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    assert df.schema == LEARNING_EPISODES_SCHEMA
    assert df.height == 2
    assert set(df["episode_id"]) == {"ep-1", "ep-2"}
    assert set(df["aligned_artifact_checksum"]) == {"checksum-1", "checksum-2"}
    row = df.filter(df["episode_id"] == "ep-1").row(0, named=True)
    assert row["export_id"] == EXPORT_ID
    assert row["alignment_config_hash"] == alignment_config_hash(_CONFIG)


def test_learning_episodes_same_episode_multiple_alignments() -> None:
    """One Episode appearing under two different aligned revisions must
    produce two distinct rows, never merged/deduped (Request 2.5 §3)."""
    ep = _episode("ep-1", [LearningStep(timestamp_us=0)])
    entries = [
        ("checksum-rev-a", _artifact(ep)),
        ("checksum-rev-b", _artifact(ep)),
    ]

    df = build_learning_episodes_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    assert df.height == 2
    assert set(df["aligned_artifact_checksum"]) == {"checksum-rev-a", "checksum-rev-b"}
    assert set(df["episode_id"]) == {"ep-1"}


# ---------------------------------------------------------------------------
# learning_steps
# ---------------------------------------------------------------------------


def test_learning_steps_step_index_is_contiguous_and_matches_position() -> None:
    steps = [
        LearningStep(timestamp_us=0),
        LearningStep(timestamp_us=100_000),
        LearningStep(timestamp_us=200_000),
    ]
    entries = [("checksum-1", _artifact(_episode("ep-1", steps)))]

    df = build_learning_steps_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    assert df.schema == LEARNING_STEPS_SCHEMA
    assert df.height == 3
    ordered = df.sort("step_index")
    assert ordered["step_index"].to_list() == [0, 1, 2]
    assert ordered["timestamp_us"].to_list() == [0, 100_000, 200_000]
    assert set(ordered["aligned_artifact_checksum"]) == {"checksum-1"}


def test_learning_steps_multi_episode_row_counts() -> None:
    entries = [
        (
            "checksum-1",
            _artifact(_episode("ep-1", [LearningStep(timestamp_us=t) for t in (0, 1)])),
        ),
        (
            "checksum-2",
            _artifact(
                _episode("ep-2", [LearningStep(timestamp_us=t) for t in (0, 1, 2)])
            ),
        ),
    ]

    df = build_learning_steps_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    assert df.height == 5
    assert df.filter(df["episode_id"] == "ep-1").height == 2
    assert df.filter(df["episode_id"] == "ep-2").height == 3


# ---------------------------------------------------------------------------
# learning_signals
# ---------------------------------------------------------------------------


def test_learning_signals_one_row_per_channel_per_step() -> None:
    steps = [
        LearningStep(timestamp_us=0, observations={"cam": _resolved_scalar("cam")}),
    ]
    entries = [("checksum-1", _artifact(_episode("ep-1", steps)))]

    df = build_learning_signals_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    assert df.schema == LEARNING_SIGNALS_SCHEMA
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["channel"] == "cam"
    assert row["namespace"] == "observation"
    assert row["status"] == "resolved"
    assert row["value_kind"] == "numeric_scalar"
    assert row["value_scalar"] == 1.0


def test_learning_signals_missing_vs_absent_channel_distinguishable() -> None:
    """A channel present at a step with status=missing produces a row; a
    channel never mentioned at all produces zero rows for that channel
    (Request 2.5 §4)."""
    steps = [
        LearningStep(
            timestamp_us=0,
            observations={
                "cam": _resolved_scalar("cam"),
                "imu": _missing("imu"),
            },
        ),
    ]
    entries = [("checksum-1", _artifact(_episode("ep-1", steps)))]

    df = build_learning_signals_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    assert df.height == 2
    imu_rows = df.filter(df["channel"] == "imu")
    assert imu_rows.height == 1
    assert imu_rows.row(0, named=True)["status"] == "missing"
    assert imu_rows.row(0, named=True)["value_kind"] is None

    # "lidar" was never mentioned in any step at all -- channel absent,
    # not merely missing -- so it produces zero rows, not a missing row.
    assert df.filter(df["channel"] == "lidar").height == 0


def test_learning_signals_same_channel_missing_then_absent_across_steps() -> None:
    """SceneOps V2 Request 2.5A §4: a channel present with status=MISSING at
    one step, then entirely absent from a later step's own dict, must
    produce a row for the first step and NO row for the second -- this
    builder never synthesizes a row for a step that doesn't mention the
    channel, even when an earlier step did."""
    steps = [
        LearningStep(
            timestamp_us=0,
            observations={"camera.front": _missing("camera.front")},
        ),
        LearningStep(
            timestamp_us=100_000,
            observations={},
        ),
    ]
    entries = [("checksum-1", _artifact(_episode("ep-1", steps)))]

    df = build_learning_signals_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    camera_rows = df.filter(df["channel"] == "camera.front")
    assert camera_rows.height == 1
    row = camera_rows.row(0, named=True)
    assert row["step_index"] == 0
    assert row["status"] == "missing"

    # Step 1 never mentions "camera.front" at all -- no synthesized row.
    assert df.filter(df["step_index"] == 1).height == 0


def test_learning_signals_dynamic_channel_set_across_steps() -> None:
    steps = [
        LearningStep(timestamp_us=0, observations={"cam": _resolved_scalar("cam")}),
        LearningStep(
            timestamp_us=100_000,
            observations={
                "cam": _resolved_scalar("cam"),
                "lidar": _resolved_scalar("lidar"),
            },
        ),
    ]
    entries = [("checksum-1", _artifact(_episode("ep-1", steps)))]

    df = build_learning_signals_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    assert df.height == 3
    assert df.filter(df["channel"] == "cam").height == 2
    assert df.filter(df["channel"] == "lidar").height == 1


def test_learning_signals_observation_and_action_same_channel_name() -> None:
    steps = [
        LearningStep(
            timestamp_us=0,
            observations={"gripper": _resolved_scalar("gripper", value=1.0)},
            actions={"gripper": _resolved_scalar("gripper", value=2.0)},
        ),
    ]
    entries = [("checksum-1", _artifact(_episode("ep-1", steps)))]

    df = build_learning_signals_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    assert df.height == 2
    obs_row = df.filter(
        (df["channel"] == "gripper") & (df["namespace"] == "observation")
    ).row(0, named=True)
    act_row = df.filter(
        (df["channel"] == "gripper") & (df["namespace"] == "action")
    ).row(0, named=True)
    assert obs_row["value_scalar"] == 1.0
    assert act_row["value_scalar"] == 2.0


def test_learning_signals_vector_value() -> None:
    signal = AlignedSignal(
        channel="pose",
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(
            kind=AlignedValueKind.NUMERIC_VECTOR, vector=[1.0, 2.0, 3.0]
        ),
        source_timestamp_us=0,
        time_delta_us=0,
    )
    steps = [LearningStep(timestamp_us=0, observations={"pose": signal})]
    entries = [("checksum-1", _artifact(_episode("ep-1", steps)))]

    df = build_learning_signals_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    row = df.row(0, named=True)
    assert row["value_kind"] == "numeric_vector"
    assert row["value_vector"] == [1.0, 2.0, 3.0]
    assert row["value_scalar"] is None


def test_learning_signals_reference_value_with_uri() -> None:
    signal = AlignedSignal(
        channel="cam_front",
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(
            kind=AlignedValueKind.REFERENCE, reference_uri="s3://x/0.png"
        ),
        source_timestamp_us=0,
        time_delta_us=0,
    )
    steps = [LearningStep(timestamp_us=0, observations={"cam_front": signal})]
    entries = [("checksum-1", _artifact(_episode("ep-1", steps)))]

    df = build_learning_signals_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    row = df.row(0, named=True)
    assert row["value_kind"] == "reference"
    assert row["reference_uri"] == "s3://x/0.png"
    assert row["status"] == "resolved"


def test_learning_signals_matched_reference_with_none_uri_distinguishable_from_missing() -> (
    None
):
    """A RESOLVED reference whose uri happens to be None (e.g. matched but
    genuinely no asset produced) must remain distinguishable from a MISSING
    signal -- distinguished by ``status``, not by ``reference_uri`` alone
    (Request 2.5 §4)."""
    matched_null = AlignedSignal(
        channel="cam_front",
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(kind=AlignedValueKind.REFERENCE, reference_uri=None),
        source_timestamp_us=0,
        time_delta_us=0,
    )
    steps = [
        LearningStep(timestamp_us=0, observations={"cam_front": matched_null}),
        LearningStep(
            timestamp_us=100_000, observations={"cam_front": _missing("cam_front")}
        ),
    ]
    entries = [("checksum-1", _artifact(_episode("ep-1", steps)))]

    df = build_learning_signals_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    ).sort("step_index")

    resolved_row = df.row(0, named=True)
    missing_row = df.row(1, named=True)
    assert resolved_row["reference_uri"] is None
    assert missing_row["reference_uri"] is None
    assert resolved_row["status"] == "resolved"
    assert resolved_row["value_kind"] == "reference"
    assert missing_row["status"] == "missing"
    assert missing_row["value_kind"] is None


def test_learning_signals_interpolated_signal_preserves_provenance() -> None:
    signal = AlignedSignal(
        channel="imu",
        policy=AssociationPolicy.LINEAR_INTERPOLATION,
        status=AlignedSignalStatus.INTERPOLATED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_SCALAR, scalar=1.5),
        source_before_timestamp_us=0,
        source_after_timestamp_us=200_000,
        interpolation_ratio=0.5,
    )
    steps = [LearningStep(timestamp_us=100_000, observations={"imu": signal})]
    entries = [("checksum-1", _artifact(_episode("ep-1", steps)))]

    df = build_learning_signals_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    row = df.row(0, named=True)
    assert row["status"] == "interpolated"
    assert row["source_before_timestamp_us"] == 0
    assert row["source_after_timestamp_us"] == 200_000
    assert row["interpolation_ratio"] == 0.5
    assert row["source_timestamp_us"] is None
    assert row["time_delta_us"] is None
