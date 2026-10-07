from __future__ import annotations

from uuid import uuid4


def generate_prefixed_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def generate_job_event_id() -> str:
    return generate_prefixed_id("jobevt")


def generate_job_id() -> str:
    return generate_prefixed_id("job")


def generate_model_version_id(model_id: str, version: str) -> str:
    return f"{model_id}:{version}"


def generate_pipeline_run_id() -> str:
    return generate_prefixed_id("pipe")


def generate_pipeline_task_run_id() -> str:
    return generate_prefixed_id("ptask")


def default_scenario_set_id(job_id: str) -> str:
    """The ScenarioSet a mining Job produces when the caller names none: a
    function of the Job, so a retry of that Job converges on the same set
    instead of forking a new one."""
    suffix = job_id.removeprefix("job-")
    return f"scset-{suffix}"


def robot_run_recording_artifact_id(robot_run_id: str) -> str:
    """Deterministic -- one RobotRun has exactly one raw recording artifact, and giving that
    artifact's id a stable, derivable value lets a second registration
    attempt for the same ``robot_run_id`` collide with the ``artifacts``
    table's own primary key instead of silently creating a duplicate row."""
    return f"art-robotrun-{robot_run_id}"


def robot_run_manifest_artifact_id(robot_run_id: str) -> str:
    """Deterministic id of a RobotRun's RobotRunManifest ArtifactRecord.

    The ``robotrunmanifest`` prefix cannot collide with any
    ``robot_run_recording_artifact_id`` value (those continue with ``-``
    after ``art-robotrun``), whatever the run id."""
    return f"art-robotrunmanifest-{robot_run_id}"


def default_inference_run_id(job_id: str) -> str:
    suffix = job_id.removeprefix("job-")
    return f"run-{suffix}"


def default_evaluation_run_id(job_id: str) -> str:
    suffix = job_id.removeprefix("job-")
    return f"eval-{suffix}"


def default_validation_run_id(job_id: str) -> str:
    suffix = job_id.removeprefix("job-")
    return f"val-{suffix}"


def default_profile_run_id(job_id: str) -> str:
    suffix = job_id.removeprefix("job-")
    return f"profile-{suffix}"


def default_mining_run_id(job_id: str) -> str:
    suffix = job_id.removeprefix("job-")
    return f"mining-{suffix}"


def default_readiness_run_id(job_id: str) -> str:
    suffix = job_id.removeprefix("job-")
    return f"readiness-{suffix}"
