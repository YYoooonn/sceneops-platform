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
