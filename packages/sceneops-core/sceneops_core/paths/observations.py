def raw_log_manifest_uri(root_uri: str, raw_log_id: str) -> str:
    return f"{root_uri.rstrip('/')}/observations/raw_logs/{raw_log_id}/manifest.json"


def raw_log_frame_index_uri(root_uri: str, raw_log_id: str) -> str:
    return f"{root_uri.rstrip('/')}/observations/raw_logs/{raw_log_id}/frames.json"
