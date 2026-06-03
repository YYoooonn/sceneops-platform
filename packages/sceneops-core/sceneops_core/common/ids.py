from __future__ import annotations

from uuid import uuid4


def sample_sensor_artifact_id(*, sample_id: str, channel: str) -> str:
    return f"{sample_id}-{channel}"


def generate_job_event_id() -> str:
    return f"jobevt-{uuid4().hex[:12]}"


def generate_job_id() -> str:
    return f"job-{uuid4().hex[:12]}"


def generate_model_version_id(model_id: str, version: str) -> str:
    return f"{model_id}:{version}"


def generate_pipeline_run_id() -> str:
    return f"pipe-{uuid4().hex[:12]}"


def generate_pipeline_step_run_id() -> str:
    return f"pstep-{uuid4().hex[:12]}"


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
