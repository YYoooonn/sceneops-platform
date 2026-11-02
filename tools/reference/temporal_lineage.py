"""Cross-artifact temporal ownership: a derived representation's clock instance is
the one its pinned canonical manifest issues (ADR-021).

Readers prove a persisted instance is well formed. Only the manifest of the revision
a representation was derived from can prove the instance is the unit's own, so that
is what this verifier does:

```text
AlignedEpisode Artifact v2 -> source_revision -> EpisodeManifest v4
learning_episodes row -> AlignedArtifactRevision -> AlignedEpisode -> EpisodeManifest
scenes / observations / keyframes row -> DatasetVersion member -> SceneManifest v5
```

Unlike the raw-source verifiers beside it (tools/reference/verification/, which import
no SceneOps package and so can catch the platform's own manifests being wrong), this
one deliberately runs on the platform's contracts: ``manifest.clock_instance(clock)``
is the authority it holds the derived artifacts to, ``restore_clock_instance`` the one
reconstruction, and it reuses the readers' structural checks instead of restating
them. Raw source -> manifest is theirs; manifest -> derived is this. Elements of a
scope are never read and no expected scope is built from a RobotRun id, a content id
or a URI: instances are compared as whole values.

Findings carry stable codes, which negative controls name
(tools/reference/temporal_lineage_controls.py). Nothing here writes: ``LineageSource``
is read-only, and one run resolves each pinned revision once.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verification.findings import Findings  # noqa: E402

from sceneops_analytics import (  # noqa: E402
    SceneAnalyticsIntegrityError,
    SceneOpsDataset,
    SceneRevisionContent,
    SceneRevisionPairingError,
    ShardMetadataError,
    UnsupportedSceneAnalyticsSchemaError,
    check_learning_export_tables,
    read_scene_table_bytes,
)
from sceneops_analytics.learning_dataset.errors import SceneOpsDatasetError  # noqa: E402
from sceneops_core.artifacts.schemas.enums import ArtifactKind  # noqa: E402
from sceneops_core.common.canonical_json import canonical_json_bytes  # noqa: E402
from sceneops_core.common.derived_ids import (  # noqa: E402
    aligned_episode_artifact_id,
    derived_artifact_id,
)
from sceneops_core.episodes.alignment import (  # noqa: E402
    AlignedEpisode,
    AlignedEpisodeArtifact,
    AlignedEpisodeValidator,
    AlignedSignal,
    ChannelNamespace,
    LearningStep,
    TemporalSourceContext,
    UnsupportedAlignedArtifactSchemaError,
    align_episode,
    alignment_config_hash,
    load_aligned_episode_artifact,
)
from sceneops_core.episodes.learning import EpisodeRef  # noqa: E402
from sceneops_core.episodes.learning_export import (  # noqa: E402
    LearningDataExportManifest,
    UnsupportedLearningDataSchemaError,
)
from sceneops_core.episodes.schemas import (  # noqa: E402
    EpisodeManifest,
    episode_id_for,
    load_canonical_episode_manifest,
)
from sceneops_core.provenance import manifest_artifact_id_for  # noqa: E402
from sceneops_core.scenes.schemas import (  # noqa: E402
    SceneManifest,
    SceneRevisionRecord,
    load_canonical_scene_manifest,
    project_scene_revision,
)
from sceneops_core.temporal import ClockInstance, restore_clock_instance  # noqa: E402

REPORT_SCHEMA = "sceneops.temporal_lineage_report/1"
SCENE_TABLES = ("scenes", "observations", "keyframes")
SHARDED_TABLES = ("learning_steps", "learning_signals")

# What the alignment engine does not derive from the manifest and the recipe: the
# clock triple has its own check, so a wrong scope alone is reported as what it is.
_CLOCK_FIELDS = {"source_clock", "clock_origin", "clock_scope"}

_validator = AlignedEpisodeValidator()
_M = TypeVar("_M", EpisodeManifest, SceneManifest)


class ReadStore(Protocol):
    """All a verification reads from the ArtifactStore. No writing method exists."""

    async def read_bytes(self, uri: str) -> bytes: ...

    async def read_range(self, uri: str, offset: int, length: int) -> bytes: ...


class LineageSource(Protocol):
    """The platform state a verification reads: ArtifactRecords and DatasetVersion
    members (snake_case fields, ``None`` when there is none), and stored bytes."""

    store: ReadStore

    def artifact_record(self, artifact_id: str) -> dict[str, Any] | None: ...

    def members(
        self, domain: str, dataset_id: str, dataset_version: str
    ) -> list[dict[str, Any]] | None: ...


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _pinned(checksum: str) -> str:
    return "sha256:" + checksum.removeprefix("sha256:")


def instance_json(instance: ClockInstance) -> dict[str, Any]:
    return {
        "clock": instance.clock,
        "origin": instance.origin.value,
        "scope": list(instance.scope),
    }


@dataclass
class VerifiedAligned:
    artifact: AlignedEpisodeArtifact
    manifest: EpisodeManifest | None
    # The instance the pinned manifest issues, when it declares the clock.
    issued: ClockInstance | None


@dataclass
class Verification:
    """Instances that passed through the verifier, whatever else it found, for a
    caller comparing units with each other through ``require_comparable``."""

    aligned: dict[str, ClockInstance] = field(default_factory=dict)
    learning: dict[EpisodeRef, ClockInstance] = field(default_factory=dict)
    scene_windows: dict[str, ClockInstance] = field(default_factory=dict)
    scene_clocks: dict[str, dict[str, ClockInstance]] = field(default_factory=dict)


class TemporalLineageVerifier:
    """One verification run. Revisions are immutable, so each is resolved once per
    run whichever number of rows state it; ``stats`` reports how often that was."""

    def __init__(self, source: LineageSource) -> None:
        self.source = source
        self.stats: Counter[str] = Counter()
        self.verified = Verification()
        self.read_errors: list[str] = []
        self._manifests: dict[tuple[str, str], Any] = {}
        self._aligned: dict[tuple[str, str], VerifiedAligned | None] = {}

    # ── pinned revisions ─────────────────────────────────────────────────────

    async def _read(self, uri: str) -> bytes | None:
        try:
            return await self.source.store.read_bytes(uri)
        except Exception as exc:  # noqa: BLE001 -- a read failure is a finding here
            self.stats["read_failures"] += 1
            self.read_errors.append(f"{uri}: {type(exc).__name__}: {exc}")
            return None

    async def _resolve_revision(
        self,
        f: Findings,
        *,
        prefix: str,
        kind: ArtifactKind,
        artifact_id: str,
        checksum: str,
        loader: Callable[[bytes], _M],
    ) -> tuple[_M, dict[str, Any]] | None:
        """The canonical manifest of exactly the pinned revision: its record, its
        bytes, those bytes' checksum and a strict canonical parse. Latest is never
        looked up."""
        pin = (artifact_id, _pinned(checksum))
        if pin in self._manifests:
            self.stats["manifest_cache_hits"] += 1
            return self._manifests[pin]
        self.stats["manifest_resolutions"] += 1
        self._manifests[pin] = None
        record = self.source.artifact_record(artifact_id)
        if not f.check(
            record is not None and record.get("kind") == kind.value,
            f"{prefix}_record",
            f"{artifact_id} is no registered {kind.value} record: {record!r}",
        ):
            return None
        assert record is not None
        data = await self._read(record["uri"])
        if not f.check(
            data is not None, f"{prefix}_record", f"{record['uri']} cannot be read"
        ):
            return None
        assert data is not None
        actual = sha256(data)
        consistent = f.equal(
            actual,
            _pinned(checksum),
            f"{prefix}_checksum",
            f"bytes of {artifact_id} vs the pin",
        )
        consistent &= f.equal(
            actual,
            _pinned(record.get("checksum") or ""),
            f"{prefix}_checksum",
            f"bytes of {artifact_id} vs its record",
        )
        if not consistent:
            return None
        try:
            manifest = loader(data)
        except ValueError as exc:
            f.fail(f"{prefix}_load", f"{artifact_id} is no canonical manifest: {exc}")
            return None
        self._manifests[pin] = (manifest, record)
        return self._manifests[pin]

    # ── AlignedEpisode -> EpisodeManifest ────────────────────────────────────

    async def verify_aligned(
        self,
        f: Findings,
        *,
        aligned_artifact_id: str,
        checksum: str | None = None,
        episode_id: str | None = None,
    ) -> VerifiedAligned | None:
        """The artifact pinned by id and checksum, against the Episode revision it
        names. ``episode_id``: the Episode the caller expects it to belong to."""
        record = self.source.artifact_record(aligned_artifact_id)
        pin = checksum or (record or {}).get("checksum") or ""
        key = (aligned_artifact_id, _pinned(pin))
        g = f.scoped(artifact="aligned", aligned_artifact_id=aligned_artifact_id)
        if key in self._aligned:
            self.stats["aligned_cache_hits"] += 1
            result = self._aligned[key]
        else:
            result = await self._verify_aligned(g, record, aligned_artifact_id, pin)
            self._aligned[key] = result
        if result is not None and episode_id is not None:
            g.equal(
                result.artifact.aligned_episode.episode_id,
                episode_id,
                "episode_identity",
                "the aligned Episode vs the Episode expected of it",
            )
        return result

    async def _verify_aligned(
        self,
        g: Findings,
        record: dict[str, Any] | None,
        aligned_artifact_id: str,
        pin: str,
    ) -> VerifiedAligned | None:
        self.stats["aligned_artifacts"] += 1
        if not g.check(
            record is not None
            and record.get("kind") == ArtifactKind.ALIGNED_EPISODE_MANIFEST.value,
            "aligned_record",
            f"{aligned_artifact_id} is no registered aligned artifact: {record!r}",
        ):
            return None
        assert record is not None
        data = await self._read(record["uri"])
        if not g.check(
            data is not None, "aligned_record", f"{record['uri']} cannot be read"
        ):
            return None
        assert data is not None
        actual = sha256(data)
        intact = g.equal(
            actual, _pinned(pin), "aligned_checksum", "aligned bytes vs the pin"
        )
        intact &= g.equal(
            actual,
            _pinned(record.get("checksum") or ""),
            "aligned_checksum",
            "aligned bytes vs the record",
        )
        if not intact:
            return None
        try:
            artifact = load_aligned_episode_artifact(data)
        except UnsupportedAlignedArtifactSchemaError as exc:
            g.fail("aligned_schema", str(exc))
            return None
        except ValueError as exc:
            g.fail("aligned_parse", f"not a valid aligned artifact: {exc}")
            return None

        aligned = artifact.aligned_episode
        revision = artifact.source_revision
        report = _validator.validate(artifact)
        g.check(
            report.valid,
            "aligned_structure",
            f"{len(report.issues)} structural issues, first: {report.issues[:1]!r}",
        )

        g.equal(
            aligned.episode_id,
            revision.episode_id,
            "episode_identity",
            "the aligned Episode vs the Episode of its source revision",
        )
        g.equal(
            record.get("owner_id"),
            aligned.episode_id,
            "aligned_record",
            "the record's owner vs the aligned Episode",
        )
        g.equal(
            aligned_artifact_id,
            aligned_episode_artifact_id(
                episode_id=aligned.episode_id, checksum=_pinned(pin)
            ),
            "aligned_record",
            "the artifact id vs the id of (Episode, checksum)",
        )

        # What the alignment recorded when it ran. Another revision of the same unit
        # with equal content would otherwise be a claim nothing else can refute.
        lineage = record.get("metadata") or {}
        for field_name, stated in (
            ("source_artifact_id", revision.source_artifact_id),
            ("source_manifest_sha256", revision.source_manifest_sha256),
            ("episode_id", aligned.episode_id),
            ("source_clock", aligned.source_clock),
        ):
            g.equal(
                stated,
                lineage.get(field_name),
                "aligned_record",
                f"the artifact's {field_name} vs the one its record was registered with",
            )

        resolved = await self._resolve_revision(
            g,
            prefix="source_revision",
            kind=ArtifactKind.EPISODE_MANIFEST,
            artifact_id=revision.source_artifact_id,
            checksum=revision.source_manifest_sha256,
            loader=load_canonical_episode_manifest,
        )
        if resolved is None:
            return VerifiedAligned(artifact, None, None)
        manifest, source_record = resolved
        g.equal(
            revision.episode_manifest_uri,
            source_record["uri"],
            "source_revision_record",
            "the revision's stated URI vs its record",
        )
        unit_id = episode_id_for(manifest.lineage.source)
        g.equal(
            aligned.episode_id,
            unit_id,
            "episode_identity",
            "the aligned Episode vs the unit the source manifest binds",
        )
        g.equal(
            revision.source_artifact_id,
            manifest_artifact_id_for(
                unit_id=unit_id,
                manifest_checksum=_pinned(revision.source_manifest_sha256),
            ),
            "source_revision_identity",
            "the source artifact id vs the id of (unit, checksum)",
        )

        issued = self._issued(
            g, manifest, aligned.source_clock, "aligned_clock_declared"
        )
        if issued is not None:
            self.stats["instance_comparisons"] += 1
            g.equal(
                aligned.clock_instance,
                issued,
                "aligned_clock_instance",
                f"the stated instance vs the one {revision.source_artifact_id} issues",
            )
            self.verified.aligned[aligned.episode_id] = issued
            self._check_aligned_content(g, manifest, aligned)
        return VerifiedAligned(artifact, manifest, issued)

    def verified_aligned(self) -> list[tuple[str, VerifiedAligned]]:
        return [(pin[0], v) for pin, v in self._aligned.items() if v is not None]

    @staticmethod
    def _issued(
        f: Findings, manifest: EpisodeManifest | SceneManifest, clock: str, code: str
    ) -> ClockInstance | None:
        declared = {d.clock for d in manifest.clock_domains}
        if not f.check(
            clock in declared,
            code,
            f"clock {clock!r} is not declared: {sorted(declared)}",
        ):
            return None
        return manifest.clock_instance(clock)

    def _check_aligned_content(
        self, g: Findings, manifest: EpisodeManifest, aligned: AlignedEpisode
    ) -> None:
        """Steps, references and timeline are what the recipe makes of this manifest:
        the engine is pure, so the same inputs give the same result."""
        self.stats["alignments_recomputed"] += 1
        try:
            recomputed = align_episode(
                manifest,
                aligned.alignment_config,
                TemporalSourceContext(source_clock=aligned.source_clock),
                episode_id=aligned.episode_id,
            )
        except ValueError as exc:
            g.fail(
                "aligned_content", f"the recipe does not apply to the manifest: {exc}"
            )
            return
        g.check(
            recomputed.model_dump(mode="json", exclude=_CLOCK_FIELDS)
            == aligned.model_dump(mode="json", exclude=_CLOCK_FIELDS),
            "aligned_content",
            "the stored steps are not what its recipe derives from the pinned manifest",
        )

    # ── Learning Data -> AlignedEpisode -> EpisodeManifest ───────────────────

    async def verify_learning_export(
        self, f: Findings, *, manifest_artifact_id: str, checksum: str | None = None
    ) -> None:
        g = f.scoped(
            artifact="learning_export", manifest_artifact_id=manifest_artifact_id
        )
        self.stats["learning_exports"] += 1
        record = self.source.artifact_record(manifest_artifact_id)
        if not g.check(
            record is not None
            and record.get("kind") == ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST.value,
            "export_record",
            f"{manifest_artifact_id} is no learning export manifest record: {record!r}",
        ):
            return
        assert record is not None
        pin = checksum or record.get("checksum") or ""
        data = await self._read(record["uri"])
        if not g.check(
            data is not None, "export_record", f"{record['uri']} cannot be read"
        ):
            return
        assert data is not None
        if not g.equal(
            sha256(data), _pinned(pin), "export_checksum", "manifest bytes vs the pin"
        ):
            return
        try:
            manifest = LearningDataExportManifest.model_validate_json(data)
        except UnsupportedLearningDataSchemaError as exc:
            g.fail("export_schema", str(exc))
            return
        except ValueError as exc:
            g.fail("export_parse", f"not a learning export manifest: {exc}")
            return
        await self._check_learning_objects(g, manifest)

        store = self.source.store
        try:
            await check_learning_export_tables(store, manifest)
        except (UnsupportedLearningDataSchemaError, ShardMetadataError) as exc:
            g.fail("learning_table_schema", str(exc))
            return
        try:
            dataset = await SceneOpsDataset.open(
                learning_manifest=manifest,
                learning_manifest_checksum=_pinned(pin),
                artifact_store=store,  # type: ignore[arg-type]
            )
        except SceneOpsDatasetError as exc:
            g.fail("learning_open", f"{type(exc).__name__}: {exc}")
            return

        members = self.source.members(
            "episode", manifest.dataset_id, manifest.dataset_version
        )
        g.check(
            members is not None,
            "learning_membership",
            f"no DatasetVersion {manifest.dataset_id}/{manifest.dataset_version}",
        )
        pins = {m["episode_id"]: m for m in members or []}
        rows = await self._learning_rows(g, manifest)

        for item in manifest.inputs:
            e = g.scoped(episode_id=item.episode_id)
            self.stats["learning_episodes"] += 1
            verified = await self.verify_aligned(
                e,
                aligned_artifact_id=item.aligned_artifact_id,
                checksum=item.aligned_artifact_checksum,
                episode_id=item.episode_id,
            )
            if verified is None:
                continue
            ref = EpisodeRef(
                episode_id=item.episode_id,
                aligned_artifact_checksum=item.aligned_artifact_checksum,
            )
            member = pins.get(item.episode_id)
            if members is not None and e.check(
                member is not None,
                "learning_membership",
                f"{item.episode_id} is no member of the export's DatasetVersion",
            ):
                e.equal(
                    member["manifest_artifact_id"],  # type: ignore[index]
                    verified.artifact.source_revision.source_artifact_id,
                    "learning_membership",
                    "the revision the DatasetVersion pins vs the one aligned",
                )
            self._check_learning_row(
                e, rows.get(ref), dataset.get_episode(ref), verified
            )
            await self._check_learning_steps(
                e, dataset, ref, verified.artifact.aligned_episode
            )

    async def _check_learning_objects(
        self, g: Findings, manifest: LearningDataExportManifest
    ) -> None:
        """Every byte of the export, base shards of an incremental export included:
        reused shards are not exempt from anything."""
        for table_name, uri in manifest.table_uris.items():
            data = await self._read(uri)
            if g.check(
                data is not None, "learning_table_checksum", f"{uri} cannot be read"
            ):
                assert data is not None
                g.equal(
                    sha256(data),
                    _pinned(manifest.table_checksums.get(table_name, "")),
                    "learning_table_checksum",
                    f"{table_name} bytes vs the manifest",
                )
        if manifest.shard_index is None:
            return
        own_key = manifest.export_id[:16]
        for table_name in SHARDED_TABLES:
            shards = getattr(manifest.shard_index, table_name)
            for shard in shards:
                self.stats["shards_verified"] += 1
                if own_key not in shard.uri:
                    self.stats["reused_shards_verified"] += 1
                data = await self._read(shard.uri)
                if not g.check(
                    data is not None,
                    "learning_shard_checksum",
                    f"{shard.uri} cannot be read",
                ):
                    continue
                assert data is not None
                if g.equal(
                    sha256(data),
                    _pinned(shard.checksum),
                    "learning_shard_checksum",
                    f"{table_name} shard {shard.shard_index} bytes vs the manifest",
                ):
                    try:
                        rows = pq.read_metadata(io.BytesIO(data)).num_rows
                    except pa.ArrowInvalid as exc:
                        g.fail("learning_table_schema", f"{shard.uri}: {exc}")
                        continue
                    g.equal(
                        rows,
                        shard.row_count,
                        "learning_row_counts",
                        f"{table_name} shard {shard.shard_index} rows",
                    )
            stated = manifest.row_counts.get(table_name)
            if stated is not None:
                g.equal(
                    sum(s.row_count for s in shards),
                    stated,
                    "learning_row_counts",
                    f"{table_name} total rows",
                )

    async def _learning_rows(
        self, g: Findings, manifest: LearningDataExportManifest
    ) -> dict[EpisodeRef, dict[str, Any]]:
        data = await self.source.store.read_bytes(
            manifest.table_uris["learning_episodes"]
        )
        rows = {}
        for row in pl.read_parquet(io.BytesIO(data)).iter_rows(named=True):
            ref = EpisodeRef(
                episode_id=row["episode_id"],
                aligned_artifact_checksum=row["aligned_artifact_checksum"],
            )
            rows[ref] = row
            # A reused row keeps the export_id it was written under, by contract.
            g.equal(
                (row["dataset_id"], row["dataset_version"]),
                (manifest.dataset_id, manifest.dataset_version),
                "learning_row_identity",
                f"the DatasetVersion a row of {row['episode_id']} states",
            )
        return rows

    def _check_learning_row(
        self,
        e: Findings,
        row: dict[str, Any] | None,
        metadata: Any,
        verified: VerifiedAligned,
    ) -> None:
        aligned = verified.artifact.aligned_episode
        stated = metadata.clock_instance
        self.verified.learning[metadata.episode_ref] = stated
        self.stats["instance_comparisons"] += 1
        e.equal(
            stated,
            aligned.clock_instance,
            "learning_clock_instance",
            "learning_episodes vs its AlignedEpisode",
        )
        if verified.issued is not None:
            self.stats["instance_comparisons"] += 1
            e.equal(
                stated,
                verified.issued,
                "learning_clock_instance",
                "learning_episodes vs the instance its pinned EpisodeManifest issues",
            )
        if row is None:  # SceneOpsDataset.open proved each input has one
            return
        e.equal(
            {
                "source_start_timestamp_us": row["source_start_timestamp_us"],
                "source_end_timestamp_us": row["source_end_timestamp_us"],
                "alignment_semantics_version": row["alignment_semantics_version"],
                "alignment_config_hash": row["alignment_config_hash"],
                "target_frequency_hz": row["target_frequency_hz"],
                "achieved_frequency_hz": row["achieved_frequency_hz"],
                "dt_us": row["dt_us"],
                "step_count": row["step_count"],
                "duplicate_discarded_count": row["duplicate_discarded_count"],
                "task": row["task"],
                "outcome": row["outcome"],
            },
            {
                "source_start_timestamp_us": aligned.source_start_timestamp_us,
                "source_end_timestamp_us": aligned.source_end_timestamp_us,
                "alignment_semantics_version": aligned.alignment_semantics_version,
                "alignment_config_hash": alignment_config_hash(
                    aligned.alignment_config
                ),
                "target_frequency_hz": aligned.target_frequency_hz,
                "achieved_frequency_hz": aligned.achieved_frequency_hz,
                "dt_us": aligned.dt_us,
                "step_count": aligned.step_count,
                "duplicate_discarded_count": aligned.duplicate_discarded_count,
                "task": aligned.task,
                "outcome": str(aligned.outcome),
            },
            "learning_metadata",
            "learning_episodes vs its AlignedEpisode",
        )

    async def _check_learning_steps(
        self,
        e: Findings,
        dataset: SceneOpsDataset,
        ref: EpisodeRef,
        aligned: AlignedEpisode,
    ) -> None:
        """Through the reader, whose row-ownership checks hold, so a row group of
        another Episode is reported as that; then each step against the artifact."""
        count = dataset.get_episode(ref).step_count
        try:
            steps = [await dataset.get_step(ref, i) for i in range(count)]
        except (
            SceneOpsDatasetError,
            UnsupportedLearningDataSchemaError,
            ShardMetadataError,
        ) as exc:
            e.fail("learning_rows_owner", f"{type(exc).__name__}: {exc}")
            return
        self.stats["learning_steps"] += len(steps)
        e.equal(
            [_step_facts(s) for s in steps],
            [_step_facts(s) for s in aligned.steps],
            "learning_steps",
            "learning_steps / learning_signals vs the AlignedEpisode",
        )

    # ── Scene Analytics -> SceneManifest ─────────────────────────────────────

    async def verify_scene_analytics(
        self,
        f: Findings,
        *,
        dataset_id: str,
        dataset_version: str,
        table_uris: Mapping[str, str],
    ) -> None:
        g = f.scoped(
            artifact="scene_analytics", dataset=f"{dataset_id}/{dataset_version}"
        )
        self.stats["scene_snapshots"] += 1
        members = self.source.members("scene", dataset_id, dataset_version)
        if not g.check(
            members is not None,
            "scene_membership",
            f"no DatasetVersion {dataset_id}/{dataset_version}",
        ):
            return
        scenes: dict[str, SceneRevisionContent] = {}
        for member in members or []:
            content = await self._scene_revision(g, member)
            if content is not None:
                scenes[content.record.scene_id] = content

        for table_name in SCENE_TABLES:
            uri = table_uris.get(table_name)
            if uri is None:
                continue
            frame = await self._scene_table(
                g, table_name, uri, dataset_id, dataset_version
            )
            if frame is None:
                continue
            self.stats[f"{table_name}_rows"] += frame.height
            keys = frame.select("dataset_id", "dataset_version").unique().rows()
            g.check(
                all(key == (dataset_id, dataset_version) for key in keys),
                "scene_membership",
                f"{table_name} states DatasetVersions {keys}",
            )
            stated = set(frame["scene_id"].to_list())
            g.check(
                stated <= {m["scene_id"] for m in members or []},
                "scene_membership",
                f"{table_name} holds Scenes outside the DatasetVersion: "
                f"{sorted(stated - {m['scene_id'] for m in members or []})}",
            )
            if table_name == "scenes":
                g.equal(
                    sorted(frame["scene_id"].to_list()),
                    sorted(m["scene_id"] for m in members or []),
                    "scene_membership",
                    "one scenes row per member",
                )
                self._check_scenes_table(g, frame, scenes)
            elif table_name == "observations":
                self._check_observations_table(g, frame, scenes)
            else:
                self._check_keyframes_table(g, frame, scenes)

    async def _scene_revision(
        self, g: Findings, member: dict[str, Any]
    ) -> SceneRevisionContent | None:
        s = g.scoped(scene_id=member["scene_id"])
        resolved = await self._resolve_revision(
            s,
            prefix="scene_revision",
            kind=ArtifactKind.SCENE_MANIFEST,
            artifact_id=member["manifest_artifact_id"],
            checksum=member["manifest_checksum"],
            loader=load_canonical_scene_manifest,
        )
        if resolved is None:
            return None
        manifest, _ = resolved
        fields = set(SceneRevisionRecord.model_fields)
        try:
            content = SceneRevisionContent(
                record=SceneRevisionRecord.model_validate(
                    {k: v for k, v in member.items() if k in fields}
                ),
                manifest=manifest,
            )
        except (SceneRevisionPairingError, ValueError) as exc:
            s.fail("scene_revision_pairing", str(exc))
            return None
        self.verified.scene_clocks[member["scene_id"]] = content.clock_instances()
        return content

    async def _scene_table(
        self,
        g: Findings,
        table_name: str,
        uri: str,
        dataset_id: str,
        dataset_version: str,
    ) -> pl.DataFrame | None:
        data = await self._read(uri)
        if not g.check(data is not None, "scene_table_record", f"{uri} cannot be read"):
            return None
        assert data is not None
        checksum = sha256(data)
        artifact_id = derived_artifact_id(
            prefix="analyticstable",
            logical_id=f"{dataset_id}:{dataset_version}:{table_name}",
            checksum=checksum,
        )
        record = self.source.artifact_record(artifact_id)
        if g.check(
            record is not None
            and record.get("kind") == ArtifactKind.ANALYTICS_TABLE.value,
            "scene_table_record",
            f"{table_name}: these bytes are not a registered table of "
            f"{dataset_id}/{dataset_version} ({artifact_id})",
        ):
            assert record is not None
            g.equal(
                record["uri"],
                uri,
                "scene_table_record",
                f"{table_name}: its record's URI",
            )
        try:
            return read_scene_table_bytes(data, table_name, uri=uri)
        except UnsupportedSceneAnalyticsSchemaError as exc:
            g.fail("scene_table_schema", str(exc))
        except SceneAnalyticsIntegrityError as exc:
            g.fail("scene_table_structure", str(exc))
        return None

    def _check_scenes_table(
        self,
        g: Findings,
        frame: pl.DataFrame,
        scenes: Mapping[str, SceneRevisionContent],
    ) -> None:
        for row in frame.iter_rows(named=True):
            s = g.scoped(scene_id=row["scene_id"])
            content = scenes.get(row["scene_id"])
            if content is None:
                continue
            record, manifest = content.record, content.manifest
            s.equal(
                (row["manifest_artifact_id"], row["manifest_checksum"]),
                (record.manifest_artifact_id, record.manifest_checksum),
                "scene_revision",
                "the revision the row states vs the one the DatasetVersion pins",
            )
            # The whole source of the revision against the whole source of the row.
            # Which id is set for which kind is the record's rule, not this one's.
            projected = project_scene_revision(
                manifest=manifest,
                manifest_artifact_id=record.manifest_artifact_id,
                manifest_checksum=record.manifest_checksum,
            )
            s.equal(
                (
                    row["source_kind"],
                    row["robot_run_id"],
                    row["content_id"],
                    row["unit_key"],
                    row["producer_fingerprint"],
                ),
                (
                    projected.source_kind,
                    projected.robot_run_id,
                    projected.content_id,
                    projected.unit_key,
                    projected.producer_fingerprint,
                ),
                "scene_provenance",
                "the row's source vs the pinned revision's",
            )
            window = manifest.declared_window()
            issued = manifest.clock_instance(window.source_clock)
            self.stats["instance_comparisons"] += 1
            s.equal(
                restore_clock_instance(
                    row["window_clock"],
                    row["window_clock_origin"],
                    row["window_clock_scope"],
                ),
                issued,
                "scene_window_instance",
                "the window's stated instance vs the one the manifest issues",
            )
            self.verified.scene_windows[row["scene_id"]] = issued
            s.equal(
                (row["window_start_timestamp_ns"], row["window_end_timestamp_ns"]),
                (window.start_timestamp_ns, window.end_timestamp_ns),
                "scene_window_time",
                "the window",
            )
            s.equal(
                (
                    row["observation_count"],
                    row["keyframe_count"],
                    row["observed_channels"],
                ),
                (
                    projected.observation_count,
                    projected.keyframe_count,
                    projected.observed_channels,
                ),
                "scene_counts",
                "counts and observed channels",
            )

    def _check_observations_table(
        self,
        g: Findings,
        frame: pl.DataFrame,
        scenes: Mapping[str, SceneRevisionContent],
    ) -> None:
        rows_by_scene = frame.partition_by("scene_id", as_dict=True)
        for scene_id, content in scenes.items():
            s = g.scoped(scene_id=scene_id)
            manifest = content.manifest
            channels = {c.channel: c for c in manifest.channels}
            issued = {
                name: manifest.clock_instance(c.source_clock)
                for name, c in channels.items()
            }
            canonical = {o.observation_id: o for o in manifest.observations}
            part = rows_by_scene.get((scene_id,))
            rows = list(part.iter_rows(named=True)) if part is not None else []
            s.equal(
                sorted(r["observation_id"] for r in rows),
                sorted(canonical),
                "observation_rows",
                "the observations of the row set vs the manifest's",
            )
            restored = _Restored("source_clock", "clock_origin", "clock_scope")
            for row in rows:
                o = canonical.get(row["observation_id"])
                if o is None:
                    continue
                self.stats["instance_comparisons"] += 1
                # Each row against the clock of its own canonical channel: not the
                # window's, which a Scene may declare apart from every observation.
                s.check(
                    restored(row) == issued[o.channel],
                    "observation_instance",
                    f"{o.observation_id}: stated {restored(row)!r}, its channel "
                    f"{o.channel!r} is on {issued[o.channel]!r}",
                )
                selector = o.payload.selector
                s.equal(
                    {
                        "channel": row["channel"],
                        "modality": row["modality"],
                        "timestamp_ns": row["timestamp_ns"],
                        "payload_uri": row["payload_uri"],
                        "payload_checksum": row["payload_checksum"],
                        "payload_media_type": row["payload_media_type"],
                        "payload_size_bytes": row["payload_size_bytes"],
                        "payload_selector": row["payload_selector"],
                        "calibration_id": row["calibration_id"],
                        "ego_pose_id": row["ego_pose_id"],
                    },
                    {
                        "channel": o.channel,
                        "modality": channels[o.channel].modality.value,
                        "timestamp_ns": o.timestamp_ns,
                        "payload_uri": o.payload.uri,
                        "payload_checksum": o.payload.checksum,
                        "payload_media_type": o.payload.media_type,
                        "payload_size_bytes": o.payload.size_bytes,
                        "payload_selector": (
                            canonical_json_bytes(
                                selector.model_dump(mode="json")
                            ).decode()
                            if selector is not None
                            else None
                        ),
                        "calibration_id": o.calibration_id,
                        "ego_pose_id": o.ego_pose_id,
                    },
                    "observation_content",
                    f"{o.observation_id}",
                )

    def _check_keyframes_table(
        self,
        g: Findings,
        frame: pl.DataFrame,
        scenes: Mapping[str, SceneRevisionContent],
    ) -> None:
        rows_by_scene = frame.partition_by("scene_id", as_dict=True)
        for scene_id, content in scenes.items():
            s = g.scoped(scene_id=scene_id)
            manifest = content.manifest
            canonical = {k.group_id: k for k in manifest.keyframes()}
            part = rows_by_scene.get((scene_id,))
            rows = list(part.iter_rows(named=True)) if part is not None else []
            s.equal(
                sorted(r["group_id"] for r in rows),
                sorted(canonical),
                "keyframe_rows",
                "the keyframes of the row set vs the manifest's",
            )
            restored = _Restored("source_clock", "clock_origin", "clock_scope")
            for row in rows:
                k = canonical.get(row["group_id"])
                if k is None:
                    continue
                self.stats["instance_comparisons"] += 1
                # The group's own clock; a keyframe need not be on the window's.
                s.check(
                    restored(row) == manifest.clock_instance(k.source_clock),
                    "keyframe_instance",
                    f"{k.group_id}: stated {restored(row)!r}, the group declares "
                    f"{manifest.clock_instance(k.source_clock)!r}",
                )
                s.equal(
                    (row["timestamp_ns"], row["observation_ids"]),
                    (k.timestamp_ns, list(k.observation_ids)),
                    "keyframe_content",
                    k.group_id,
                )


class _Restored:
    """The one reconstruction, once per distinct stated triple of a Scene's rows."""

    def __init__(self, clock: str, origin: str, scope: str) -> None:
        self._columns = (clock, origin, scope)
        self._cache: dict[tuple[Any, ...], ClockInstance] = {}

    def __call__(self, row: Mapping[str, Any]) -> ClockInstance:
        clock, origin, scope = (row[c] for c in self._columns)
        key = (clock, origin, tuple(scope))
        if key not in self._cache:
            self._cache[key] = restore_clock_instance(clock, origin, scope)
        return self._cache[key]


