def scene_manifest_uri(root_uri: str, scene_id: str) -> str:
    return f"{root_uri.rstrip('/')}/scenes/{scene_id}/manifest.json"


def world_state_manifest_uri(root_uri: str, scene_id: str) -> str:
    return f"{root_uri.rstrip('/')}/scenes/{scene_id}/world_state.json"


def scene_package_uri(root_uri: str, scene_id: str, package_type: str) -> str:
    return f"{root_uri.rstrip('/')}/scenes/{scene_id}/packages/{package_type}"
