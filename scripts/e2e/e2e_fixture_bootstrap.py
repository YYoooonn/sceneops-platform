"""Persistent E2E fixture bootstrap (SceneOps V2 Request 3.2C, hardened by
Request 3.2C.1): materializes the shared E2E fixture catalog
(scripts/e2e/lib.sh's ``resolve_e2e_fixture`` -- core/interop/raw-log,
SceneOps V2 Request 3.2B) into real PostgreSQL + MinIO/ArtifactStore, using
only real SceneOps abstractions (sceneops-db repositories, ArtifactStore,
AnalyticsTableWriter) -- never raw SQL or direct boto3/MinIO calls.

Lives in scripts/e2e/, not packages/sceneops-analytics/, because this
module needs sceneops-db (real Postgres repositories) purely to set up
test/E2E prerequisite state. sceneops-analytics is a production package
(the columnar analytics export layer); it must not depend on sceneops-db
just to support E2E bootstrap (SceneOps V2 Request 3.2C.1 §1). Only the
deterministic, DB-free golden-data definitions
(``sceneops_analytics.testing.interop_dataset``'s
``build_interop_entries``/``compute_expected_interop_episodes``, etc.)
are imported from that package; nothing here is imported by production
code, and nothing in this module is imported by sceneops_analytics.

Not an installable package -- imported two ways:
  * ``scripts/e2e/bootstrap_e2e_fixtures.py`` (the CLI) imports it as a
    plain sibling module (Python puts a script's own directory on
    ``sys.path`` automatically).
  * ``scripts/e2e/tests/test_e2e_fixture_bootstrap*.py`` insert
    ``scripts/e2e`` onto ``sys.path`` themselves at import time (no
    ``conftest.py`` here, deliberately -- see those test files' own
    docstrings for why).

Seed boundary per fixture (bootstrap prerequisite state, never the output
whose production is the behavior under test):

  core     -- ensure the canonical DatasetVersion row exists. Never creates
              Scenes/Episodes -- producing those IS the behavior under test
              for pipeline-contracts/dataset-ingestion/episode-building.
              Verification additionally checks that the external nuScenes
              source fixture is present on disk.
  raw-log  -- same seed boundary as core (its own isolated DatasetVersion
              row only) -- raw_log_scene_building's own build_scenes step
              is the behavior under test, not this bootstrap.
  interop  -- the full golden LearningDataExportManifest + three Parquet
              tables + their ArtifactRecords. Unlike core/raw-log, nothing
              about *producing* this snapshot is itself under test by any
              current or planned E2E -- future external-adapter/round-trip
              tests read FROM it, so materializing it fully is prerequisite
              state, not a tested output. Reuses Request 3.2's
              interop_dataset module for the golden data itself
              (build_interop_entries/compute_expected_interop_episodes) --
              never redefined here.

Create/reuse/verify contract (SceneOps V2 Request 3.2C.1 §2) -- every
successful return from ``bootstrap_e2e_fixtures``/``ensure_e2e_fixture``
means the fixture is actually ready (verified), not merely that a matching
manifest/DatasetVersion record exists:

  missing                                   -> create -> verify -> success
  existing, matching semantic identity      -> verify -> success only if valid
  existing, matching identity, corrupted/
    missing artifact                        -> FixtureVerificationError
  existing, incompatible semantic identity  -> FixtureConflictError

``verify_e2e_fixture`` remains separately callable (e.g. ``--verify-only``)
to re-check already-bootstrapped state without writing anything.

Partial-write recovery (SceneOps V2 Request 3.2C.1 §3): there is no
transaction spanning the Postgres commit and the MinIO writes for the
``interop`` fixture's tables/manifest -- writes to MinIO happen first,
then one Postgres commit registers all four ArtifactRecords together. A
crash between "some tables written" and "commit" leaves orphaned MinIO
objects with no matching ArtifactRecord (harmless -- verification simply
won't find them) or, if it crashes mid-way through ``artifacts.create()``
calls before the final commit, leaves nothing committed at all (the whole
create attempt is invisible next run, so it retries cleanly). The one
unsafe window is a crash *after* commit but before this function returns --
practically unreachable (no more awaits happen after commit except the
verify-and-return, which only reads). This module never attempts automatic
repair of a corrupted/partial fixture: a verification failure always fails
loudly and requires `make local-reset` (destructive, whole local stack) to
recover. Local E2E state is disposable by design -- this is an accepted v1
limitation, not a bug.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from sceneops_analytics.learning_dataset import SceneOpsDataset
from sceneops_analytics.learning_tables import (
    build_learning_episodes_table,
    build_learning_signals_table,
    build_learning_steps_table,
)
from sceneops_analytics.testing.interop_dataset import (
    INTEROP_DATASET_ID,
    INTEROP_DATASET_VERSION,
    INTEROP_FEATURE_PROJECTION,
    build_interop_entries,
    compute_expected_interop_episodes,
)
from sceneops_analytics.writer import AnalyticsTableWriter
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType, ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.datasets.schemas import DatasetRecord, DatasetVersionRecord
from sceneops_core.episodes.learning_export import (
    LEARNING_DATA_SCHEMA_VERSION,
    AlignedArtifactRevision,
    LearningDataExportConfig,
    LearningDataExportManifest,
    learning_data_export_id,
)
from sceneops_db.postgres import (
    PostgresArtifactRefRepository,
    PostgresDatasetRepository,
    PostgresDatasetVersionRepository,
)
from sceneops_storage import ArtifactStore
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURE_CATALOG_VERSION = "sceneops-e2e-v1"
FIXTURE_NAMES: tuple[str, ...] = ("core", "interop", "raw-log")

# Mirrors scripts/e2e/lib.sh's resolve_e2e_fixture (SceneOps V2 Request
# 3.2B). Kept as a separate, documented Python source of truth -- like
# INTEROP_DATASET_ID/VERSION already were before this request -- rather
# than parsed out of the shell script; there is no automatic sync between
# the two, so a change to one must be mirrored in the other by hand.
#
# "/data/raw/nuscenes" is the E2E pipelines' own default -- correct from
# *inside* the api/worker containers, where `./data:/data` is bind-mounted
# (docs/development/local-development.md). This module's verification runs
# from wherever the caller runs it, which for the bootstrap CLI
# (scripts/e2e/bootstrap_e2e_fixtures.py) is normally the HOST, where that
# absolute path does not exist -- only "<repo_root>/data/raw/nuscenes"
# does. E2E_BOOTSTRAP_SOURCE_ROOT_URI lets a host-side caller point
# verification at the right filesystem location without changing what
# in-container pipelines themselves default to.
CORE_DATASET_ID = "test-e2e-core"
CORE_DATASET_VERSION = "test-v1"
CORE_SOURCE_ROOT_URI = os.environ.get(
    "E2E_BOOTSTRAP_SOURCE_ROOT_URI", "/data/raw/nuscenes"
)
CORE_SOURCE_FORMAT_VERSION = "v1.0-mini"

RAW_LOG_DATASET_ID = "test-e2e-raw-log"
RAW_LOG_DATASET_VERSION = "test-v1"
RAW_LOG_SOURCE_ROOT_URI = CORE_SOURCE_ROOT_URI
RAW_LOG_SOURCE_FORMAT_VERSION = "v1.0-mini"


class FixtureConflictError(Exception):
    """An existing persisted fixture's content disagrees with what the
    current golden-data definition would produce -- e.g. interop_dataset's
    formulas changed after the fixture was already bootstrapped once. Never
    auto-resolved; requires a human decision (SceneOps V2 Request 3.2C
    §5)."""


class FixtureVerificationError(Exception):
    """A fixture's semantic identity matched (freshly created, or an
    existing record reused) but its persisted state failed independent
    verification -- a missing MinIO object, a checksum mismatch, a missing
    DatasetVersion row, a missing external source directory, etc. Never
    auto-repaired (SceneOps V2 Request 3.2C.1 §2/§3): the caller must
    `make local-reset` and re-run bootstrap."""


@dataclass
class FixtureBootstrapResult:
    """Machine-readable result of bootstrapping one E2E fixture (SceneOps
    V2 Request 3.2C §6). Stable field names for scripting -- future E2Es
    should consume this, never scrape human-readable logs. Returned only
    after the fixture has also been independently verified (SceneOps V2
    Request 3.2C.1 §2) -- a return here always means "ready", never merely
    "a matching record exists"."""

    fixture_catalog_version: str
    fixture_name: str
    dataset_id: str
    dataset_version: str
    created: bool
    detail: str

    learning_manifest_artifact_id: str | None = None
    learning_manifest_checksum: str | None = None
    learning_manifest_uri: str | None = None
    episode_refs: list[dict[str, str]] | None = None

    def to_json_dict(self) -> dict:
        return {
            "fixture_catalog_version": self.fixture_catalog_version,
            "fixture_name": self.fixture_name,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "created": self.created,
            "detail": self.detail,
            "learning_manifest_artifact_id": self.learning_manifest_artifact_id,
            "learning_manifest_checksum": self.learning_manifest_checksum,
            "learning_manifest_uri": self.learning_manifest_uri,
            "episode_refs": self.episode_refs,
        }


@dataclass
class FixtureVerificationResult:
    """Machine-readable result of verifying one E2E fixture (SceneOps V2
    Request 3.2C §8)."""

    fixture_name: str
    ok: bool
    checks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict:
        return {
            "fixture_name": self.fixture_name,
            "ok": self.ok,
            "checks": self.checks,
            "errors": self.errors,
        }


def _sha256_prefixed(data: bytes) -> str:
    """Matches AnalyticsTableWriter's own checksum convention exactly
    (``sceneops_analytics.writer._sha256_hex``, prefixed with
    ``"sha256:"``) -- every checksum this module compares against was
    produced that way, so verification must hash the same way or every
    comparison would spuriously fail."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _episode_ref_dicts(
    entries: list[tuple[str, object]],
) -> list[dict[str, str]]:
    return [
        {
            "episode_id": artifact.aligned_episode.episode_id,
            "aligned_artifact_checksum": checksum,
        }
        for checksum, artifact in entries
    ]


