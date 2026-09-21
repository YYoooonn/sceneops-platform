"""SceneOpsDataset: read-only, Parquet-backed native dataset access layer
(SceneOps V2 Request 2.7B).

::

    LearningDataExportManifest + EpisodeCurationManifest (optional)
            -> learning_episodes/learning_steps/learning_signals.parquet
            -> SceneOpsDataset
            -> Episode / Step / Window access
            -> sceneops_core.episodes.learning (Request 2.7A) projection

This module never redefines the 2.7A contracts (EpisodeRef, FeatureProjection,
FeatureSchema, StepSample, SequenceRef, SequenceSample, resolve_feature_schema,
project_step, project_sequence, validate_sequence_bounds) -- it resolves
AlignedEpisode/LearningStep data from Parquet and hands it to those pure
functions unchanged.
"""

from __future__ import annotations

import io

import polars as pl

from sceneops_core.episodes.alignment import LearningStep
from sceneops_core.episodes.curation import EpisodeCurationManifest
from sceneops_core.episodes.learning import (
    EpisodeRef,
    FeatureProjection,
    FeatureSchema,
    MissingFeaturePolicy,
    SequenceRef,
    SequenceSample,
    StepSample,
)
from sceneops_core.episodes.learning import project_sequence as _pure_project_sequence
from sceneops_core.episodes.learning import project_step as _pure_project_step
from sceneops_core.episodes.learning import (
    resolve_feature_schema as _pure_resolve_feature_schema,
)
from sceneops_core.episodes.learning_export import LearningDataExportManifest
from sceneops_storage import ArtifactStore

from .errors import (
    CurationManifestMismatchError,
    DatasetManifestMismatchError,
    EpisodeNotFoundError,
    LearningDataIntegrityError,
    LearningTableMissingError,
    StepOutOfRangeError,
)
from .reconstruct import step_from_rows
from .schemas import EpisodeMetadata

_REQUIRED_TABLES = ("learning_episodes", "learning_steps", "learning_signals")


def _strip_sha_prefix(checksum: str) -> str:
    return checksum.removeprefix("sha256:")


async def _read_parquet_table(artifact_store: ArtifactStore, uri: str) -> pl.DataFrame:
    data = await artifact_store.read_bytes(uri)
    return pl.read_parquet(io.BytesIO(data))


def _check_curation_matches_export(
    curation_manifest: EpisodeCurationManifest,
    learning_manifest: LearningDataExportManifest,
    learning_manifest_checksum: str,
) -> None:
    if (
        curation_manifest.dataset_id != learning_manifest.dataset_id
        or curation_manifest.dataset_version != learning_manifest.dataset_version
    ):
        raise DatasetManifestMismatchError(
            f"curation_manifest dataset={curation_manifest.dataset_id!r}/"
            f"{curation_manifest.dataset_version!r} does not match "
            f"learning_manifest dataset={learning_manifest.dataset_id!r}/"
            f"{learning_manifest.dataset_version!r}"
        )

    expected = _strip_sha_prefix(curation_manifest.source_learning_export.checksum)
    actual = _strip_sha_prefix(learning_manifest_checksum)
    if expected != actual:
        raise CurationManifestMismatchError(
            "curation_manifest.source_learning_export.checksum "
            f"({curation_manifest.source_learning_export.checksum!r}) does "
            "not match the opened learning_manifest_checksum "
            f"({learning_manifest_checksum!r}) -- this curation run did not "
            "select over the exact export being opened"
        )


def _schema_cache_key(
    ref: EpisodeRef, projection: FeatureProjection
) -> tuple[EpisodeRef, tuple[str, ...], tuple[str, ...]]:
    return (
        ref,
        tuple(projection.observation_channels),
        tuple(projection.action_channels),
    )


