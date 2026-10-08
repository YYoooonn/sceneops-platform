"""Registered Scenes -> labels -> sample views -> ScenarioSet -> detection ->
evaluation, on real PostgreSQL and real MinIO (ADR-007 §33).

The recording is a real ROS 2 CDR MCAP stored in MinIO as a registered
RobotRun's recording. Scenes are built and registered through the real jobs,
then every derived step runs in its own session, like separate jobs. What the
in-process tests cannot prove is proven here: pins persist in PostgreSQL
(ScenarioSet, inference run), deterministic ArtifactRecord ids converge on
retry against the real primary key, and write-once keys hold on S3.

Requires SCENEOPS_DATABASE_URL (migrated to head) and a reachable MinIO;
skips otherwise. Rows are created under unique ids and removed afterwards.
"""

from __future__ import annotations


import pytest

from sceneops_core.jobs.schemas import (
    BuildSceneSampleViewsJobParams,
    EvaluateDetectionJobParams,
    ImportLabelsJobParams,
    MineScenariosJobParams,
    PredictDetectionJobParams,
)
from sceneops_core.labels import LabelProvenance, LabelSetManifest, LabelSourceKind
from sceneops_core.sample_views import (
    MemberPolicy,
    PosePolicy,
    SampleAnchorPolicy,
    SampleViewPolicy,
)
from sceneops_db.models.model_registry import ModelModel, ModelVersionModel
from sceneops_db.session import get_async_sessionmaker
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.jobs.dataset.build_recording_scenes import (
    BuildRecordingScenesJobHandler,
)
from sceneops_worker.jobs.dataset.register_scenes import RegisterScenesJobHandler
from sceneops_worker.jobs.derived import (
    BuildSceneSampleViewsJobHandler,
    ImportLabelsJobHandler,
)
from sceneops_worker.jobs.evaluation import EvaluateDetectionJobHandler
from sceneops_worker.jobs.inference import PredictDetectionJobHandler
from sceneops_worker.jobs.scenarios import MineScenariosJobHandler

from sceneops_derived.testing import anchor, box_label
from tests.scenes.test_recording_scene_vertical_integration import _Vertical

from sceneops_recording.testing.ros2_recordings import (  # noqa: E402
    CAMERA_TOPIC,
    LIDAR_TOPIC,
    build_config,
    default_recording,
)

CLOCK = "sensor.header_stamp"
# The fixture's lidar header stamps lead the camera by 7 ns.
LIDAR_STAMPS = [1_000_000_007, 1_500_000_007, 2_000_000_007, 2_500_000_007]
DATASET_VERSION = "v1"


class _DerivedVertical(_Vertical):
    def __init__(self, settings, unique_id, tmp_path) -> None:
        super().__init__(settings, unique_id)
        self.model_id = unique_id("model-derived")
        self.label_set_id = unique_id("gt")
        self.tmp_path = tmp_path

    async def seed_model(self) -> None:
        async with get_async_sessionmaker()() as session:
            session.add(ModelModel(model_id=self.model_id, task_type="detection"))
            await session.flush()
            session.add(
                ModelVersionModel(
                    id=f"{self.model_id}:v1",
                    model_id=self.model_id,
                    version="v1",
                    task_type="detection",
                    backend="mock",
                    status="registered",
                )
            )
            await session.commit()

    def label_document(self) -> LabelSetManifest:
        labels = [
            box_label(
                f"car-{i}",
                self.run_id,
                ts,
                x=10.0 + 5 * i,
                frame="map",
            ).model_copy(
                update={
                    "anchor": anchor(self.run_id, ts, channel=LIDAR_TOPIC).model_copy(
                        update={"source_clock": CLOCK}
                    )
                }
            )
            for i, ts in enumerate(LIDAR_STAMPS[:2])
        ]
        coverage = [
            anchor(self.run_id, ts, channel=LIDAR_TOPIC).model_copy(
                update={"source_clock": CLOCK}
            )
            for ts in LIDAR_STAMPS
        ]
        return LabelSetManifest.normalized(
            label_set_id=self.label_set_id,
            provenance=LabelProvenance(
                kind=LabelSourceKind.HUMAN, producer="annotators"
            ),
            coverage=coverage,
            labels=labels,
        )

    async def write_label_document(self) -> str:
        path = self.tmp_path / "labels.json"
        path.write_bytes(self.label_document().to_canonical_bytes())
        return str(path)

    def views_params(self, label_sets):
        return BuildSceneSampleViewsJobParams(
            dataset_id=self.dataset_id,
            dataset_version=DATASET_VERSION,
            policy=SampleViewPolicy(
                anchor=SampleAnchorPolicy(channel=CAMERA_TOPIC),
                members=[MemberPolicy(channel=LIDAR_TOPIC, tolerance_ns=1_000)],
                pose=PosePolicy(
                    parent_frame_id="map",
                    child_frame_id="base_link",
                    tolerance_ns=1_000,
                ),
            ),
            label_sets=label_sets,
        )


