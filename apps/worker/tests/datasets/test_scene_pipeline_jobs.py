"""Tests for scene-first persistence in register_scene, validate_scene, profile_scene.

All tests are local: no Docker, no MinIO, no real DB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenes.schemas.enums import SceneStatus
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)
from sceneops_worker.jobs.dataset.profile_scene import ProfileSceneJobHandler
from sceneops_worker.jobs.dataset.register_scene import RegisterSceneJobHandler
from sceneops_worker.jobs.dataset.validate_scene import (
    ValidateSceneJobHandler,
    _per_scene_validation_run_id,
)
from sceneops_worker.scenes.validation.reports import (
    SceneValidationIssue,
    SceneValidationResult,
)


# ── helpers ───────────────────────────────────────────────────────────────────

SCENE_ID = "scene-001"
DATASET_ID = "nuscenes"
DATASET_VERSION = "v1.0-mini"
JOB_ID = "job-abc123"
PIPELINE_RUN_ID = "pipe-xyz"
MANIFEST_URI = f"file:///scenes/{SCENE_ID}/manifest.json"


def _job(scene_manifest_uris: list[str] | None = None) -> MagicMock:
    j = MagicMock()
    j.job_id = JOB_ID
    j.pipeline_run_id = PIPELINE_RUN_ID
    j.pipeline_task_run_id = "ptask-001"
    j.params = {
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "scene_manifest_uris": scene_manifest_uris or [MANIFEST_URI],
    }
    return j


def _scene_manifest(
    scene_id: str = SCENE_ID,
    sample_count: int = 40,
    frame_count: int = 80,
    annotation_count: int = 50,
    channels: list[str] | None = None,
) -> MagicMock:
    m = MagicMock()
    m.scene_id = scene_id
    m.dataset_id = DATASET_ID
    m.dataset_version = DATASET_VERSION
    m.sample_count = sample_count
    m.frame_count = frame_count
    m.annotation_count = annotation_count
    m.channels = channels or ["CAM_FRONT", "LIDAR_TOP"]
    m.has_ground_truth = annotation_count > 0
    m.ground_truth_source = "nuscenes" if annotation_count > 0 else None
    m.metadata = {}
    m.calibrated_sensors = []
    m.ego_poses = []
    m.samples = []
    return m


def _scene_record(
    scene_id: str = SCENE_ID,
    status: SceneStatus = SceneStatus.BUILT,
) -> SceneRecord:
    return SceneRecord(
        scene_id=scene_id,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        status=status,
        sample_count=40,
        frame_count=80,
        channels=["CAM_FRONT", "LIDAR_TOP"],
    )


def _context(
    scene_manifest: MagicMock | None = None,
    existing_scene: SceneRecord | None = None,
) -> MagicMock:
    ctx = MagicMock()

    ctx.scene_artifact_store = MagicMock()
    ctx.scene_artifact_store.load_scene_manifest = AsyncMock(
        return_value=scene_manifest or _scene_manifest()
    )

    upserted: list[SceneRecord] = []

    async def upsert_scene(record: SceneRecord) -> SceneRecord:
        upserted.append(record)
        return record

    ctx.scene_store = MagicMock()
    ctx.scene_store.get = AsyncMock(return_value=existing_scene)
    ctx.scene_store.upsert = upsert_scene
    ctx._upserted_scenes = upserted

    created_artifacts: list[dict] = []

    async def create_artifact(**kwargs) -> MagicMock:
        created_artifacts.append(kwargs)
        return MagicMock()

    ctx.artifact_record_store = MagicMock()
    ctx.artifact_record_store.create = create_artifact
    ctx._created_artifacts = created_artifacts

    upserted_runs: list = []

    async def upsert_run(run) -> object:
        upserted_runs.append(run)
        return run

    ctx.runs = MagicMock()
    ctx.runs.scene_runs = MagicMock()
    ctx.runs.scene_runs.upsert = upsert_run
    ctx._upserted_runs = upserted_runs

    ctx.artifact_store = MagicMock()
    ctx.artifact_store.join_uri = lambda *parts: "/".join(parts)
    ctx.artifact_store.write_json = AsyncMock()

    ctx.settings = MagicMock()
    ctx.settings.run_root_uri = "file:///runs"

    ctx.dataset_store = MagicMock()
    ctx.dataset_store.update_quality_cache = AsyncMock()

    ctx.commit = AsyncMock()

    return ctx


def _params(uris: list[str] | None = None, **extra) -> MagicMock:
    p = MagicMock()
    p.scene_manifest_uris = uris or [MANIFEST_URI]
    p.scene_manifest_uri = None
    p.require_target_channels = []
    p.sample_validation = MagicMock()
    p.sample_validation.validate_samples = False
    p.sample_validation.block_on_sample_missing_channels = False
    p.dataset_id = DATASET_ID
    p.dataset_version = DATASET_VERSION
    p.replace_existing = False
    p.origin_type = "real"
    p.generation_method = "unknown"
    for k, v in extra.items():
        setattr(p, k, v)
    return p


# ── register_scene ────────────────────────────────────────────────────────────


async def test_register_scene_registers_scene_manifest_artifact():
    ctx = _context()
    handler = RegisterSceneJobHandler()
    job = _job()
    params = _params()
    request = MagicMock()
    request.params = params
    request.context = ctx
    request.job = job

    with patch(
        "sceneops_worker.jobs.dataset.register_scene._build_scene_record_from_manifest",
        return_value=_scene_record(),
    ):
        await handler.run(request)

    artifacts = ctx._created_artifacts
    assert any(
        a.get("ref") is not None
        and getattr(a["ref"], "kind", None) == ArtifactKind.SCENE_MANIFEST
        for a in artifacts
    ), "Expected SCENE_MANIFEST artifact to be registered"


async def test_register_scene_artifact_has_scene_id_and_owner():
    ctx = _context()
    handler = RegisterSceneJobHandler()
    job = _job()
    params = _params()
    request = MagicMock()
    request.params = params
    request.context = ctx
    request.job = job

    with patch(
        "sceneops_worker.jobs.dataset.register_scene._build_scene_record_from_manifest",
        return_value=_scene_record(),
    ):
        await handler.run(request)

    scene_artifacts = [
        a
        for a in ctx._created_artifacts
        if getattr(a.get("ref"), "kind", None) == ArtifactKind.SCENE_MANIFEST
    ]
    assert len(scene_artifacts) == 1
    a = scene_artifacts[0]
    assert a["scene_id"] == SCENE_ID
    assert a["owner_type"] == ArtifactOwnerType.SCENE
    assert a["owner_id"] == SCENE_ID


# ── validate_scene ────────────────────────────────────────────────────────────


def _validation_result(
    scene_id: str = SCENE_ID,
    should_block: bool = False,
    blocking_issues: int = 0,
    warning_issues: int = 0,
) -> SceneValidationResult:
    issues = []
    for _ in range(blocking_issues):
        issues.append(
            SceneValidationIssue(type="missing_channel", message="x", blocking=True)
        )
    for _ in range(warning_issues):
        issues.append(
            SceneValidationIssue(
                type="missing_calibration_ref", message="y", blocking=False
            )
        )
    status = (
        "failed" if should_block else ("warning" if warning_issues > 0 else "ready")
    )
    return SceneValidationResult(
        scene_id=scene_id,
        status=status,
        should_block=should_block,
        required_channels=[],
        observed_channels=["CAM_FRONT"],
        missing_channels=[],
        sample_count=40,
        frame_count=80,
        issues=issues,
    )


def _validate_params(uris: list[str] | None = None) -> MagicMock:
    p = MagicMock()
    p.scene_manifest_uris = uris or [MANIFEST_URI]
    p.scene_manifest_uri = None
    p.require_target_channels = []
    p.sample_validation = MagicMock()
    p.sample_validation.validate_samples = False
    p.sample_validation.block_on_sample_missing_channels = False
    return p


def _initial_validation_record() -> SceneValidationRunRecord:
    return SceneValidationRunRecord(
        run_id=f"val-{JOB_ID.removeprefix('job-')}",
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        status=RunStatus.RUNNING,
        pipeline_run_id=PIPELINE_RUN_ID,
        job_id=JOB_ID,
    )


async def test_validate_scene_per_scene_run_record_has_scene_id():
    ctx = _context(existing_scene=_scene_record())

    with patch(
        "sceneops_worker.jobs.dataset.validate_scene.SceneManifestValidator.validate",
        return_value=_validation_result(should_block=False),
    ):
        handler = ValidateSceneJobHandler()
        await handler.execute(
            job=_job(),
            params=_validate_params(),
            context=ctx,
            initial_record=_initial_validation_record(),
            started_at=MagicMock(),
        )

    per_scene_runs = [
        r
        for r in ctx._upserted_runs
        if isinstance(r, SceneValidationRunRecord) and r.scene_id == SCENE_ID
    ]
    assert (
        len(per_scene_runs) >= 1
    ), "Expected per-scene validation run record with scene_id"


async def test_validate_scene_scene_status_set_to_validated_on_pass():
    ctx = _context(existing_scene=_scene_record(status=SceneStatus.BUILT))

    with patch(
        "sceneops_worker.jobs.dataset.validate_scene.SceneManifestValidator.validate",
        return_value=_validation_result(should_block=False),
    ):
        handler = ValidateSceneJobHandler()
        await handler.execute(
            job=_job(),
            params=_validate_params(),
            context=ctx,
            initial_record=_initial_validation_record(),
            started_at=MagicMock(),
        )

    updated = [s for s in ctx._upserted_scenes if s.status == SceneStatus.VALIDATED]
    assert (
        len(updated) >= 1
    ), "Scene status should be VALIDATED after passing validation"


async def test_validate_scene_scene_status_set_to_failed_on_block():
    ctx = _context(existing_scene=_scene_record(status=SceneStatus.BUILT))

    with patch(
        "sceneops_worker.jobs.dataset.validate_scene.SceneManifestValidator.validate",
        return_value=_validation_result(should_block=True, blocking_issues=1),
    ):
        handler = ValidateSceneJobHandler()
        await handler.execute(
            job=_job(),
            params=_validate_params(),
            context=ctx,
            initial_record=_initial_validation_record(),
            started_at=MagicMock(),
        )

    failed = [s for s in ctx._upserted_scenes if s.status == SceneStatus.FAILED]
    assert len(failed) >= 1, "Scene status should be FAILED after blocking validation"


async def test_validate_scene_warning_status_for_non_blocking_issues():
    ctx = _context(existing_scene=_scene_record())

    with patch(
        "sceneops_worker.jobs.dataset.validate_scene.SceneManifestValidator.validate",
        return_value=_validation_result(should_block=False, warning_issues=2),
    ):
        handler = ValidateSceneJobHandler()
        result_record, job_result = await handler.execute(
            job=_job(),
            params=_validate_params(),
            context=ctx,
            initial_record=_initial_validation_record(),
            started_at=MagicMock(),
        )

    assert job_result.status == "warning"
    per_scene = [
        r
        for r in ctx._upserted_runs
        if isinstance(r, SceneValidationRunRecord) and r.scene_id == SCENE_ID
    ]
    assert per_scene[0].validation_status == "warning"
    assert per_scene[0].warning_count == 2
    assert per_scene[0].error_count == 0


async def test_validate_scene_warning_and_error_counts_separated():
    ctx = _context(existing_scene=_scene_record())

    with patch(
        "sceneops_worker.jobs.dataset.validate_scene.SceneManifestValidator.validate",
        return_value=_validation_result(
            should_block=True, blocking_issues=1, warning_issues=3
        ),
    ):
        handler = ValidateSceneJobHandler()
        await handler.execute(
            job=_job(),
            params=_validate_params(),
            context=ctx,
            initial_record=_initial_validation_record(),
            started_at=MagicMock(),
        )

    per_scene = [
        r
        for r in ctx._upserted_runs
        if isinstance(r, SceneValidationRunRecord) and r.scene_id == SCENE_ID
    ]
    assert per_scene[0].error_count == 1
    assert per_scene[0].warning_count == 3
    assert per_scene[0].issue_count == 4


async def test_validate_scene_artifact_has_scene_id():
    ctx = _context(existing_scene=_scene_record())

    with patch(
        "sceneops_worker.jobs.dataset.validate_scene.SceneManifestValidator.validate",
        return_value=_validation_result(),
    ):
        handler = ValidateSceneJobHandler()
        await handler.execute(
            job=_job(),
            params=_validate_params(),
            context=ctx,
            initial_record=_initial_validation_record(),
            started_at=MagicMock(),
        )

    # The per-scene artifact should have scene_id set
    scene_artifacts = [
        a for a in ctx._created_artifacts if a.get("scene_id") == SCENE_ID
    ]
    assert len(scene_artifacts) >= 1, "Validation report artifact should carry scene_id"


# ── profile_scene ─────────────────────────────────────────────────────────────


def _initial_profile_record() -> SceneProfileRunRecord:
    return SceneProfileRunRecord(
        run_id=f"profile-{JOB_ID.removeprefix('job-')}",
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        status=RunStatus.RUNNING,
        pipeline_run_id=PIPELINE_RUN_ID,
        job_id=JOB_ID,
    )


def _profile_params(uris: list[str] | None = None) -> MagicMock:
    p = MagicMock()
    p.scene_manifest_uris = uris or [MANIFEST_URI]
    p.scene_manifest_uri = None
    return p


async def test_profile_scene_per_scene_run_record_has_scene_id():
    ctx = _context(existing_scene=_scene_record(status=SceneStatus.VALIDATED))

    handler = ProfileSceneJobHandler()
    await handler.execute(
        job=_job(),
        params=_profile_params(),
        context=ctx,
        initial_record=_initial_profile_record(),
        started_at=MagicMock(),
    )

    per_scene = [
        r
        for r in ctx._upserted_runs
        if isinstance(r, SceneProfileRunRecord) and r.scene_id == SCENE_ID
    ]
    assert len(per_scene) >= 1, "Expected per-scene profile run record with scene_id"


async def test_profile_scene_per_scene_record_has_annotation_count():
    ctx = _context(existing_scene=_scene_record(status=SceneStatus.VALIDATED))
    manifest = _scene_manifest(annotation_count=50)
    ctx.scene_artifact_store.load_scene_manifest = AsyncMock(return_value=manifest)

    handler = ProfileSceneJobHandler()
    await handler.execute(
        job=_job(),
        params=_profile_params(),
        context=ctx,
        initial_record=_initial_profile_record(),
        started_at=MagicMock(),
    )

    per_scene = [
        r
        for r in ctx._upserted_runs
        if isinstance(r, SceneProfileRunRecord) and r.scene_id == SCENE_ID
    ]
    # annotation_count is computed from manifest.samples annotations
    assert per_scene[0].annotation_count is not None


async def test_profile_scene_scene_status_set_to_profiled():
    ctx = _context(existing_scene=_scene_record(status=SceneStatus.VALIDATED))

    handler = ProfileSceneJobHandler()
    await handler.execute(
        job=_job(),
        params=_profile_params(),
        context=ctx,
        initial_record=_initial_profile_record(),
        started_at=MagicMock(),
    )

    profiled = [s for s in ctx._upserted_scenes if s.status == SceneStatus.PROFILED]
    assert len(profiled) >= 1, "Scene status should be PROFILED after profiling"


async def test_profile_scene_artifact_has_scene_id():
    ctx = _context(existing_scene=_scene_record(status=SceneStatus.VALIDATED))

    handler = ProfileSceneJobHandler()
    await handler.execute(
        job=_job(),
        params=_profile_params(),
        context=ctx,
        initial_record=_initial_profile_record(),
        started_at=MagicMock(),
    )

    scene_artifacts = [
        a for a in ctx._created_artifacts if a.get("scene_id") == SCENE_ID
    ]
    assert len(scene_artifacts) >= 1, "Profile report artifact should carry scene_id"


async def test_profile_scene_job_result_has_annotation_count():
    ctx = _context(existing_scene=_scene_record(status=SceneStatus.VALIDATED))

    handler = ProfileSceneJobHandler()
    _, job_result = await handler.execute(
        job=_job(),
        params=_profile_params(),
        context=ctx,
        initial_record=_initial_profile_record(),
        started_at=MagicMock(),
    )

    assert hasattr(
        job_result, "annotation_count"
    ), "ProfileSceneJobResult needs annotation_count"


# ── per_scene run id determinism ──────────────────────────────────────────────


def test_per_scene_validation_run_id_is_deterministic():
    id1 = _per_scene_validation_run_id("job-abc", "scene-001")
    id2 = _per_scene_validation_run_id("job-abc", "scene-001")
    assert id1 == id2


def test_per_scene_validation_run_id_differs_by_scene():
    id1 = _per_scene_validation_run_id("job-abc", "scene-001")
    id2 = _per_scene_validation_run_id("job-abc", "scene-002")
    assert id1 != id2