def _signal_facts(signal: AlignedSignal) -> dict[str, Any]:
    """What learning_signals keeps of a signal: everything but the reference channel
    and non-selector reference metadata, which the columnar export never persisted."""
    value = signal.value
    return {
        "policy": str(signal.policy),
        "status": str(signal.status),
        "kind": str(value.kind) if value is not None else None,
        "scalar": value.scalar if value is not None else None,
        "vector": value.vector if value is not None else None,
        "modality": (
            str(value.reference_modality)
            if value is not None and value.reference_modality is not None
            else None
        ),
        "uri": value.reference_uri if value is not None else None,
        "selector": (
            json.dumps(value.reference_metadata.get("selector"), sort_keys=True)
            if value is not None
            else None
        ),
        "source_timestamp_us": signal.source_timestamp_us,
        "time_delta_us": signal.time_delta_us,
        "source_before_timestamp_us": signal.source_before_timestamp_us,
        "source_after_timestamp_us": signal.source_after_timestamp_us,
        "interpolation_ratio": signal.interpolation_ratio,
    }


def _step_facts(step: LearningStep) -> dict[str, Any]:
    return {
        "timestamp_us": step.timestamp_us,
        str(ChannelNamespace.OBSERVATION): {
            c: _signal_facts(s) for c, s in sorted(step.observations.items())
        },
        str(ChannelNamespace.ACTION): {
            c: _signal_facts(s) for c, s in sorted(step.actions.items())
        },
    }


def report_of(verifier: TemporalLineageVerifier, findings: Findings) -> dict[str, Any]:
    """The verification's result, a function of its inputs alone: no time, host or
    path, so identical inputs give identical reports."""
    verified = verifier.verified
    return {
        "schema": REPORT_SCHEMA,
        "ok": findings.ok,
        "checks": dict(sorted(findings.checked.items())),
        "check_count": sum(findings.checked.values()),
        "violations": findings.violations,
        "stats": dict(sorted(verifier.stats.items())),
        "instances": {
            "aligned": {
                k: instance_json(v) for k, v in sorted(verified.aligned.items())
            },
            "learning": {
                f"{r.episode_id}@{r.aligned_artifact_checksum}": instance_json(v)
                for r, v in sorted(
                    verified.learning.items(),
                    key=lambda item: (
                        item[0].episode_id,
                        item[0].aligned_artifact_checksum,
                    ),
                )
            },
            "scene_windows": {
                k: instance_json(v) for k, v in sorted(verified.scene_windows.items())
            },
            "scene_clocks": {
                k: {c: instance_json(i) for c, i in sorted(clocks.items())}
                for k, clocks in sorted(verified.scene_clocks.items())
            },
        },
    }
