"""Deterministic, parameterized synthetic learning-data fixture (SceneOps V2
Request 5.1, extended by Request 5.2) -- a scaled-up sibling of
``interop_dataset.py`` used only to generate reproducible benchmark inputs
for the Phase 5 scaling baseline and physical-layout comparison.

::

    ScaleSpec (episode/step/channel counts + realistic-variation knobs)
            -> build_scaled_entries()                (pure, in-memory)
            -> build_learning_episodes_table (Request 2.5, single file)
            -> write_sharded_learning_tables (Request 5.2, learning_steps/
               learning_signals, one or more Parquet objects per table)
            -> LearningDataExportManifest (layout_version="v2-sharded")
            -> write_scaled_dataset_artifacts() returns identifiers only
               (never opens a SceneOpsDataset -- callers that want to
               *measure* dataset.open() need a fresh, uninstrumented open()
               call of their own)

Built from the exact same Phase 2/2.5/5.2 contracts real pipeline code uses
(AlignedEpisode, AlignedEpisodeArtifact, build_learning_*_table,
write_sharded_learning_tables) -- no handcrafted Parquet, no mocked schema.

Realistic variation (SceneOps V2 Request 5.2 §1), all deterministic (no
``random`` module -- reproducible formulas keyed only on episode/channel/step
index, never a seed to manage): episode length jitter
(``length_jitter_fraction``), multiple aligned revisions for some logical
episode_ids (``extra_revision_every``), a cycling task/outcome distribution,
and a core/extra channel split where only "extra" (non-core) channels ever
go ABSENT (omitted from a step's dict entirely) or MISSING (present with
status=MISSING) -- core channels are always RESOLVED and always present, so
``feature_projection_for()``'s core-only FeatureProjection is guaranteed
projectable across every episode this module can generate, at any scale.

This module ships inside sceneops_analytics but is test/benchmark-support
code, not a domain contract: nothing here is re-exported from
sceneops_analytics's top-level ``__init__``, and it must never be imported
by production code. The separate, frozen ``interop_dataset.py`` golden
fixture is untouched by this module and must stay that way (SceneOps V2
Request 5.2: "keep the existing tiny golden fixture separate; do not
replace correctness fixtures with scale fixtures").
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_analytics.learning_tables import build_learning_episodes_table
from sceneops_analytics.learning_tables_sharded import write_sharded_learning_tables
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
    LEARNING_DATA_LAYOUT_VERSION_SHARDED,
    AlignedArtifactRevision,
    LearningDataExportConfig,
    LearningDataExportManifest,
    ShardPolicy,
    default_shard_policy,
    learning_data_export_id,
)
from sceneops_core.episodes.schemas.enums import EpisodeOutcome
from sceneops_storage import ArtifactStore, LocalArtifactStore

DT_US = 100_000
TARGET_FREQUENCY_HZ = 10.0
VECTOR_DIM = 3
# Keeps revision-1 (and beyond) content ranges disjoint from any
# episode_index's own base range even at the largest planned scale
# (episode_index up to ~10_000 -> base up to 1e8, well under this).
_REVISION_BASE_STRIDE = 1_000_000_000.0

_ALIGNMENT_CONFIG = TemporalAlignmentConfig(target_frequency_hz=TARGET_FREQUENCY_HZ)

_TASKS = ("pick", "place", "insert", "calibrate")
_OUTCOMES = (
    EpisodeOutcome.SUCCESS,
    EpisodeOutcome.SUCCESS,
    EpisodeOutcome.FAILURE,
    EpisodeOutcome.UNKNOWN,
)


@dataclass(frozen=True)
class ScaleSpec:
    """One benchmark scale point (SceneOps V2 Request 5.1 §3, extended by
    Request 5.2 §1): episode/step/channel counts plus deterministic
    realistic-variation knobs. Deliberately independent of nuScenes-mini or
    any other concrete SceneOps dataset -- the goal is to sweep scale and
    heterogeneity, not to reproduce one specific source dataset.

    Every variation knob defaults to "off" (uniform length, single
    revision, every channel core) so a caller that only sets
    ``num_episodes``/``steps_per_episode`` gets the exact SceneOps V2
    Request 5.1 behavior back -- ``DEFAULT_SCALE_LADDER`` below is what
    turns them on.
    """

    name: str
    num_episodes: int
    steps_per_episode: int
    num_observation_channels: int = 6
    num_action_channels: int = 4

    # None => every declared channel is "core" (always present, always
    # RESOLVED) -- legacy/backward-compatible default. A smaller value
    # marks observation_channels[:n]/action_channels[:n] as core; the
    # remainder are "extra" channels subject to ABSENT/MISSING variation
    # (see _extra_channel_present/_extra_channel_step_missing).
    num_core_observation_channels: int | None = None
    num_core_action_channels: int | None = None

    # Fraction of steps_per_episode the per-episode step_count may vary by
    # (deterministic, not random -- see _step_count_for). 0 => every
    # episode has exactly steps_per_episode steps.
    length_jitter_fraction: float = 0.0

    # Every Nth logical episode_id (by index) gets a second aligned
    # revision in addition to its first -- 0 => every episode has exactly
    # one revision (legacy/backward-compatible default).
    extra_revision_every: int = 0

    # Every Nth (episode_index + channel_index) combination omits an extra
    # channel entirely (ABSENT) rather than including it.
    extra_channel_absent_period: int = 3
    # Every Nth step of a present extra channel is status=MISSING instead
    # of RESOLVED.
    extra_channel_missing_period: int = 5

    @property
    def observation_channels(self) -> list[str]:
        return [f"obs_{i}" for i in range(self.num_observation_channels)]

    @property
    def action_channels(self) -> list[str]:
        return [f"act_{i}" for i in range(self.num_action_channels)]

    @property
    def core_observation_channel_count(self) -> int:
        return (
            self.num_observation_channels
            if self.num_core_observation_channels is None
            else self.num_core_observation_channels
        )

    @property
    def core_action_channel_count(self) -> int:
        return (
            self.num_action_channels
            if self.num_core_action_channels is None
            else self.num_core_action_channels
        )

    @property
    def core_observation_channels(self) -> list[str]:
        return self.observation_channels[: self.core_observation_channel_count]

    @property
    def core_action_channels(self) -> list[str]:
        return self.action_channels[: self.core_action_channel_count]


# The scale ladder (SceneOps V2 Request 5.2 §1): tiny/small/medium mirror
# real episode/step/channel counts; large deliberately keeps
# steps_per_episode/channel-count small (not "steps_per_episode=200 again")
# because the physical-layout question this tier exists to stress is
# EPISODE COUNT (small-file risk at 10,000+ EpisodeRefs), not total byte
# volume -- see docs/architecture/learning-data-scaling-baseline.md for why
# a naive steps_per_episode=200 * 10_000 episodes tier was impractical to
# build with today's per-row Python table builders (out of this request's
# scope to vectorize; see that doc's "remaining limitations").
DEFAULT_SCALE_LADDER: tuple[ScaleSpec, ...] = (
    ScaleSpec(
        name="tiny",
        num_episodes=10,
        steps_per_episode=60,
        num_observation_channels=6,
        num_action_channels=4,
        num_core_observation_channels=4,
        num_core_action_channels=3,
        length_jitter_fraction=0.3,
        extra_revision_every=4,
    ),
    ScaleSpec(
        name="small",
        num_episodes=100,
        steps_per_episode=100,
        num_observation_channels=8,
        num_action_channels=5,
        num_core_observation_channels=5,
        num_core_action_channels=3,
        length_jitter_fraction=0.3,
        extra_revision_every=10,
    ),
    ScaleSpec(
        name="medium",
        num_episodes=1_000,
        steps_per_episode=60,
        num_observation_channels=8,
        num_action_channels=5,
        num_core_observation_channels=5,
        num_core_action_channels=3,
        length_jitter_fraction=0.3,
        extra_revision_every=25,
    ),
    ScaleSpec(
        name="large",
        num_episodes=10_000,
        steps_per_episode=30,
        num_observation_channels=6,
        num_action_channels=4,
        num_core_observation_channels=4,
        num_core_action_channels=3,
        length_jitter_fraction=0.3,
        extra_revision_every=200,
    ),
)


def _task_for(episode_index: int) -> str:
    return _TASKS[episode_index % len(_TASKS)]


def _outcome_for(episode_index: int) -> EpisodeOutcome:
    return _OUTCOMES[episode_index % len(_OUTCOMES)]


def _step_count_for(spec: ScaleSpec, episode_index: int) -> int:
    """Deterministic per-episode step-count variation (SceneOps V2 Request
    5.2 §1) -- not random: a fixed formula keyed on ``episode_index``, so
    it is exactly reproducible without seed management. Both revisions of
    the same logical episode_id share the same step_count (mirrors
    interop_dataset.py's EPISODE_A_REV1/REV2, which also keep step_count
    identical across revisions and vary only content)."""
    if spec.length_jitter_fraction <= 0:
        return spec.steps_per_episode
    amplitude = max(1, int(spec.steps_per_episode * spec.length_jitter_fraction))
    offset = (episode_index * 7 + 3) % (2 * amplitude + 1) - amplitude
    return max(1, spec.steps_per_episode + offset)


def episode_revisions(spec: ScaleSpec, episode_index: int) -> list[int]:
    """Every revision number this logical episode_id has (SceneOps V2
    Request 5.2 §1) -- always includes 0; includes 1 as well when
    ``extra_revision_every`` marks this episode_index for a second
    revision."""
    revisions = [0]
    if spec.extra_revision_every > 0 and episode_index % spec.extra_revision_every == 0:
        revisions.append(1)
    return revisions


def all_episode_keys(spec: ScaleSpec) -> list[tuple[int, int]]:
    """Every ``(episode_index, revision)`` pair this spec actually
    generates, in generation order -- the total EpisodeRef count is
    ``len(all_episode_keys(spec))``, which is ``>= num_episodes`` whenever
    ``extra_revision_every`` is enabled."""
    return [
        (episode_index, revision)
        for episode_index in range(spec.num_episodes)
        for revision in episode_revisions(spec, episode_index)
    ]


def _channel_offset(channel_index: int, *, is_action: bool) -> float:
    # Widely spaced per-channel/per-namespace offsets (mirrors
    # interop_dataset.py's _CHANNEL_OFFSETS) so a channel-order or
    # namespace-mixup bug produces a visibly wrong number rather than a
    # coincidentally-plausible one.
    base = 0.5 if is_action else 0.0
    return base + 0.01 * (channel_index + 1)


def _is_scalar_channel(channel_index: int) -> bool:
    # Channel 0 in each namespace is scalar-valued, the rest are
    # VECTOR_DIM-vectors (mirrors interop_dataset.py's
    # gripper_position/gripper_command scalar-among-vectors mix).
    return channel_index == 0


def _channel_value(
    base: float, step_index: int, offset: float, *, scalar: bool
) -> list[float]:
    """Raw numeric component(s) for one channel at one step -- length 1 if
    ``scalar``, else ``VECTOR_DIM``. Shared by both the fixture generator
    and the independent expected-value formulas below, so the two can
    never silently drift apart."""
    root = base + step_index + offset
    if scalar:
        return [root]
    return [root + 0.001 * component for component in range(VECTOR_DIM)]


def _extra_channel_present(
    spec: ScaleSpec, episode_index: int, channel_index: int, *, is_action: bool
) -> bool:
    """Whether an *extra* (non-core) channel is included in this episode's
    steps at all (SceneOps V2 Request 5.2 §1) -- deterministic ABSENT
    variation. Never called for a core channel (always present)."""
    salt = 1 if is_action else 0
    return (
        episode_index + channel_index + salt
    ) % spec.extra_channel_absent_period != 0


def _extra_channel_step_missing(spec: ScaleSpec, step_index: int) -> bool:
    """Whether a present extra channel is status=MISSING at this step
    (SceneOps V2 Request 5.2 §1) -- deterministic, distinct from ABSENT
    (the channel is still in the dict, just without a resolved value)."""
    return (
        step_index % spec.extra_channel_missing_period
        == spec.extra_channel_missing_period - 1
    )


def _base_for(episode_index: int, revision: int) -> float:
    return float(episode_index) * 10_000.0 + float(revision) * _REVISION_BASE_STRIDE


def expected_observation(
    spec: ScaleSpec, episode_index: int, step_index: int, revision: int = 0
) -> list[float]:
    """Independent reference formula (never derived by reading the fixture
    back) for the dense observation vector at one (episode, step,
    revision), over ``core_observation_channels`` only -- the sole
    projection ``feature_projection_for()`` returns, and the only one
    guaranteed projectable at every step of every episode this module can
    generate (extra channels may be ABSENT/MISSING; core channels never
    are). Used by ``test_scale_fixture.py`` to check the generator against
    the real projection path at the smallest scale only."""
    base = _base_for(episode_index, revision)
    values: list[float] = []
    for i in range(spec.core_observation_channel_count):
        values.extend(
            _channel_value(
                base,
                step_index,
                _channel_offset(i, is_action=False),
                scalar=_is_scalar_channel(i),
            )
        )
    return values


def expected_action(
    spec: ScaleSpec, episode_index: int, step_index: int, revision: int = 0
) -> list[float]:
    base = _base_for(episode_index, revision)
    values: list[float] = []
    for i in range(spec.core_action_channel_count):
        values.extend(
            _channel_value(
                base,
                step_index,
                _channel_offset(i, is_action=True),
                scalar=_is_scalar_channel(i),
            )
        )
    return values


def _build_signal(
    *,
    channel: str,
    base: float,
    step_index: int,
    channel_index: int,
    is_action: bool,
    status: AlignedSignalStatus,
) -> AlignedSignal:
    scalar = _is_scalar_channel(channel_index)
    offset = _channel_offset(channel_index, is_action=is_action)
    value = None
    if status is AlignedSignalStatus.RESOLVED:
        components = _channel_value(base, step_index, offset, scalar=scalar)
        value = AlignedValue(
            kind=AlignedValueKind.NUMERIC_SCALAR
            if scalar
            else AlignedValueKind.NUMERIC_VECTOR,
            scalar=components[0] if scalar else None,
            vector=None if scalar else components,
        )
    return AlignedSignal(
        channel=channel,
        policy=AssociationPolicy.NEAREST,
        status=status,
        value=value,
        source_timestamp_us=step_index * DT_US,
        time_delta_us=0,
    )


def _learning_step(
    spec: ScaleSpec, episode_index: int, revision: int, step_index: int
) -> LearningStep:
    base = _base_for(episode_index, revision)

    observations: dict[str, AlignedSignal] = {}
    for i, channel in enumerate(spec.observation_channels):
        is_core = i < spec.core_observation_channel_count
        if not is_core and not _extra_channel_present(
            spec, episode_index, i, is_action=False
        ):
            continue
        status = AlignedSignalStatus.RESOLVED
        if not is_core and _extra_channel_step_missing(spec, step_index):
            status = AlignedSignalStatus.MISSING
        observations[channel] = _build_signal(
            channel=channel,
            base=base,
            step_index=step_index,
            channel_index=i,
            is_action=False,
            status=status,
        )

    actions: dict[str, AlignedSignal] = {}
    for i, channel in enumerate(spec.action_channels):
        is_core = i < spec.core_action_channel_count
        if not is_core and not _extra_channel_present(
            spec, episode_index, i, is_action=True
        ):
            continue
        status = AlignedSignalStatus.RESOLVED
        if not is_core and _extra_channel_step_missing(spec, step_index):
            status = AlignedSignalStatus.MISSING
        actions[channel] = _build_signal(
            channel=channel,
            base=base,
            step_index=step_index,
            channel_index=i,
            is_action=True,
            status=status,
        )

    return LearningStep(
        timestamp_us=step_index * DT_US, observations=observations, actions=actions
    )


def _episode_id(episode_index: int) -> str:
    return f"bench-ep-{episode_index:06d}"


def _checksum(episode_index: int, revision: int = 0) -> str:
    return f"chk-bench-{episode_index:06d}-r{revision}"


def episode_ref(spec: ScaleSpec, episode_index: int, revision: int = 0) -> EpisodeRef:
    return EpisodeRef(
        episode_id=_episode_id(episode_index),
        aligned_artifact_checksum=_checksum(episode_index, revision),
    )


def feature_projection_for(spec: ScaleSpec) -> FeatureProjection:
    """The core-channel-only projection -- guaranteed projectable across
    every episode this spec can generate (core channels are never
    ABSENT/MISSING). Equivalent to "every declared channel" when
    ``num_core_*_channels`` are unset (legacy/backward-compatible
    default)."""
    return FeatureProjection(
        observation_channels=spec.core_observation_channels,
        action_channels=spec.core_action_channels,
    )


def _aligned_episode(
    spec: ScaleSpec, episode_index: int, revision: int
) -> AlignedEpisode:
    step_count = _step_count_for(spec, episode_index)
    steps = [
        _learning_step(spec, episode_index, revision, step_index)
        for step_index in range(step_count)
    ]
    return AlignedEpisode(
        episode_id=_episode_id(episode_index),
        source_start_timestamp_us=0,
        source_end_timestamp_us=(step_count - 1) * DT_US,
        source_clock="mcap_log_time",
        alignment_semantics_version=ALIGNMENT_SEMANTICS_VERSION,
        alignment_config=_ALIGNMENT_CONFIG,
        target_frequency_hz=TARGET_FREQUENCY_HZ,
        achieved_frequency_hz=TARGET_FREQUENCY_HZ,
        dt_us=DT_US,
        step_count=step_count,
        duplicate_discarded_count=0,
        task=_task_for(episode_index),
        outcome=_outcome_for(episode_index),
        steps=steps,
    )


def _aligned_artifact(
    episode: AlignedEpisode, episode_index: int, revision: int
) -> AlignedEpisodeArtifact:
    checksum = _checksum(episode_index, revision)
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
    in ``all_episode_keys`` order -- pure, I/O-free, mirrors
    ``interop_dataset.build_interop_entries``'s role but parameterized by
    ``spec`` (including its realistic-variation knobs, SceneOps V2 Request
    5.2 §1)."""
    entries = []
    for episode_index, revision in all_episode_keys(spec):
        episode = _aligned_episode(spec, episode_index, revision)
        entries.append(
            (
                _checksum(episode_index, revision),
                _aligned_artifact(episode, episode_index, revision),
            )
        )
    return entries


