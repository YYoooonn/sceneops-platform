"""Container/CLI entrypoint executing one frozen ``IntegrationRequest``
against ``LeRobotDatasetAdapter`` and returning an ``IntegrationResult``
(SceneOps V2 Request 4.2).

::

    IntegrationRequest JSON (file)
            -> execute()
            -> SceneOpsDataset.open()        (canonical_inputs["learning_manifest"])
            -> LeRobotDatasetAdapter.export()
            -> IntegrationResult JSON (stdout, optionally also a file)

This module is the container's one entrypoint, not a second implementation
of anything Request 3.1/3.1A/3.3 already froze: it only resolves the
generic ``IntegrationRequest``/``IntegrationResult`` contract (SceneOps V2
Request 4.1/4.1A, ``sceneops_core.integration_runtime``) into calls against
the existing, unchanged ``LeRobotDatasetAdapter``/``ExternalExportConfig``/
``SceneOpsDataset.open()``. Feature mapping, FPS derivation, timestamp
semantics, semantic-loss classification, and the write lifecycle all still
live exactly where Request 3.1-3.4 put them.

Supports only ``operation=EXPORT`` / ``external_ref.format="lerobot"`` for
now (Request 4.2 §2) -- anything else is rejected clearly, before any
ArtifactStore access.

Runtime ownership (Request 4.2 §4): this module may use the LeRobot SDK,
``sceneops-core``/``sceneops-storage``/``sceneops-analytics``, and
``ArtifactStore`` access. It never opens a DB session, never imports
``sceneops-db``, and never registers an ``ArtifactRecord`` or mutates a
``DatasetVersion`` -- the main platform remains solely responsible for
that, using this process's ``IntegrationResult.produced_artifacts``.

Transport (Request 4.2 §3): a JSON ``IntegrationRequest`` file in
(``--request-file``), a JSON ``IntegrationResult`` on stdout on success
(and optionally also written to ``--output-file``) -- fully serializable,
no DB objects or in-process Python object sharing, trivial to invoke via
``docker run``. Failure is a non-zero exit code plus a clear stderr
message, never a caught error serialized as "success" -- the same
convention every ``scripts/e2e/e2e_lerobot_*.py`` script already uses.

ArtifactStore backend/credentials (Request 4.2 follow-up §2) are selected
entirely from environment variables, via the same ``pydantic_settings.
BaseSettings`` + ``sceneops_core.config.ArtifactSettings`` pattern
``apps/worker/sceneops_worker/config.py``'s ``WorkerSettings`` already
uses -- never hardcoded to one backend (MinIO) in this LeRobot-specific
module, and never a field on ``IntegrationRequest``. See
``LeRobotContainerSettings`` below; e.g.::

    SCENEOPS_INTEGRATION_ARTIFACT__BACKEND=minio
    SCENEOPS_INTEGRATION_ARTIFACT__ROOT_URI=s3://sceneops/artifacts
    SCENEOPS_INTEGRATION_ARTIFACT__ENDPOINT_URL=http://minio:9000
    SCENEOPS_INTEGRATION_ARTIFACT__ACCESS_KEY_ID=minioadmin
    SCENEOPS_INTEGRATION_ARTIFACT__SECRET_ACCESS_KEY=minioadmin

This container never deletes a caller-owned ``external_ref.uri`` target
(Request 4.2 follow-up §1): it attempts the export and fails clearly (a
wrapped ``IntegrationRuntimeError``) if the target already exists. Cleaning
a known test-owned output directory between reruns is the test
orchestrator's job (``scripts/e2e/lerobot_container_smoke.sh``), never this
runtime's.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import traceback
from pathlib import Path

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.config import ArtifactSettings
from sceneops_core.episodes.learning_export import LearningDataExportManifest
from sceneops_core.integration_runtime import (
    IntegrationOperation,
    IntegrationRequest,
    IntegrationResult,
)
from sceneops_analytics.external_adapters.schemas import ExternalExportConfig
from sceneops_analytics.learning_dataset import SceneOpsDataset
from sceneops_storage.factory import create_artifact_store

from .adapter import LeRobotDatasetAdapter

SUPPORTED_OPERATION = IntegrationOperation.EXPORT
SUPPORTED_FORMAT = "lerobot"

LEARNING_MANIFEST_INPUT_KEY = "learning_manifest"


class IntegrationRuntimeError(RuntimeError):
    """One request/execution this container refuses or fails to complete.
    Always caught at the top level (``main``) and reported as a clear,
    single-line stderr message plus a non-zero exit -- never a bare
    traceback, never a partially-written ``IntegrationResult``."""


class LeRobotContainerSettings(BaseSettings):
    """This container's only environment-driven configuration surface: the
    ArtifactStore backend (Request 4.2 follow-up §2). Reuses
    ``sceneops_core.config.ArtifactSettings`` unchanged -- backend
    selection lives there and in ``sceneops_storage.factory.
    create_artifact_store``, never re-implemented here, exactly like
    ``WorkerSettings``/``ApiSettings`` already do for the main platform."""

    model_config = SettingsConfigDict(
        env_file=[".env.local", ".env"],
        env_prefix="SCENEOPS_INTEGRATION_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    artifact: ArtifactSettings = Field(default_factory=ArtifactSettings)


def _sha256_prefixed(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _build_artifact_store() -> ArtifactStore:
    """Backend/credentials come entirely from ``LeRobotContainerSettings``
    (environment variables) -- never hardcoded here, never a field on
    ``IntegrationRequest``."""
    return create_artifact_store(LeRobotContainerSettings().artifact)


def _check_supported(request: IntegrationRequest) -> None:
    if request.operation is not SUPPORTED_OPERATION:
        raise IntegrationRuntimeError(
            f"unsupported operation {request.operation.value!r}: this "
            f"container only executes operation={SUPPORTED_OPERATION.value!r}"
        )
    if request.external_ref.format != SUPPORTED_FORMAT:
        raise IntegrationRuntimeError(
            f"unsupported external_ref.format {request.external_ref.format!r}: "
            f"this container only executes format={SUPPORTED_FORMAT!r}"
        )


async def _open_canonical_dataset(
    request: IntegrationRequest, artifact_store: ArtifactStore
) -> SceneOpsDataset:
    manifest_ref = request.canonical_inputs.get(LEARNING_MANIFEST_INPUT_KEY)
    if manifest_ref is None:
        raise IntegrationRuntimeError(
            f"canonical_inputs[{LEARNING_MANIFEST_INPUT_KEY!r}] is required "
            "for a lerobot EXPORT"
        )

    manifest_bytes = await artifact_store.read_bytes(manifest_ref.uri)
    if manifest_ref.checksum is not None:
        actual_checksum = _sha256_prefixed(manifest_bytes)
        if actual_checksum != manifest_ref.checksum:
            raise IntegrationRuntimeError(
                f"canonical_inputs[{LEARNING_MANIFEST_INPUT_KEY!r}] checksum "
                f"mismatch for {manifest_ref.uri}: "
                f"expected={manifest_ref.checksum} actual={actual_checksum}"
            )
    else:
        actual_checksum = _sha256_prefixed(manifest_bytes)

    manifest = LearningDataExportManifest.model_validate(json.loads(manifest_bytes))
    return await SceneOpsDataset.open(
        learning_manifest=manifest,
        learning_manifest_checksum=manifest_ref.checksum or actual_checksum,
        artifact_store=artifact_store,
    )


def _resolve_export_root(uri: str) -> Path:
    """Never deletes anything (Request 4.2 follow-up §1) -- this runtime
    only ensures the target's *parent* exists (purely additive, required
    to write at all into a fresh nested mount path) and otherwise attempts
    the export exactly against ``external_ref.uri`` as given. A caller-
    owned target that already exists is the export's problem to fail on,
    not this function's to silently clear -- see ``execute()``'s own
    ``FileExistsError`` handling for that clear failure. Idempotent-rerun
    cleanup of a known test-owned directory belongs to the test
    orchestrator (``scripts/e2e/lerobot_container_smoke.sh``), never this
    runtime."""
    export_root = Path(uri)
    export_root.parent.mkdir(parents=True, exist_ok=True)
    return export_root


async def execute(
    request: IntegrationRequest, *, artifact_store: ArtifactStore
) -> IntegrationResult:
    """The testable core: given an already-validated ``IntegrationRequest``
    and an ``ArtifactStore`` (real or a ``LocalArtifactStore`` fixture),
    run the export and return the ``IntegrationResult``. No argv/env/stdio
    here -- those belong to ``main`` only."""
    _check_supported(request)

    dataset = await _open_canonical_dataset(request, artifact_store)

    try:
        config = ExternalExportConfig.model_validate(request.config)
    except ValidationError as exc:
        raise IntegrationRuntimeError(
            f"invalid config for a lerobot export: {exc}"
        ) from exc

    export_root = _resolve_export_root(request.external_ref.uri)
    repo_id = request.external_ref.external_name or export_root.name

    adapter = LeRobotDatasetAdapter(repo_id=repo_id, root=export_root)
    try:
        report = await adapter.export(dataset, config)
    except FileExistsError as exc:
        raise IntegrationRuntimeError(
            f"cannot write lerobot export target {str(export_root)!r}: it "
            "already exists. This container never deletes a caller-owned "
            "external_ref.uri target -- remove it (or target a fresh "
            "path) before retrying."
        ) from exc

    return IntegrationResult(
        operation=IntegrationOperation.EXPORT,
        external_ref=adapter.dataset_ref,
        canonical_ref=request.canonical_ref,
        produced_artifacts={},
        result_metadata=report.model_dump(mode="json", by_alias=True),
    )


async def _run(request_file: Path, output_file: Path | None) -> IntegrationResult:
    try:
        payload = json.loads(request_file.read_text())
    except OSError as exc:
        raise IntegrationRuntimeError(f"cannot read --request-file: {exc}") from exc

    try:
        request = IntegrationRequest.model_validate(payload)
    except ValidationError as exc:
        raise IntegrationRuntimeError(f"invalid IntegrationRequest: {exc}") from exc

    artifact_store = _build_artifact_store()
    result = await execute(request, artifact_store=artifact_store)

    result_json = result.model_dump_json(by_alias=True, indent=2)
    if output_file is not None:
        output_file.write_text(result_json)
    print(result_json)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request-file",
        required=True,
        type=Path,
        help="Path to a JSON IntegrationRequest (Request 4.1/4.1A shape).",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Optional path to also write the JSON IntegrationResult "
        "(always printed to stdout on success).",
    )
    args = parser.parse_args(argv)

    try:
        asyncio.run(_run(args.request_file, args.output_file))
    except IntegrationRuntimeError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- top-level: always fail clearly
        traceback.print_exc(file=sys.stderr)
        print(
            f"❌ lerobot integration container failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
