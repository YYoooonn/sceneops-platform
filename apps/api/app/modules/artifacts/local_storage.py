from urllib.parse import quote


class LocalArtifactStorage:
    def __init__(self, api_base_url: str) -> None:
        self.api_base_url = api_base_url.rstrip("/")

    def get_download_url(self, path: str) -> str:
        encoded_path = quote(path, safe="")
        return f"{self.api_base_url}/api/v1/files/nuscenes?path={encoded_path}"