def _interop_export_id() -> tuple[
    list[tuple[str, object]], LearningDataExportConfig, str
]:
    entries = build_interop_entries()
    export_config = LearningDataExportConfig()
    export_id = learning_data_export_id(
        aligned_checksums=[checksum for checksum, _ in entries],
        export_config=export_config,
    )
    return entries, export_config, export_id


# ── ensure DatasetVersion (shared by core/raw-log/interop) ─────────────────


async def _ensure_dataset_version(
    session: AsyncSession, *, dataset_id: str, dataset_version: str, name: str
) -> tuple[DatasetVersionRecord, bool]:
    """Idempotently ensure ``dataset_id`` and ``dataset_id``/``dataset_version``
    exist. Returns ``(record, created)`` -- ``created`` is True only if the
    version row did not already exist. Never touches Scene/Episode summary
    fields -- producing those is always the behavior under test, never this
    bootstrap's job."""
    datasets = PostgresDatasetRepository(session)
    versions = PostgresDatasetVersionRepository(session)

    if await datasets.get(dataset_id) is None:
        await datasets.create(DatasetRecord(dataset_id=dataset_id, name=name))

    existing = await versions.get(dataset_id=dataset_id, version=dataset_version)
    if existing is not None:
        return existing, False

    created = await versions.upsert(
        DatasetVersionRecord(dataset_id=dataset_id, version=dataset_version)
    )
    return created, True


