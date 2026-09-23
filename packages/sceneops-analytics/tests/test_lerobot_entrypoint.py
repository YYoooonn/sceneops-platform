"""Tests for the LeRobot integration container entrypoint (SceneOps V2
Request 4.2): resolving a frozen ``IntegrationRequest`` (Request 4.1/4.1A)
into a real ``LeRobotDatasetAdapter.export()`` call, and back into an
``IntegrationResult``.

Only the CLI's testable core (``execute()``) is exercised here, against a
``LocalArtifactStore`` fixture built the same way
``test_lerobot_adapter.py``/the real interop E2E fixture are -- never
against real MinIO/Postgres (that is ``scripts/e2e/`` E2E territory, and
Request 4.2 §7's container smoke test). ``main()``'s argv/env/stdio glue is
intentionally not covered here.

Imports ``lerobot`` transitively (via ``LeRobotDatasetAdapter``) -- skipped
entirely, not erroring, when the ``lerobot`` extra isn't installed, exactly
like ``test_lerobot_adapter.py``.
"""

from __future__ import annotations

import hashlib
import json

import pytest

pytest.importorskip("lerobot")

from sceneops_analytics.external_adapters import (  # noqa: E402
    ExternalExportConfig,
    UnsupportedSemanticPolicy,
)
from sceneops_analytics.external_adapters.lerobot.entrypoint import (  # noqa: E402
    LeRobotContainerSettings,
    IntegrationRuntimeError,
    execute,
)
from sceneops_analytics.testing.interop_dataset import (  # noqa: E402
    INTEROP_DATASET_ID,
    INTEROP_DATASET_VERSION,
    INTEROP_FEATURE_PROJECTION,
    build_interop_test_dataset,
)
from sceneops_core.artifacts.schemas import (  # noqa: E402
    ArtifactBackend,
    ArtifactKind,
    ArtifactRef,
)
from sceneops_core.datasets.schemas.external import ExternalDatasetRef  # noqa: E402
from sceneops_core.integration_runtime import (  # noqa: E402
    CanonicalDatasetRef,
    IntegrationOperation,
    IntegrationRequest,
)