@pytest.fixture()
async def derived(
    _fresh_database_connection, _minio_reachable, worker_settings, unique_id, tmp_path
):
    dependencies_module._artifact_store = None
    dependencies_module._input_store = None
    env = _DerivedVertical(worker_settings, unique_id, tmp_path)
    await env.seed(default_recording().write(tmp_path / "r.mcap").read_bytes())
    await env.seed_model()
    yield env
    dependencies_module._artifact_store = None
    dependencies_module._input_store = None


async def test_scenes_to_pinned_evaluation_on_real_infrastructure(derived):
    # -- canonical Scenes (L2) -------------------------------------------------
    built = await derived.run(
        BuildRecordingScenesJobHandler(), derived.build_params(build_config())
    )
    registered = await derived.run(
        RegisterScenesJobHandler(), derived.register_params(built)
    )
    assert registered.registered_scene_count == 2

    # -- labels: independent lineage -------------------------------------------
    imported = await derived.run(
        ImportLabelsJobHandler(),
        ImportLabelsJobParams(document_uri=await derived.write_label_document()),
    )
    assert imported.label_count == 2 and imported.covered_anchor_count == 4
    reimported = await derived.run(
        ImportLabelsJobHandler(),
        ImportLabelsJobParams(document_uri=await derived.write_label_document()),
    )
    assert reimported.label_set == imported.label_set and not reimported.created

    # -- sample views ----------------------------------------------------------
    views = await derived.run(
        BuildSceneSampleViewsJobHandler(), derived.views_params([imported.label_set])
    )
    assert (views.scene_count, views.sample_count) == (2, 4)
    again = await derived.run(
        BuildSceneSampleViewsJobHandler(), derived.views_params([imported.label_set])
    )
    assert again.views == views.views and again.created_count == 0

    # -- ScenarioSet revision --------------------------------------------------
    mined = await derived.run(
        MineScenariosJobHandler(),
        MineScenariosJobParams(
            dataset_id=derived.dataset_id,
            dataset_version=DATASET_VERSION,
            sample_views=views.views,
            label_set_id=derived.label_set_id,
            sort_by="scene_id",
            order="asc",
        ),
    )
    assert mined.selected_count == 2
    async with get_async_sessionmaker()() as session:
        record = await derived.context(session).scenario_store.get(
            mined.scenario_set_id
        )
        # The pin is persisted on the real row.
        assert record.manifest_checksum == mined.scenario_set_checksum
        assert record.manifest_artifact_id == mined.scenario_set_manifest_artifact_id

    # -- detection over the pinned set, then a pinned evaluation ----------------
    predicted = await derived.run(
        PredictDetectionJobHandler(),
        PredictDetectionJobParams(
            dataset_id=derived.dataset_id,
            dataset_version=DATASET_VERSION,
            model_id=derived.model_id,
            model_version="v1",
            scenario_set_id=mined.scenario_set_id,
            camera_channel=CAMERA_TOPIC,
        ),
    )
    assert (predicted.scene_count, predicted.sample_count) == (2, 4)
    async with get_async_sessionmaker()() as session:
        run = await derived.context(session).runs.inference.get(
            predicted.inference_run_id
        )
        assert (
            run.prediction_manifest_checksum == predicted.prediction_manifest_checksum
        )

    evaluated = await derived.run(
        EvaluateDetectionJobHandler(),
        EvaluateDetectionJobParams(
            dataset_id=derived.dataset_id,
            dataset_version=DATASET_VERSION,
            inference_run_id=predicted.inference_run_id,
            label_set=imported.label_set,
        ),
    )
    assert (
        evaluated.prediction_manifest_checksum == predicted.prediction_manifest_checksum
    )
    assert evaluated.label_set_checksum == imported.label_set.manifest_checksum
    assert evaluated.sample_count == 4
    # Two keyframe labels (the first sample of each Scene's window) are ground truth.
    assert evaluated.metrics["tp"] + evaluated.metrics["fn"] == 2

    async with get_async_sessionmaker()() as session:
        stored = await derived.context(session).runs.evaluations.get(
            evaluated.evaluation_run_id
        )
        inputs = stored.summary["inputs"]
        assert (
            inputs["scenario_set"]["manifest_checksum"] == mined.scenario_set_checksum
        )
        assert (
            inputs["label_set"]["manifest_checksum"]
            == imported.label_set.manifest_checksum
        )

    # -- retry convergence: the same run id reproduces the same revision --------
    repeat = await derived.run(
        PredictDetectionJobHandler(),
        PredictDetectionJobParams(
            dataset_id=derived.dataset_id,
            dataset_version=DATASET_VERSION,
            model_id=derived.model_id,
            model_version="v1",
            scenario_set_id=mined.scenario_set_id,
            camera_channel=CAMERA_TOPIC,
            inference_run_id=predicted.inference_run_id,
        ),
    )
    assert repeat.prediction_manifest_checksum == predicted.prediction_manifest_checksum
    assert (
        repeat.prediction_manifest_artifact_id
        == predicted.prediction_manifest_artifact_id
    )
