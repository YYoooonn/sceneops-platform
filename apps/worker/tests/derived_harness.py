"""An in-process harness for derived (L3) workflow tests (ADR-007 §33).

It wires the real worker stores over a real local filesystem
(``LocalArtifactStore`` on ``tmp_path``) and replaces only the PostgreSQL
repositories with small dict-backed fakes. The real
``ArtifactRecordStore.register`` logic runs against a fake repository, so
deterministic-id idempotence and conflict behaviour are exercised, and every
manifest is written, pinned and read back through the real stores.

Scenes are registered the way the Scene registrar leaves them: a canonical
manifest published at its checksum-qualified key, an ArtifactRecord naming
it, a SceneRecord pinning that revision and ``OBSERVATION_PAYLOAD`` records
for every payload the manifest references.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord, ArtifactRef
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_core.models.schemas.enums import ModelBackend
from sceneops_core.models.schemas.records import ModelVersionRecord
from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenarios.schemas.records import ScenarioSetRecord
from sceneops_core.scenes.schemas import (
    SceneManifest,
    SceneRecord,
    project_scene_record,
)
from sceneops_core.scenes.schemas.runs import SceneValidationRunRecord
from sceneops_core.scenes.testing import (
    build_scene_manifest,
    payload_bytes,
    recording_source,
)
from sceneops_storage import LocalArtifactStore

from sceneops_worker.derived import DerivedManifestStore
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.scenes.artifacts import SceneArtifactStore
from sceneops_worker.stores.artifacts import ArtifactRecordStore

DATASET_ID = "ds"
DATASET_VERSION = "v1"


class FakeArtifactRepo:
    """The slice of PostgresArtifactRefRepository the stores use."""

    def __init__(self) -> None:
        self.records: dict[str, ArtifactRecord] = {}

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        return self.records.get(artifact_id)

    async def get_many(self, ids: list[str]) -> dict[str, ArtifactRecord]:
        return {i: self.records[i] for i in ids if i in self.records}

    async def create(self, *, artifact_id: str, ref: ArtifactRef, **fields: Any):
        if artifact_id in self.records:
            raise RuntimeError(f"duplicate artifact id {artifact_id}")
        record = ArtifactRecord(
            artifact_id=artifact_id,
            kind=ref.kind.value,
            uri=ref.uri,
            media_type=ref.media_type,
            size_bytes=ref.size_bytes,
            checksum=ref.checksum,
            metadata=ref.metadata,
            **{k: v for k, v in fields.items() if v is not None and k != "backend"},
        )
        self.records[artifact_id] = record
        return record

    async def create_if_absent(
        self, *, artifact_id: str, ref: ArtifactRef, **fields: Any
    ) -> tuple[ArtifactRecord, bool]:
        if artifact_id in self.records:
            return self.records[artifact_id], False
        return await self.create(artifact_id=artifact_id, ref=ref, **fields), True

    async def list(self, **filters: Any) -> list[ArtifactRecord]:
        out = []
        for record in self.records.values():
            if all(
                getattr(record, key, None)
                == (value.value if hasattr(value, "value") else value)
                for key, value in filters.items()
                if value is not None and key not in ("limit", "offset")
            ):
                out.append(record)
        return out


class FakeSceneStore:
    def __init__(self) -> None:
        self.records: dict[str, SceneRecord] = {}

    async def get(self, scene_id: str) -> SceneRecord | None:
        return self.records.get(scene_id)

    async def list(
        self, *, dataset_id=None, dataset_version=None, limit=100, offset=0, **_
    ):
        rows = [
            r
            for r in self.records.values()
            if (dataset_id is None or r.dataset_id == dataset_id)
            and (dataset_version is None or r.dataset_version == dataset_version)
        ]
        return sorted(rows, key=lambda r: r.scene_id)[offset : offset + limit]


class FakeRunStore:
    def __init__(self) -> None:
        self.records: dict[str, Any] = {}

    async def get(self, run_id: str):
        return self.records.get(run_id)

    async def upsert(self, record):
        self.records[record.run_id] = record
        return record


class FakeSceneRunStore(FakeRunStore):
    async def list(
        self,
        *,
        type=None,
        status=None,
        scene_id=None,
        manifest_artifact_id=None,
        limit=100,
        **_,
    ):
        return [
            r
            for r in self.records.values()
            if (type is None or r.type == type)
            and (status is None or r.status == status)
            and (scene_id is None or r.scene_id == scene_id)
            and (
                manifest_artifact_id is None
                or r.manifest_artifact_id == manifest_artifact_id
            )
        ][:limit]


class FakeScenarioStore:
    def __init__(self) -> None:
        self.sets: dict[str, ScenarioSetRecord] = {}
        self.runs: dict[str, Any] = {}

    async def get(self, scenario_set_id: str):
        return self.sets.get(scenario_set_id)

    async def upsert(self, record):
        self.sets[record.scenario_set_id] = record
        return record

    async def upsert_run(self, run):
        self.runs[run.run_id] = run
        return run


class FakeRobotStore:
    def __init__(self, run_ids: set[str]) -> None:
        self.run_ids = run_ids

    async def get_run(self, run_id: str):
        if run_id not in self.run_ids:
            return None
        return SimpleNamespace(run_id=run_id)


@dataclass
class Harness:
    """Stands in for ``WorkerContext`` in handler tests."""

    root: str
    artifact_store: LocalArtifactStore
    input_store: LocalArtifactStore
    derived_store: DerivedManifestStore
    run_artifact_store: RunArtifactStore
    scene_artifact_store: SceneArtifactStore
    artifact_record_store: ArtifactRecordStore
    repo: FakeArtifactRepo
    scene_store: FakeSceneStore = field(default_factory=FakeSceneStore)
    scenario_store: FakeScenarioStore = field(default_factory=FakeScenarioStore)
    robot_store: FakeRobotStore = field(default_factory=lambda: FakeRobotStore(set()))
    inference_runs: FakeRunStore = field(default_factory=FakeRunStore)
    evaluation_runs: FakeRunStore = field(default_factory=FakeRunStore)
    scene_runs: FakeSceneRunStore = field(default_factory=FakeSceneRunStore)
    commits: int = 0

    def __post_init__(self) -> None:
        self.settings = SimpleNamespace(run_root_uri=f"{self.root}/runs")
        self.runs = SimpleNamespace(
            inference=self.inference_runs,
            evaluations=self.evaluation_runs,
            scene_runs=self.scene_runs,
        )
        self.dataset_store = SimpleNamespace(
            get_version=AsyncMock(
                side_effect=lambda dataset_id, version: DatasetVersionRecord(
                    dataset_id=dataset_id, version=version
                )
            )
        )
        self.model_store = SimpleNamespace(
            get_version=AsyncMock(
                side_effect=lambda model_id, version: ModelVersionRecord(
                    id=f"{model_id}:{version}",
                    model_id=model_id,
                    version=version,
                    backend=ModelBackend.MOCK,
                )
            )
        )

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass

    # ── Scene registration (what the Scene registrar leaves behind) ─────────

    async def register_scene(
        self,
        manifest: SceneManifest | None = None,
        *,
        robot_run_id: str = "run-001",
        unit_key: str = "segment-0000",
        dataset_id: str = DATASET_ID,
        dataset_version: str = DATASET_VERSION,
        **manifest_kwargs: Any,
    ) -> SceneRecord:
        if manifest is None:
            # Payload ids are scoped to the RobotRun, as in production.
            manifest_kwargs.setdefault("payload_namespace", f"art-{robot_run_id}")
            manifest = build_scene_manifest(
                source=recording_source(robot_run_id=robot_run_id, unit_key=unit_key),
                **manifest_kwargs,
            )
        self.robot_store.run_ids.add(manifest.lineage.source.robot_run_id)
        scene_id_probe = project_scene_record(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            manifest=manifest,
            manifest_artifact_id="pending",
            manifest_checksum=manifest.checksum(),
        )
        published = await self.scene_artifact_store.publish_canonical_manifest(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_id=scene_id_probe.scene_id,
            manifest=manifest,
        )
        artifact_id = (
            f"scene-manifest-{scene_id_probe.scene_id}-{published.checksum[7:15]}"
        )
        await self.artifact_record_store.register(
            artifact_id=artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.SCENE_MANIFEST,
                uri=published.uri,
                media_type="application/json",
                checksum=published.checksum,
                size_bytes=published.size_bytes,
            ),
            owner_type=ArtifactOwnerType.SCENE,
            owner_id=scene_id_probe.scene_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_id=scene_id_probe.scene_id,
        )
        for observation in manifest.observations:
            ref = observation.payload
            uri = self.artifact_store.join_uri(
                self.root,
                "payloads",
                manifest.lineage.source.robot_run_id,
                ref.artifact_id,
            )
            await self.artifact_store.write_bytes(
                uri, payload_bytes(observation.observation_id)
            )
            await self.artifact_record_store.register(
                artifact_id=ref.artifact_id,
                ref=ArtifactRef(
                    kind=ArtifactKind.OBSERVATION_PAYLOAD,
                    uri=uri,
                    media_type=ref.media_type,
                    checksum=ref.checksum,
                    size_bytes=ref.size_bytes,
                ),
                owner_type=ArtifactOwnerType.ROBOT_RUN,
                owner_id=manifest.lineage.source.robot_run_id,
            )
        record = project_scene_record(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            manifest=manifest,
            manifest_artifact_id=artifact_id,
            manifest_checksum=published.checksum,
        )
        self.scene_store.records[record.scene_id] = record
        return record

    def add_validation(
        self, scene: SceneRecord, *, status: str = "ready", block: bool = False
    ) -> None:
        run = SceneValidationRunRecord(
            run_id=f"val-{scene.scene_id}",
            status=RunStatus.SUCCEEDED,
            scene_id=scene.scene_id,
            manifest_artifact_id=scene.manifest_artifact_id,
            manifest_checksum=scene.manifest_checksum,
            validation_status=status,
            should_block_pipeline=block,
        )
        self.scene_runs.records[run.run_id] = run

    # ── handler invocation ─────────────────────────────────────────────────

    def request(self, job_type: JobType, params) -> JobHandlerRequest:
        job = JobManifest(
            job_id="job-1",
            type=job_type,
            status=JobStatus.RUNNING,
            params=params.model_dump(mode="json"),
        )
        return JobHandlerRequest(job=job, params=params, context=self)


def make_harness(tmp_path) -> Harness:
    root = str(tmp_path)
    store = LocalArtifactStore(root_uri=root)
    repo = FakeArtifactRepo()
    records = ArtifactRecordStore.__new__(ArtifactRecordStore)
    records._repo = repo
    return Harness(
        root=root,
        artifact_store=store,
        input_store=store,
        derived_store=DerivedManifestStore(
            artifact_store=store,
            dataset_root_uri=f"{root}/datasets",
            runs_root_uri=f"{root}/runs",
            label_root_uri=f"{root}/labels",
        ),
        run_artifact_store=RunArtifactStore(
            artifact_store=store, runs_root_uri=f"{root}/runs"
        ),
        scene_artifact_store=SceneArtifactStore(
            artifact_store=store,
            dataset_root_uri=f"{root}/datasets",
            payload_root_uri=f"{root}/payloads",
        ),
        artifact_record_store=records,
        repo=repo,
    )


__all__ = [
    "DATASET_ID",
    "DATASET_VERSION",
    "Harness",
    "RunStatus",
    "RunType",
    "make_harness",
]
