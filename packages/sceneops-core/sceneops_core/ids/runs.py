def default_inference_run_id(job_id: str) -> str:
    return f"run-{job_id}"


def default_evaluation_run_id(job_id: str) -> str:
    return f"eval-{job_id}"