# ── core / raw-log: bootstrap + verify ──────────────────────────────────────


async def _verify_dataset_version_and_source(
    session: AsyncSession,
    *,
    fixture_name: str,
    dataset_id: str,
    dataset_version: str,
    source_root_uri: str,
    source_format_version: str,
) -> FixtureVerificationResult:
    checks: list[str] = []
    errors: list[str] = []

    versions = PostgresDatasetVersionRepository(session)
    record = await versions.get(dataset_id=dataset_id, version=dataset_version)
    if record is None:
        errors.append(f"missing DatasetVersion {dataset_id}/{dataset_version}")
    else:
        checks.append("dataset_version_exists")

    source_dir = Path(source_root_uri) / source_format_version
    if source_dir.is_dir():
        checks.append("external_source_available")
    else:
        errors.append(
            f"missing external source fixture directory: {source_dir} "
            f"(format_version={source_format_version!r})"
        )

    return FixtureVerificationResult(
        fixture_name=fixture_name, ok=not errors, checks=checks, errors=errors
    )


async def _bootstrap_dataset_version_only(
    session: AsyncSession,
    *,
    fixture_name: str,
    dataset_id: str,
    dataset_version: str,
    source_root_uri: str,
    source_format_version: str,
) -> FixtureBootstrapResult:
    _record, created = await _ensure_dataset_version(
        session,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        name=f"SceneOps E2E fixture ({fixture_name})",
    )
    await session.commit()

    verification = await _verify_dataset_version_and_source(
        session,
        fixture_name=fixture_name,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        source_root_uri=source_root_uri,
        source_format_version=source_format_version,
    )
    if not verification.ok:
        raise FixtureVerificationError(
            f"{fixture_name} fixture {dataset_id}/{dataset_version} failed "
            "post-bootstrap verification: " + "; ".join(verification.errors)
        )

    return FixtureBootstrapResult(
        fixture_catalog_version=FIXTURE_CATALOG_VERSION,
        fixture_name=fixture_name,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        created=created,
        detail=(
            f"DatasetVersion {dataset_id}/{dataset_version} "
            + ("created and verified" if created else "already existed, verified")
        ),
    )


