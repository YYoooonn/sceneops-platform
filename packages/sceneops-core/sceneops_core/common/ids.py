from __future__ import annotations

from uuid import uuid4


def generate_prefixed_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def sample_sensor_artifact_id(*, sample_id: str, channel: str) -> str:
    return f"{sample_id}-{channel}"


def generate_scene_db_id() -> str:
    return generate_prefixed_id("scene")


def generate_sample_db_id() -> str:
    return generate_prefixed_id("smpl")


def generate_raw_log_id() -> str:
    return generate_prefixed_id("rawlog")


def generate_segment_id() -> str:
    return generate_prefixed_id("seg")


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


def generate_execution_id() -> str:
    return generate_prefixed_id("exec")


def generate_scenario_id() -> str:
    return generate_prefixed_id("scenario")


def generate_scenario_set_id() -> str:
    return generate_prefixed_id("scset")


def generate_artifact_id() -> str:
    return generate_prefixed_id("art")


def generate_robot_id() -> str:
    return generate_prefixed_id("robot")


def generate_robot_run_id() -> str:
    return generate_prefixed_id("robotrun")


def generate_mission_id() -> str:
    return generate_prefixed_id("mission")


def generate_robot_state_id() -> str:
    return generate_prefixed_id("robotstate")


def robot_run_recording_artifact_id(robot_run_id: str) -> str:
    """Deterministic (not random, unlike ``generate_artifact_id``) --
    one RobotRun has exactly one raw recording artifact, and giving that
    artifact's id a stable, derivable value lets a second registration
    attempt for the same ``robot_run_id`` collide with the ``artifacts``
    table's own primary key instead of silently creating a duplicate row,
    mirroring ``sample_sensor_artifact_id``'s identical reasoning for its
    own one-artifact-per-(sample, channel) identity."""
    return f"art-robotrun-{robot_run_id}"


def robot_run_manifest_artifact_id(robot_run_id: str) -> str:
    """Deterministic id of a RobotRun's RobotRunManifest ArtifactRecord.

    The ``robotrunmanifest`` prefix cannot collide with any
    ``robot_run_recording_artifact_id`` value (those continue with ``-``
    after ``art-robotrun``), whatever the run id."""
    return f"art-robotrunmanifest-{robot_run_id}"


def generate_comparison_run_id(job_id: str) -> str:
    suffix = job_id.removeprefix("job-")
    return f"cmp-{suffix}"


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


def default_auto_label_run_id(job_id: str) -> str:
    suffix = job_id.removeprefix("job-")
    return f"al-{suffix}"


def default_mining_run_id(job_id: str) -> str:
    suffix = job_id.removeprefix("job-")
    return f"mining-{suffix}"


def default_readiness_run_id(job_id: str) -> str:
    suffix = job_id.removeprefix("job-")
    return f"readiness-{suffix}"
