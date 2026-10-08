"""Shared set-up for derived-workflow tests: registered Scenes, imported
label sets and built sample views, all through the real job handlers."""

from __future__ import annotations

from sceneops_core.jobs.schemas import (
    BuildSceneSampleViewsJobParams,
    ImportLabelsJobParams,
    JobType,
)
from sceneops_core.labels import LabelSetRef
from sceneops_core.sample_views import (
    MemberPolicy,
    PosePolicy,
    SampleAnchorPolicy,
    SampleViewPolicy,
)
from sceneops_worker.jobs.derived import (
    BuildSceneSampleViewsJobHandler,
    ImportLabelsJobHandler,
)
from sceneops_derived.testing import label_document, write_document
from tests.derived_harness import DATASET_ID, DATASET_VERSION

MS = 1_000_000
T0, T1 = 1_000_000_000, 1_500_000_000


def detection_policy(
    *, lidar_tolerance_ns: int = 10 * MS, stride: int = 1
) -> SampleViewPolicy:
    """Camera-anchored samples with the nearest lidar sweep and the ego pose."""
    return SampleViewPolicy(
        anchor=SampleAnchorPolicy(channel="CAM_FRONT", stride=stride),
        members=[MemberPolicy(channel="LIDAR_TOP", tolerance_ns=lidar_tolerance_ns)],
        pose=PosePolicy(parent_frame_id="world", child_frame_id="ego", tolerance_ns=MS),
    )


async def import_label_set(harness, label_set_id: str, **document) -> LabelSetRef:
    doc = label_document(label_set_id, **document)
    uri = await write_document(
        harness, f"{label_set_id}-{doc.checksum()[7:15]}.json", doc
    )
    result = await ImportLabelsJobHandler().run(
        harness.request(JobType.IMPORT_LABELS, ImportLabelsJobParams(document_uri=uri))
    )
    return result.label_set


async def build_views(harness, *, policy=None, label_sets=(), scene_ids=()):
    params = BuildSceneSampleViewsJobParams(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        scene_ids=list(scene_ids),
        policy=policy or detection_policy(),
        label_sets=list(label_sets),
    )
    return await BuildSceneSampleViewsJobHandler().run(
        harness.request(JobType.BUILD_SCENE_SAMPLE_VIEWS, params)
    )


async def mine(harness, views, **params):
    from sceneops_core.jobs.schemas import JobType, MineScenariosJobParams
    from sceneops_worker.jobs.scenarios import MineScenariosJobHandler

    job_id = params.pop("job_id", None)
    params.setdefault("dataset_id", DATASET_ID)
    params.setdefault("dataset_version", DATASET_VERSION)
    return await MineScenariosJobHandler().run(
        harness.request(
            JobType.MINE_SCENARIOS,
            MineScenariosJobParams(sample_views=list(views), **params),
            job_id=job_id,
        )
    )


async def predict(harness, **params):
    from sceneops_core.jobs.schemas import JobType, PredictDetectionJobParams
    from sceneops_worker.jobs.inference import PredictDetectionJobHandler

    job_id = params.pop("job_id", None)
    params.setdefault("dataset_id", DATASET_ID)
    params.setdefault("dataset_version", DATASET_VERSION)
    params.setdefault("model_id", "dummy")
    params.setdefault("model_version", "v1")
    params.setdefault("camera_channel", "CAM_FRONT")
    return await PredictDetectionJobHandler().run(
        harness.request(
            JobType.PREDICT_DETECTION,
            PredictDetectionJobParams(**params),
            job_id=job_id,
        )
    )


async def evaluate(harness, **params):
    from sceneops_core.jobs.schemas import EvaluateDetectionJobParams, JobType
    from sceneops_worker.jobs.evaluation import EvaluateDetectionJobHandler

    job_id = params.pop("job_id", None)
    params.setdefault("dataset_id", DATASET_ID)
    params.setdefault("dataset_version", DATASET_VERSION)
    return await EvaluateDetectionJobHandler().run(
        harness.request(
            JobType.EVALUATE_DETECTION,
            EvaluateDetectionJobParams(**params),
            job_id=job_id,
        )
    )
