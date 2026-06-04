def scenario_set_manifest_uri(root_uri: str, scenario_set_id: str) -> str:
    return f"{root_uri.rstrip('/')}/scenarios/sets/{scenario_set_id}/manifest.json"


def scenario_manifest_uri(root_uri: str, scenario_id: str) -> str:
    return f"{root_uri.rstrip('/')}/scenarios/{scenario_id}/manifest.json"