# ── interop: bootstrap + verify ─────────────────────────────────────────────


async def _verify_interop(
    session: AsyncSession, *, artifact_store: ArtifactStore
) -> FixtureVerificationResult:
    checks: list[str] = []
    errors: list[str] = []

    versions = PostgresDatasetVersionRepository(session)
    record = await versions.get(
        dataset_id=INTEROP_DATASET_ID, version=INTEROP_DATASET_VERSION
    )
    if record is None:
        errors.append(
            f"missing DatasetVersion {INTEROP_DATASET_ID}/{INTEROP_DATASET_VERSION}"
        )
        return FixtureVerificationResult("interop", False, checks, errors)
    checks.append("dataset_version_exists")

    _entries, _config, export_id = _interop_export_id()
    artifacts = PostgresArtifactRefRepository(session)
    manifests = await artifacts.list(
        kind=ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
        dataset_id=INTEROP_DATASET_ID,
        dataset_version=INTEROP_DATASET_VERSION,
    )
    matching = [m for m in manifests if m.metadata.get("export_id") == export_id]
    if not matching:
        errors.append(
            "missing ArtifactRecord: no LEARNING_DATA_EXPORT_MANIFEST with "
            f"export_id={export_id!r} (current golden-data definition)"
        )
        return FixtureVerificationResult("interop", False, checks, errors)
    checks.append("manifest_artifact_exists")
    manifest_record = matching[0]

    try:
        manifest_bytes = await artifact_store.read_bytes(manifest_record.uri)
    except Exception as exc:  # noqa: BLE001 -- surfaced as a verification error, not raised
        errors.append(f"missing MinIO object for manifest {manifest_record.uri}: {exc}")
        return FixtureVerificationResult("interop", False, checks, errors)

    actual_manifest_checksum = _sha256_prefixed(manifest_bytes)
    if actual_manifest_checksum != manifest_record.checksum:
        errors.append(
            f"checksum mismatch for manifest {manifest_record.uri}: "
            f"recorded={manifest_record.checksum} actual={actual_manifest_checksum}"
        )
        return FixtureVerificationResult("interop", False, checks, errors)
    checks.append("manifest_checksum_matches")

    manifest = LearningDataExportManifest.model_validate(json.loads(manifest_bytes))

    for table_name, uri in manifest.table_uris.items():
        try:
            table_bytes = await artifact_store.read_bytes(uri)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"missing MinIO object for table {table_name} ({uri}): {exc}")
            continue
        actual = _sha256_prefixed(table_bytes)
        expected = manifest.table_checksums.get(table_name)
        if actual != expected:
            errors.append(
                f"checksum mismatch for table {table_name} ({uri}): "
                f"recorded={expected} actual={actual}"
            )
    if errors:
        return FixtureVerificationResult("interop", False, checks, errors)
    checks.append("table_checksums_match")

    dataset = await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=manifest_record.checksum,
        artifact_store=artifact_store,
    )
    checks.append("sceneops_dataset_opened")

    expected_by_ref = compute_expected_interop_episodes()
    actual_refs = set(dataset.episodes())
    expected_refs = set(expected_by_ref.keys())
    if actual_refs != expected_refs:
        errors.append(
            "wrong EpisodeRefs: "
            f"missing={sorted(str(r) for r in expected_refs - actual_refs)} "
            f"unexpected={sorted(str(r) for r in actual_refs - expected_refs)}"
        )
        return FixtureVerificationResult("interop", False, checks, errors)
    checks.append("episode_refs_match")

    for ref, expected in expected_by_ref.items():
        metadata = dataset.get_episode(ref)
        if metadata.step_count != expected.step_count:
            errors.append(
                f"{ref}: step_count mismatch expected={expected.step_count} "
                f"actual={metadata.step_count}"
            )
            continue
        window = await dataset.get_window(
            ref, 0, expected.step_count, INTEROP_FEATURE_PROJECTION
        )
        expected_timestamps = [s.timestamp_us for s in expected.steps]
        expected_observation = [s.observation for s in expected.steps]
        expected_action = [s.action for s in expected.steps]
        if window.timestamps_us != expected_timestamps:
            errors.append(f"{ref}: timestamps_us mismatch")
        if window.observation != expected_observation:
            errors.append(f"{ref}: observation values mismatch")
        if window.action != expected_action:
            errors.append(f"{ref}: action values mismatch")

    if not errors:
        checks.append("step_counts_timestamps_features_match")

    return FixtureVerificationResult(
        fixture_name="interop", ok=not errors, checks=checks, errors=errors
    )


