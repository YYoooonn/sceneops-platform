"""Preservation of the pinned LeRobot pusht dataset (M3 S1 acceptance 1 and 3,
database-free part).

The expectations are the committed lock (config/reference/lerobot-pusht/
source.lock.json), whose sha256 values were accepted only where they agree with
the checksums Hugging Face publishes (LFS sha256, git blob sha1), and the counts
of the M3 plan. The local copy is git-ignored: `make reference-source-fetch`
creates it. Without it these tests skip and say why.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sceneops_core.sources import SnapshotDeclaration, SnapshotSelection
from sceneops_sources import StoredContentFiles, publish_snapshot
from sceneops_storage import LocalArtifactStore

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = REPO_ROOT / "config" / "reference" / "lerobot-pusht"
DEFINITION = json.loads((SOURCE / "source.json").read_text())
LOCK = json.loads((SOURCE / "source.lock.json").read_text())
PUSHT = REPO_ROOT / DEFINITION["local_root"]
REVISION = "7628202a2180972f291ba1bc6723834921e72c19"

pytestmark = pytest.mark.skipif(
    not PUSHT.is_dir(), reason=f"{PUSHT} absent: run `make reference-source-fetch`"
)


def _canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def _declaration(revision: str = REVISION) -> SnapshotDeclaration:
    return SnapshotDeclaration(
        format="lerobot",
        upstream="lerobot/pusht",
        upstream_revision=revision,
        selection=SnapshotSelection(include=tuple(DEFINITION["include"])),
    )


def _expected_content_id() -> str:
    listing = [
        {"path": f["path"], "size_bytes": f["size_bytes"], "sha256": f["sha256"]}
        for f in LOCK["files"]
    ]
    checksum = "sha256:" + hashlib.sha256(_canonical(listing)).hexdigest()
    document = {
        "content_id_schema": "sceneops.content_id/v1",
        "content_checksum": checksum,
    }
    return "content-" + hashlib.sha256(_canonical(document)).hexdigest()[:32]


def test_the_lock_is_the_plan_selection():
    assert DEFINITION["origin"]["revision"] == REVISION
    assert LOCK["file_count"] == 7 and LOCK["total_bytes"] == 7_684_382
    video = next(f for f in LOCK["files"] if f["path"].endswith(".mp4"))
    assert video["path"] == "videos/observation.image/chunk-000/file-000.mp4"
    assert video["sha256"].startswith("sha256:f58d1185")


async def test_pusht_publishes_its_locked_bytes_and_a_second_revision_writes_no_file(
    tmp_path,
):
    store = LocalArtifactStore(root_uri=str(tmp_path / "store"))
    sources_root = str(tmp_path / "store" / "sources")
    first = await publish_snapshot(
        store=store,
        sources_root=sources_root,
        source_dir=PUSHT,
        declaration=_declaration(),
        annotations={"license": DEFINITION["license"]},
    )
    assert [(e.path, e.size_bytes, e.sha256) for e in first.inventory.files] == [
        (f["path"], f["size_bytes"], f["sha256"]) for f in LOCK["files"]
    ]
    assert first.content_id == _expected_content_id()
    assert (first.files_written, first.inventory.total_bytes) == (7, 7_684_382)

    second = await publish_snapshot(
        store=store,
        sources_root=sources_root,
        source_dir=PUSHT,
        declaration=_declaration("0" * 40),
        annotations={"license": DEFINITION["license"]},
    )
    assert second.content_id == first.content_id
    assert second.snapshot_id != first.snapshot_id
    assert (second.files_written, second.inventory_written) == (0, False)
    descriptors = [o.uri for o in await store.list_objects(f"{sources_root}/snapshots")]
    assert descriptors == sorted([first.descriptor_uri, second.descriptor_uri])

    files = await StoredContentFiles.materialize(
        store=store,
        inventory_uri=first.inventory_uri,
        content_checksum=first.inventory.content_checksum,
        workdir=tmp_path / "work",
    )
    for entry in LOCK["files"]:
        data = files.local_path(entry["path"]).read_bytes()
        assert "sha256:" + hashlib.sha256(data).hexdigest() == entry["sha256"]
