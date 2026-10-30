"""The observed platform state the checks read: ``sceneops.reference_observation/1``.

```text
robot_runs        {run_id: {record, recording_artifact, recording_object}}
units             {scene|episode: {unit_id: {record, revisions[]}}}
manifests         {manifest_artifact_id: {artifact, bytes}}        bytes: the stored UTF-8
payloads          {artifact_id: {artifact, object}}
dataset_versions  {"<dataset_id>/<version>": {version, members[]}}
object            {size_bytes, checksum?}   what the store holds; checksum only
                                            when the bytes were read back
```

Records are the FastAPI rows with snake_case keys; nothing is interpreted while
collecting. The collector is read-only: it issues GET / HEAD requests only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

from . import rules

OBSERVATION_SCHEMA = "sceneops.reference_observation/1"


def empty_observation() -> dict[str, Any]:
    return {
        "schema": OBSERVATION_SCHEMA,
        "robot_runs": {},
        "units": {"scene": {}, "episode": {}},
        "manifests": {},
        "payloads": {},
        "dataset_versions": {},
    }


_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def snake(value: Any) -> Any:
    """camelCase API keys to the snake_case the domain documents use."""
    if isinstance(value, dict):
        return {_CAMEL.sub("_", k).lower(): snake(v) for k, v in value.items()}
    if isinstance(value, list):
        return [snake(v) for v in value]
    return value


class Store(Protocol):
    def head(self, uri: str) -> int | None: ...

    def get(self, uri: str) -> bytes | None: ...


class Api(Protocol):
    def __call__(self, path: str) -> Any: ...


class HttpApi:
    def __init__(self, base_url: str, prefix: str = "/api/v1") -> None:
        self.base = base_url.rstrip("/") + prefix

    def __call__(self, path: str) -> Any:
        try:
            with urllib.request.urlopen(self.base + path, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise


class S3Store:
    """Read-only access to s3:// URIs of the ArtifactStore (MinIO)."""

    def __init__(self, endpoint_url: str, access_key: str, secret_key: str) -> None:
        import boto3  # the repository environment's S3 client; reads only

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    @classmethod
    def from_env(cls) -> S3Store:
        return cls(
            os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9000"),
            os.environ["MINIO_ROOT_USER"],
            os.environ["MINIO_ROOT_PASSWORD"],
        )

    @staticmethod
    def _split(uri: str) -> tuple[str, str]:
        parsed = urllib.parse.urlparse(uri)
        if parsed.scheme != "s3":
            raise ValueError(f"not an s3 URI: {uri}")
        return parsed.netloc, parsed.path.lstrip("/")

    def head(self, uri: str) -> int | None:
        bucket, key = self._split(uri)
        try:
            return int(self.client.head_object(Bucket=bucket, Key=key)["ContentLength"])
        except self.client.exceptions.ClientError:
            return None

    def get(self, uri: str) -> bytes | None:
        bucket, key = self._split(uri)
        try:
            return self.client.get_object(Bucket=bucket, Key=key)["Body"].read()
        except self.client.exceptions.ClientError:
            return None


