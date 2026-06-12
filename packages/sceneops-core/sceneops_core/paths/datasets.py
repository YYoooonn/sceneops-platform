# paths/datasets.py
def dataset_manifest_uri(root_uri: str, dataset_id: str, dataset_version: str) -> str:
    return (
        f"{root_uri.rstrip('/')}/datasets/"
        f"{dataset_id}/versions/{dataset_version}/manifest.json"
    )
