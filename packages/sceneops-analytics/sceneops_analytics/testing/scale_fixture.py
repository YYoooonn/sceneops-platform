"""Deterministic, parameterized synthetic learning-data fixture (SceneOps V2
Request 5.1) -- a scaled-up sibling of ``interop_dataset.py`` used only to
generate reproducible benchmark inputs for the Phase 5 scaling baseline.

::

    ScaleSpec (episode/step/channel counts)
            -> build_scaled_entries()                (pure, in-memory)
            -> build_learning_*_table (Request 2.5)
            -> real Parquet, written through AnalyticsTableWriter
            -> LearningDataExportManifest
            -> write_scaled_dataset_artifacts() returns identifiers only
               (never opens a SceneOpsDataset -- callers that want to
               *measure* dataset.open() need a fresh, uninstrumented open()
               call of their own)

Built from the exact same Phase 2/2.5 contracts real pipeline code uses
(AlignedEpisode, AlignedEpisodeArtifact, build_learning_*_table,
AnalyticsTableWriter) -- no handcrafted Parquet, no mocked schema.

This module ships inside sceneops_analytics but is test/benchmark-support
code, not a domain contract: nothing here is re-exported from
sceneops_analytics's top-level ``__init__``, and it must never be imported
by production code. It exists to give SceneOps V2 Request 5.1's benchmark
harness (``scripts/dev/benchmark_learning_data_scaling.py``) and its own
correctness smoke test (``packages/sceneops-analytics/tests/
test_scale_fixture.py``) one shared, deterministic generator instead of each
hand-rolling synthetic episodes at a different scale.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_analytics.learning_tables import (
    build_learning_episodes_table,
    build_learning_signals_table,
    build_learning_steps_table,
)
from sceneops_analytics.writer import AnalyticsTableWriter
from sceneops_core.episodes.alignment import (
    ALIGNMENT_SEMANTICS_VERSION,
    AlignedEpisodeArtifact,
    AlignedSignal,
    AlignedSignalStatus,
    AlignedValue,
    AlignedValueKind,
    AssociationPolicy,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
)
from sceneops_core.episodes.alignment.schemas import AlignedEpisode, LearningStep
from sceneops_core.episodes.learning import EpisodeRef, FeatureProjection
from sceneops_core.episodes.learning_export import (
    AlignedArtifactRevision,
    LearningDataExportConfig,
    LearningDataExportManifest,
    learning_data_export_id,
)
from sceneops_core.episodes.schemas.enums import EpisodeOutcome
from sceneops_storage import ArtifactStore, LocalArtifactStore

DT_US = 100_000
TARGET_FREQUENCY_HZ = 10.0
VECTOR_DIM = 3

_ALIGNMENT_CONFIG = TemporalAlignmentConfig(target_frequency_hz=TARGET_FREQUENCY_HZ)


@dataclass(frozen=True)
class ScaleSpec:
    """One benchmark scale point (SceneOps V2 Request 5.1 §3): how many
    EpisodeRefs, how many steps each, and how wide the per-step feature
    schema is. Deliberately independent of nuScenes-mini or any other
    concrete SceneOps dataset -- the goal is to sweep scale, not to
    reproduce one specific source dataset."""

    name: str
    num_episodes: int
    steps_per_episode: int
    num_observation_channels: int = 6
    num_action_channels: int = 4

    @property
    def observation_channels(self) -> list[str]:
        return [f"obs_{i}" for i in range(self.num_observation_channels)]

    @property
    def action_channels(self) -> list[str]:
        return [f"act_{i}" for i in range(self.num_action_channels)]

    @property
    def total_steps(self) -> int:
        return self.num_episodes * self.steps_per_episode

    @property
    def total_signal_rows(self) -> int:
        return self.total_steps * (
            self.num_observation_channels + self.num_action_channels
        )


# A representative scale ladder (SceneOps V2 Request 5.1 §3): xs mirrors the
# order of magnitude of the existing interop golden fixture; each step up
# multiplies episode count, step count, and channel width so total row
# counts grow roughly an order of magnitude at a time without any single
# scale taking more than a few seconds to build.
DEFAULT_SCALE_LADDER: tuple[ScaleSpec, ...] = (
    ScaleSpec(name="xs", num_episodes=4, steps_per_episode=50),
    ScaleSpec(name="s", num_episodes=20, steps_per_episode=100),
    ScaleSpec(name="m", num_episodes=60, steps_per_episode=150),
    ScaleSpec(name="l", num_episodes=150, steps_per_episode=200),
)


def _channel_offset(channel_index: int, *, is_action: bool) -> float:
    # Widely spaced per-channel/per-namespace offsets (mirrors
    # interop_dataset.py's _CHANNEL_OFFSETS) so a channel-order or
    # namespace-mixup bug produces a visibly wrong number rather than a
    # coincidentally-plausible one.
    base = 0.5 if is_action else 0.0
    return base + 0.01 * (channel_index + 1)


def _vector_value(base: float, step_index: int, offset: float) -> list[float]:
    root = base + step_index + offset
    return [root + 0.001 * component for component in range(VECTOR_DIM)]


def expected_observation(
    spec: ScaleSpec, episode_index: int, step_index: int
) -> list[float]:
    """Independent reference formula (never derived by reading the fixture
    back) for the dense observation vector at one (episode, step) -- used by
    ``test_scale_fixture.py`` to check the generator against the real
    projection path at the smallest scale only."""
    base = float(episode_index) * 10_000.0
    values: list[float] = []
    for i in range(spec.num_observation_channels):
        values.extend(
            _vector_value(base, step_index, _channel_offset(i, is_action=False))
        )
    return values


def expected_action(
    spec: ScaleSpec, episode_index: int, step_index: int
) -> list[float]:
    base = float(episode_index) * 10_000.0
    values: list[float] = []
    for i in range(spec.num_action_channels):
        values.extend(
            _vector_value(base, step_index, _channel_offset(i, is_action=True))
        )
    return values


def _learning_step(
    spec: ScaleSpec, episode_index: int, step_index: int
) -> LearningStep:
    base = float(episode_index) * 10_000.0
    observations = {
        channel: AlignedSignal(
            channel=channel,
            policy=AssociationPolicy.NEAREST,
            status=AlignedSignalStatus.RESOLVED,
            value=AlignedValue(
                kind=AlignedValueKind.NUMERIC_VECTOR,
                vector=_vector_value(
                    base, step_index, _channel_offset(i, is_action=False)
                ),
            ),
            source_timestamp_us=step_index * DT_US,
            time_delta_us=0,
        )
        for i, channel in enumerate(spec.observation_channels)
    }
    actions = {
        channel: AlignedSignal(
            channel=channel,
            policy=AssociationPolicy.NEAREST,
            status=AlignedSignalStatus.RESOLVED,
            value=AlignedValue(
                kind=AlignedValueKind.NUMERIC_VECTOR,
                vector=_vector_value(
                    base, step_index, _channel_offset(i, is_action=True)
                ),
            ),
            source_timestamp_us=step_index * DT_US,
            time_delta_us=0,
        )
        for i, channel in enumerate(spec.action_channels)
    }
    return LearningStep(
        timestamp_us=step_index * DT_US, observations=observations, actions=actions
    )


def _episode_id(episode_index: int) -> str:
    return f"bench-ep-{episode_index:06d}"


def _checksum(episode_index: int) -> str:
    return f"chk-bench-{episode_index:06d}"


def episode_ref(spec: ScaleSpec, episode_index: int) -> EpisodeRef:
    return EpisodeRef(
        episode_id=_episode_id(episode_index),
        aligned_artifact_checksum=_checksum(episode_index),
    )


def feature_projection_for(spec: ScaleSpec) -> FeatureProjection:
    return FeatureProjection(
        observation_channels=spec.observation_channels,
        action_channels=spec.action_channels,
    )


def _aligned_episode(spec: ScaleSpec, episode_index: int) -> AlignedEpisode:
    steps = [
        _learning_step(spec, episode_index, step_index)
        for step_index in range(spec.steps_per_episode)
    ]
    return AlignedEpisode(
        episode_id=_episode_id(episode_index),
        source_start_timestamp_us=0,
        source_end_timestamp_us=(spec.steps_per_episode - 1) * DT_US,
        source_clock="mcap_log_time",
        alignment_semantics_version=ALIGNMENT_SEMANTICS_VERSION,
        alignment_config=_ALIGNMENT_CONFIG,
        target_frequency_hz=TARGET_FREQUENCY_HZ,
        achieved_frequency_hz=TARGET_FREQUENCY_HZ,
        dt_us=DT_US,
        step_count=spec.steps_per_episode,
        duplicate_discarded_count=0,
        task="bench-task",
        outcome=EpisodeOutcome.SUCCESS,
        steps=steps,
    )


def _aligned_artifact(
    episode: AlignedEpisode, episode_index: int
) -> AlignedEpisodeArtifact:
    checksum = _checksum(episode_index)
    return AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id=episode.episode_id,
            episode_manifest_uri=f"mem://benchmark/{episode.episode_id}/{checksum}.json",
            source_artifact_id=f"src-{checksum}",
            source_manifest_sha256="b" * 64,
        ),
        aligned_episode=episode,
    )


def build_scaled_entries(spec: ScaleSpec) -> list[tuple[str, AlignedEpisodeArtifact]]:
    """The scaled fixture's raw (checksum, AlignedEpisodeArtifact) entries,
    in episode-index order -- pure, I/O-free, mirrors
    ``interop_dataset.build_interop_entries``'s role but parameterized by
    ``spec``."""
    entries = []
    for episode_index in range(spec.num_episodes):
        episode = _aligned_episode(spec, episode_index)
        entries.append(
            (_checksum(episode_index), _aligned_artifact(episode, episode_index))
        )
    return entries


@dataclass
class ScaledDatasetArtifacts:
    """Everything needed to open a fresh, uninstrumented SceneOpsDataset
    over one written scale point (SceneOps V2 Request 5.1). Deliberately
    does *not* hold an already-open SceneOpsDataset or the ArtifactStore
    used to write it -- the benchmark harness opens its own dataset (through
    its own, possibly-instrumented ArtifactStore) so that dataset.open()'s
    cost is actually measured, not hidden inside fixture construction."""

    spec: ScaleSpec
    dataset_id: str
    dataset_version: str
    storage_root_uri: str
    learning_manifest: LearningDataExportManifest
    learning_manifest_checksum: str
    feature_projection: FeatureProjection


async def write_scaled_dataset_artifacts(
    tmp_path, spec: ScaleSpec, *, dataset_id: str | None = None
) -> ScaledDatasetArtifacts:
    """Write one scale point's learning_episodes/learning_steps/
    learning_signals Parquet tables + LearningDataExportManifest through the
    real AnalyticsTableWriter (SceneOps V2 Request 2.5) -- exactly the
    physical layout ``EXPORT_LEARNING_DATA`` produces in production (one
    Parquet file per table, under
    ``{dataset_id}/{dataset_version}/learning/{export_id[:16]}/``).

    Returns identifiers only (no open SceneOpsDataset, no ArtifactStore) --
    callers construct their own ArtifactStore against ``storage_root_uri``
    and call ``SceneOpsDataset.open()`` themselves so open()'s cost is part
    of what gets measured, not fixture setup.
    """
    dataset_id = dataset_id or f"bench-{spec.name}"
    dataset_version = "v1"
    storage_root_uri = str(tmp_path / "storage")
    artifact_store: ArtifactStore = LocalArtifactStore(root_uri=storage_root_uri)

    entries = build_scaled_entries(spec)

    export_config = LearningDataExportConfig()
    export_id = learning_data_export_id(
        aligned_checksums=[checksum for checksum, _ in entries],
        export_config=export_config,
    )

    writer = AnalyticsTableWriter(
        artifact_store=artifact_store, root_uri=str(tmp_path / "analytics")
    )
    builders = {
        "learning_episodes": build_learning_episodes_table,
        "learning_steps": build_learning_steps_table,
        "learning_signals": build_learning_signals_table,
    }
    table_uris: dict[str, str] = {}
    table_checksums: dict[str, str] = {}
    row_counts: dict[str, int] = {}
    for name, builder in builders.items():
        df = builder(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
            entries=entries,
        )
        result = await writer.write_learning_table(
            name,
            df,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            export_id=export_id,
        )
        table_uris[name] = result.uri
        table_checksums[name] = result.checksum
        row_counts[name] = df.height

    learning_manifest = LearningDataExportManifest(
        export_id=export_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        inputs=[
            AlignedArtifactRevision(
                episode_id=artifact.aligned_episode.episode_id,
                aligned_artifact_id=f"art-{checksum}",
                aligned_artifact_checksum=checksum,
            )
            for checksum, artifact in entries
        ],
        export_config=export_config,
        table_uris=table_uris,
        table_checksums=table_checksums,
        row_counts=row_counts,
        episode_count=spec.num_episodes,
    )
    write_result = await writer.write_learning_export_manifest(
        learning_manifest,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        export_id=export_id,
    )

    return ScaledDatasetArtifacts(
        spec=spec,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        storage_root_uri=storage_root_uri,
        learning_manifest=learning_manifest,
        learning_manifest_checksum=write_result.checksum,
        feature_projection=feature_projection_for(spec),
    )


__all__ = [
    "DEFAULT_SCALE_LADDER",
    "ScaleSpec",
    "ScaledDatasetArtifacts",
    "build_scaled_entries",
    "episode_ref",
    "expected_action",
    "expected_observation",
    "feature_projection_for",
    "write_scaled_dataset_artifacts",
]
