"""Tests for sceneops_analytics.external_adapters (SceneOps V2 Request 3.1,
write lifecycle refined in Request 3.1A): the shared, framework-neutral
ExternalDatasetAdapter/ExternalDatasetWriter contract built on top of
SceneOpsDataset (Request 2.7B).

No concrete LeRobot/RLDS adapter exists yet -- _RecordingAdapter/
_RecordingWriter below are synthetic, in-memory test-only doubles used
solely to exercise ExternalDatasetAdapter.export()'s shared orchestration
(sourcing, ordering, schema consistency, write lifecycle, semantic-loss
bookkeeping, report assembly) against real Parquet-backed SceneOpsDataset
instances, mirroring test_learning_dataset.py's fixture-building approach.
"""

from __future__ import annotations

from typing import Any, Literal

import pytest

from sceneops_analytics import (
    AnalyticsTableWriter,
    SceneOpsDataset,
    build_learning_episodes_table,
    build_learning_signals_table,
    build_learning_steps_table,
)
from sceneops_analytics.external_adapters import (
    EpisodeRefTraceabilityError,
    ExternalDatasetAdapter,
    ExternalDatasetWriter,
    ExternalEpisode,
    ExternalExportConfig,
    ExternalFeatureSchemaMismatchError,
    ExternalStep,
    MappingKind,
    SemanticField,
    StepOrderingError,
    UnsupportedSemanticError,
    UnsupportedSemanticPolicy,
    validate_episode_ref_traceability,
    validate_step_ordering,
)
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
from sceneops_storage import LocalArtifactStore

DATASET_ID = "ds1"
DATASET_VERSION = "v1"
_CONFIG = TemporalAlignmentConfig(target_frequency_hz=10.0)
_PROJECTION = FeatureProjection(
    observation_channels=["pos"], action_channels=["steering"]
)
_FULL_CAPABILITIES: dict[SemanticField, MappingKind] = {
    field: MappingKind.LOSSLESS for field in SemanticField
}


# ── fixture builders (mirrors test_learning_dataset.py's pattern) ─────────


def _scalar(value: float) -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_SCALAR, scalar=value),
        source_timestamp_us=0,
        time_delta_us=0,
    )


def _vector(values: list[float]) -> AlignedSignal:
    return AlignedSignal(
        channel="c",
        policy=AssociationPolicy.NEAREST,
        status=AlignedSignalStatus.RESOLVED,
        value=AlignedValue(kind=AlignedValueKind.NUMERIC_VECTOR, vector=values),
        source_timestamp_us=0,
        time_delta_us=0,
    )


def _episode(
    episode_id: str, steps: list[LearningStep], **overrides: Any
) -> AlignedEpisode:
    defaults = dict(
        episode_id=episode_id,
        source_start_timestamp_us=0,
        source_end_timestamp_us=max(len(steps) - 1, 0) * 100_000,
        source_clock="mcap_log_time",
        alignment_semantics_version=ALIGNMENT_SEMANTICS_VERSION,
        alignment_config=_CONFIG,
        target_frequency_hz=10.0,
        achieved_frequency_hz=10.0,
        dt_us=100_000,
        step_count=len(steps),
        duplicate_discarded_count=0,
        task="pick",
        outcome=EpisodeOutcome.SUCCESS,
        steps=steps,
    )
    defaults.update(overrides)
    return AlignedEpisode(**defaults)


def _artifact(episode: AlignedEpisode) -> AlignedEpisodeArtifact:
    return AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id=episode.episode_id,
            episode_manifest_uri=f"mem://episodes/{episode.episode_id}.json",
            source_artifact_id="src-1",
            source_manifest_sha256="s" * 64,
        ),
        aligned_episode=episode,
    )


def _steps(n: int, *, offset: float = 0.0) -> list[LearningStep]:
    return [
        LearningStep(
            timestamp_us=i * 100_000,
            observations={"pos": _scalar(offset + i)},
            actions={"steering": _scalar(offset + i * 0.5)},
        )
        for i in range(n)
    ]