async def _bootstrap_interop(
    session: AsyncSession, *, artifact_store: ArtifactStore, analytics_root_uri: str
) -> FixtureBootstrapResult:
    await _ensure_dataset_version(
        session,
        dataset_id=INTEROP_DATASET_ID,
        dataset_version=INTEROP_DATASET_VERSION,
        name="SceneOps interoperability golden fixture",
    )

    entries, export_config, export_id = _interop_export_id()
    episode_refs = _episode_ref_dicts(entries)
    owner_id = f"{INTEROP_DATASET_ID}:{INTEROP_DATASET_VERSION}"

    artifacts = PostgresArtifactRefRepository(session)
    existing_manifests = await artifacts.list(
        kind=ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
        dataset_id=INTEROP_DATASET_ID,
        dataset_version=INTEROP_DATASET_VERSION,
    )

    matching = [
        m for m in existing_manifests if m.metadata.get("export_id") == export_id
    ]
    if matching:
        # existing, matching semantic identity -> verify -> success only if
        # valid (SceneOps V2 Request 3.2C.1 §2). Commit not needed: nothing
        # is written on this path.
        record = matching[0]
        verification = await _verify_interop(session, artifact_store=artifact_store)
        if not verification.ok:
            raise FixtureVerificationError(
                "interop fixture matches export_id="
                f"{export_id!r} but failed verification (corrupted or "
                "partially-written artifact) -- not auto-repaired; run "
                "`make local-reset` and re-bootstrap: " + "; ".join(verification.errors)
            )
        return FixtureBootstrapResult(
            fixture_catalog_version=FIXTURE_CATALOG_VERSION,
            fixture_name="interop",
            dataset_id=INTEROP_DATASET_ID,
            dataset_version=INTEROP_DATASET_VERSION,
            created=False,
            detail=(
                f"reused and verified existing LearningDataExportManifest "
                f"export_id={export_id}"
            ),
            learning_manifest_artifact_id=record.artifact_id,
            learning_manifest_checksum=record.checksum,
            learning_manifest_uri=record.uri,
            episode_refs=episode_refs,
        )

    if existing_manifests:
        conflicting = ", ".join(
            str(m.metadata.get("export_id", "<none>")) for m in existing_manifests
        )
        raise FixtureConflictError(
            f"interop fixture {owner_id} already has "
            f"{len(existing_manifests)} LEARNING_DATA_EXPORT_MANIFEST "
            f"artifact(s) with export_id(s) [{conflicting}], none of which "
            f"match the current golden-data definition's export_id="
            f"{export_id!r}. Refusing to create a second, incompatible "
            "fixture under the same canonical identity -- fix or remove "
            "the stale record(s) before re-running bootstrap."
        )

    # Missing -- create fresh, exactly mirroring
    # apps/worker/sceneops_worker/jobs/dataset/export_learning_data.py's
    # persistence pattern (real AnalyticsTableWriter + real ArtifactRecord
    # registration), just without the AlignedEpisodeArtifact
    # resolve/validate step that job performs (this fixture's
    # AlignedEpisodeArtifacts are already-trusted, hand-built golden data,
    # not resolved from a prior ALIGN_EPISODE job).
    writer = AnalyticsTableWriter(
        artifact_store=artifact_store, root_uri=analytics_root_uri
    )
    builders = {
        "learning_episodes": build_learning_episodes_table,
        "learning_steps": build_learning_steps_table,
        "learning_signals": build_learning_signals_table,
    }
    table_uris: dict[str, str] = {}
    table_checksums: dict[str, str] = {}
    table_size_bytes: dict[str, int] = {}
    row_counts: dict[str, int] = {}
    for table_name, builder in builders.items():
        df = builder(
            dataset_id=INTEROP_DATASET_ID,
            dataset_version=INTEROP_DATASET_VERSION,
            export_id=export_id,
            entries=entries,
        )
        result = await writer.write_learning_table(
            table_name,
            df,
            dataset_id=INTEROP_DATASET_ID,
            dataset_version=INTEROP_DATASET_VERSION,
            export_id=export_id,
        )
        table_uris[table_name] = result.uri
        table_checksums[table_name] = result.checksum
        table_size_bytes[table_name] = result.size_bytes
        row_counts[table_name] = df.height

    episode_count = len(
        {artifact.aligned_episode.episode_id for _, artifact in entries}
    )
    manifest = LearningDataExportManifest(
        schema_version=LEARNING_DATA_SCHEMA_VERSION,
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
        episode_count=episode_count,
    )
    manifest_write_result = await writer.write_learning_export_manifest(
        manifest,
        dataset_id=INTEROP_DATASET_ID,
        dataset_version=INTEROP_DATASET_VERSION,
        export_id=export_id,
    )

    for table_name, uri in table_uris.items():
        await artifacts.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.ANALYTICS_TABLE,
                uri=uri,
                media_type="application/vnd.apache.parquet",
                checksum=table_checksums[table_name],
                size_bytes=table_size_bytes[table_name],
                metadata={"export_id": export_id, "table_name": table_name},
            ),
            owner_type=ArtifactOwnerType.DATASET_VERSION,
            owner_id=owner_id,
            dataset_id=INTEROP_DATASET_ID,
            dataset_version=INTEROP_DATASET_VERSION,
        )

    manifest_artifact_id = generate_artifact_id()
    await artifacts.create(
        artifact_id=manifest_artifact_id,
        ref=ArtifactRef(
            kind=ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
            uri=manifest_write_result.uri,
            media_type="application/json",
            checksum=manifest_write_result.checksum,
            size_bytes=manifest_write_result.size_bytes,
            metadata={"export_id": export_id, "episode_count": episode_count},
        ),
        owner_type=ArtifactOwnerType.DATASET_VERSION,
        owner_id=owner_id,
        dataset_id=INTEROP_DATASET_ID,
        dataset_version=INTEROP_DATASET_VERSION,
    )
    await session.commit()

    # missing -> create -> verify -> success (SceneOps V2 Request 3.2C.1
    # §2). Re-reads what was just written through the same independent
    # path a standalone `verify_e2e_fixture` call would use -- catches a
    # write that silently didn't persist (e.g. a MinIO write that
    # succeeded locally but the object never became readable) before ever
    # reporting success.
    verification = await _verify_interop(session, artifact_store=artifact_store)
    if not verification.ok:
        raise FixtureVerificationError(
            f"interop fixture just created (export_id={export_id!r}) failed "
            "immediate post-write verification: " + "; ".join(verification.errors)
        )

    return FixtureBootstrapResult(
        fixture_catalog_version=FIXTURE_CATALOG_VERSION,
        fixture_name="interop",
        dataset_id=INTEROP_DATASET_ID,
        dataset_version=INTEROP_DATASET_VERSION,
        created=True,
        detail=f"created and verified LearningDataExportManifest export_id={export_id}",
        learning_manifest_artifact_id=manifest_artifact_id,
        learning_manifest_checksum=manifest_write_result.checksum,
        learning_manifest_uri=manifest_write_result.uri,
        episode_refs=episode_refs,
    )


