"""Derived-layer rows against real Postgres: run records re-saved under an
existing id, and the revision pins the derived workflows persist
(ADR-007 §33.1, §33.4, §33.5)."""

from __future__ import annotations

import pytest

from sceneops_core.inference.schemas.runs import InferenceRunRecord
from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenarios.schemas.records import ScenarioSetRecord
from sceneops_db.postgres.inference import PostgresInferenceRunRepository
from sceneops_db.postgres.scenarios import PostgresScenarioSetRepository

CHECKSUM = "sha256:" + "a" * 64


def _run(run_id: str, **fields) -> InferenceRunRecord:
    return InferenceRunRecord(
        run_id=run_id,
        dataset_id="ds",
        dataset_version="v1",
        model_id="m",
        model_version="1",
        **fields,
    )


@pytest.mark.asyncio
async def test_a_rerun_under_an_existing_run_id_keeps_the_row_timestamps(
    db_session, unique_id
):
    """A fresh in-memory record carries no created_at / updated_at; saving it
    over the existing row must not write NULL into the NOT NULL columns."""
    repo = PostgresInferenceRunRepository(db_session)
    run_id = unique_id("run")
    created = await repo.create(_run(run_id, status=RunStatus.RUNNING))
    assert created.created_at is not None

    rerun = _run(run_id, status=RunStatus.RUNNING, job_id="job-2")
    assert rerun.created_at is None and rerun.updated_at is None
    updated = await repo.update(rerun)

    assert updated.created_at == created.created_at
    assert updated.updated_at is not None
    assert updated.job_id == "job-2"


@pytest.mark.asyncio
async def test_the_prediction_revision_pin_round_trips(db_session, unique_id):
    repo = PostgresInferenceRunRepository(db_session)
    run_id = unique_id("run")
    await repo.create(_run(run_id, status=RunStatus.RUNNING))
    await repo.update(
        _run(
            run_id,
            status=RunStatus.SUCCEEDED,
            prediction_manifest_uri="s3://b/prediction_manifest-aaaa.json",
            prediction_manifest_checksum=CHECKSUM,
        )
    )
    stored = await repo.get(run_id)
    assert stored.prediction_manifest_checksum == CHECKSUM
    assert not hasattr(stored, "dataset_manifest_uri")


@pytest.mark.asyncio
async def test_a_scenario_set_record_pins_exactly_one_manifest_revision(
    db_session, unique_id
):
    repo = PostgresScenarioSetRepository(db_session)
    scenario_set_id = unique_id("scset")
    await repo.upsert(
        ScenarioSetRecord(
            scenario_set_id=scenario_set_id,
            dataset_id="ds",
            dataset_version="v1",
            scenario_set_uri="s3://b/manifest-aaaa.json",
            manifest_artifact_id="scenarioset-1",
            manifest_checksum=CHECKSUM,
            scenario_count=3,
        )
    )
    stored = await repo.get(scenario_set_id)
    assert (stored.manifest_artifact_id, stored.manifest_checksum) == (
        "scenarioset-1",
        CHECKSUM,
    )
    # A legacy record (no pin) still round-trips as unpinned; the derived
    # workflows, not the table, refuse it.
    legacy_id = unique_id("scset")
    await repo.upsert(ScenarioSetRecord(scenario_set_id=legacy_id))
    legacy = await repo.get(legacy_id)
    assert legacy.manifest_checksum is None
