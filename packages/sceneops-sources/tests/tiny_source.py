"""A tiny real source tree and independent expectations: every expected id and
checksum is computed with hashlib from the bytes written here, never with the code
under test."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sceneops_core.sources import SnapshotDeclaration, SnapshotSelection
from sceneops_storage import LocalArtifactStore

# A LeRobot-v3-shaped tree with real bytes; one file spans several hash chunks.
TREE: dict[str, bytes] = {
    ".gitattributes": b"*.parquet filter=lfs diff=lfs merge=lfs -text\n",
    "README.md": b"---\nlicense: mit\n---\n# tiny\n",
    "data/chunk-000/file-000.parquet": b"PAR1" + bytes(range(256)) * 9000 + b"PAR1",
    "meta/info.json": json.dumps({"codebase_version": "v3.0", "fps": 10}).encode(),
    "meta/tasks.parquet": b"PAR1tasksPAR1",
    "videos/observation.image/chunk-000/file-000.mp4": b"\x00\x00\x00\x18ftypisom"
    + b"\x01" * 4096,
    "notes/empty.txt": b"",
}
INCLUDE = ("README.md", "data/**", "meta/**", "videos/**")
SELECTED = sorted(
    p for p in TREE if p.split("/")[0] in {"README.md", "data", "meta", "videos"}
)


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def expected_content_id(files: dict[str, bytes]) -> str:
    listing = [
        {"path": p, "size_bytes": len(files[p]), "sha256": sha(files[p])}
        for p in sorted(files)
    ]
    checksum = sha(canonical(listing))
    document = {
        "content_id_schema": "sceneops.content_id/v1",
        "content_checksum": checksum,
    }
    return "content-" + hashlib.sha256(canonical(document)).hexdigest()[:32]


def write_tree(root: Path, files: dict[str, bytes]) -> Path:
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def declaration(**overrides) -> SnapshotDeclaration:
    fields = {
        "format": "lerobot",
        "upstream": "lerobot/tiny",
        "upstream_revision": "7628202a2180972f291ba1bc6723834921e72c19",
        "selection": SnapshotSelection(include=INCLUDE),
    }
    fields.update(overrides)
    return SnapshotDeclaration(**fields)


async def object_bytes(store: LocalArtifactStore, prefix: str) -> dict[str, bytes]:
    """Every stored object under ``prefix`` with its bytes."""
    return {
        o.uri: await store.read_bytes(o.uri) for o in await store.list_objects(prefix)
    }