async def _write_snapshot(
    tmp_path, artifact_store, *, entries: list[tuple[str, AlignedEpisodeArtifact]]
) -> tuple[LearningDataExportManifest, str]:
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
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            export_id=export_id,
            entries=entries,
        )
        result = await writer.write_learning_table(
            name,
            df,
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            export_id=export_id,
        )
        table_uris[name] = result.uri
        table_checksums[name] = result.checksum
        row_counts[name] = df.height

    manifest = LearningDataExportManifest(
        export_id=export_id,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
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
        episode_count=len({a.aligned_episode.episode_id for _, a in entries}),
    )
    write_result = await writer.write_learning_export_manifest(
        manifest,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        export_id=export_id,
    )
    return manifest, write_result.checksum


def _ref(episode_id: str, checksum: str) -> EpisodeRef:
    return EpisodeRef(episode_id=episode_id, aligned_artifact_checksum=checksum)


async def _open_dataset(
    tmp_path, entries: list[tuple[str, AlignedEpisodeArtifact]]
) -> SceneOpsDataset:
    artifact_store = LocalArtifactStore(root_uri=str(tmp_path / "storage"))
    manifest, checksum = await _write_snapshot(
        tmp_path, artifact_store, entries=entries
    )
    return await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=checksum,
        artifact_store=artifact_store,
    )


_FailPoint = Literal["initialize", "write_episode", "finalize"]


class _RecordingWriter(ExternalDatasetWriter):
    """Synthetic in-memory write session (test-only). Records lifecycle-call
    counts and every ExternalEpisode handed to it, in call order, so tests
    can assert on ExternalDatasetAdapter.export()'s write-lifecycle
    orchestration without a real target format (none exists in Request
    3.1/3.1A). ``fail_on`` optionally raises from one lifecycle method --
    for write_episode, on the *second* call, so a failure mid-export can be
    observed leaving one episode already written."""

    def __init__(self, *, fail_on: _FailPoint | None = None):
        self.initialize_calls = 0
        self.finalize_calls = 0
        self.written: list[ExternalEpisode] = []
        self._fail_on = fail_on

    async def initialize(self) -> None:
        self.initialize_calls += 1
        if self._fail_on == "initialize":
            raise RuntimeError("boom-initialize")

    async def write_episode(self, episode: ExternalEpisode) -> None:
        if self._fail_on == "write_episode" and len(self.written) == 1:
            raise RuntimeError("boom-write_episode")
        self.written.append(episode)

    async def finalize(self) -> None:
        self.finalize_calls += 1
        if self._fail_on == "finalize":
            raise RuntimeError("boom-finalize")


class _RecordingAdapter(ExternalDatasetAdapter):
    """Synthetic in-memory adapter (test-only). Opens one _RecordingWriter
    per export() call so tests can assert on export()'s shared orchestration
    without a real target format (none exists in Request 3.1/3.1A)."""

    def __init__(
        self,
        capabilities: dict[SemanticField, MappingKind] | None = None,
        *,
        fail_on: _FailPoint | None = None,
    ):
        self._capabilities = capabilities if capabilities is not None else {}
        self._fail_on = fail_on
        self.writers: list[_RecordingWriter] = []

    @property
    def format_name(self) -> str:
        return "test-format"

    @property
    def format_version(self) -> str:
        return "0"

    def semantic_capabilities(self):
        return self._capabilities

    def open_writer(self, config: ExternalExportConfig) -> _RecordingWriter:
        writer = _RecordingWriter(fail_on=self._fail_on)
        self.writers.append(writer)
        return writer

    @property
    def written(self) -> list[ExternalEpisode]:
        """Convenience accessor for tests that only ever call export() once
        per adapter -- the episodes its (single) writer recorded."""
        return self.writers[-1].written if self.writers else []


# ── tests ───────────────────────────────────────────────────────────────


async def test_deterministic_episode_and_step_ordering(tmp_path):
    entries = [
        ("chk-a", _artifact(_episode("ep-a", _steps(3)))),
        ("chk-b", _artifact(_episode("ep-b", _steps(2, offset=100.0)))),
    ]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter(_FULL_CAPABILITIES)

    report = await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))

    assert [e.episode_ref for e in adapter.written] == dataset.episodes()
    for episode in adapter.written:
        assert [s.step_index for s in episode.steps] == list(range(len(episode.steps)))
        timestamps = [s.timestamp_us for s in episode.steps]
        assert timestamps == sorted(timestamps)
    assert report.exported_episode_count == 2
    assert report.exported_step_count == 5


