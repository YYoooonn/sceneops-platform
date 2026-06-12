from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.common.schemas import ArtifactUri
from sceneops_core.config import StorageSettings

from sceneops_storage.exceptions import (
    ArtifactNotFoundError,
    ArtifactReadError,
    ArtifactWriteError,
)
from sceneops_storage.uri import join_uri


class S3ArtifactStore(ArtifactStore):
    """Object-storage artifact store for S3-compatible backends (AWS S3, MinIO).

    URIs are ``s3://<bucket>/<key>``. MinIO is selected by pointing
    ``endpoint_url`` at the MinIO service.
    """

    def __init__(self, *, settings: StorageSettings) -> None:
        addressing_style = "path" if settings.endpoint_url else "auto"
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            region_name=settings.region,
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
            config=Config(s3={"addressing_style": addressing_style}),
        )

    def join_uri(self, root: ArtifactUri, *parts: str) -> ArtifactUri:
        return join_uri(root, *parts)

    async def exists(self, uri: ArtifactUri) -> bool:
        return await asyncio.to_thread(self._exists, uri)

    async def read_json(self, uri: ArtifactUri) -> Any:
        return await asyncio.to_thread(self._read_json, uri)

    async def write_json(self, uri: ArtifactUri, payload: Any) -> None:
        await asyncio.to_thread(self._write_json, uri, payload)

    async def read_bytes(self, uri: ArtifactUri) -> bytes:
        return await asyncio.to_thread(self._read_bytes, uri)

    async def write_bytes(self, uri: ArtifactUri, data: bytes) -> None:
        await asyncio.to_thread(self._write_bytes, uri, data)

    async def list_json(self, uri: ArtifactUri) -> list[ArtifactUri]:
        return await asyncio.to_thread(self._list_json, uri)

    async def delete_prefix(self, uri: ArtifactUri) -> None:
        await asyncio.to_thread(self._delete_prefix, uri)

    def public_url(self, uri: ArtifactUri) -> str:
        return uri

    # -- sync implementations run via asyncio.to_thread ----------------------

    def _exists(self, uri: ArtifactUri) -> bool:
        bucket, key = self._parse(uri)
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            if self._is_not_found(exc):
                return False
            raise

    def _read_json(self, uri: ArtifactUri) -> Any:
        bucket, key = self._parse(uri)
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            return json.loads(response["Body"].read().decode("utf-8"))
        except ClientError as exc:
            if self._is_not_found(exc):
                raise ArtifactNotFoundError(uri) from exc
            raise ArtifactReadError(f"Failed to read JSON artifact: {uri}") from exc

    def _write_json(self, uri: ArtifactUri, payload: Any) -> None:
        bucket, key = self._parse(uri)
        try:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType="application/json",
            )
        except ClientError as exc:
            raise ArtifactWriteError(f"Failed to write JSON artifact: {uri}") from exc

    def _read_bytes(self, uri: ArtifactUri) -> bytes:
        bucket, key = self._parse(uri)
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            return response["Body"].read()
        except ClientError as exc:
            if self._is_not_found(exc):
                raise ArtifactNotFoundError(uri) from exc
            raise ArtifactReadError(f"Failed to read binary artifact: {uri}") from exc

    def _write_bytes(self, uri: ArtifactUri, data: bytes) -> None:
        bucket, key = self._parse(uri)
        try:
            self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                ContentType="application/octet-stream",
            )
        except ClientError as exc:
            raise ArtifactWriteError(f"Failed to write binary artifact: {uri}") from exc

    def _list_json(self, uri: ArtifactUri) -> list[ArtifactUri]:
        bucket, prefix = self._parse(uri)
        list_prefix = prefix.rstrip("/") + "/" if prefix else ""

        uris: list[ArtifactUri] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=bucket, Prefix=list_prefix, Delimiter="/"
        ):
            for item in page.get("Contents", []):
                key = item["Key"]
                if key.endswith(".json"):
                    uris.append(f"s3://{bucket}/{key}")

        return sorted(uris)

    def _delete_prefix(self, uri: ArtifactUri) -> None:
        bucket, key = self._parse(uri)
        for k in self._collect_keys(bucket, key):
            self._client.delete_object(Bucket=bucket, Key=k)

    def _collect_keys(self, bucket: str, key: str) -> list[str]:
        keys: set[str] = set()

        try:
            self._client.head_object(Bucket=bucket, Key=key)
            keys.add(key)
        except ClientError as exc:
            if not self._is_not_found(exc):
                raise

        prefix = key.rstrip("/") + "/"
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                keys.add(item["Key"])

        return list(keys)

    @staticmethod
    def _parse(uri: ArtifactUri) -> tuple[str, str]:
        parsed = urlparse(uri)
        if parsed.scheme != "s3":
            raise ValueError(f"Unsupported S3 artifact URI scheme: {uri}")
        bucket = parsed.netloc
        if not bucket:
            raise ValueError(f"S3 artifact URI is missing a bucket: {uri}")
        return bucket, parsed.path.lstrip("/")

    @staticmethod
    def _is_not_found(error: ClientError) -> bool:
        code = error.response.get("Error", {}).get("Code", "")
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code in {"404", "NoSuchKey", "NotFound"} or status == 404
