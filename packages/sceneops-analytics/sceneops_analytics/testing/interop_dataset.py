"""Deterministic SceneOps interoperability golden dataset (SceneOps V2
Request 3.2): a small, fully in-memory Phase 2 learning-data snapshot that
future external-format adapters (LeRobot, RLDS, ...) and their round-trip
tests can share, instead of every adapter's test suite hand-rolling its own
fixture episodes.

::

    hand-built AlignedEpisode/AlignedEpisodeArtifact fixtures (Phase 2)
            -> build_learning_*_table (Request 2.5)
            -> real Parquet, written through AnalyticsTableWriter
            -> LearningDataExportManifest
            -> SceneOpsDataset.open() (Request 2.7B)
            -> InteropDatasetBootstrap

Everything here is built from the exact same Phase 2 contracts real
pipeline code uses (AlignedEpisode, AlignedEpisodeArtifact,
LearningDataExportManifest, build_learning_*_table, AnalyticsTableWriter,
SceneOpsDataset.open) -- no handcrafted SceneOpsDataset, no mocked Parquet
access.

Scope (SceneOps V2 Request 3.2 §8): numeric observations/actions only.
Deliberately excludes MISSING/ABSENT/REFERENCE/IMAGE/VIDEO -- this
establishes a clean, fully-lossless numeric interoperability path first;
later fixtures can layer lossy/visual cases on top without changing this
one.

This module ships inside sceneops_analytics but is test-support code, not a
domain contract: nothing here is re-exported from sceneops_analytics's
top-level ``__init__``, and it must never be imported by production code.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_analytics.learning_dataset import SceneOpsDataset
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

# SceneOps V2 Request 3.2B: aligned with the shared E2E fixture catalog's
# "interop" member (scripts/e2e/lib.sh's resolve_e2e_fixture) -- was
# "sceneops-interop-golden"/"v1" before this request.
INTEROP_DATASET_ID = "test-e2e-interop"
INTEROP_DATASET_VERSION = "test-v1"

DT_US = 100_000
TARGET_FREQUENCY_HZ = 10.0

_ALIGNMENT_CONFIG = TemporalAlignmentConfig(target_frequency_hz=TARGET_FREQUENCY_HZ)

# Simple dense numeric schema (SceneOps V2 Request 3.2 §4) -- vectors then
# scalars, in this exact declared order, matching the platform's existing
# FeatureProjection convention (order is never normalized; it directly
# determines dense output ordering).
INTEROP_FEATURE_PROJECTION = FeatureProjection(
    observation_channels=["joint_position", "joint_velocity", "gripper_position"],
    action_channels=["target_joint", "gripper_command"],
)

# Per-channel fractional offset, deliberately spaced out so a channel-order
# mistake (e.g. swapping joint_position and joint_velocity) or a
# vector-component-order mistake produces a visibly wrong number rather than
# a coincidentally-plausible one.
_CHANNEL_OFFSETS: dict[str, float] = {
    "joint_position": 0.01,
    "joint_velocity": 0.11,
    "gripper_position": 0.21,
    "target_joint": 0.31,
    "gripper_command": 0.41,
}


def _vector_value(base: float, step_index: int, channel: str) -> list[float]:
    root = base + step_index + _CHANNEL_OFFSETS[channel]
    return [root, root + 0.001, root + 0.002]


def _scalar_value(base: float, step_index: int, channel: str) -> float:
    return base + step_index + _CHANNEL_OFFSETS[channel]


def _vector_signal(base: float, step_index: int, channel: str) -> AlignedSignal:
    return AlignedSignal(
        channel=channel,
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(
            kind=AlignedValueKind.NUMERIC_VECTOR,
            vector=_vector_value(base, step_index, channel),
        ),
        source_timestamp_us=step_index * DT_US,
        time_delta_us=0,
    )


def _scalar_signal(base: float, step_index: int, channel: str) -> AlignedSignal:
    return AlignedSignal(
        channel=channel,
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(
            kind=AlignedValueKind.NUMERIC_SCALAR,
            scalar=_scalar_value(base, step_index, channel),
        ),
        source_timestamp_us=step_index * DT_US,
        time_delta_us=0,
    )


def _learning_step(base: float, step_index: int) -> LearningStep:
    return LearningStep(
        timestamp_us=step_index * DT_US,
        observations={
            "joint_position": _vector_signal(base, step_index, "joint_position"),
            "joint_velocity": _vector_signal(base, step_index, "joint_velocity"),
            "gripper_position": _scalar_signal(base, step_index, "gripper_position"),
        },
        actions={
            "target_joint": _vector_signal(base, step_index, "target_joint"),
            "gripper_command": _scalar_signal(base, step_index, "gripper_command"),
        },
    )


def _expected_observation(base: float, step_index: int) -> list[float]:
    # Same channel order as INTEROP_FEATURE_PROJECTION.observation_channels,
    # vector components flattened in place -- mirrors
    # sceneops_core.episodes.learning.projection's dense-projection order
    # exactly, computed independently from the same _vector_value/
    # _scalar_value formulas used to build the source AlignedSignals above
    # (never by reading the exported dataset back).
    return [
        *_vector_value(base, step_index, "joint_position"),
        *_vector_value(base, step_index, "joint_velocity"),
        _scalar_value(base, step_index, "gripper_position"),
    ]


def _expected_action(base: float, step_index: int) -> list[float]:
    return [
        *_vector_value(base, step_index, "target_joint"),
        _scalar_value(base, step_index, "gripper_command"),
    ]


@dataclass(frozen=True)
class InteropEpisodeSpec:
    """One fixture episode's identity + generator parameters (SceneOps V2
    Request 3.2 §3). ``base`` is the sole per-episode free variable in the
    value formulas above -- widely spaced (0 / 1_000 / 2_000) so a
    cross-episode data mixup is never coincidentally plausible."""

    episode_id: str
    aligned_artifact_checksum: str
    task: str
    outcome: EpisodeOutcome
    step_count: int
    base: float


# Episode A, Episode B, and a second revision of Episode A -- same
# episode_id as A, a different aligned_artifact_checksum, different content
# (base=1_000.0 instead of 0.0). Proves episode_id != revision identity: an
# EpisodeRef is (episode_id, aligned_artifact_checksum), and the two "ep-a"
# revisions must remain distinct EpisodeRefs throughout (SceneOps V2 Request
# 3.2 §3).
EPISODE_A_REV1 = InteropEpisodeSpec(
    episode_id="ep-a",
    aligned_artifact_checksum="chk-ep-a-r1",
    task="pick",
    outcome=EpisodeOutcome.SUCCESS,
    step_count=8,
    base=0.0,
)
EPISODE_A_REV2 = InteropEpisodeSpec(
    episode_id="ep-a",
    aligned_artifact_checksum="chk-ep-a-r2",
    task="pick",
    outcome=EpisodeOutcome.SUCCESS,
    step_count=8,
    base=1_000.0,
)
EPISODE_B = InteropEpisodeSpec(
    episode_id="ep-b",
    aligned_artifact_checksum="chk-ep-b",
    task="place",
    outcome=EpisodeOutcome.FAILURE,
    step_count=6,
    base=2_000.0,
)

INTEROP_EPISODE_SPECS: tuple[InteropEpisodeSpec, ...] = (
    EPISODE_A_REV1,
    EPISODE_A_REV2,
    EPISODE_B,
)

EPISODE_A_REV1_REF = EpisodeRef(
    episode_id=EPISODE_A_REV1.episode_id,
    aligned_artifact_checksum=EPISODE_A_REV1.aligned_artifact_checksum,
)
EPISODE_A_REV2_REF = EpisodeRef(
    episode_id=EPISODE_A_REV2.episode_id,
    aligned_artifact_checksum=EPISODE_A_REV2.aligned_artifact_checksum,
)
EPISODE_B_REF = EpisodeRef(
    episode_id=EPISODE_B.episode_id,
    aligned_artifact_checksum=EPISODE_B.aligned_artifact_checksum,
)


def _aligned_episode(spec: InteropEpisodeSpec) -> AlignedEpisode:
    steps = [_learning_step(spec.base, i) for i in range(spec.step_count)]
    return AlignedEpisode(
        episode_id=spec.episode_id,
        source_start_timestamp_us=0,
        source_end_timestamp_us=(spec.step_count - 1) * DT_US,
        source_clock="mcap_log_time",
        alignment_semantics_version=ALIGNMENT_SEMANTICS_VERSION,
        alignment_config=_ALIGNMENT_CONFIG,
        target_frequency_hz=TARGET_FREQUENCY_HZ,
        achieved_frequency_hz=TARGET_FREQUENCY_HZ,
        dt_us=DT_US,
        step_count=spec.step_count,
        duplicate_discarded_count=0,
        task=spec.task,
        outcome=spec.outcome,
        steps=steps,
    )


def _aligned_artifact(
    episode: AlignedEpisode, spec: InteropEpisodeSpec
) -> AlignedEpisodeArtifact:
    return AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id=episode.episode_id,
            episode_manifest_uri=(
                f"mem://interop/{spec.episode_id}/{spec.aligned_artifact_checksum}.json"
            ),
            source_artifact_id=f"src-{spec.aligned_artifact_checksum}",
            source_manifest_sha256="s" * 64,
        ),
        aligned_episode=episode,
    )


@dataclass(frozen=True)
class ExpectedStep:
    """One step's golden values, derived directly from the fixture's own
    generator formulas (SceneOps V2 Request 3.2 §6) -- never by reading the
    exported dataset back."""

    step_index: int
    timestamp_us: int
    observation: list[float]
    action: list[float]


@dataclass(frozen=True)
class ExpectedEpisode:
    """One episode's golden reference (SceneOps V2 Request 3.2 §6)."""

    episode_ref: EpisodeRef
    task: str
    outcome: EpisodeOutcome
    steps: list[ExpectedStep]

    @property
    def step_count(self) -> int:
        return len(self.steps)


