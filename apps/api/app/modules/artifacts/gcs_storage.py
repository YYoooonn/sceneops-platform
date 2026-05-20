class GcsArtifactStorage:
    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    def get_download_url(self, path: str) -> str:
        raise NotImplementedError("GcsArtifactStorage is not implemented yet.")