class SceneOpsDataset:
    """Read-only, Parquet-backed native dataset over one pinned
    LearningDataExportManifest snapshot, optionally narrowed by one pinned
    EpisodeCurationManifest selection (SceneOps V2 Request 2.7B).

    Construct via ``await SceneOpsDataset.open(...)`` -- never directly.

    Episode metadata (learning_episodes.parquet) is read once, eagerly, at
    open() time -- it is small (one row per exposed EpisodeRef) and always
    needed for episodes()/len()/get_episode()/boundary checks.
    learning_steps.parquet/learning_signals.parquet are fetched at most once
    each, lazily, on first access that actually needs step-level data for
    *some* episode -- opening a dataset or listing/inspecting episodes never
    touches them. Every method that may trigger that first fetch is async;
    everything answerable from already-loaded episode metadata is
    synchronous.

    A resolved FeatureSchema and a reconstructed episode's full step list
    are each cached in memory per EpisodeRef (and, for schemas, per
    FeatureProjection) once computed -- never persisted, never shared
    across `SceneOpsDataset` instances.
    """

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        learning_manifest: LearningDataExportManifest,
        episode_refs: list[EpisodeRef],
        metadata_by_ref: dict[EpisodeRef, EpisodeMetadata],
    ) -> None:
        self._artifact_store = artifact_store
        self._learning_manifest = learning_manifest
        self._episode_refs = episode_refs
        self._metadata_by_ref = metadata_by_ref

        self._steps_df: pl.DataFrame | None = None
        self._signals_df: pl.DataFrame | None = None
        self._episode_steps_cache: dict[EpisodeRef, list[LearningStep]] = {}
        self._schema_cache: dict[
            tuple[EpisodeRef, tuple[str, ...], tuple[str, ...]], FeatureSchema
        ] = {}

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    @classmethod
    async def open(
        cls,
        *,
        learning_manifest: LearningDataExportManifest,
        learning_manifest_checksum: str,
        artifact_store: ArtifactStore,
        curation_manifest: EpisodeCurationManifest | None = None,
    ) -> "SceneOpsDataset":
        """Open a dataset over ``learning_manifest`` (already resolved and
        checksum-verified by the caller -- mirrors every other
        resolve-then-verify-then-parse call site in this codebase, e.g.
        apps/worker's resolve_and_verify_learning_export_manifest).

        Without ``curation_manifest``: exposes every aligned revision
        declared by ``learning_manifest.inputs`` (SceneOps V2 Request 2.7B
        §3) -- never "latest".

        With ``curation_manifest``: exposes only the (episode_id,
        aligned_artifact_checksum) pairs its decisions marked
        ``selected=True``. ``curation_manifest.source_learning_export``
        must pin this exact ``learning_manifest`` (by checksum, not
        export_id/dataset_id alone) or open() fails.
        """
        missing_tables = [
            name
            for name in _REQUIRED_TABLES
            if name not in learning_manifest.table_uris
        ]
        if missing_tables:
            raise LearningTableMissingError(
                f"learning_manifest.table_uris is missing required table(s) "
                f"{missing_tables} (export_id={learning_manifest.export_id!r})"
            )

        if curation_manifest is not None:
            _check_curation_matches_export(
                curation_manifest, learning_manifest, learning_manifest_checksum
            )

        episodes_df = await _read_parquet_table(
            artifact_store, learning_manifest.table_uris["learning_episodes"]
        )

        declared = {
            (item.episode_id, item.aligned_artifact_checksum)
            for item in learning_manifest.inputs
        }
        present = {
            (row["episode_id"], row["aligned_artifact_checksum"])
            for row in episodes_df.iter_rows(named=True)
        }

        if curation_manifest is not None:
            exposed_keys = sorted(
                {
                    (decision.episode_id, decision.aligned_artifact_checksum)
                    for decision in curation_manifest.decisions
                    if decision.selected
                }
            )
            for key in exposed_keys:
                if key not in declared:
                    raise CurationManifestMismatchError(
                        f"curation selected {key!r}, which is not among "
                        "learning_manifest.inputs (export_id="
                        f"{learning_manifest.export_id!r})"
                    )
        else:
            if declared != present:
                raise LearningDataIntegrityError(
                    "learning_manifest.inputs and learning_episodes.parquet "
                    "rows disagree: "
                    f"declared_only={sorted(declared - present)}, "
                    f"table_only={sorted(present - declared)}"
                )
            exposed_keys = sorted(present)

        missing_rows = [key for key in exposed_keys if key not in present]
        if missing_rows:
            raise LearningDataIntegrityError(
                "the following selected/declared (episode_id, "
                "aligned_artifact_checksum) pairs have no row in "
                f"learning_episodes.parquet: {missing_rows}"
            )

        episode_refs = [
            EpisodeRef(episode_id=episode_id, aligned_artifact_checksum=checksum)
            for episode_id, checksum in exposed_keys
        ]
        exposed_ref_set = set(episode_refs)

        metadata_by_ref: dict[EpisodeRef, EpisodeMetadata] = {}
        for row in episodes_df.iter_rows(named=True):
            ref = EpisodeRef(
                episode_id=row["episode_id"],
                aligned_artifact_checksum=row["aligned_artifact_checksum"],
            )
            if ref not in exposed_ref_set:
                continue
            metadata_by_ref[ref] = EpisodeMetadata(
                episode_ref=ref,
                source_start_timestamp_us=row["source_start_timestamp_us"],
                source_end_timestamp_us=row["source_end_timestamp_us"],
                source_clock=row["source_clock"],
                alignment_semantics_version=row["alignment_semantics_version"],
                alignment_config_hash=row["alignment_config_hash"],
                target_frequency_hz=row["target_frequency_hz"],
                achieved_frequency_hz=row["achieved_frequency_hz"],
                dt_us=row["dt_us"],
                step_count=row["step_count"],
                duplicate_discarded_count=row["duplicate_discarded_count"],
                task=row["task"],
                outcome=row["outcome"],
            )

        return cls(
            artifact_store=artifact_store,
            learning_manifest=learning_manifest,
            episode_refs=episode_refs,
            metadata_by_ref=metadata_by_ref,
        )

    # ------------------------------------------------------------------
    # sync accessors -- answerable from already-loaded episode metadata
    # ------------------------------------------------------------------

    def episodes(self) -> list[EpisodeRef]:
        """Every exposed EpisodeRef, sorted by (episode_id,
        aligned_artifact_checksum) -- deterministic, independent of
        learning_episodes.parquet's physical row order (Request 2.7B §2)."""
        return list(self._episode_refs)

    def __len__(self) -> int:
        return len(self._episode_refs)

    def __contains__(self, ref: EpisodeRef) -> bool:
        return ref in self._metadata_by_ref

    def get_episode(self, ref: EpisodeRef) -> EpisodeMetadata:
        metadata = self._metadata_by_ref.get(ref)
        if metadata is None:
            raise EpisodeNotFoundError(f"{ref!r} is not exposed by this dataset")
        return metadata

    # ------------------------------------------------------------------
    # lazy table loading -- fetched at most once each, on first need
    # ------------------------------------------------------------------

    async def _get_steps_df(self) -> pl.DataFrame:
        if self._steps_df is None:
            self._steps_df = await _read_parquet_table(
                self._artifact_store,
                self._learning_manifest.table_uris["learning_steps"],
            )
        return self._steps_df

    async def _get_signals_df(self) -> pl.DataFrame:
        if self._signals_df is None:
            self._signals_df = await _read_parquet_table(
                self._artifact_store,
                self._learning_manifest.table_uris["learning_signals"],
            )
        return self._signals_df

    # ------------------------------------------------------------------
    # step reconstruction
    # ------------------------------------------------------------------

    async def get_step(self, ref: EpisodeRef, step_index: int) -> LearningStep:
        """Reconstruct exactly one LearningStep for ``ref`` (SceneOps V2
        Request 2.7B §4) -- filters learning_steps/learning_signals by
        (aligned_artifact_checksum, step_index) only; never touches rows
        belonging to any other EpisodeRef, and never loads/parses another
        episode's full step list to answer a single-step request."""
        metadata = self.get_episode(ref)
        if not (0 <= step_index < metadata.step_count):
            raise StepOutOfRangeError(
                f"step_index={step_index} is outside [0, {metadata.step_count}) "
                f"for {ref!r}"
            )
        return await self._reconstruct_step(ref, step_index)

    async def _reconstruct_step(self, ref: EpisodeRef, step_index: int) -> LearningStep:
        steps_df = await self._get_steps_df()
        signals_df = await self._get_signals_df()

        step_rows = steps_df.filter(
            (pl.col("aligned_artifact_checksum") == ref.aligned_artifact_checksum)
            & (pl.col("step_index") == step_index)
        )
        if step_rows.height == 0:
            raise LearningDataIntegrityError(
                f"no learning_steps.parquet row for {ref!r} step_index="
                f"{step_index}, despite learning_episodes.parquet declaring "
                "a larger step_count"
            )
        timestamp_us = step_rows.row(0, named=True)["timestamp_us"]

        signal_rows = list(
            signals_df.filter(
                (pl.col("aligned_artifact_checksum") == ref.aligned_artifact_checksum)
                & (pl.col("step_index") == step_index)
            ).iter_rows(named=True)
        )
        return step_from_rows(timestamp_us=timestamp_us, signal_rows=signal_rows)

    async def _reconstruct_all_steps(self, ref: EpisodeRef) -> list[LearningStep]:
        """Full, ordered step list for one EpisodeRef -- needed wherever
        the 2.7A contract requires whole-Episode shape stability
        (resolve_feature_schema) or a single contiguous Episode-bounded
        window (get_window/project_sequence). Cached per EpisodeRef so a
        second call (e.g. get_window after resolve_feature_schema) never
        re-fetches or re-parses. Filters learning_steps/learning_signals to
        this EpisodeRef's aligned_artifact_checksum only -- never touches
        another episode's rows."""
        cached = self._episode_steps_cache.get(ref)
        if cached is not None:
            return cached

        metadata = self.get_episode(ref)
        steps_df = await self._get_steps_df()
        signals_df = await self._get_signals_df()

        episode_steps = steps_df.filter(
            pl.col("aligned_artifact_checksum") == ref.aligned_artifact_checksum
        ).sort("step_index")
        episode_signals = signals_df.filter(
            pl.col("aligned_artifact_checksum") == ref.aligned_artifact_checksum
        )

        signals_by_step: dict[int, list[dict]] = {}
        for row in episode_signals.iter_rows(named=True):
            signals_by_step.setdefault(row["step_index"], []).append(row)

        steps = [
            step_from_rows(
                timestamp_us=row["timestamp_us"],
                signal_rows=signals_by_step.get(row["step_index"], []),
            )
            for row in episode_steps.iter_rows(named=True)
        ]

        if len(steps) != metadata.step_count:
            raise LearningDataIntegrityError(
                f"learning_steps.parquet has {len(steps)} rows for {ref!r}, "
                f"but learning_episodes.parquet declares step_count="
                f"{metadata.step_count}"
            )

        self._episode_steps_cache[ref] = steps
        return steps

    # ------------------------------------------------------------------
    # 2.7A projection integration
    # ------------------------------------------------------------------

    async def resolve_feature_schema(
        self, ref: EpisodeRef, projection: FeatureProjection
    ) -> FeatureSchema:
        """Delegates to sceneops_core.episodes.learning.resolve_feature_schema
        (Request 2.7A) over this EpisodeRef's full reconstructed step list.
        Cached per (EpisodeRef, FeatureProjection)."""
        self.get_episode(ref)  # EpisodeNotFoundError if ref isn't exposed
        cache_key = _schema_cache_key(ref, projection)
        cached = self._schema_cache.get(cache_key)
        if cached is not None:
            return cached

        steps = await self._reconstruct_all_steps(ref)
        schema = _pure_resolve_feature_schema(steps, projection)
        self._schema_cache[cache_key] = schema
        return schema

    async def project_step(
        self,
        ref: EpisodeRef,
        step_index: int,
        projection: FeatureProjection,
        *,
        missing_policy: MissingFeaturePolicy = MissingFeaturePolicy.ERROR,
    ) -> StepSample:
        """Delegates to sceneops_core.episodes.learning.project_step (Request
        2.7A) over one lazily-reconstructed LearningStep."""
        schema = await self.resolve_feature_schema(ref, projection)
        step = await self.get_step(ref, step_index)
        return _pure_project_step(
            step,
            step_index=step_index,
            episode_ref=ref,
            schema=schema,
            missing_policy=missing_policy,
        )

    async def get_window(
        self,
        ref: EpisodeRef,
        start_step: int,
        horizon: int,
        projection: FeatureProjection,
        *,
        missing_policy: MissingFeaturePolicy = MissingFeaturePolicy.ERROR,
    ) -> SequenceSample:
        """One contiguous sequence from one exact EpisodeRef (SceneOps V2
        Request 2.7B §5) -- builds a SequenceRef and delegates directly to
        sceneops_core.episodes.learning.project_sequence (Request 2.7A),
        which itself calls validate_sequence_bounds before projecting. No
        padding, no cross-Episode continuation."""
        schema = await self.resolve_feature_schema(ref, projection)
        steps = await self._reconstruct_all_steps(ref)
        sequence_ref = SequenceRef(
            episode_ref=ref, start_step=start_step, horizon=horizon
        )
        return _pure_project_sequence(
            steps,
            schema=schema,
            sequence_ref=sequence_ref,
            missing_policy=missing_policy,
        )


__all__ = ["SceneOpsDataset"]