def _expected_episode(spec: InteropEpisodeSpec) -> ExpectedEpisode:
    steps = [
        ExpectedStep(
            step_index=i,
            timestamp_us=i * DT_US,
            observation=_expected_observation(spec.base, i),
            action=_expected_action(spec.base, i),
        )
        for i in range(spec.step_count)
    ]
    return ExpectedEpisode(
        episode_ref=EpisodeRef(
            episode_id=spec.episode_id,
            aligned_artifact_checksum=spec.aligned_artifact_checksum,
        ),
        task=spec.task,
        outcome=spec.outcome,
        steps=steps,
    )


@dataclass
class InteropDatasetBootstrap:
    """Everything a future external-adapter/round-trip test needs from one
    SceneOps interoperability golden dataset (SceneOps V2 Request 3.2).

    ``expected_episodes``/``expected_by_ref`` are derived directly from this
    module's fixture generator formulas -- never by reading
    ``learning_manifest``/``dataset`` back -- so they are a meaningful
    independent reference for future round-trip validation.

    ``episode_refs`` is exactly ``dataset.episodes()`` (already
    deterministic, sorted by (episode_id, aligned_artifact_checksum)); every
    other list here is aligned to it index-for-index.
    """

    artifact_store: ArtifactStore
    learning_manifest: LearningDataExportManifest
    learning_manifest_checksum: str
    dataset: SceneOpsDataset
    feature_projection: FeatureProjection
    episode_refs: list[EpisodeRef]
    expected_episodes: list[ExpectedEpisode]
    expected_by_ref: dict[EpisodeRef, ExpectedEpisode]