@dataclass
class ScaledDatasetArtifacts:
    """Everything needed to open a fresh, uninstrumented SceneOpsDataset
    over one written scale point (SceneOps V2 Request 5.1, extended by
    5.2). Deliberately does *not* hold an already-open SceneOpsDataset or
    the ArtifactStore used to write it -- the benchmark harness opens its
    own dataset (through its own, possibly-instrumented ArtifactStore) so
    that dataset.open()'s cost is actually measured, not hidden inside
    fixture construction."""

    spec: ScaleSpec
    dataset_id: str
    dataset_version: str
    storage_root_uri: str
    learning_manifest: LearningDataExportManifest
    learning_manifest_checksum: str
    feature_projection: FeatureProjection


async def write_scaled_dataset_artifacts(
    tmp_path,
    spec: ScaleSpec,
    *,
    dataset_id: str | None = None,
    shard_policy: ShardPolicy | None = None,
    artifact_store: ArtifactStore | None = None,
    storage_root_uri: str | None = None,
) -> ScaledDatasetArtifacts:
    """Write one scale point's learning_episodes (single file) +
    learning_steps/learning_signals (sharded, SceneOps V2 Request 5.2) +
    LearningDataExportManifest, through the real
    ``write_sharded_learning_tables`` orchestration -- exactly the physical
    layout ``EXPORT_LEARNING_DATA`` produces in production.

    Returns identifiers only (no open SceneOpsDataset) -- callers
    construct their own ArtifactStore against ``storage_root_uri`` (or, if
    they passed one in, reuse it) and call ``SceneOpsDataset.open()``
    themselves so open()'s cost is part of what gets measured, not fixture
    setup.

    ``artifact_store``/``storage_root_uri`` default to a fresh
    ``LocalArtifactStore`` under ``tmp_path`` (the Request 5.1/5.2
    behavior, unchanged) -- pass both explicitly to target a different
    backend (e.g. a real ``S3ArtifactStore`` for MinIO integration
    coverage, SceneOps V2 Request 5.3); ``tmp_path`` is still used for
    ``AnalyticsTableWriter``'s own root (any object supporting ``/`` and
    ``str()`` works, not necessarily a filesystem path).
    """
    dataset_id = dataset_id or f"bench-{spec.name}"
    dataset_version = "v1"
    policy = shard_policy or default_shard_policy()
    if artifact_store is None:
        storage_root_uri = str(tmp_path / "storage")
        artifact_store = LocalArtifactStore(root_uri=storage_root_uri)
    elif storage_root_uri is None:
        raise ValueError("storage_root_uri is required when artifact_store is given")

    entries = build_scaled_entries(spec)

    export_config = LearningDataExportConfig()
    export_id = learning_data_export_id(
        aligned_checksums=[checksum for checksum, _ in entries],
        export_config=export_config,
    )

    writer = AnalyticsTableWriter(
        artifact_store=artifact_store, root_uri=str(tmp_path / "analytics")
    )

    episodes_df = build_learning_episodes_table(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        export_id=export_id,
        entries=entries,
    )
    episodes_result = await writer.write_learning_table(
        "learning_episodes",
        episodes_df,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        export_id=export_id,
    )

    shard_index = await write_sharded_learning_tables(
        writer,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        export_id=export_id,
        entries=entries,
        policy=policy,
    )

    table_uris = {"learning_episodes": episodes_result.uri}
    table_checksums = {"learning_episodes": episodes_result.checksum}
    row_counts = {
        "learning_episodes": episodes_df.height,
        "learning_steps": sum(shard.row_count for shard in shard_index.learning_steps),
        "learning_signals": sum(
            shard.row_count for shard in shard_index.learning_signals
        ),
    }

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
        layout_version=LEARNING_DATA_LAYOUT_VERSION_SHARDED,
        shard_index=shard_index,
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
    "all_episode_keys",
    "build_scaled_entries",
    "episode_ref",
    "episode_revisions",
    "expected_action",
    "expected_observation",
    "feature_projection_for",
    "write_scaled_dataset_artifacts",
]
