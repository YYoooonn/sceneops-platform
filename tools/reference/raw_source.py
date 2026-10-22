#!/usr/bin/env python3
"""raw_source.py — pin a raw upstream dataset locally with a checksum lock.

    fetch    download every locked file that is missing under the local root
             (config/reference/<source>/source.json `local_root`) from the
             pinned upstream revision, verify each against the lock, and then
             verify the whole local tree. With --update-lock the lock is
             (re)written from the upstream listing at the pinned revision; that
             is the only way it changes.
    verify   the same local checks, offline and read-only.

The lock (`source.lock.json`, Git) records each selected file's path, size,
sha256 and the checksum the upstream itself publishes for it: the LFS sha256
of an LFS file, the git blob sha1 of any other file. A download is accepted only
when it agrees with both, so the lock is independent evidence of the bytes, not
a record of whatever was downloaded. The local copy is git-ignored.

Standard library only: this tool imports no SceneOps package, so the checksums
it pins can serve as independent expectations for SceneOps tests. Supported
origin: Hugging Face datasets. Downloaded bytes are only hashed and written,
never parsed or executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPO_ROOT / "config" / "reference"
DEFINITION_SCHEMA = "sceneops.raw_source/1"
LOCK_SCHEMA = "sceneops.raw_source_lock/1"
HF_ENDPOINT = "https://huggingface.co"
TIMEOUT_SECONDS = 60
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class RawSourceError(RuntimeError):
    pass


# --- upstream access (replaced in tests) -----------------------------------------


def http_get(url: str) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": "sceneops-raw-source"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read(), {k.lower(): v for k, v in response.headers.items()}


# --- definition and lock ---------------------------------------------------------


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def load_definition(source_dir: Path) -> dict[str, Any]:
    definition = json.loads((source_dir / "source.json").read_text())
    if definition.get("schema") != DEFINITION_SCHEMA:
        raise RawSourceError(
            f"unsupported definition schema {definition.get('schema')!r}"
        )
    origin = definition["origin"]
    if (origin.get("host"), origin.get("repo_type")) != ("huggingface", "dataset"):
        raise RawSourceError("only Hugging Face dataset origins are supported")
    if not _REVISION_RE.fullmatch(origin.get("revision", "")):
        raise RawSourceError("origin.revision must be a full 40-hex commit sha")
    for pattern in definition["include"]:
        _include_matcher(pattern)
    return definition


def definition_sha256(definition: dict[str, Any]) -> str:
    return _sha256(_canonical(definition))


def _include_matcher(pattern: str) -> Callable[[str], bool]:
    """Exact paths and `<dir>/**` only: the lock tool needs no richer dialect."""
    if pattern.endswith("/**") and "*" not in pattern[:-3] and "?" not in pattern:
        prefix = pattern[:-2]
        return lambda path: path.startswith(prefix)
    if any(c in pattern for c in "*?[]{}!\\"):
        raise RawSourceError(f"unsupported include pattern {pattern!r}")
    return lambda path: path == pattern


def select(definition: dict[str, Any], paths: list[str]) -> list[str]:
    selected = set()
    for pattern in definition["include"]:
        hits = [p for p in paths if _include_matcher(pattern)(p)]
        if not hits:
            raise RawSourceError(
                f"include pattern {pattern!r} matches no upstream file"
            )
        selected.update(hits)
    return sorted(selected)


def lock_path(source_dir: Path) -> Path:
    return source_dir / "source.lock.json"


def load_lock(source_dir: Path, definition: dict[str, Any]) -> dict[str, Any]:
    path = lock_path(source_dir)
    if not path.is_file():
        raise RawSourceError(
            f"{path} does not exist; create it with fetch --update-lock"
        )
    lock = json.loads(path.read_text())
    problems = []
    if lock.get("schema") != LOCK_SCHEMA:
        problems.append(f"lock schema {lock.get('schema')!r}")
    if lock.get("definition_sha256") != definition_sha256(definition):
        problems.append("source.json changed since the lock was written")
    files = lock.get("files", [])
    if [f["path"] for f in files] != sorted({f["path"] for f in files}):
        problems.append("lock files are not sorted and unique")
    if lock.get("file_count") != len(files):
        problems.append("file_count disagrees with files")
    if lock.get("total_bytes") != sum(f["size_bytes"] for f in files):
        problems.append("total_bytes disagrees with files")
    if problems:
        raise RawSourceError(f"{path}: " + "; ".join(problems))
    return lock


def write_lock(source_dir: Path, lock: dict[str, Any]) -> None:
    lock_path(source_dir).write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")


# --- upstream listing and download ----------------------------------------------


def upstream_listing(origin: dict[str, str]) -> list[dict[str, Any]]:
    repo = urllib.parse.quote(origin["repo"], safe="/")
    url = f"{HF_ENDPOINT}/api/datasets/{repo}/tree/{origin['revision']}?recursive=true"
    entries: list[dict[str, Any]] = []
    while url:
        body, headers = http_get(url)
        entries.extend(e for e in json.loads(body) if e.get("type") == "file")
        match = _LINK_NEXT_RE.search(headers.get("link", ""))
        url = match.group(1) if match else ""
    return entries


def upstream_checksum(entry: dict[str, Any]) -> str:
    lfs = entry.get("lfs")
    if lfs:
        return f"lfs-sha256:{lfs['oid']}"
    return f"git-blob-sha1:{entry['oid']}"


def _check_upstream(data: bytes, expected: str) -> bool:
    kind, _, digest = expected.partition(":")
    if kind == "lfs-sha256":
        return hashlib.sha256(data).hexdigest() == digest
    if kind == "git-blob-sha1":
        return git_blob_sha1(data) == digest
    raise RawSourceError(f"unknown upstream checksum kind {kind!r}")


def download(origin: dict[str, str], path: str) -> bytes:
    repo = urllib.parse.quote(origin["repo"], safe="/")
    quoted = urllib.parse.quote(path, safe="/")
    data, _ = http_get(
        f"{HF_ENDPOINT}/datasets/{repo}/resolve/{origin['revision']}/{quoted}"
    )
    return data


# --- local tree -------------------------------------------------------------------


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _local_files(root: Path) -> dict[str, Path]:
    found = {}
    for directory, _, filenames in os.walk(root):
        for name in filenames:
            path = Path(directory) / name
            found[path.relative_to(root).as_posix()] = path
    return found


def verify_tree(root: Path, lock: dict[str, Any]) -> list[str]:
    """Problems of the local tree against the lock; empty when it is exact."""
    if not root.is_dir():
        return [f"{root} does not exist; run fetch"]
    problems = []
    local = _local_files(root)
    locked = {f["path"]: f for f in lock["files"]}
    for path in sorted(set(local) - set(locked)):
        problems.append(f"{path}: not in the lock (unexpected local file)")
    for path, entry in locked.items():
        file = local.get(path)
        if file is None:
            problems.append(f"{path}: missing")
            continue
        if file.is_symlink() or not file.is_file():
            problems.append(f"{path}: not a regular file")
            continue
        data = file.read_bytes()
        if len(data) != entry["size_bytes"]:
            problems.append(f"{path}: {len(data)} bytes, locked {entry['size_bytes']}")
        elif _sha256(data) != entry["sha256"]:
            problems.append(f"{path}: sha256 {_sha256(data)}, locked {entry['sha256']}")
    return problems


# --- commands ---------------------------------------------------------------------


def _accept(data: bytes, entry: dict[str, Any], path: str) -> None:
    if len(data) != entry["size_bytes"] or _sha256(data) != entry["sha256"]:
        raise RawSourceError(f"{path}: downloaded bytes disagree with the lock")
    if not _check_upstream(data, entry["upstream_checksum"]):
        raise RawSourceError(
            f"{path}: downloaded bytes disagree with {entry['upstream_checksum']}"
        )


def update_lock(
    source_dir: Path, definition: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, bytes]]:
    origin = definition["origin"]
    listing = {e["path"]: e for e in upstream_listing(origin)}
    files, downloaded = [], {}
    for path in select(definition, sorted(listing)):
        entry = listing[path]
        data = download(origin, path)
        expected = upstream_checksum(entry)
        if not _check_upstream(data, expected) or len(data) != entry["size"]:
            raise RawSourceError(f"{path}: download disagrees with upstream {expected}")
        files.append(
            {
                "path": path,
                "size_bytes": len(data),
                "sha256": _sha256(data),
                "upstream_checksum": expected,
            }
        )
        downloaded[path] = data
    lock = {
        "schema": LOCK_SCHEMA,
        "source_id": definition["source_id"],
        "definition_sha256": definition_sha256(definition),
        "revision": origin["revision"],
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(f["size_bytes"] for f in files),
    }
    write_lock(source_dir, lock)
    return lock, downloaded


def fetch(source_dir: Path, root: Path, *, update: bool) -> dict[str, Any]:
    definition = load_definition(source_dir)
    downloaded: dict[str, bytes] = {}
    if update:
        lock, downloaded = update_lock(source_dir, definition)
    lock = load_lock(source_dir, definition)
    fetched = 0
    for entry in lock["files"]:
        path = root / entry["path"]
        if path.exists() or path.is_symlink():
            continue  # verified below, never overwritten
        if entry["path"] in downloaded:
            data = downloaded[entry["path"]]
        else:
            data = download(definition["origin"], entry["path"])
        _accept(data, entry, entry["path"])
        _atomic_write(path, data)
        fetched += 1
    problems = verify_tree(root, lock)
    return _report(definition, lock, root, problems, fetched=fetched)


def verify(source_dir: Path, root: Path) -> dict[str, Any]:
    definition = load_definition(source_dir)
    lock = load_lock(source_dir, definition)
    return _report(definition, lock, root, verify_tree(root, lock))


def _report(
    definition, lock, root, problems, *, fetched: int | None = None
) -> dict[str, Any]:
    report = {
        "source_id": definition["source_id"],
        "revision": lock["revision"],
        "root": str(root),
        "file_count": lock["file_count"],
        "total_bytes": lock["total_bytes"],
        "status": "failed" if problems else "verified",
        "problems": problems,
    }
    if fetched is not None:
        report["fetched"] = fetched
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["fetch", "verify"])
    parser.add_argument("--source", required=True, help="config/reference/<source>")
    parser.add_argument(
        "--root", type=Path, help="override the definition's local_root"
    )
    parser.add_argument("--update-lock", action="store_true")
    args = parser.parse_args(argv)
    if args.update_lock and args.command != "fetch":
        parser.error("--update-lock applies to fetch only")
    source_dir = CONFIG_ROOT / args.source
    try:
        definition = load_definition(source_dir)
        root = args.root or REPO_ROOT / definition["local_root"]
        if args.command == "fetch":
            report = fetch(source_dir, root, update=args.update_lock)
        else:
            report = verify(source_dir, root)
    except (RawSourceError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"source": args.source, "status": "failed", "problems": [str(exc)]}
            )
        )
        return 1
    print(json.dumps(report))
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
