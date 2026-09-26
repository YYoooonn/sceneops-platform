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
import pyarrow as pa
import pyarrow.parquet as pq

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
from sceneops_core.episodes.learning_export import (
    LearningDataExportManifest,
    LearningDataShard,
    ShardEpisodeMember,
)
from sceneops_storage import ArtifactStore

from .errors import (
    CurationManifestMismatchError,
    DatasetManifestMismatchError,
    EpisodeNotFoundError,
    LearningDataIntegrityError,
    LearningTableMissingError,
    ShardIndexMismatchError,
    StepOutOfRangeError,
)
from .parquet_range_reader import read_episode_row_group
from .reconstruct import step_from_rows
from .schemas import EpisodeMetadata

_REQUIRED_TABLES = ("learning_episodes", "learning_steps", "learning_signals")
_SHARDED_TABLES = ("learning_steps", "learning_signals")

ShardLocation = tuple[LearningDataShard, ShardEpisodeMember]


def _strip_sha_prefix(checksum: str) -> str:
    return checksum.removeprefix("sha256:")


async def _read_parquet_table(artifact_store: ArtifactStore, uri: str) -> pl.DataFrame:
    data = await artifact_store.read_bytes(uri)
    return pl.read_parquet(io.BytesIO(data))


def _table_is_present(
    learning_manifest: LearningDataExportManifest, table_name: str
) -> bool:
    """Whether ``table_name`` has data under *either* layout (SceneOps V2
    Request 5.2/5.3) -- used only for ``open()``'s presence validation, not
    for fetching. ``learning_episodes`` is never sharded, so it is present
    iff ``table_uris`` has it; ``learning_steps``/``learning_signals`` are
    present via ``table_uris`` (legacy v1 single-file layout, read by
    ``_get_steps_df``/``_get_signals_df`` below) or via a non-empty
    ``shard_index.<table_name>`` (v2 sharded layout, read selectively --
    see ``_fetch_episode_arrow_table``) -- never both for the same table in
    one manifest."""
    if table_name in learning_manifest.table_uris:
        return True
    shard_index = learning_manifest.shard_index
    return bool(shard_index is not None and getattr(shard_index, table_name, None))


