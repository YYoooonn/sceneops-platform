from typing import Protocol


class ArtifactStorage(Protocol):
    def get_download_url(self, path: str) -> str: ...