def build_interop_entries() -> list[tuple[str, AlignedEpisodeArtifact]]:
    """The golden dataset's raw (checksum, AlignedEpisodeArtifact) entries,
    in ``INTEROP_EPISODE_SPECS`` order (SceneOps V2 Request 3.2). Pure,
    I/O-free -- shared by ``build_interop_test_dataset`` (in-memory/
    LocalArtifactStore, for unit tests) and the persistent fixture
    bootstrap (real ArtifactStore/Postgres, Request 3.2C), so the golden
    fixture's actual content is defined exactly once and never duplicated.
    """
    return [
        (
            spec.aligned_artifact_checksum,
            _aligned_artifact(_aligned_episode(spec), spec),
        )
        for spec in INTEROP_EPISODE_SPECS
    ]


def compute_expected_interop_episodes() -> dict[EpisodeRef, ExpectedEpisode]:
    """The golden dataset's expected per-episode reference, keyed by
    EpisodeRef (SceneOps V2 Request 3.2 §6). Pure, I/O-free -- derived
    directly from this module's generator formulas, never by reading an
    exported dataset back. Shared by ``build_interop_test_dataset`` and the
    persistent fixture bootstrap/verification (Request 3.2C).
    """
    return {
        EpisodeRef(
            episode_id=spec.episode_id,
            aligned_artifact_checksum=spec.aligned_artifact_checksum,
        ): _expected_episode(spec)
        for spec in INTEROP_EPISODE_SPECS
    }