def _paged(api: Api, path: str, key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    limit = 500
    while True:
        sep = "&" if "?" in path else "?"
        page = (api(f"{path}{sep}limit={limit}&offset={len(rows)}") or {}).get(key, [])
        rows += page
        if len(page) < limit:
            return rows


def _object(store: Store, uri: str | None, *, read: bool) -> dict[str, Any] | None:
    if not uri:
        return None
    if read:
        data = store.get(uri)
        if data is None:
            return None
        return {
            "size_bytes": len(data),
            "checksum": "sha256:" + hashlib.sha256(data).hexdigest(),
        }
    size = store.head(uri)
    return None if size is None else {"size_bytes": size}


def collect(
    api: Api,
    store: Store,
    *,
    robot_run_ids: Iterable[str],
    dataset_versions: Iterable[tuple[str, str]],
    deep: bool = False,
    recording_sink: Callable[[str, bytes], None] | None = None,
) -> dict[str, Any]:
    """Everything the checks read about the given RobotRuns and DatasetVersions.

    deep: read every payload object back and hash it (otherwise HEAD: size only).
    recording_sink(run_id, bytes): receives each recording read back (it is
    always read back, hashed and, for a captured recording, re-derived).
    """
    obs = empty_observation()
    for run_id in robot_run_ids:
        found = api(f"/robot-runs/{urllib.parse.quote(run_id)}")
        if not found:
            continue
        record = snake(found["robotRun"])
        recording = (
            snake(
                (api(f"/artifacts/{record['recording_artifact_id']}") or {}).get(
                    "artifact"
                )
            )
            or {}
        )
        data = store.get(recording["uri"]) if recording.get("uri") else None
        stored = None
        if data is not None:
            stored = {
                "size_bytes": len(data),
                "checksum": "sha256:" + hashlib.sha256(data).hexdigest(),
            }
            if recording_sink is not None:
                recording_sink(run_id, data)
        obs["robot_runs"][run_id] = {
            "record": record,
            "recording_artifact": recording,
            "recording_object": stored,
        }

        for domain, plural in (("scene", "scenes"), ("episode", "episodes")):
            for unit in _paged(
                api, f"/{plural}?robot_run_id={urllib.parse.quote(run_id)}", plural
            ):
                unit = snake(unit)
                unit_id = unit[f"{domain}_id"]
                detail = snake(api(f"/{plural}/{unit_id}") or {})
                revisions = []
                for r in detail.get("revisions", []):
                    unit_of = r.pop(f"{domain}_id")
                    revisions.append({**r, "unit_id": unit_of})
                obs["units"][domain][unit_id] = {"record": unit, "revisions": revisions}
                for revision in revisions:
                    _collect_manifest(
                        api, store, obs, revision["manifest_artifact_id"], deep=deep
                    )

    for dataset_id, version in dataset_versions:
        base = f"/datasets/{urllib.parse.quote(dataset_id)}/versions/{urllib.parse.quote(version)}"
        row = api(base)
        if not row:
            continue
        members = []
        for domain, plural in (("scene", "scenes"), ("episode", "episodes")):
            for revision in _paged(api, f"{base}/{plural}", plural):
                revision = snake(revision)
                members.append(
                    {
                        "domain": domain,
                        "unit_id": revision[f"{domain}_id"],
                        "manifest_artifact_id": revision["manifest_artifact_id"],
                        "manifest_checksum": revision["manifest_checksum"],
                    }
                )
        obs["dataset_versions"][f"{dataset_id}/{version}"] = {
            "version": snake(row["version"]),
            "members": members,
        }
    return obs


def _collect_manifest(
    api: Api, store: Store, obs: dict[str, Any], artifact_id: str, *, deep: bool
) -> None:
    if artifact_id in obs["manifests"]:
        return
    artifact = snake((api(f"/artifacts/{artifact_id}") or {}).get("artifact"))
    if artifact is None:
        obs["manifests"][artifact_id] = {"artifact": {}, "bytes": None}
        return
    data = store.get(artifact["uri"]) if artifact.get("uri") else None
    obs["manifests"][artifact_id] = {
        "artifact": artifact,
        "bytes": None if data is None else data.decode("utf-8"),
    }
    if data is None:
        return
    manifest = json.loads(data)
    for ref in _payload_refs(manifest):
        payload_id = rules.payload_artifact_id_of(ref)
        if payload_id is None or payload_id in obs["payloads"]:
            continue
        record = snake((api(f"/artifacts/{payload_id}") or {}).get("artifact"))
        obs["payloads"][payload_id] = {
            "artifact": record or {},
            "object": _object(store, (record or {}).get("uri"), read=deep),
        }


def _payload_refs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(manifest.get("observations", []))
    for role in ("states", "actions", "events"):
        rows += manifest.get(role, [])
    return [r["payload"] for r in rows if isinstance(r.get("payload"), dict)]


def write_observation(obs: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(obs, sort_keys=True))
