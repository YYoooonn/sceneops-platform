import json

from typing import Any

from urllib.parse import quote
from pathlib import Path


class LocalArtifactStorage:
    def __init__(self, root: str) -> None:
        self.api_base_url = root.rstrip("/")

    def public_url(self, uri: str) -> str:
        raise NotImplementedError("Artifact storage public url NOT IMPLEMENTED YET")

    async def read_json(self, uri: str) -> Any:
        path = (
            Path(uri.removeprefix("file://"))
            if uri.startswith("file://")
            else Path(uri)
        )
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def get_download_url(self, path: str) -> str:
        encoded_path = quote(path, safe="")
        return f"{self.api_base_url}/api/v1/files/nuscenes?path={encoded_path}"
