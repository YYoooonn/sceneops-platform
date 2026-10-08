import boto3
from botocore.config import Config
from botocore.client import BaseClient

from inference_server.config import InferenceServerSettings


def build_s3_client(
    settings: InferenceServerSettings,
) -> BaseClient | None:
    if not settings.s3_enabled:
        return None

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        config=Config(
            s3={
                "addressing_style": settings.s3_addressing_style,
            }
        ),
    )