def _sha256_prefixed(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


async def _write_manifest_artifact(bootstrap, tmp_path) -> ArtifactRef:
    """Write the bootstrap's already-open learning manifest to a fresh URI
    in its own ArtifactStore, so it can be referenced by URI+checksum
    exactly as ``canonical_inputs["learning_manifest"]`` expects -- the
    same real-artifact-store round trip ``scripts/e2e/
    e2e_lerobot_resolve.py``/``e2e_lerobot_export.py`` do against MinIO,
    just against a ``LocalArtifactStore`` here."""
    uri = str(tmp_path / "canonical_inputs" / "learning_manifest.json")
    await bootstrap.artifact_store.write_json(
        uri, bootstrap.learning_manifest.model_dump(mode="json")
    )
    manifest_bytes = await bootstrap.artifact_store.read_bytes(uri)
    return ArtifactRef(
        kind=ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
        uri=uri,
        checksum=_sha256_prefixed(manifest_bytes),
    )


def _request(
    tmp_path, *, manifest_ref: ArtifactRef, config: dict | None = None, **overrides
) -> IntegrationRequest:
    defaults: dict = dict(
        operation=IntegrationOperation.EXPORT,
        external_ref=ExternalDatasetRef(
            format="lerobot",
            format_version="3.0",
            uri=str(tmp_path / "lerobot-out"),
            external_name="interop",
        ),
        canonical_ref=CanonicalDatasetRef(
            dataset_id=INTEROP_DATASET_ID, dataset_version=INTEROP_DATASET_VERSION
        ),
        canonical_inputs={"learning_manifest": manifest_ref},
        config=config
        if config is not None
        else ExternalExportConfig(
            projection=INTEROP_FEATURE_PROJECTION,
            unsupported_semantic_policy=UnsupportedSemanticPolicy.RECORD,
        ).model_dump(mode="json"),
    )
    defaults.update(overrides)
    return IntegrationRequest(**defaults)


async def test_execute_runs_a_real_lerobot_export_and_returns_a_result(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    manifest_ref = await _write_manifest_artifact(bootstrap, tmp_path)
    request = _request(tmp_path, manifest_ref=manifest_ref)

    result = await execute(request, artifact_store=bootstrap.artifact_store)

    assert result.operation is IntegrationOperation.EXPORT
    assert result.canonical_ref == request.canonical_ref
    assert result.external_ref.format == "lerobot"
    assert result.external_ref.uri == str(tmp_path / "lerobot-out")
    assert result.produced_artifacts == {}
    # result_metadata is dumped by_alias=True, consistent with the rest of
    # the camelCase IntegrationResult envelope.
    assert result.result_metadata["exportedEpisodeCount"] == 3
    assert result.result_metadata["exportedStepCount"] == 22


async def test_execute_rejects_ingest_operation(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    manifest_ref = await _write_manifest_artifact(bootstrap, tmp_path)
    # INGEST forbids the EXPORT-only non-empty canonical_inputs validation,
    # so build the request as EXPORT first, then flip the field directly --
    # this test targets execute()'s own format/operation check, not
    # IntegrationRequest's construction-time validator.
    request = _request(tmp_path, manifest_ref=manifest_ref).model_copy(
        update={"operation": IntegrationOperation.INGEST}
    )

    with pytest.raises(IntegrationRuntimeError, match="unsupported operation 'ingest'"):
        await execute(request, artifact_store=bootstrap.artifact_store)


async def test_execute_rejects_non_lerobot_format(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    manifest_ref = await _write_manifest_artifact(bootstrap, tmp_path)
    request = _request(tmp_path, manifest_ref=manifest_ref).model_copy(
        update={
            "external_ref": ExternalDatasetRef(
                format="rlds", format_version="1.0.0", uri=str(tmp_path / "out")
            )
        }
    )

    with pytest.raises(
        IntegrationRuntimeError, match="unsupported external_ref.format 'rlds'"
    ):
        await execute(request, artifact_store=bootstrap.artifact_store)


async def test_execute_requires_learning_manifest_canonical_input(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    manifest_ref = await _write_manifest_artifact(bootstrap, tmp_path)
    # A generic IntegrationRequest only requires *some* canonical_inputs
    # entry for EXPORT (Request 4.1A follow-up) -- it does not know the
    # "learning_manifest" key name, so a request naming a different key is
    # still a structurally valid IntegrationRequest. execute() is what
    # enforces the lerobot-specific key.
    request = _request(
        tmp_path,
        manifest_ref=manifest_ref,
        canonical_inputs={"something_else": manifest_ref},
    )

    with pytest.raises(
        IntegrationRuntimeError, match="canonical_inputs\\['learning_manifest'\\]"
    ):
        await execute(request, artifact_store=bootstrap.artifact_store)


async def test_execute_rejects_checksum_mismatch(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    manifest_ref = await _write_manifest_artifact(bootstrap, tmp_path)
    corrupted_ref = manifest_ref.model_copy(update={"checksum": "sha256:" + "0" * 64})
    request = _request(tmp_path, manifest_ref=corrupted_ref)

    with pytest.raises(IntegrationRuntimeError, match="checksum mismatch"):
        await execute(request, artifact_store=bootstrap.artifact_store)


async def test_execute_rejects_invalid_config(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    manifest_ref = await _write_manifest_artifact(bootstrap, tmp_path)
    request = _request(tmp_path, manifest_ref=manifest_ref, config={})

    with pytest.raises(IntegrationRuntimeError, match="invalid config"):
        await execute(request, artifact_store=bootstrap.artifact_store)


async def test_execute_never_duplicates_the_export_target_as_an_artifact_ref(
    tmp_path,
):
    """Request 4.1A §5 / 4.2 §2: the LeRobot dataset root is fully
    identified by ``external_ref`` -- never re-expressed as a
    ``produced_artifacts`` entry pointing at the same location."""
    bootstrap = await build_interop_test_dataset(tmp_path)
    manifest_ref = await _write_manifest_artifact(bootstrap, tmp_path)
    request = _request(tmp_path, manifest_ref=manifest_ref)

    result = await execute(request, artifact_store=bootstrap.artifact_store)

    assert result.produced_artifacts == {}


async def test_execute_never_deletes_a_preexisting_target(tmp_path):
    """SceneOps V2 Request 4.2 follow-up §1: this runtime never deletes a
    caller-owned external_ref.uri target -- a pre-existing directory (even
    an unrelated one that happens to sit at the same path) is left exactly
    as it was, and the export fails clearly instead of silently clearing
    it."""
    bootstrap = await build_interop_test_dataset(tmp_path)
    manifest_ref = await _write_manifest_artifact(bootstrap, tmp_path)
    request = _request(tmp_path, manifest_ref=manifest_ref)

    export_root = tmp_path / "lerobot-out"
    export_root.mkdir()
    sentinel = export_root / "caller-owned-file.txt"
    sentinel.write_text("do not touch")

    with pytest.raises(IntegrationRuntimeError, match="already exists"):
        await execute(request, artifact_store=bootstrap.artifact_store)

    assert sentinel.exists()
    assert sentinel.read_text() == "do not touch"


async def test_execute_fails_clearly_on_a_rerun_against_the_same_target(tmp_path):
    """A second execute() against the same external_ref.uri fails clearly
    rather than silently overwriting -- idempotent reruns are the test
    orchestrator's responsibility (scripts/e2e/lerobot_container_smoke.sh),
    never this runtime's (SceneOps V2 Request 4.2 follow-up §1)."""
    bootstrap = await build_interop_test_dataset(tmp_path)
    manifest_ref = await _write_manifest_artifact(bootstrap, tmp_path)
    request = _request(tmp_path, manifest_ref=manifest_ref)

    first = await execute(request, artifact_store=bootstrap.artifact_store)
    assert first.result_metadata["exportedEpisodeCount"] == 3

    with pytest.raises(IntegrationRuntimeError, match="already exists"):
        await execute(request, artifact_store=bootstrap.artifact_store)


async def test_execute_result_round_trips_through_json(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    manifest_ref = await _write_manifest_artifact(bootstrap, tmp_path)
    request = _request(tmp_path, manifest_ref=manifest_ref)

    result = await execute(request, artifact_store=bootstrap.artifact_store)

    payload = json.loads(result.model_dump_json(by_alias=True))
    assert payload["canonicalRef"]["datasetId"] == INTEROP_DATASET_ID
    assert payload["operation"] == "export"


# ── environment-driven ArtifactStore backend (SceneOps V2 Request 4.2 ──────
# follow-up §2) -- LeRobotContainerSettings, never a hardcoded backend in
# this LeRobot-specific module.


def test_container_settings_are_environment_driven(monkeypatch):
    monkeypatch.setenv("SCENEOPS_INTEGRATION_ARTIFACT__BACKEND", "minio")
    monkeypatch.setenv(
        "SCENEOPS_INTEGRATION_ARTIFACT__ROOT_URI", "s3://sceneops/artifacts"
    )
    monkeypatch.setenv(
        "SCENEOPS_INTEGRATION_ARTIFACT__ENDPOINT_URL", "http://minio:9000"
    )
    monkeypatch.setenv("SCENEOPS_INTEGRATION_ARTIFACT__ACCESS_KEY_ID", "an-id")
    monkeypatch.setenv("SCENEOPS_INTEGRATION_ARTIFACT__SECRET_ACCESS_KEY", "a-secret")

    settings = LeRobotContainerSettings()

    assert settings.artifact.backend is ArtifactBackend.MINIO
    assert settings.artifact.root_uri == "s3://sceneops/artifacts"
    assert settings.artifact.endpoint_url == "http://minio:9000"
    assert settings.artifact.access_key_id == "an-id"
    assert settings.artifact.secret_access_key == "a-secret"


def test_container_settings_default_to_local_backend_without_env(monkeypatch):
    for var in (
        "SCENEOPS_INTEGRATION_ARTIFACT__BACKEND",
        "SCENEOPS_INTEGRATION_ARTIFACT__ROOT_URI",
        "SCENEOPS_INTEGRATION_ARTIFACT__ENDPOINT_URL",
        "SCENEOPS_INTEGRATION_ARTIFACT__ACCESS_KEY_ID",
        "SCENEOPS_INTEGRATION_ARTIFACT__SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = LeRobotContainerSettings()

    # ArtifactSettings' own default -- never a LeRobot/MinIO-specific
    # default reintroduced in this module.
    assert settings.artifact.backend is ArtifactBackend.LOCAL