# ── public dispatch ──────────────────────────────────────────────────────


def _resolve_fixture_names(fixture: str) -> tuple[str, ...]:
    if fixture == "all":
        return FIXTURE_NAMES
    if fixture in FIXTURE_NAMES:
        return (fixture,)
    raise ValueError(
        f"unknown E2E fixture {fixture!r} (expected 'all' or one of {FIXTURE_NAMES})"
    )


async def bootstrap_e2e_fixtures(
    fixture: str,
    *,
    session: AsyncSession,
    artifact_store: ArtifactStore,
    analytics_root_uri: str,
) -> list[FixtureBootstrapResult]:
    """Idempotently materialize one or all shared E2E fixtures (SceneOps V2
    Request 3.2C) into real Postgres + ``artifact_store``, following the
    create/reuse/verify contract documented at module level (SceneOps V2
    Request 3.2C.1 §2) -- a successful return always means the fixture is
    verified-ready.

    ``fixture`` is one of ``"all"``/``"core"``/``"interop"``/``"raw-log"``,
    matching ``scripts/e2e/lib.sh``'s ``resolve_e2e_fixture`` catalog
    exactly -- this function never redefines fixture identity, it only
    persists what that catalog already declares.
    """
    results: list[FixtureBootstrapResult] = []
    for name in _resolve_fixture_names(fixture):
        if name == "core":
            results.append(
                await _bootstrap_dataset_version_only(
                    session,
                    fixture_name="core",
                    dataset_id=CORE_DATASET_ID,
                    dataset_version=CORE_DATASET_VERSION,
                    source_root_uri=CORE_SOURCE_ROOT_URI,
                    source_format_version=CORE_SOURCE_FORMAT_VERSION,
                )
            )
        elif name == "raw-log":
            results.append(
                await _bootstrap_dataset_version_only(
                    session,
                    fixture_name="raw-log",
                    dataset_id=RAW_LOG_DATASET_ID,
                    dataset_version=RAW_LOG_DATASET_VERSION,
                    source_root_uri=RAW_LOG_SOURCE_ROOT_URI,
                    source_format_version=RAW_LOG_SOURCE_FORMAT_VERSION,
                )
            )
        elif name == "interop":
            results.append(
                await _bootstrap_interop(
                    session,
                    artifact_store=artifact_store,
                    analytics_root_uri=analytics_root_uri,
                )
            )
    return results


