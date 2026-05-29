from enum import StrEnum

class ArtifactBackend(StrEnum):
    LOCAL = "local"
    MINIO = "minio"
    S3 = "s3"
    GCS = "gcs"
