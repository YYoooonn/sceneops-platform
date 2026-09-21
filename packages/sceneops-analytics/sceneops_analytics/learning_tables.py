"""Pure AlignedEpisodeArtifact -> columnar table builders (SceneOps V2
Request 2.5).

No I/O here — callers resolve/verify/parse AlignedEpisodeArtifact bytes
themselves and pass in the parsed objects alongside their content checksum
(the checksum is not itself a field on AlignedEpisodeArtifact — it is
computed at persistence time — so it travels alongside each entry as an
explicit ``(aligned_artifact_checksum, artifact)`` pair rather than being
re-derived here).

Three tables, mirroring the existing Scene precedent
(scenes/samples/sensor_frames+annotations):

- ``learning_episodes``: one row per (episode_id, aligned_artifact_checksum)
  — Episode-level alignment metadata that would otherwise repeat on every
  step/signal row.
- ``learning_steps``: one row per LearningStep (per aligned artifact).
- ``learning_signals``: one row per (namespace, channel) actually present in
  a given LearningStep's ``observations``/``actions`` dict, whatever that
  entry's ``status`` is (resolved/missing/interpolated) -- SceneOps V2
  Request 2.5A.

  Signal presence semantics (frozen v1 contract):

    - channel absent from a LearningStep's namespace dict
      -> no learning_signals row is produced for it at that step.
    - channel present in the dict with status=MISSING
      -> one learning_signals row is produced, status="missing",
         all value fields null.

  Therefore: *absent* channel != *temporally missing* channel. This module
  never synthesizes a row for a channel a given step doesn't mention --
  whether every channel the AlignedEpisode ever uses is present at every
  step (with an explicit MISSING entry when unavailable) is a property of
  the pure alignment engine's own output (Request 2.2), not something this
  builder enforces or assumes. A caller who wants "all known episode
  channels present at every step" gets that only if align_episode() itself
  already produced that shape; this builder is a faithful, non-inventive
  materialization of whatever AlignedEpisode.steps actually contains.
"""

from __future__ import annotations

import polars as pl

from sceneops_core.episodes.alignment import (
    AlignedEpisodeArtifact,
    ChannelNamespace,
    alignment_config_hash,
)
from sceneops_core.episodes.alignment.schemas import AlignedSignal, LearningStep

LEARNING_EPISODES_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "export_id": pl.Utf8,
    "episode_id": pl.Utf8,
    "aligned_artifact_checksum": pl.Utf8,
    "source_start_timestamp_us": pl.Int64,
    "source_end_timestamp_us": pl.Int64,
    "source_clock": pl.Utf8,
    "alignment_semantics_version": pl.Utf8,
    "alignment_config_hash": pl.Utf8,
    "target_frequency_hz": pl.Float64,
    "achieved_frequency_hz": pl.Float64,
    "dt_us": pl.Int64,
    "step_count": pl.Int64,
    "duplicate_discarded_count": pl.Int64,
    "task": pl.Utf8,
    "outcome": pl.Utf8,
}

LEARNING_STEPS_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "export_id": pl.Utf8,
    "episode_id": pl.Utf8,
    "aligned_artifact_checksum": pl.Utf8,
    "step_index": pl.Int64,
    "timestamp_us": pl.Int64,
}

LEARNING_SIGNALS_SCHEMA: dict[str, pl.PolarsDataType] = {
    "dataset_id": pl.Utf8,
    "dataset_version": pl.Utf8,
    "export_id": pl.Utf8,
    "episode_id": pl.Utf8,
    "aligned_artifact_checksum": pl.Utf8,
    "step_index": pl.Int64,
    "timestamp_us": pl.Int64,
    "namespace": pl.Utf8,
    "channel": pl.Utf8,
    "policy": pl.Utf8,
    "status": pl.Utf8,
    "value_kind": pl.Utf8,
    "value_scalar": pl.Float64,
    "value_vector": pl.List(pl.Float64),
    "reference_modality": pl.Utf8,
    "reference_uri": pl.Utf8,
    "source_timestamp_us": pl.Int64,
    "time_delta_us": pl.Int64,
    "source_before_timestamp_us": pl.Int64,
    "source_after_timestamp_us": pl.Int64,
    "interpolation_ratio": pl.Float64,
}


