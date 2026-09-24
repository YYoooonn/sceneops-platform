"""Tests for the external-integration-runtime execution contract (SceneOps
V2 Request 4.1, reference semantics cleaned up in Request 4.1A):
IntegrationRequest/IntegrationResult, the frozen input/output shape for
running one external dataset integration (nuScenes ingest, LeRobot export,
...) outside the main SceneOps runtime.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.datasets import ExternalDatasetRef
from sceneops_core.integration_runtime import (
    CanonicalDatasetRef,
    IntegrationOperation,
    IntegrationRequest,
    IntegrationResult,
)

NUSCENES_REF = ExternalDatasetRef(
    format="nuscenes", format_version="v1.0-mini", uri="/data/raw/nuscenes"
)
LEROBOT_REF = ExternalDatasetRef(
    format="lerobot",
    format_version="3.0",
    uri="s3://sceneops/exports/lerobot/test-e2e-core",
    external_name="test-e2e-core",
)


def _manifest_ref(
    uri: str = "s3://sceneops/artifacts/learning/export.json",
) -> ArtifactRef:
    return ArtifactRef(
        kind=ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
        uri=uri,
        checksum="sha256:" + "a" * 64,
    )


def _raw_log_manifest_ref() -> ArtifactRef:
    return ArtifactRef(
        kind=ArtifactKind.RAW_LOG_MANIFEST,
        uri="s3://sceneops/artifacts/raw/nuscenes-mini/v1/manifest.json",
        checksum="sha256:" + "b" * 64,
    )


def _raw_log_frame_index_ref() -> ArtifactRef:
    return ArtifactRef(
        kind=ArtifactKind.RAW_LOG_FRAME_INDEX,
        uri="s3://sceneops/artifacts/raw/nuscenes-mini/v1/frame_index.json",
        checksum="sha256:" + "c" * 64,
    )


# ── identity-only CanonicalDatasetRef ───────────────────────────────────────


def test_canonical_dataset_ref_is_identity_only():
    """Request 4.1A: CanonicalDatasetRef answers 'which DatasetVersion'
    only -- it has no artifact-pointer field at all."""
    ref = CanonicalDatasetRef(dataset_id="interop", dataset_version="v1")
    assert CanonicalDatasetRef.model_fields.keys() == {"dataset_id", "dataset_version"}
    assert ref.dataset_id == "interop"


# ── direction model ──────────────────────────────────────────────────────────


def test_export_requires_at_least_one_canonical_input():
    """EXPORT reads an already-existing canonical artifact -- mirrors
    e2e_lerobot_resolve.py resolving a real manifest_uri/checksum *before*
    the isolated LeRobot process ever runs."""
    with pytest.raises(
        ValidationError, match="EXPORT requires at least one canonical_inputs entry"
    ):
        IntegrationRequest(
            operation=IntegrationOperation.EXPORT,
            external_ref=LEROBOT_REF,
            canonical_ref=CanonicalDatasetRef(
                dataset_id="interop", dataset_version="v1"
            ),
        )


def test_export_request_with_canonical_input_is_valid():
    request = IntegrationRequest(
        operation=IntegrationOperation.EXPORT,
        external_ref=LEROBOT_REF,
        canonical_ref=CanonicalDatasetRef(dataset_id="interop", dataset_version="v1"),
        canonical_inputs={"learning_manifest": _manifest_ref()},
    )
    assert request.operation is IntegrationOperation.EXPORT
    assert request.canonical_inputs["learning_manifest"].kind == (
        ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST
    )


def test_ingest_with_canonical_inputs_is_also_valid():
    """INGEST may legitimately consume existing canonical context
    (calibration, schema, ...) while still producing new canonical
    artifacts -- nuScenes ingestion happening to need none today is an
    implementation detail, not a universal runtime constraint (Request
    4.1A follow-up)."""
    request = IntegrationRequest(
        operation=IntegrationOperation.INGEST,
        external_ref=NUSCENES_REF,
        canonical_ref=CanonicalDatasetRef(
            dataset_id="nuscenes-mini", dataset_version="v1"
        ),
        canonical_inputs={"calibration": _manifest_ref()},
    )
    assert request.canonical_inputs["calibration"] == _manifest_ref()


def test_ingest_request_without_canonical_inputs_is_valid():
    request = IntegrationRequest(
        operation=IntegrationOperation.INGEST,
        external_ref=NUSCENES_REF,
        canonical_ref=CanonicalDatasetRef(
            dataset_id="nuscenes-mini", dataset_version="v1"
        ),
    )
    assert request.canonical_inputs == {}


def test_ingest_result_requires_at_least_one_produced_artifact():
    with pytest.raises(
        ValidationError, match="INGEST result requires at least one produced_artifacts"
    ):
        IntegrationResult(
            operation=IntegrationOperation.INGEST,
            external_ref=NUSCENES_REF,
            canonical_ref=CanonicalDatasetRef(
                dataset_id="nuscenes-mini", dataset_version="v1"
            ),
        )


def test_export_result_may_have_no_produced_artifacts():
    """Request 4.1A §5/§7: a LeRobot dataset root is fully identified by
    external_ref -- normally nothing new lands in the SceneOps
    ArtifactStore, so produced_artifacts is allowed to stay empty."""
    result = IntegrationResult(
        operation=IntegrationOperation.EXPORT,
        external_ref=LEROBOT_REF,
        canonical_ref=CanonicalDatasetRef(dataset_id="interop", dataset_version="v1"),
    )
    assert result.produced_artifacts == {}


# ── reference-type separation ───────────────────────────────────────────────


def test_external_output_is_not_duplicated_as_an_artifact_ref():
    """LeRobot's export target is represented exactly once, as
    external_ref -- never re-expressed as an ArtifactRef pointing at the
    same location under a second name (Request 4.1A §5)."""
    result = IntegrationResult(
        operation=IntegrationOperation.EXPORT,
        external_ref=LEROBOT_REF,
        canonical_ref=CanonicalDatasetRef(dataset_id="interop", dataset_version="v1"),
    )
    assert result.external_ref.uri == LEROBOT_REF.uri
    assert all(ref.uri != LEROBOT_REF.uri for ref in result.produced_artifacts.values())


def test_canonical_ref_never_carries_an_artifact_pointer():
    """CanonicalDatasetRef, ExternalDatasetRef, and ArtifactRef stay three
    distinct reference kinds -- a "manifest"-shaped keyword has no field to
    land in and is silently dropped, never accepted as part of canonical
    identity."""
    ref = CanonicalDatasetRef(
        dataset_id="interop",
        dataset_version="v1",
        manifest=_manifest_ref().model_dump(),  # type: ignore[call-arg]
    )
    assert not hasattr(ref, "manifest")
    assert ref.model_dump() == {"dataset_id": "interop", "dataset_version": "v1"}


# ── input/output ArtifactRef preservation ───────────────────────────────────


def test_ingest_result_preserves_both_produced_artifact_refs():
    result = IntegrationResult(
        operation=IntegrationOperation.INGEST,
        external_ref=NUSCENES_REF,
        canonical_ref=CanonicalDatasetRef(
            dataset_id="nuscenes-mini", dataset_version="v1"
        ),
        produced_artifacts={
            "raw_log_manifest": _raw_log_manifest_ref(),
            "raw_log_frame_index": _raw_log_frame_index_ref(),
        },
    )
    assert result.produced_artifacts["raw_log_manifest"].kind == (
        ArtifactKind.RAW_LOG_MANIFEST
    )
    assert result.produced_artifacts["raw_log_frame_index"].kind == (
        ArtifactKind.RAW_LOG_FRAME_INDEX
    )


def test_export_request_preserves_the_learning_manifest_input():
    manifest = _manifest_ref()
    request = IntegrationRequest(
        operation=IntegrationOperation.EXPORT,
        external_ref=LEROBOT_REF,
        canonical_ref=CanonicalDatasetRef(dataset_id="interop", dataset_version="v1"),
        canonical_inputs={"learning_manifest": manifest},
    )
    assert request.canonical_inputs["learning_manifest"] == manifest


# ── ExternalDatasetRef / identity preservation ──────────────────────────────


def test_ingest_result_echoes_external_source():
    result = IntegrationResult(
        operation=IntegrationOperation.INGEST,
        external_ref=NUSCENES_REF,
        canonical_ref=CanonicalDatasetRef(
            dataset_id="nuscenes-mini", dataset_version="v1"
        ),
        produced_artifacts={"raw_log_manifest": _raw_log_manifest_ref()},
    )
    assert result.external_ref == NUSCENES_REF
    assert result.canonical_ref.dataset_id == "nuscenes-mini"


def test_same_external_dataset_ref_shape_covers_both_directions():
    """Direction belongs to IntegrationRequest.operation, never to a
    separate source/export ref type (SceneOps V2 Request 4.1 §3, reusing
    ExternalDatasetRef's own Request 3.2B invariant)."""
    ingest = IntegrationRequest(
        operation=IntegrationOperation.INGEST,
        external_ref=NUSCENES_REF,
        canonical_ref=CanonicalDatasetRef(dataset_id="d", dataset_version="v1"),
    )
    export = IntegrationRequest(
        operation=IntegrationOperation.EXPORT,
        external_ref=LEROBOT_REF,
        canonical_ref=CanonicalDatasetRef(dataset_id="d", dataset_version="v1"),
        canonical_inputs={"learning_manifest": _manifest_ref()},
    )
    assert type(ingest.external_ref) is type(export.external_ref)


# ── deterministic serialization ──────────────────────────────────────────────


def test_request_round_trips_through_json():
    request = IntegrationRequest(
        operation=IntegrationOperation.EXPORT,
        external_ref=LEROBOT_REF,
        canonical_ref=CanonicalDatasetRef(dataset_id="interop", dataset_version="v1"),
        canonical_inputs={"learning_manifest": _manifest_ref()},
        config={"projection": {"observation_channels": ["joint_state"]}},
        metadata={"requested_by": "e2e-lerobot-roundtrip"},
    )
    payload = json.loads(request.model_dump_json(by_alias=True))
    restored = IntegrationRequest.model_validate(payload)
    assert restored == request


def test_result_round_trips_through_json():
    result = IntegrationResult(
        operation=IntegrationOperation.INGEST,
        external_ref=NUSCENES_REF,
        canonical_ref=CanonicalDatasetRef(
            dataset_id="nuscenes-mini", dataset_version="v1"
        ),
        produced_artifacts={
            "raw_log_manifest": _raw_log_manifest_ref(),
            "raw_log_frame_index": _raw_log_frame_index_ref(),
        },
        result_metadata={"frame_count": 1200, "sequence_count": 10},
    )
    payload = json.loads(result.model_dump_json(by_alias=True))
    restored = IntegrationResult.model_validate(payload)
    assert restored == result


def test_serialization_is_stable_across_repeated_dumps():
    request = IntegrationRequest(
        operation=IntegrationOperation.INGEST,
        external_ref=NUSCENES_REF,
        canonical_ref=CanonicalDatasetRef(dataset_id="d", dataset_version="v1"),
        config={"max_source_sequences": 2},
    )
    assert request.model_dump_json() == request.model_dump_json()


# ── no SDK dependency ────────────────────────────────────────────────────────


def test_integration_runtime_module_imports_no_lerobot_or_nuscenes_symbols():
    """sceneops-core must stay importable from both the main workspace venv
    and tools/lerobot-integration's isolated venv -- and vice versa, from a
    hypothetical future nuScenes-only runtime that never installs lerobot.
    Reusing this contract must never pull in either SDK.

    Checked in a fresh subprocess, never via ``sys.modules`` in the current
    test process -- ``make test`` runs this alongside
    ``apps/worker/tests/`` in one pytest session, which imports
    ``nuscenes-devkit`` itself for unrelated tests, so ``sys.modules`` here
    would already be polluted regardless of what this module itself
    imports.
    """
    import subprocess
    import sys

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import sceneops_core.integration_runtime; "
            "loaded = set(sys.modules); "
            "assert not any(n.startswith('lerobot') for n in loaded); "
            "assert not any(n.startswith('nuscenes') for n in loaded)",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


# ── missing required references ─────────────────────────────────────────────


def test_missing_external_ref_is_rejected():
    with pytest.raises(ValidationError):
        IntegrationRequest.model_validate(
            {
                "operation": "ingest",
                "canonicalRef": {"datasetId": "d", "datasetVersion": "v1"},
            }
        )


def test_missing_canonical_ref_is_rejected():
    with pytest.raises(ValidationError):
        IntegrationRequest.model_validate(
            {
                "operation": "ingest",
                "externalRef": NUSCENES_REF.model_dump(by_alias=True, mode="json"),
            }
        )


def test_unknown_operation_is_rejected():
    with pytest.raises(ValidationError):
        IntegrationRequest.model_validate(
            {
                "operation": "sync",
                "externalRef": NUSCENES_REF.model_dump(by_alias=True, mode="json"),
                "canonicalRef": {"datasetId": "d", "datasetVersion": "v1"},
            }
        )
