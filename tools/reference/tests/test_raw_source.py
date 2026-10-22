"""raw_source.py on a fake Hugging Face origin: the lock is written only from bytes that
agree with the checksums the upstream publishes, and the local tree is verified exactly.
No network is used."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "raw_source.py"
spec = importlib.util.spec_from_file_location("raw_source", SCRIPT)
rs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rs
spec.loader.exec_module(rs)

REVISION = "a" * 40
FILES = {
    ".gitattributes": b"*.parquet filter=lfs\n",
    "README.md": b"# fake pusht\n",
    "data/chunk-000/file-000.parquet": b"PAR1" + bytes(range(256)) * 8,
    "meta/info.json": b'{"fps": 10}',
}
LFS = {"data/chunk-000/file-000.parquet"}


def _listing(files=FILES):
    entries = []
    for path, data in files.items():
        entry = {"type": "file", "path": path, "size": len(data)}
        if path in LFS:
            entry["oid"] = "0" * 40  # the pointer blob, not the content
            entry["lfs"] = {"oid": hashlib.sha256(data).hexdigest(), "size": len(data)}
        else:
            entry["oid"] = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        entries.append(entry)
    return entries


@pytest.fixture()
def origin(monkeypatch):
    """Serves FILES at REVISION; `served` can be changed to simulate a bad mirror."""
    state = {"served": dict(FILES), "listing": _listing(), "requests": []}

    def fake_get(url):
        state["requests"].append(url)
        if "/api/datasets/" in url:
            return json.dumps(state["listing"]).encode(), {}
        path = url.split(f"/resolve/{REVISION}/", 1)[1]
        return state["served"][path], {}

    monkeypatch.setattr(rs, "http_get", fake_get)
    return state


@pytest.fixture()
def source_dir(tmp_path):
    directory = tmp_path / "config" / "fake"
    directory.mkdir(parents=True)
    (directory / "source.json").write_text(
        json.dumps(
            {
                "schema": "sceneops.raw_source/1",
                "source_id": "fake",
                "origin": {
                    "host": "huggingface",
                    "repo_type": "dataset",
                    "repo": "lerobot/fake",
                    "revision": REVISION,
                },
                "include": ["README.md", "data/**", "meta/**"],
                "local_root": "unused",
            }
        )
    )
    return directory


def test_update_lock_pins_the_selection_with_upstream_checksums(
    origin, source_dir, tmp_path
):
    root = tmp_path / "raw"
    report = rs.fetch(source_dir, root, update=True)
    assert report["status"] == "verified", report
    lock = json.loads((source_dir / "source.lock.json").read_text())
    assert [f["path"] for f in lock["files"]] == sorted(set(FILES) - {".gitattributes"})
    assert lock["total_bytes"] == sum(len(FILES[f["path"]]) for f in lock["files"])
    parquet = next(f for f in lock["files"] if f["path"].endswith(".parquet"))
    assert parquet["upstream_checksum"] == (
        "lfs-sha256:" + hashlib.sha256(FILES[parquet["path"]]).hexdigest()
    )
    readme = next(f for f in lock["files"] if f["path"] == "README.md")
    assert readme["upstream_checksum"].startswith("git-blob-sha1:")
    for entry in lock["files"]:
        assert (root / entry["path"]).read_bytes() == FILES[entry["path"]]


def test_fetch_without_update_reuses_the_lock_and_rewrites_nothing(
    origin, source_dir, tmp_path
):
    rs.fetch(source_dir, tmp_path / "first", update=True)
    lock_bytes = (source_dir / "source.lock.json").read_bytes()
    second = rs.fetch(source_dir, tmp_path / "second", update=False)
    assert second["status"] == "verified" and second["fetched"] == 3
    again = rs.fetch(source_dir, tmp_path / "second", update=False)
    assert again["fetched"] == 0
    assert (source_dir / "source.lock.json").read_bytes() == lock_bytes


def test_a_download_that_disagrees_with_upstream_is_never_locked(
    origin, source_dir, tmp_path
):
    origin["served"]["README.md"] = b"# tampered\n"
    with pytest.raises(rs.RawSourceError, match="README.md"):
        rs.fetch(source_dir, tmp_path / "raw", update=True)
    assert not (source_dir / "source.lock.json").exists()


def test_a_download_that_disagrees_with_the_lock_is_never_written(
    origin, source_dir, tmp_path
):
    rs.fetch(source_dir, tmp_path / "first", update=True)
    origin["served"]["meta/info.json"] = b'{"fps": 30}'
    root = tmp_path / "second"
    with pytest.raises(rs.RawSourceError, match="meta/info.json"):
        rs.fetch(source_dir, root, update=False)
    assert not (root / "meta" / "info.json").exists()


def test_verify_reports_changed_missing_and_unexpected_files(
    origin, source_dir, tmp_path
):
    root = tmp_path / "raw"
    rs.fetch(source_dir, root, update=True)
    assert rs.verify(source_dir, root)["problems"] == []
    (root / "README.md").write_bytes(b"# fake pusht!\n")
    (root / "meta" / "info.json").unlink()
    (root / "data" / ".DS_Store").write_bytes(b"x")
    problems = rs.verify(source_dir, root)["problems"]
    assert any(p.startswith("README.md:") for p in problems)
    assert "meta/info.json: missing" in problems
    assert any(p.startswith("data/.DS_Store: not in the lock") for p in problems)


def test_a_changed_definition_invalidates_the_lock(origin, source_dir, tmp_path):
    rs.fetch(source_dir, tmp_path / "raw", update=True)
    definition = json.loads((source_dir / "source.json").read_text())
    definition["origin"]["revision"] = "b" * 40
    (source_dir / "source.json").write_text(json.dumps(definition))
    with pytest.raises(rs.RawSourceError, match="source.json changed"):
        rs.verify(source_dir, tmp_path / "raw")


def test_an_include_pattern_matching_nothing_fails(origin, source_dir, tmp_path):
    definition = json.loads((source_dir / "source.json").read_text())
    definition["include"].append("videos/**")
    (source_dir / "source.json").write_text(json.dumps(definition))
    with pytest.raises(rs.RawSourceError, match="videos"):
        rs.fetch(source_dir, tmp_path / "raw", update=True)


def test_git_blob_sha1_matches_git():
    # `printf 'hello\n' | git hash-object --stdin`
    assert rs.git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_the_committed_pusht_lock_is_consistent_with_its_definition():
    source_dir = rs.CONFIG_ROOT / "lerobot-pusht"
    lock = rs.load_lock(source_dir, rs.load_definition(source_dir))
    assert lock["file_count"] == 7 and lock["total_bytes"] == 7_684_382
    for entry in lock["files"]:
        if entry["upstream_checksum"].startswith("lfs-sha256:"):
            assert (
                entry["sha256"].removeprefix("sha256:")
                == entry["upstream_checksum"].split(":")[1]
            )