def _build_shard_lookup(
    learning_manifest: LearningDataExportManifest,
) -> dict[str, dict[EpisodeRef, ShardLocation]] | None:
    """(table_name -> (EpisodeRef -> (shard, member))) for a v2-sharded
    manifest (SceneOps V2 Request 5.3 §3) -- ``None`` for a v1 single-file
    manifest (nothing to look up; ``_get_steps_df``/``_get_signals_df``
    handle that layout directly). Built once, from manifest metadata only
    -- no Parquet file is opened, no object-store listing happens here.
    Raises ``ShardIndexMismatchError`` immediately if the same EpisodeRef
    appears more than once for one table (ambiguous shard membership) --
    a missing mapping (an exposed EpisodeRef absent from this lookup) is
    instead detected lazily, at first access, since it is only observable
    once a specific EpisodeRef is actually requested."""
    shard_index = learning_manifest.shard_index
    if shard_index is None:
        return None

    lookup: dict[str, dict[EpisodeRef, ShardLocation]] = {}
    for table_name in _SHARDED_TABLES:
        table_lookup: dict[EpisodeRef, ShardLocation] = {}
        for shard in getattr(shard_index, table_name):
            for member in shard.episodes:
                if member.episode_ref in table_lookup:
                    raise ShardIndexMismatchError(
                        f"{member.episode_ref!r} appears in more than one "
                        f"shard for {table_name!r} (export_id="
                        f"{learning_manifest.export_id!r}) -- shard "
                        "membership must be unambiguous"
                    )
                table_lookup[member.episode_ref] = (shard, member)
        lookup[table_name] = table_lookup
    return lookup


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
    needed for episodes()/len()/get_episode()/boundary checks. Every method
    that may trigger a step/signal fetch is async; everything answerable
    from already-loaded episode metadata is synchronous.

    Two physical layouts, two access strategies (dispatched once, at
    open(), via ``LearningDataExportManifest.shard_index``'s presence --
    never re-checked per call):

    - v1 (legacy single-file, ``table_uris``): learning_steps.parquet/
      learning_signals.parquet are each fetched at most once, lazily, in
      full, on first access that needs step-level data for *some*
      episode -- then every subsequent episode is answered by an in-memory
      filter over that cached whole table (SceneOps V2 Request 2.7B/5.1).
      Still used by the frozen correctness/golden fixtures.
    - v2 (sharded, ``shard_index``, SceneOps V2 Request 5.2): the whole
      table is **never** fetched. Each episode's own shard + row group is
      looked up in an in-memory index built once at open() (no Parquet
      scanning, no object-store listing) and fetched selectively via
      targeted ``ArtifactStore.read_range`` calls -- see
      ``parquet_range_reader.py`` (SceneOps V2 Request 5.3). A shard's
      Parquet footer/metadata is parsed at most once per shard per
      instance and cached; row-group bytes are fetched fresh per episode
      (not cached beyond the existing per-episode ``_episode_steps_cache``
      Python-object cache, unchanged from Request 2.7B). Production
      exports are always v2 as of Request 5.2.

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
        shard_lookup: dict[str, dict[EpisodeRef, ShardLocation]] | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._learning_manifest = learning_manifest
        self._episode_refs = episode_refs
        self._metadata_by_ref = metadata_by_ref
        # None (v1, legacy single-file) vs a dict (v2, sharded) -- SceneOps
        # V2 Request 5.3. `_is_sharded` dispatches every step/window read
        # below; v1 never touches shard_lookup/_shard_metadata_cache, v2
        # never touches _steps_df/_signals_df.
        self._shard_lookup = shard_lookup
        self._is_sharded = shard_lookup is not None

        self._steps_df: pl.DataFrame | None = None
        self._signals_df: pl.DataFrame | None = None
        self._shard_metadata_cache: dict[tuple[str, int], pq.FileMetaData] = {}
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
            if not _table_is_present(learning_manifest, name)
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
            shard_lookup=_build_shard_lookup(learning_manifest),
        )

    # ------------------------------------------------------------------
    # sync accessors -- answerable from already-loaded episode metadata
    # ------------------------------------------------------------------

    @property
    def learning_manifest(self) -> LearningDataExportManifest:
        """The pinned LearningDataExportManifest this dataset was opened
        over (SceneOps V2 Request 2.7B) -- exposed read-only so a downstream
        layer (e.g. Request 3.1's external dataset adapters) can report
        source-snapshot identity (dataset_id/dataset_version/export_id)
        without re-deriving or re-fetching it."""
        return self._learning_manifest

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
        """v1 (legacy single-file) only -- never called for a v2-sharded
        manifest (SceneOps V2 Request 5.3 §6); see
        ``_fetch_episode_arrow_table`` for the v2 selective path."""
        if self._steps_df is None:
            self._steps_df = await _read_parquet_table(
                self._artifact_store,
                self._learning_manifest.table_uris["learning_steps"],
            )
        return self._steps_df

    async def _get_signals_df(self) -> pl.DataFrame:
        """v1 only -- see ``_get_steps_df``."""
        if self._signals_df is None:
            self._signals_df = await _read_parquet_table(
                self._artifact_store,
                self._learning_manifest.table_uris["learning_signals"],
            )
        return self._signals_df

    # ------------------------------------------------------------------
    # v2 (sharded) selective row-group loading -- SceneOps V2 Request 5.3
    # ------------------------------------------------------------------

    async def _fetch_episode_arrow_table(
        self, table_name: str, ref: EpisodeRef
    ) -> pa.Table:
        """Fetch exactly one EpisodeRef's row group from exactly the one
        shard it lives in -- never any other shard, never any other
        episode's rows, and never the whole table (SceneOps V2 Request
        5.3 §4). The shard's Parquet footer/metadata is parsed at most
        once per shard per ``SceneOpsDataset`` instance (cached in
        ``_shard_metadata_cache``, keyed by (table_name, shard_index)) --
        a bounded, per-shard cache, not another whole-table cache."""
        table_lookup = (
            self._shard_lookup.get(table_name) if self._shard_lookup else None
        )
        location = table_lookup.get(ref) if table_lookup else None
        if location is None:
            raise ShardIndexMismatchError(
                f"{ref!r} has no shard mapping for {table_name!r} "
                f"(export_id={self._learning_manifest.export_id!r}) -- "
                "learning_episodes.parquet exposes this EpisodeRef but "
                "LearningDataShardIndex does not account for it"
            )
        shard, member = location

        cache_key = (table_name, shard.shard_index)
        cached_metadata = self._shard_metadata_cache.get(cache_key)
        table, metadata = await read_episode_row_group(
            self._artifact_store,
            shard.uri,
            shard.size_bytes,
            member.row_group_index,
            cached_metadata=cached_metadata,
        )
        if cache_key not in self._shard_metadata_cache:
            self._shard_metadata_cache[cache_key] = metadata
        return table

    async def _reconstruct_all_steps_sharded(
        self, ref: EpisodeRef, metadata: EpisodeMetadata
    ) -> list[LearningStep]:
        """v2 counterpart of the v1 whole-table-filter logic in
        ``_reconstruct_all_steps`` below -- two targeted row-group fetches
        (one per table), then the exact same pure ``step_from_rows``
        reconstruction. Fetches this EpisodeRef's *entire* row group in
        both tables even when only one step or a sub-range is ultimately
        wanted: one row group per episode (Request 5.2) is the finest
        granularity this physical layout supports, so a fixed window's
        step-range narrowing happens only in-memory, after this fetch --
        documented honestly per this request's own instruction, not
        claimed as finer pruning than it is."""
        steps_table = await self._fetch_episode_arrow_table("learning_steps", ref)
        signals_table = await self._fetch_episode_arrow_table("learning_signals", ref)

        signals_by_step: dict[int, list[dict]] = {}
        for row in signals_table.to_pylist():
            signals_by_step.setdefault(row["step_index"], []).append(row)

        steps_rows = sorted(steps_table.to_pylist(), key=lambda row: row["step_index"])
        steps = [
            step_from_rows(
                timestamp_us=row["timestamp_us"],
                signal_rows=signals_by_step.get(row["step_index"], []),
            )
            for row in steps_rows
        ]

        if len(steps) != metadata.step_count:
            raise LearningDataIntegrityError(
                f"{ref!r}'s learning_steps shard row group has {len(steps)} "
                f"rows, but learning_episodes.parquet declares "
                f"step_count={metadata.step_count}"
            )
        return steps

    # ------------------------------------------------------------------
    # step reconstruction
    # ------------------------------------------------------------------

    async def get_step(self, ref: EpisodeRef, step_index: int) -> LearningStep:
        """Reconstruct exactly one LearningStep for ``ref`` (SceneOps V2
        Request 2.7B §4).

        v1 (legacy single-file): filters learning_steps/learning_signals by
        (aligned_artifact_checksum, step_index) only; never touches rows
        belonging to any other EpisodeRef, and never loads/parses another
        episode's full step list to answer a single-step request.

        v2 (sharded, Request 5.3): one row group per episode is the finest
        granularity the physical layout supports, so there is no cheaper
        single-step fetch than the whole-episode one
        ``_reconstruct_all_steps`` already performs (and caches) -- this
        reuses that cache rather than re-fetching."""
        metadata = self.get_episode(ref)
        if not (0 <= step_index < metadata.step_count):
            raise StepOutOfRangeError(
                f"step_index={step_index} is outside [0, {metadata.step_count}) "
                f"for {ref!r}"
            )
        if self._is_sharded:
            steps = await self._reconstruct_all_steps(ref)
            return steps[step_index]
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
        re-fetches or re-parses.

        v1 (legacy single-file): filters the whole cached
        learning_steps/learning_signals tables to this EpisodeRef's
        aligned_artifact_checksum only -- never touches another episode's
        rows, but does require the whole table already loaded once
        (``_get_steps_df``/``_get_signals_df``).

        v2 (sharded, SceneOps V2 Request 5.3 §6): never touches
        ``_steps_df``/``_signals_df`` at all -- delegates to
        ``_reconstruct_all_steps_sharded``, which fetches only this
        EpisodeRef's own shard row groups."""
        cached = self._episode_steps_cache.get(ref)
        if cached is not None:
            return cached

        metadata = self.get_episode(ref)

        if self._is_sharded:
            steps = await self._reconstruct_all_steps_sharded(ref, metadata)
        else:
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
