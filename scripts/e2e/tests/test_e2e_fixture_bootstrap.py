"""Tests for scripts/e2e/e2e_fixture_bootstrap.py (SceneOps V2 Request
3.2C, hardened by Request 3.2C.1): idempotent create/reuse/verify decision
logic for the shared E2E fixture catalog (core/interop/raw-log).

Postgres repositories are faked here with small in-memory stand-ins (unit
-level, no real DB needed) -- the interop fixture's actual Parquet/manifest
writing goes through a real ``LocalArtifactStore`` under ``tmp_path``
(cheap, no external service, and exercises the real
AnalyticsTableWriter/SceneOpsDataset path for real). A separate live-infra
test (test_e2e_fixture_bootstrap_integration.py, `make test-integration`)
covers the real-Postgres round trip.

Fakes over MagicMock-based repo patching (Request 3.2C's original
approach) because Request 3.2C.1 §2 requires ``bootstrap_e2e_fixtures`` to
call verification *within the same call* that created or matched a
fixture -- verification re-reads the DatasetVersion/ArtifactRecord state
through the exact same repository, so the fake must actually behave like
a small persistent store across the ensure-then-verify sequence, not just
return one static canned value.

No conftest.py here (module-local sys.path insertion instead) -- same
"--import-mode=importlib multiple same-named conftest.py collide" reason
documented in test_e2e_fixture_bootstrap_integration.py.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SCRIPTS_E2E_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_E2E_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_E2E_DIR))

from e2e_fixture_bootstrap import (  # noqa: E402
    CORE_DATASET_ID,
    CORE_DATASET_VERSION,
    FIXTURE_CATALOG_VERSION,
    INTEROP_DATASET_ID,
    INTEROP_DATASET_VERSION,
    RAW_LOG_DATASET_ID,
    FixtureConflictError,
    FixtureVerificationError,
    _interop_export_id,
    _resolve_fixture_names,
    bootstrap_e2e_fixtures,
    verify_e2e_fixture,
)
from sceneops_core.artifacts.schemas import ArtifactKind  # noqa: E402
from sceneops_core.datasets.schemas.records import DatasetVersionRecord  # noqa: E402
from sceneops_storage import LocalArtifactStore  # noqa: E402

_MODULE = "e2e_fixture_bootstrap"


def _mock_session() -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    return session


# ── fake Postgres repositories ────────────────────────────────────────────
#
# Small, real (stateful) in-memory stand-ins for
# PostgresDataset(Version)Repository/PostgresArtifactRefRepository -- see
# module docstring for why plain MagicMocks aren't enough here.


@dataclass
class _FakeArtifactRecord:
    artifact_id: str
    uri: str
    checksum: str
    metadata: dict
    kind: ArtifactKind
    dataset_id: str
    dataset_version: str


class _FakeDatasetRepo:
    def __init__(self, store: dict):
        self._store = store

    async def get(self, dataset_id):
        return self._store.get(dataset_id)

    async def create(self, record):
        self._store[record.dataset_id] = record
        return record


class _FakeVersionRepo:
    def __init__(self, store: dict):
        self._store = store

    async def get(self, *, dataset_id, version):
        return self._store.get((dataset_id, version))

    async def upsert(self, record):
        self._store[(record.dataset_id, record.version)] = record
        return record


class _FakeArtifactRepo:
    def __init__(self, records: list):
        self._records = records

    async def list(self, *, kind, dataset_id, dataset_version):
        return [
            r
            for r in self._records
            if r.kind == kind
            and r.dataset_id == dataset_id
            and r.dataset_version == dataset_version
        ]

    async def create(
        self, *, artifact_id, ref, owner_type, owner_id, dataset_id, dataset_version
    ):
        record = _FakeArtifactRecord(
            artifact_id=artifact_id,
            uri=ref.uri,
            checksum=ref.checksum,
            metadata=ref.metadata,
            kind=ref.kind,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        self._records.append(record)
        return record


@dataclass
class _FakeRepoStores:
    """One call to bootstrap_e2e_fixtures()/verify_e2e_fixture() is many
    repository instantiations under the hood (a fresh
    PostgresXRepository(session) per helper function) -- these dict/list
    stores are what actually persists *across* those instantiations and
    across repeated calls within one `with _fake_repos(...) as stores:`
    block, exactly like a real session would."""

    datasets: dict = field(default_factory=dict)
    versions: dict = field(default_factory=dict)
    artifacts: list = field(default_factory=list)


def _fake_repos(stores: _FakeRepoStores | None = None):
    stores = stores or _FakeRepoStores()
    return (
        patch(
            f"{_MODULE}.PostgresDatasetRepository",
            lambda session: _FakeDatasetRepo(stores.datasets),
        ),
        patch(
            f"{_MODULE}.PostgresDatasetVersionRepository",
            lambda session: _FakeVersionRepo(stores.versions),
        ),
        patch(
            f"{_MODULE}.PostgresArtifactRefRepository",
            lambda session: _FakeArtifactRepo(stores.artifacts),
        ),
        stores,
    )


def _make_source_dir(tmp_path: Path, format_version: str = "v1.0-mini") -> Path:
    source_root = tmp_path / "raw" / "nuscenes"
    (source_root / format_version).mkdir(parents=True)
    return source_root


# ── _resolve_fixture_names ──────────────────────────────────────────────


def test_resolve_fixture_names_all_returns_full_catalog():
    assert _resolve_fixture_names("all") == ("core", "interop", "raw-log")


def test_resolve_fixture_names_single_fixture():
    assert _resolve_fixture_names("core") == ("core",)


def test_resolve_fixture_names_rejects_unknown():
    with pytest.raises(ValueError, match="unknown E2E fixture"):
        _resolve_fixture_names("bogus")


# ── core / raw-log: bootstrap now also verifies before returning ────────


async def test_bootstrap_core_creates_and_verifies_when_missing(tmp_path):
    source_root = _make_source_dir(tmp_path)
    p1, p2, p3, _stores = _fake_repos()
    with p1, p2, p3, patch(f"{_MODULE}.CORE_SOURCE_ROOT_URI", str(source_root)):
        [result] = await bootstrap_e2e_fixtures(
            "core",
            session=_mock_session(),
            artifact_store=MagicMock(),
            analytics_root_uri="analytics",
        )

    assert result.fixture_name == "core"
    assert result.fixture_catalog_version == FIXTURE_CATALOG_VERSION
    assert result.dataset_id == CORE_DATASET_ID
    assert result.dataset_version == CORE_DATASET_VERSION
    assert result.created is True


async def test_bootstrap_core_reuses_and_verifies_when_existing(tmp_path):
    source_root = _make_source_dir(tmp_path)
    stores = _FakeRepoStores(
        versions={
            (CORE_DATASET_ID, CORE_DATASET_VERSION): DatasetVersionRecord(
                dataset_id=CORE_DATASET_ID, version=CORE_DATASET_VERSION
            )
        }
    )
    p1, p2, p3, _stores = _fake_repos(stores)
    with p1, p2, p3, patch(f"{_MODULE}.CORE_SOURCE_ROOT_URI", str(source_root)):
        [result] = await bootstrap_e2e_fixtures(
            "core",
            session=_mock_session(),
            artifact_store=MagicMock(),
            analytics_root_uri="analytics",
        )

    assert result.created is False


async def test_bootstrap_core_fails_clearly_when_source_fixture_missing(tmp_path):
    p1, p2, p3, _stores = _fake_repos()
    with (
        p1,
        p2,
        p3,
        patch(f"{_MODULE}.CORE_SOURCE_ROOT_URI", str(tmp_path / "nonexistent")),
    ):
        with pytest.raises(FixtureVerificationError, match="missing external source"):
            await bootstrap_e2e_fixtures(
                "core",
                session=_mock_session(),
                artifact_store=MagicMock(),
                analytics_root_uri="analytics",
            )


async def test_bootstrap_raw_log_uses_its_own_isolated_identity(tmp_path):
    source_root = _make_source_dir(tmp_path)
    p1, p2, p3, _stores = _fake_repos()
    with (
        p1,
        p2,
        p3,
        patch(f"{_MODULE}.CORE_SOURCE_ROOT_URI", str(source_root)),
        patch(f"{_MODULE}.RAW_LOG_SOURCE_ROOT_URI", str(source_root)),
    ):
        [result] = await bootstrap_e2e_fixtures(
            "raw-log",
            session=_mock_session(),
            artifact_store=MagicMock(),
            analytics_root_uri="analytics",
        )

    assert result.fixture_name == "raw-log"
    assert result.dataset_id == RAW_LOG_DATASET_ID
    assert result.dataset_id != CORE_DATASET_ID


# ── interop: fresh create -> verify ──────────────────────────────────────


async def test_bootstrap_interop_creates_and_verifies_when_missing(tmp_path):
    artifact_store = LocalArtifactStore(root_uri=str(tmp_path / "storage"))
    p1, p2, p3, stores = _fake_repos()

    with p1, p2, p3:
        [result] = await bootstrap_e2e_fixtures(
            "interop",
            session=_mock_session(),
            artifact_store=artifact_store,
            analytics_root_uri=str(tmp_path / "analytics"),
        )

    assert result.fixture_name == "interop"
    assert result.dataset_id == INTEROP_DATASET_ID
    assert result.dataset_version == INTEROP_DATASET_VERSION
    assert result.created is True
    assert result.learning_manifest_artifact_id is not None
    assert result.learning_manifest_checksum is not None
    assert result.learning_manifest_uri is not None
    assert result.episode_refs is not None
    assert len(result.episode_refs) == 3
    # 3 tables + 1 manifest artifact registered.
    assert len(stores.artifacts) == 4
    kinds = {r.kind for r in stores.artifacts}
    assert kinds == {
        ArtifactKind.ANALYTICS_TABLE,
        ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
    }

    # bootstrap already verified internally -- an independent verify call
    # must see exactly the same success, in the documented check order.
    with p1, p2, p3:
        [verification] = await verify_e2e_fixture(
            "interop", session=_mock_session(), artifact_store=artifact_store
        )
    assert verification.ok, verification.errors
    assert verification.checks == [
        "dataset_version_exists",
        "manifest_artifact_exists",
        "manifest_checksum_matches",
        "table_checksums_match",
        "sceneops_dataset_opened",
        "episode_refs_match",
        "step_counts_timestamps_features_match",
    ]


async def test_bootstrap_interop_does_not_require_nuscenes_source_path(tmp_path):
    """Interop's create+verify path must never touch CORE_SOURCE_ROOT_URI --
    only core/raw-log's DatasetVersion verification does (SceneOps V2
    Request 3.2C.1 §7)."""
    artifact_store = LocalArtifactStore(root_uri=str(tmp_path / "storage"))
    p1, p2, p3, _stores = _fake_repos()

    with (
        p1,
        p2,
        p3,
        patch(f"{_MODULE}.CORE_SOURCE_ROOT_URI", "/definitely/does/not/exist"),
    ):
        [result] = await bootstrap_e2e_fixtures(
            "interop",
            session=_mock_session(),
            artifact_store=artifact_store,
            analytics_root_uri=str(tmp_path / "analytics"),
        )

    assert result.created is True


# ── interop: existing + matching identity -> verify -> reuse ────────────


async def test_bootstrap_interop_reuses_after_verifying_existing_fixture(tmp_path):
    artifact_store = LocalArtifactStore(root_uri=str(tmp_path / "storage"))
    p1, p2, p3, stores = _fake_repos()

    with p1, p2, p3:
        [first] = await bootstrap_e2e_fixtures(
            "interop",
            session=_mock_session(),
            artifact_store=artifact_store,
            analytics_root_uri=str(tmp_path / "analytics"),
        )
    assert first.created is True
    assert len(stores.artifacts) == 4

    with (
        p1,
        p2,
        p3,
        patch(
            f"{_MODULE}.AnalyticsTableWriter.write_learning_table"
        ) as mock_write_table,
    ):
        [second] = await bootstrap_e2e_fixtures(
            "interop",
            session=_mock_session(),
            artifact_store=artifact_store,
            analytics_root_uri=str(tmp_path / "analytics"),
        )

    assert second.created is False
    assert second.learning_manifest_artifact_id == first.learning_manifest_artifact_id
    assert second.learning_manifest_checksum == first.learning_manifest_checksum
    assert second.learning_manifest_uri == first.learning_manifest_uri
    # Reuse path must never write anything new.
    mock_write_table.assert_not_called()
    assert len(stores.artifacts) == 4


async def test_bootstrap_interop_reuse_fails_when_minio_object_missing(tmp_path):
    artifact_store = LocalArtifactStore(root_uri=str(tmp_path / "storage"))
    p1, p2, p3, stores = _fake_repos()

    with p1, p2, p3:
        [first] = await bootstrap_e2e_fixtures(
            "interop",
            session=_mock_session(),
            artifact_store=artifact_store,
            analytics_root_uri=str(tmp_path / "analytics"),
        )

    # Simulate the manifest object having disappeared from MinIO out from
    # under Postgres (matching manifest ArtifactRecord still exists).
    await artifact_store.delete_prefix(first.learning_manifest_uri)

    with p1, p2, p3:
        with pytest.raises(FixtureVerificationError, match="missing MinIO object"):
            await bootstrap_e2e_fixtures(
                "interop",
                session=_mock_session(),
                artifact_store=artifact_store,
                analytics_root_uri=str(tmp_path / "analytics"),
            )


async def test_bootstrap_interop_reuse_fails_when_checksum_mismatch(tmp_path):
    artifact_store = LocalArtifactStore(root_uri=str(tmp_path / "storage"))
    p1, p2, p3, stores = _fake_repos()

    with p1, p2, p3:
        [first] = await bootstrap_e2e_fixtures(
            "interop",
            session=_mock_session(),
            artifact_store=artifact_store,
            analytics_root_uri=str(tmp_path / "analytics"),
        )

    # Corrupt the persisted manifest bytes in place.
    await artifact_store.write_bytes(first.learning_manifest_uri, b'{"tampered": true}')

    with p1, p2, p3:
        with pytest.raises(FixtureVerificationError, match="checksum mismatch"):
            await bootstrap_e2e_fixtures(
                "interop",
                session=_mock_session(),
                artifact_store=artifact_store,
                analytics_root_uri=str(tmp_path / "analytics"),
            )


async def test_bootstrap_interop_raises_on_incompatible_existing_fixture(tmp_path):
    stale_record = _FakeArtifactRecord(
        artifact_id="art-stale",
        uri="file:///stale-manifest.json",
        checksum="sha256:stale",
        metadata={"export_id": "some-stale-export-id-from-an-old-definition"},
        kind=ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
        dataset_id=INTEROP_DATASET_ID,
        dataset_version=INTEROP_DATASET_VERSION,
    )
    stores = _FakeRepoStores(
        versions={
            (INTEROP_DATASET_ID, INTEROP_DATASET_VERSION): DatasetVersionRecord(
                dataset_id=INTEROP_DATASET_ID, version=INTEROP_DATASET_VERSION
            )
        },
        artifacts=[stale_record],
    )
    p1, p2, p3, stores = _fake_repos(stores)
    artifact_store = LocalArtifactStore(root_uri=str(tmp_path / "storage"))

    with p1, p2, p3:
        with pytest.raises(FixtureConflictError, match="incompatible"):
            await bootstrap_e2e_fixtures(
                "interop",
                session=_mock_session(),
                artifact_store=artifact_store,
                analytics_root_uri=str(tmp_path / "analytics"),
            )

    # Conflict must be detected before any write is attempted.
    assert stores.artifacts == [stale_record]


# ── bootstrap_e2e_fixtures("all") ───────────────────────────────────────


async def test_bootstrap_all_dispatches_every_fixture(tmp_path):
    source_root = _make_source_dir(tmp_path)
    artifact_store = LocalArtifactStore(root_uri=str(tmp_path / "storage"))
    p1, p2, p3, _stores = _fake_repos()

    with (
        p1,
        p2,
        p3,
        patch(f"{_MODULE}.CORE_SOURCE_ROOT_URI", str(source_root)),
        patch(f"{_MODULE}.RAW_LOG_SOURCE_ROOT_URI", str(source_root)),
    ):
        results = await bootstrap_e2e_fixtures(
            "all",
            session=_mock_session(),
            artifact_store=artifact_store,
            analytics_root_uri=str(tmp_path / "analytics"),
        )

    assert {r.fixture_name for r in results} == {"core", "interop", "raw-log"}


# ── verify: missing state fails clearly ─────────────────────────────────


async def test_verify_core_reports_missing_dataset_version_and_source(tmp_path):
    p1, p2, p3, _stores = _fake_repos()
    with (
        p1,
        p2,
        p3,
        patch(f"{_MODULE}.CORE_SOURCE_ROOT_URI", str(tmp_path / "nonexistent")),
    ):
        [result] = await verify_e2e_fixture(
            "core", session=_mock_session(), artifact_store=MagicMock()
        )

    assert result.ok is False
    assert any("missing DatasetVersion" in e for e in result.errors)
    assert any("missing external source fixture directory" in e for e in result.errors)


async def test_verify_core_source_available_when_directory_exists(tmp_path):
    source_root = _make_source_dir(tmp_path)
    stores = _FakeRepoStores(
        versions={
            (CORE_DATASET_ID, CORE_DATASET_VERSION): DatasetVersionRecord(
                dataset_id=CORE_DATASET_ID, version=CORE_DATASET_VERSION
            )
        }
    )
    p1, p2, p3, _stores = _fake_repos(stores)
    with p1, p2, p3, patch(f"{_MODULE}.CORE_SOURCE_ROOT_URI", str(source_root)):
        [result] = await verify_e2e_fixture(
            "core", session=_mock_session(), artifact_store=MagicMock()
        )

    assert result.ok is True
    assert "dataset_version_exists" in result.checks
    assert "external_source_available" in result.checks


async def test_verify_interop_missing_dataset_version(tmp_path):
    p1, p2, p3, _stores = _fake_repos()
    with p1, p2, p3:
        [verification] = await verify_e2e_fixture(
            "interop", session=_mock_session(), artifact_store=MagicMock()
        )

    assert verification.ok is False
    assert any("missing DatasetVersion" in e for e in verification.errors)


async def test_verify_interop_missing_manifest_artifact(tmp_path):
    stores = _FakeRepoStores(
        versions={
            (INTEROP_DATASET_ID, INTEROP_DATASET_VERSION): DatasetVersionRecord(
                dataset_id=INTEROP_DATASET_ID, version=INTEROP_DATASET_VERSION
            )
        }
    )
    p1, p2, p3, _stores = _fake_repos(stores)
    with p1, p2, p3:
        [verification] = await verify_e2e_fixture(
            "interop", session=_mock_session(), artifact_store=MagicMock()
        )

    assert verification.ok is False
    assert any("missing ArtifactRecord" in e for e in verification.errors)


def test_interop_export_id_is_deterministic():
    _entries1, _config1, export_id1 = _interop_export_id()
    _entries2, _config2, export_id2 = _interop_export_id()
    assert export_id1 == export_id2