async def test_multiple_revisions_of_same_episode_remain_distinct(tmp_path):
    entries = [
        ("chk-a", _artifact(_episode("ep-1", _steps(3)))),
        ("chk-b", _artifact(_episode("ep-1", _steps(2, offset=50.0)))),
    ]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter(_FULL_CAPABILITIES)

    report = await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))

    refs = [e.episode_ref for e in adapter.written]
    assert refs == [_ref("ep-1", "chk-a"), _ref("ep-1", "chk-b")]
    assert len(set(refs)) == 2
    assert report.exported_episode_count == 2

    by_ref = {e.episode_ref: e for e in adapter.written}
    assert len(by_ref[_ref("ep-1", "chk-a")].steps) == 3
    assert len(by_ref[_ref("ep-1", "chk-b")].steps) == 2
    assert by_ref[_ref("ep-1", "chk-b")].steps[0].observation == [50.0]


async def test_explicit_feature_mapping(tmp_path):
    entries = [("chk-a", _artifact(_episode("ep-1", _steps(2))))]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter(_FULL_CAPABILITIES)

    report = await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))

    assert report.feature_schema is not None
    assert [e.channel for e in report.feature_schema.observations] == ["pos"]
    assert [e.channel for e in report.feature_schema.actions] == ["steering"]

    episode = adapter.written[0]
    assert episode.task == "pick"
    assert episode.outcome == EpisodeOutcome.SUCCESS
    assert episode.steps[0].observation == [0.0]
    assert episode.steps[0].action == [0.0]
    assert episode.steps[1].observation == [1.0]
    assert episode.steps[1].action == [0.5]


async def test_feature_schema_mismatch_across_episodes_raises(tmp_path):
    vector_step = LearningStep(
        timestamp_us=0,
        observations={"pos": _vector([1.0, 2.0])},
        actions={"steering": _scalar(0.0)},
    )
    entries = [
        ("chk-a", _artifact(_episode("ep-1", _steps(2)))),
        ("chk-b", _artifact(_episode("ep-2", [vector_step]))),
    ]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter(_FULL_CAPABILITIES)

    with pytest.raises(ExternalFeatureSchemaMismatchError):
        await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))


async def test_episode_ref_traceability(tmp_path):
    entries = [("chk-a", _artifact(_episode("ep-1", _steps(2))))]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter(_FULL_CAPABILITIES)

    report = await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))

    assert report.source_episode_refs == dataset.episodes()
    for episode in adapter.written:
        assert episode.episode_ref in dataset.episodes()


def test_episode_ref_traceability_validation_rejects_unknown_ref():
    known = [_ref("ep-1", "chk-a")]
    rogue = ExternalEpisode(episode_ref=_ref("ep-99", "chk-x"), steps=[])

    with pytest.raises(EpisodeRefTraceabilityError):
        validate_episode_ref_traceability([rogue], known)


def test_step_ordering_validation_rejects_non_increasing_timestamps():
    episode = ExternalEpisode(
        episode_ref=_ref("ep-1", "chk-a"),
        steps=[
            ExternalStep(step_index=0, timestamp_us=100),
            ExternalStep(step_index=1, timestamp_us=100),
        ],
    )

    with pytest.raises(StepOrderingError):
        validate_step_ordering(episode)


async def test_semantic_loss_reporting(tmp_path):
    entries = [("chk-a", _artifact(_episode("ep-1", _steps(2))))]
    dataset = await _open_dataset(tmp_path, entries)
    capabilities = dict(_FULL_CAPABILITIES)
    capabilities[SemanticField.SIGNAL_STATUS] = MappingKind.LOSSY_EXPLICIT
    adapter = _RecordingAdapter(capabilities)

    report = await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))

    losses = {loss.field: loss for loss in report.semantic_losses}
    assert losses.keys() == {SemanticField.SIGNAL_STATUS}
    assert losses[SemanticField.SIGNAL_STATUS].mapping is MappingKind.LOSSY_EXPLICIT


