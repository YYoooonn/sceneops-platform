from pathlib import Path


def inference_run_root(*, runs_root: Path, run_id: str) -> Path:
    return runs_root / "inference" / run_id


def inference_run_manifest_path(*, runs_root: Path, run_id: str) -> Path:
    return inference_run_root(runs_root=runs_root, run_id=run_id) / "run.json"


def prediction_manifest_path(
    *,
    runs_root: Path,
    run_id: str,
    sample_id: str,
) -> Path:
    return (
        inference_run_root(runs_root=runs_root, run_id=run_id)
        / "predictions"
        / f"{sample_id}.json"
    )


def evaluation_run_root(*, runs_root: Path, evaluation_run_id: str) -> Path:
    return runs_root / "evaluations" / evaluation_run_id


def evaluation_run_manifest_path(
    *,
    runs_root: Path,
    evaluation_run_id: str,
) -> Path:
    return (
        evaluation_run_root(
            runs_root=runs_root,
            evaluation_run_id=evaluation_run_id,
        )
        / "evaluation.json"
    )


def sample_evaluation_manifest_path(
    *,
    runs_root: Path,
    evaluation_run_id: str,
    sample_id: str,
) -> Path:
    return (
        evaluation_run_root(
            runs_root=runs_root,
            evaluation_run_id=evaluation_run_id,
        )
        / "samples"
        / f"{sample_id}.json"
    )


def jobs_root(*, runs_root: Path) -> Path:
    return runs_root / "jobs"


def job_manifest_path(*, runs_root: Path, job_id: str) -> Path:
    return jobs_root(runs_root=runs_root) / f"{job_id}.json"
