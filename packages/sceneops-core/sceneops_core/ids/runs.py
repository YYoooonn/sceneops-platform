def default_inference_run_id(job_id: str) -> str:
    suffix = job_id.removeprefix("job-")
    return f"run-{suffix}"


def default_evaluation_run_id(job_id: str) -> str:
    suffix = job_id.removeprefix("job-")
    return f"eval-{suffix}"