def build_learning_episodes_table(
    *,
    dataset_id: str,
    dataset_version: str,
    export_id: str,
    entries: list[tuple[str, AlignedEpisodeArtifact]],
) -> pl.DataFrame:
    rows = [
        {
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "export_id": export_id,
            "episode_id": artifact.aligned_episode.episode_id,
            "aligned_artifact_checksum": checksum,
            "source_start_timestamp_us": artifact.aligned_episode.source_start_timestamp_us,
            "source_end_timestamp_us": artifact.aligned_episode.source_end_timestamp_us,
            "source_clock": artifact.aligned_episode.source_clock,
            "alignment_semantics_version": artifact.aligned_episode.alignment_semantics_version,
            "alignment_config_hash": alignment_config_hash(
                artifact.aligned_episode.alignment_config
            ),
            "target_frequency_hz": artifact.aligned_episode.target_frequency_hz,
            "achieved_frequency_hz": artifact.aligned_episode.achieved_frequency_hz,
            "dt_us": artifact.aligned_episode.dt_us,
            "step_count": artifact.aligned_episode.step_count,
            "duplicate_discarded_count": artifact.aligned_episode.duplicate_discarded_count,
            "task": artifact.aligned_episode.task,
            "outcome": str(artifact.aligned_episode.outcome),
        }
        for checksum, artifact in entries
    ]
    return pl.DataFrame(rows, schema=LEARNING_EPISODES_SCHEMA)


def build_learning_steps_table(
    *,
    dataset_id: str,
    dataset_version: str,
    export_id: str,
    entries: list[tuple[str, AlignedEpisodeArtifact]],
) -> pl.DataFrame:
    rows = [
        {
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "export_id": export_id,
            "episode_id": artifact.aligned_episode.episode_id,
            "aligned_artifact_checksum": checksum,
            "step_index": step_index,
            "timestamp_us": step.timestamp_us,
        }
        for checksum, artifact in entries
        for step_index, step in enumerate(artifact.aligned_episode.steps)
    ]
    return pl.DataFrame(rows, schema=LEARNING_STEPS_SCHEMA)


def _signal_row(
    *,
    dataset_id: str,
    dataset_version: str,
    export_id: str,
    episode_id: str,
    checksum: str,
    step_index: int,
    step: LearningStep,
    namespace: ChannelNamespace,
    channel: str,
    signal: AlignedSignal,
) -> dict[str, object]:
    value = signal.value
    return {
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "export_id": export_id,
        "episode_id": episode_id,
        "aligned_artifact_checksum": checksum,
        "step_index": step_index,
        "timestamp_us": step.timestamp_us,
        "namespace": str(namespace),
        "channel": channel,
        "policy": str(signal.policy),
        "status": str(signal.status),
        "value_kind": str(value.kind) if value is not None else None,
        "value_scalar": value.scalar if value is not None else None,
        "value_vector": value.vector if value is not None else None,
        "reference_modality": (
            str(value.reference_modality)
            if value is not None and value.reference_modality is not None
            else None
        ),
        "reference_uri": value.reference_uri if value is not None else None,
        "source_timestamp_us": signal.source_timestamp_us,
        "time_delta_us": signal.time_delta_us,
        "source_before_timestamp_us": signal.source_before_timestamp_us,
        "source_after_timestamp_us": signal.source_after_timestamp_us,
        "interpolation_ratio": signal.interpolation_ratio,
    }


def build_learning_signals_table(
    *,
    dataset_id: str,
    dataset_version: str,
    export_id: str,
    entries: list[tuple[str, AlignedEpisodeArtifact]],
) -> pl.DataFrame:
    rows = [
        _signal_row(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
            episode_id=artifact.aligned_episode.episode_id,
            checksum=checksum,
            step_index=step_index,
            step=step,
            namespace=namespace,
            channel=channel,
            signal=signal,
        )
        for checksum, artifact in entries
        for step_index, step in enumerate(artifact.aligned_episode.steps)
        for namespace, channel_map in (
            (ChannelNamespace.OBSERVATION, step.observations),
            (ChannelNamespace.ACTION, step.actions),
        )
        for channel, signal in channel_map.items()
    ]
    return pl.DataFrame(rows, schema=LEARNING_SIGNALS_SCHEMA)


LEARNING_TABLE_BUILDERS = ("learning_episodes", "learning_steps", "learning_signals")
