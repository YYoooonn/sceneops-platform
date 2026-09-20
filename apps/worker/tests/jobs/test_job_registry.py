"""Registry-level coverage for the job handler registry.

Covers the Stabilization Request 1 audit finding that INGEST_ROBOT_STATES
and EXPORT_ROBOT_ANALYTICS_SNAPSHOT have registered handlers but no pipeline
wrapper — confirmed here as intentional: both are direct Job-API-only
operations (submitted via POST /jobs, not through a pipeline run), not an
oversight. Stabilization Request 4 confirms the trigger path these tests
assert on is real and reachable.
"""

from __future__ import annotations

from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.builtin import BUILTIN_PIPELINE_DEFINITIONS
from sceneops_worker.jobs.registry import create_default_job_handler_registry


def test_ingest_robot_states_has_a_registered_handler():
    registry = create_default_job_handler_registry()
    handler = registry.get(JobType.INGEST_ROBOT_STATES)
    assert handler.job_type == JobType.INGEST_ROBOT_STATES


def test_export_robot_analytics_snapshot_has_a_registered_handler():
    registry = create_default_job_handler_registry()
    handler = registry.get(JobType.EXPORT_ROBOT_ANALYTICS_SNAPSHOT)
    assert handler.job_type == JobType.EXPORT_ROBOT_ANALYTICS_SNAPSHOT


def test_robot_jobs_are_intentionally_absent_from_every_pipeline():
    """Confirms the pipeline-less-ness is real, not a documentation claim:
    neither job type appears as a task in any built-in pipeline definition,
    so the only way to invoke them is the generic Job API
    (POST /jobs {"type": "ingest_robot_states" | "export_robot_analytics_snapshot", ...}),
    exactly like scripts/e2e/e2e_robot_can_replay.sh and
    scripts/e2e/e2e_analytics_export.sh already do live."""
    robot_job_types = {
        JobType.INGEST_ROBOT_STATES,
        JobType.EXPORT_ROBOT_ANALYTICS_SNAPSHOT,
    }

    for pipeline in BUILTIN_PIPELINE_DEFINITIONS:
        task_job_types = {task.job_type for task in pipeline.tasks}
        assert not (task_job_types & robot_job_types), (
            f"{pipeline.type} unexpectedly references a robot job type — "
            "if this is now intentional, update this test and the docstring "
            "above, don't just delete the assertion."
        )


def test_orphan_job_types_are_exactly_the_documented_reservations():
    """Locks in the Stabilization Request 5 cleanup decision register:
    exactly these 5 JobType values have no registered handler, each with a
    matching ArtifactOwnerType reservation documented on the enum itself
    (see jobs/schemas/enums.py). JobType.CHECK_DISTRIBUTION — previously a
    6th handler-less value with no such matching evidence — was removed
    entirely in this same cleanup, alongside its dead
    ArtifactOwnerType.DATASET_DISTRIBUTION_RUN / ArtifactKind.DISTRIBUTION_REPORT.

    If this test starts failing because a *new* orphan appeared, that's a
    real regression to investigate, not a reason to just widen the set
    below."""
    registry = create_default_job_handler_registry()
    registered = set(registry.list_job_types())
    orphans = set(JobType) - registered

    assert orphans == {
        JobType.COMPARE_SCENES,
        JobType.AUTO_LABEL_SCENE,
        JobType.EXPORT_SCENE_PACKAGE,
        JobType.AUTO_LABEL_DATASET,
        JobType.EXPORT_DATASET,
    }