async def build_interop_test_dataset(tmp_path) -> InteropDatasetBootstrap:
    """Build the SceneOps interoperability golden dataset (SceneOps V2
    Request 3.2) via the real Phase 2 pipeline -- build_learning_*_table,
    AnalyticsTableWriter, LearningDataExportManifest, SceneOpsDataset.open()
    -- never a handcrafted SceneOpsDataset or mocked Parquet access.

    ``tmp_path`` should be a fresh directory per call (e.g. pytest's
    ``tmp_path``/``tmp_path_factory`` fixtures) -- the returned
    artifact_store/table URIs live under it, but nothing semantic depends on
    it: two independent calls with two different ``tmp_path``s produce
    identical EpisodeRefs, step ordering, timestamps, feature values, and
    expected metadata (SceneOps V2 Request 3.2 §7). ``export_id`` in
    particular is a hash of the fixture's aligned checksums + export config
    only (see learning_data_export_id) -- never of any path -- so it is
    identical across independent calls too.
    """
    artifact_store = LocalArtifactStore(root_uri=str(tmp_path / "storage"))

    entries = build_interop_entries()

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
            dataset_id=INTEROP_DATASET_ID,
            dataset_version=INTEROP_DATASET_VERSION,
            export_id=export_id,
            entries=entries,
        )
        result = await writer.write_learning_table(
            name,
            df,
            dataset_id=INTEROP_DATASET_ID,
            dataset_version=INTEROP_DATASET_VERSION,
            export_id=export_id,
        )
        table_uris[name] = result.uri
        table_checksums[name] = result.checksum
        row_counts[name] = df.height

    learning_manifest = LearningDataExportManifest(
        export_id=export_id,
        dataset_id=INTEROP_DATASET_ID,
        dataset_version=INTEROP_DATASET_VERSION,
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
        episode_count=len({spec.episode_id for spec in INTEROP_EPISODE_SPECS}),
    )
    write_result = await writer.write_learning_export_manifest(
        learning_manifest,
        dataset_id=INTEROP_DATASET_ID,
        dataset_version=INTEROP_DATASET_VERSION,
        export_id=export_id,
    )
    learning_manifest_checksum = write_result.checksum

    dataset = await SceneOpsDataset.open(
        learning_manifest=learning_manifest,
        learning_manifest_checksum=learning_manifest_checksum,
        artifact_store=artifact_store,
    )

    expected_by_ref = compute_expected_interop_episodes()
    episode_refs = dataset.episodes()
    expected_episodes = [expected_by_ref[ref] for ref in episode_refs]

    return InteropDatasetBootstrap(
        artifact_store=artifact_store,
        learning_manifest=learning_manifest,
        learning_manifest_checksum=learning_manifest_checksum,
        dataset=dataset,
        feature_projection=INTEROP_FEATURE_PROJECTION,
        episode_refs=episode_refs,
        expected_episodes=expected_episodes,
        expected_by_ref=expected_by_ref,
    )


__all__ = [
    "EPISODE_A_REV1",
    "EPISODE_A_REV1_REF",
    "EPISODE_A_REV2",
    "EPISODE_A_REV2_REF",
    "EPISODE_B",
    "EPISODE_B_REF",
    "INTEROP_DATASET_ID",
    "INTEROP_DATASET_VERSION",
    "INTEROP_EPISODE_SPECS",
    "INTEROP_FEATURE_PROJECTION",
    "ExpectedEpisode",
    "ExpectedStep",
    "InteropDatasetBootstrap",
    "InteropEpisodeSpec",
    "build_interop_entries",
    "build_interop_test_dataset",
    "compute_expected_interop_episodes",
]
