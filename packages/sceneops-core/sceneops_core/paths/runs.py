def inference_run_manifest_uri(root_uri: str, run_id: str) -> str:
    return f"{root_uri.rstrip('/')}/runs/inference/{run_id}/manifest.json"


def evaluation_run_manifest_uri(root_uri: str, run_id: str) -> str:
    return f"{root_uri.rstrip('/')}/runs/evaluations/{run_id}/manifest.json"


def evaluation_run_metrics_uri(root_uri: str, run_id: str) -> str:
    return f"{root_uri.rstrip('/')}/runs/evaluations/{run_id}/metrics.json"
