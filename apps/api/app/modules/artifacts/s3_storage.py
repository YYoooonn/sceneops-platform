class S3ArtifactStorage:
    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    def get_download_url(self, path: str) -> str:
        raise NotImplementedError("S3ArtifactStorage is not implemented yet.")
