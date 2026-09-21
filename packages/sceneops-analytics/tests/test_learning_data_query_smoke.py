"""Parquet round-trip + DuckDB query smoke tests for the columnar
learning-data tables (SceneOps V2 Request 2.5 §16).

Builds real AlignedEpisodeArtifact-derived tables, writes them to real local
Parquet files (not just in-memory DataFrames), then queries them back
through query_parquet -- proving the schemas/values survive a genuine
write/read round-trip and that the four representative query shapes an
analyst would run all work: all steps for one aligned artifact, all signals
for one channel, all missing observations, all interpolated signals.
"""

from __future__ import annotations

from sceneops_core.episodes.alignment import (
    ALIGNMENT_SEMANTICS_VERSION,
    AlignedEpisodeArtifact,
    AlignedSignalStatus,
    AlignedValueKind,
    AssociationPolicy,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
)
from sceneops_core.episodes.alignment.schemas import (
    AlignedEpisode,
    AlignedSignal,
    AlignedValue,
    LearningStep,
)

from sceneops_analytics import (
    build_learning_signals_table,
    build_learning_steps_table,
    query_parquet,
)

DATASET_ID = "ds1"
DATASET_VERSION = "v1"
EXPORT_ID = "export-smoke-1"

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


def _resolved(channel: str, value: float) -> AlignedSignal:
    return AlignedSignal(
        channel=channel,
        policy=AssociationPolicy.NEAREST,
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


def _interpolated(channel: str, value: float) -> AlignedSignal:
    return AlignedSignal(
        channel=channel,
        policy=AssociationPolicy.LINEAR_INTERPOLATION,
        status=AlignedSignalStatus.INTERPOLATED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_SCALAR, scalar=value),
        source_before_timestamp_us=0,
        source_after_timestamp_us=200_000,
        interpolation_ratio=0.5,
    )


def _build_entries() -> list[tuple[str, AlignedEpisodeArtifact]]:
    ep1_steps = [
        LearningStep(
            timestamp_us=0,
            observations={"cam": _resolved("cam", 1.0), "imu": _missing("imu")},
        ),
        LearningStep(
            timestamp_us=100_000,
            observations={
                "cam": _resolved("cam", 2.0),
                "imu": _interpolated("imu", 1.5),
            },
        ),
    ]
    ep2_steps = [
        LearningStep(
            timestamp_us=0,
            observations={"cam": _resolved("cam", 5.0), "imu": _resolved("imu", 0.1)},
        ),
    ]
    return [
        ("checksum-ep1", _artifact(_episode("ep-1", ep1_steps))),
        ("checksum-ep2", _artifact(_episode("ep-2", ep2_steps))),
    ]


def test_parquet_round_trip_and_representative_queries(tmp_path) -> None:
    entries = _build_entries()

    steps_df = build_learning_steps_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )
    signals_df = build_learning_signals_table(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=EXPORT_ID,
        entries=entries,
    )

    steps_path = str(tmp_path / "learning_steps.parquet")
    signals_path = str(tmp_path / "learning_signals.parquet")
    steps_df.write_parquet(steps_path)
    signals_df.write_parquet(signals_path)

    views = {"learning_steps": steps_path, "learning_signals": signals_path}

    # 1. All steps for one aligned artifact.
    steps_for_ep1 = query_parquet(
        views,
        "SELECT step_index, timestamp_us FROM learning_steps "
        "WHERE aligned_artifact_checksum = 'checksum-ep1' ORDER BY step_index",
    )
    assert steps_for_ep1["step_index"].to_list() == [0, 1]
    assert steps_for_ep1["timestamp_us"].to_list() == [0, 100_000]

    # 2. All signals for one channel (across both aligned artifacts).
    cam_signals = query_parquet(
        views,
        "SELECT aligned_artifact_checksum, step_index, value_scalar "
        "FROM learning_signals WHERE channel = 'cam' "
        "ORDER BY aligned_artifact_checksum, step_index",
    )
    assert cam_signals.height == 3
    assert cam_signals["value_scalar"].to_list() == [1.0, 2.0, 5.0]

    # 3. All missing observations.
    missing_signals = query_parquet(
        views,
        "SELECT aligned_artifact_checksum, channel, step_index "
        "FROM learning_signals WHERE namespace = 'observation' "
        "AND status = 'missing'",
    )
    assert missing_signals.height == 1
    row = missing_signals.row(0, named=True)
    assert row["aligned_artifact_checksum"] == "checksum-ep1"
    assert row["channel"] == "imu"
    assert row["step_index"] == 0

    # 4. All interpolated signals.
    interpolated_signals = query_parquet(
        views,
        "SELECT aligned_artifact_checksum, channel, interpolation_ratio "
        "FROM learning_signals WHERE status = 'interpolated'",
    )
    assert interpolated_signals.height == 1
    row = interpolated_signals.row(0, named=True)
    assert row["aligned_artifact_checksum"] == "checksum-ep1"
    assert row["channel"] == "imu"
    assert row["interpolation_ratio"] == 0.5