async def verify_e2e_fixture(
    fixture: str, *, session: AsyncSession, artifact_store: ArtifactStore
) -> list[FixtureVerificationResult]:
    """Independently verify one or all shared E2E fixtures already exist
    and match their frozen definition (SceneOps V2 Request 3.2C §8). Never
    writes anything. ``bootstrap_e2e_fixtures`` already runs this
    internally before returning success -- call this separately only to
    re-check existing state without also attempting to create/reuse
    anything (e.g. the CLI's ``--verify-only``)."""
    results: list[FixtureVerificationResult] = []
    for name in _resolve_fixture_names(fixture):
        if name == "core":
            results.append(
                await _verify_dataset_version_and_source(
                    session,
                    fixture_name="core",
                    dataset_id=CORE_DATASET_ID,
                    dataset_version=CORE_DATASET_VERSION,
                    source_root_uri=CORE_SOURCE_ROOT_URI,
                    source_format_version=CORE_SOURCE_FORMAT_VERSION,
                )
            )
        elif name == "raw-log":
            results.append(
                await _verify_dataset_version_and_source(
                    session,
                    fixture_name="raw-log",
                    dataset_id=RAW_LOG_DATASET_ID,
                    dataset_version=RAW_LOG_DATASET_VERSION,
                    source_root_uri=RAW_LOG_SOURCE_ROOT_URI,
                    source_format_version=RAW_LOG_SOURCE_FORMAT_VERSION,
                )
            )
        elif name == "interop":
            results.append(
                await _verify_interop(session, artifact_store=artifact_store)
            )
    return results