async def test_unsupported_mapping_fails_by_default(tmp_path):
    entries = [("chk-a", _artifact(_episode("ep-1", _steps(1))))]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter({})  # declares nothing -- every field UNSUPPORTED

    with pytest.raises(UnsupportedSemanticError):
        await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))


async def test_unsupported_mapping_recorded_when_policy_is_record(tmp_path):
    entries = [("chk-a", _artifact(_episode("ep-1", _steps(1))))]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter({})
    config = ExternalExportConfig(
        projection=_PROJECTION,
        unsupported_semantic_policy=UnsupportedSemanticPolicy.RECORD,
    )

    report = await adapter.export(dataset, config)

    unsupported_fields = {
        loss.field
        for loss in report.semantic_losses
        if loss.mapping is MappingKind.UNSUPPORTED
    }
    assert unsupported_fields == set(SemanticField)


async def test_deterministic_export_metadata(tmp_path):
    entries = [
        ("chk-a", _artifact(_episode("ep-1", _steps(2)))),
        ("chk-b", _artifact(_episode("ep-2", _steps(3, offset=10.0)))),
    ]
    dataset = await _open_dataset(tmp_path, entries)
    config = ExternalExportConfig(projection=_PROJECTION)

    report1 = await _RecordingAdapter(_FULL_CAPABILITIES).export(dataset, config)
    report2 = await _RecordingAdapter(_FULL_CAPABILITIES).export(dataset, config)

    assert report1 == report2
    assert report1.source_dataset_id == DATASET_ID
    assert report1.source_dataset_version == DATASET_VERSION
    assert report1.source_export_id == dataset.learning_manifest.export_id


# ── write lifecycle (Request 3.1A) ─────────────────────────────────────────


async def test_writer_initialize_called_exactly_once(tmp_path):
    entries = [
        ("chk-a", _artifact(_episode("ep-a", _steps(2)))),
        ("chk-b", _artifact(_episode("ep-b", _steps(1)))),
    ]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter(_FULL_CAPABILITIES)

    await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))

    assert len(adapter.writers) == 1
    assert adapter.writers[-1].initialize_calls == 1


async def test_multiple_episodes_share_one_writer_session(tmp_path):
    entries = [
        ("chk-a", _artifact(_episode("ep-a", _steps(2)))),
        ("chk-b", _artifact(_episode("ep-b", _steps(3)))),
    ]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter(_FULL_CAPABILITIES)

    await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))

    assert len(adapter.writers) == 1
    assert len(adapter.writers[-1].written) == 2


async def test_finalize_called_exactly_once_after_successful_export(tmp_path):
    entries = [("chk-a", _artifact(_episode("ep-1", _steps(2))))]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter(_FULL_CAPABILITIES)

    await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))

    assert adapter.writers[-1].finalize_calls == 1


async def test_initialize_failure_propagates_and_skips_write_and_finalize(tmp_path):
    entries = [("chk-a", _artifact(_episode("ep-1", _steps(1))))]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter(_FULL_CAPABILITIES, fail_on="initialize")

    with pytest.raises(RuntimeError, match="boom-initialize"):
        await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))

    assert adapter.writers[-1].written == []
    assert adapter.writers[-1].finalize_calls == 0


async def test_write_episode_failure_propagates_and_skips_finalize(tmp_path):
    entries = [
        ("chk-a", _artifact(_episode("ep-a", _steps(1)))),
        ("chk-b", _artifact(_episode("ep-b", _steps(1)))),
    ]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter(_FULL_CAPABILITIES, fail_on="write_episode")

    with pytest.raises(RuntimeError, match="boom-write_episode"):
        await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))

    assert len(adapter.writers[-1].written) == 1
    assert adapter.writers[-1].finalize_calls == 0


async def test_finalize_failure_propagates(tmp_path):
    entries = [("chk-a", _artifact(_episode("ep-1", _steps(1))))]
    dataset = await _open_dataset(tmp_path, entries)
    adapter = _RecordingAdapter(_FULL_CAPABILITIES, fail_on="finalize")

    with pytest.raises(RuntimeError, match="boom-finalize"):
        await adapter.export(dataset, ExternalExportConfig(projection=_PROJECTION))

    assert len(adapter.writers[-1].written) == 1
    assert adapter.writers[-1].finalize_calls == 1
