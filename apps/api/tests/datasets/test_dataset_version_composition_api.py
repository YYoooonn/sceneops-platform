"""The API's DatasetVersion surface (ADR-015 §7): versions are read-only
through the dataset routes and are composed only by a
COMPOSE_DATASET_VERSION Job submitted through the Jobs API."""

from __future__ import annotations

import pytest

from app.domains.datasets import schemas as dataset_schemas
from app.domains.datasets.router import router
from app.domains.datasets.service import DatasetService
from sceneops_core.jobs.schemas import CreateJobRequest, JobType
from sceneops_core.provenance import manifest_artifact_id_for
from sceneops_core.scenes.schemas import scene_id_for
from sceneops_core.scenes.testing import recording_source
from sceneops_execution.jobs.service import JobService


def test_dataset_routes_never_write_a_version():
    version_writes = [
        (route.path, sorted(route.methods))
        for route in router.routes
        if "/versions" in route.path and route.methods - {"GET", "HEAD"}
    ]
    assert version_writes == []
    assert {route.path for route in router.routes if "GET" in route.methods} >= {
        "/{dataset_id}/versions",
        "/{dataset_id}/versions/{version}",
    }
    assert not hasattr(DatasetService, "create_dataset_version")
    assert not hasattr(dataset_schemas, "CreateDatasetVersionBody")


class _Jobs:
    def __init__(self) -> None:
        self.created = []

    async def find_by_execution_key(self, execution_key, *, statuses):
        return next((j for j in self.created if j.execution_key == execution_key), None)

    async def create(self, job):
        self.created.append(job)
        return job


class _Events:
    async def append(self, event):
        return event


def _pin(unit_key: str) -> dict:
    unit_id = scene_id_for(recording_source(unit_key=unit_key))
    checksum = "sha256:" + unit_key[-1] * 64
    return {
        "domain": "scene",
        "unit_id": unit_id,
        "manifest_artifact_id": manifest_artifact_id_for(
            unit_id=unit_id, manifest_checksum=checksum
        ),
        "manifest_checksum": checksum,
    }


@pytest.mark.asyncio
async def test_composition_is_submitted_as_a_job_and_dedups_on_its_pins():
    jobs = _Jobs()
    service = JobService(
        repository=jobs, event_repository=_Events(), artifact_repository=None
    )
    a, b = _pin("segment-a"), _pin("segment-b")

    first = await service.create_job(
        CreateJobRequest(
            type=JobType.COMPOSE_DATASET_VERSION,
            dataset_id="ds",
            dataset_version="v1",
            params={"pins": [a, b]},
        )
    )
    reordered = await service.create_job(
        CreateJobRequest(
            type=JobType.COMPOSE_DATASET_VERSION,
            dataset_id="ds",
            dataset_version="v1",
            params={"pins": [b, a]},
        )
    )

    assert (first.dataset_id, first.dataset_version) == ("ds", "v1")
    assert first.params["dataset_version"] == "v1"
    assert [p["unit_id"] for p in first.params["pins"]] == sorted(
        [a["unit_id"], b["unit_id"]]
    )
    # The same composition request is one Job, whatever the pin order.
    assert reordered.job_id == first.job_id
    assert len(jobs.created) == 1

    with pytest.raises(ValueError, match="exactly one of pins or selector"):
        await service.create_job(
            CreateJobRequest(
                type=JobType.COMPOSE_DATASET_VERSION,
                dataset_id="ds",
                dataset_version="v2",
                params={},
            )
        )