async def ensure_e2e_fixture(
    fixture: str,
    *,
    session: AsyncSession,
    artifact_store: ArtifactStore,
    analytics_root_uri: str,
) -> list[FixtureBootstrapResult]:
    """Convenience alias for :func:`bootstrap_e2e_fixtures` -- the name an
    E2E workflow calls before its own execution to guarantee known
    prerequisite state exists, without depending on any other E2E having
    run first (SceneOps V2 Request 3.2C §10). Identical semantics -- both
    names exist because "bootstrap" reads naturally standalone (a CLI
    entry point) and "ensure" reads naturally inline (a one-line guard at
    the top of another workflow). Per SceneOps V2 Request 3.2C.1 §2,
    "ensures" now means what it says: the fixture is verified-ready, not
    merely that a matching record exists."""
    return await bootstrap_e2e_fixtures(
        fixture,
        session=session,
        artifact_store=artifact_store,
        analytics_root_uri=analytics_root_uri,
    )


__all__ = [
    "CORE_DATASET_ID",
    "CORE_DATASET_VERSION",
    "CORE_SOURCE_FORMAT_VERSION",
    "CORE_SOURCE_ROOT_URI",
    "FIXTURE_CATALOG_VERSION",
    "FIXTURE_NAMES",
    "INTEROP_DATASET_ID",
    "INTEROP_DATASET_VERSION",
    "RAW_LOG_DATASET_ID",
    "RAW_LOG_DATASET_VERSION",
    "RAW_LOG_SOURCE_FORMAT_VERSION",
    "RAW_LOG_SOURCE_ROOT_URI",
    "FixtureBootstrapResult",
    "FixtureConflictError",
    "FixtureVerificationError",
    "FixtureVerificationResult",
    "bootstrap_e2e_fixtures",
    "ensure_e2e_fixture",
    "verify_e2e_fixture",
]
