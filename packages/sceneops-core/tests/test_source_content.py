"""Contract tests for the Dataset Integration output records: the
SourceContent inventory and the SourceSnapshot declaration / descriptor
(ADR-015 §3, §5).

Expected identities are recomputed here from the documented formulas with
hashlib and json, not with the helpers under test.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from sceneops_core.sources import (
    SnapshotDeclaration,
    SnapshotSelection,
    SourceContentError,
    SourceContentInventory,
    SourceFileEntry,
    SourceSnapshotDescriptor,
    SourceSnapshotError,
    load_canonical_source_content_inventory,
    load_canonical_source_snapshot_descriptor,
)


def _sha(char: str) -> str:
    return "sha256:" + char * 64


def _canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _entry(path: str, size: int = 3, sha: str = _sha("1")) -> SourceFileEntry:
    return SourceFileEntry(path=path, size_bytes=size, sha256=sha)


FILES = [
    _entry("meta/info.json", 120, _sha("2")),
    _entry("data/chunk-000/file-000.parquet", 4096, _sha("3")),
    _entry("LICENSE", 0, _sha("4")),
]


def _inventory(files=FILES) -> SourceContentInventory:
    return SourceContentInventory.create(files)


def _declaration(**overrides) -> SnapshotDeclaration:
    fields = {
        "format": "lerobot",
        "upstream": "lerobot/pusht",
        "upstream_revision": "7628202a" + "0" * 32,
        "selection": SnapshotSelection(include=("LICENSE", "data/**", "meta/**")),
    }
    fields.update(overrides)
    return SnapshotDeclaration(**fields)


def _descriptor(**overrides) -> SourceSnapshotDescriptor:
    fields = {
        "declaration": _declaration(),
        "content_id": _inventory().content_id,
        "annotations": {"license": "MIT"},
    }
    fields.update(overrides)
    return SourceSnapshotDescriptor(**fields)


# --- SourceContent inventory -------------------------------------------------


def test_inventory_identity_matches_the_documented_formulas():
    inventory = _inventory()
    assert [entry.path for entry in inventory.files] == [
        "LICENSE",
        "data/chunk-000/file-000.parquet",
        "meta/info.json",
    ]
    file_list = [
        {"path": "LICENSE", "size_bytes": 0, "sha256": _sha("4")},
        {
            "path": "data/chunk-000/file-000.parquet",
            "size_bytes": 4096,
            "sha256": _sha("3"),
        },
        {"path": "meta/info.json", "size_bytes": 120, "sha256": _sha("2")},
    ]
    content_checksum = "sha256:" + hashlib.sha256(_canonical(file_list)).hexdigest()
    assert inventory.content_checksum == content_checksum
    content_id = (
        "content-"
        + hashlib.sha256(
            _canonical(
                {
                    "content_id_schema": "sceneops.content_id/v1",
                    "content_checksum": content_checksum,
                }
            )
        ).hexdigest()[:32]
    )
    assert inventory.content_id == content_id
    assert inventory.total_bytes == 4216


def test_inventory_is_independent_of_input_order():
    assert _inventory(list(reversed(FILES))) == _inventory()


@pytest.mark.parametrize(
    "files",
    [
        [*FILES[:2], _entry("LICENSE", 1, _sha("4"))],  # one size
        [*FILES[:2], _entry("LICENSE", 0, _sha("5"))],  # one byte
        [*FILES[:2], _entry("COPYING", 0, _sha("4"))],  # one path
        FILES[:2],  # one file fewer
    ],
)
def test_any_file_change_is_a_new_content(files):
    assert _inventory(files).content_id != _inventory().content_id


def test_inventory_rejects_unsorted_duplicate_or_tampered_files():
    inventory = _inventory()
    payload = inventory.model_dump(mode="json")
    with pytest.raises(ValidationError, match="sorted"):
        SourceContentInventory.model_validate(
            {**payload, "files": list(reversed(payload["files"]))}
        )
    with pytest.raises(ValidationError, match="unique"):
        SourceContentInventory.create([*FILES, FILES[0]])
    with pytest.raises(ValidationError, match="content_checksum"):
        SourceContentInventory.model_validate(
            {**payload, "content_checksum": _sha("f")}
        )
    with pytest.raises(ValidationError):
        SourceContentInventory.create([])


@pytest.mark.parametrize(
    "path",
    [
        "/abs/file.bin",
        "../outside.bin",
        "a/../b.bin",
        "./a.bin",
        "a//b.bin",
        "a/",
        "a\\b.bin",
        " a.bin",
        "a\nb.bin",
        "",
    ],
)
def test_inventory_paths_have_one_spelling(path):
    with pytest.raises(ValidationError):
        _entry(path)


@pytest.mark.parametrize(
    "path",
    [
        "samples/CAM_FRONT/n015-2018-07-24-11-22-45+0800__CAM_FRONT__1532402927612460.jpg",
        "maps/36092f0b03a857c6a3403e25b4b7aab3.png",
        "videos/observation.image/chunk-000/file-000.mp4",
        "notes/read me.txt",
        "데이터/파일.bin",
    ],
)
def test_inventory_paths_are_kept_verbatim(path):
    assert _entry(path).path == path


@pytest.mark.parametrize(
    "extra", ["format", "upstream", "snapshot_id", "uri", "root", "registered_at"]
)
def test_inventory_has_no_declaration_location_or_time(extra):
    payload = _inventory().model_dump(mode="json")
    with pytest.raises(ValidationError):
        SourceContentInventory.model_validate({**payload, extra: "x"})


def test_inventory_canonical_bytes_round_trip_and_reject_other_bytes():
    inventory = _inventory()
    data = inventory.to_canonical_bytes()
    assert load_canonical_source_content_inventory(data) == inventory
    pretty = json.dumps(json.loads(data), indent=2).encode()
    with pytest.raises(SourceContentError, match="canonical"):
        load_canonical_source_content_inventory(pretty)
    other_schema = json.loads(data)
    other_schema["schema_version"] = "sceneops.source_content_inventory/v2"
    with pytest.raises(SourceContentError, match="schema_version"):
        load_canonical_source_content_inventory(_canonical(other_schema))
    with pytest.raises(SourceContentError):
        load_canonical_source_content_inventory(b"\xff")


# --- SourceSnapshot declaration and descriptor ---------------------------------


def test_snapshot_identity_matches_the_documented_formulas():
    declaration = _declaration()
    selection_checksum = (
        "sha256:"
        + hashlib.sha256(
            _canonical({"include": ["LICENSE", "data/**", "meta/**"]})
        ).hexdigest()
    )
    assert declaration.selection.selection_checksum == selection_checksum
    snapshot_id = (
        "snapshot-"
        + hashlib.sha256(
            _canonical(
                {
                    "snapshot_id_schema": "sceneops.snapshot_id/v1",
                    "format": "lerobot",
                    "upstream": "lerobot/pusht",
                    "upstream_revision": "7628202a" + "0" * 32,
                    "selection_checksum": selection_checksum,
                }
            )
        ).hexdigest()[:32]
    )
    assert declaration.snapshot_id == snapshot_id
    assert _descriptor().snapshot_id == snapshot_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"format": "nuscenes"},
        {"upstream": "lerobot/aloha_static_coffee"},
        {"upstream_revision": "v1.0-mini"},
        {"selection": SnapshotSelection(include=("data/**", "meta/**"))},
    ],
)
def test_every_declaration_field_is_snapshot_identity(overrides):
    assert _declaration(**overrides).snapshot_id != _declaration().snapshot_id


def test_selection_rejects_unsorted_duplicate_empty_or_absolute_patterns():
    for include in (
        ("meta/**", "data/**"),
        ("data/**", "data/**"),
        (),
        ("/data/**",),
        (" data/**",),
    ):
        with pytest.raises(ValidationError):
            SnapshotSelection(include=include)


@pytest.mark.parametrize(
    "overrides",
    [
        {"format": "LeRobot"},
        {"format": ""},
        {"upstream": ""},
        {"upstream_revision": " v1.0-mini"},
    ],
)
def test_declaration_rejects_non_canonical_fields(overrides):
    with pytest.raises(ValidationError):
        _declaration(**overrides)


@pytest.mark.parametrize(
    "extra",
    ["uri", "local_path", "retrieved_at", "operator", "format_version", "snapshot_id"],
)
def test_descriptor_carries_no_retrieval_facts_or_format_version(extra):
    payload = _descriptor().model_dump(mode="json")
    with pytest.raises(ValidationError):
        SourceSnapshotDescriptor.model_validate({**payload, extra: "x"})
    with pytest.raises(ValidationError):
        SourceSnapshotDescriptor.model_validate(
            {**payload, "declaration": {**payload["declaration"], extra: "x"}}
        )


def test_descriptor_validates_content_id_and_annotation_keys():
    with pytest.raises(ValidationError, match="content_id"):
        _descriptor(content_id="content-xyz")
    with pytest.raises(ValidationError, match="annotation key"):
        _descriptor(annotations={"License": "MIT"})


def test_descriptor_canonical_bytes_round_trip_and_checksum():
    descriptor = _descriptor()
    data = descriptor.to_canonical_bytes()
    assert load_canonical_source_snapshot_descriptor(data) == descriptor
    assert descriptor.descriptor_checksum == (
        "sha256:" + hashlib.sha256(data).hexdigest()
    )
    reordered = b" " + data
    with pytest.raises(SourceSnapshotError):
        load_canonical_source_snapshot_descriptor(reordered)


# --- ADR-015 §3 case table, at the identity level ------------------------------
# Registration (not implemented here) decides reuse / no-op / conflict from
# exactly these ids; the table is only decidable if they separate as below.


def test_new_upstream_revision_with_identical_bytes_reuses_the_content():
    first = _descriptor()
    second = _descriptor(declaration=_declaration(upstream_revision="b" * 40))
    assert first.content_id == second.content_id
    assert first.snapshot_id != second.snapshot_id


def test_new_upstream_revision_with_different_bytes_is_a_new_content():
    changed = _inventory([*FILES[:2], _entry("LICENSE", 1, _sha("9"))])
    second = _descriptor(
        declaration=_declaration(upstream_revision="b" * 40),
        content_id=changed.content_id,
    )
    assert second.content_id != _descriptor().content_id


def test_same_declaration_with_different_bytes_is_one_snapshot_id_two_contents():
    changed = _inventory([*FILES[:2], _entry("LICENSE", 1, _sha("9"))])
    conflicting = _descriptor(content_id=changed.content_id)
    assert conflicting.snapshot_id == _descriptor().snapshot_id
    assert conflicting.content_id != _descriptor().content_id


def test_same_declaration_with_other_annotations_is_one_snapshot_id_two_descriptors():
    relabelled = _descriptor(annotations={"license": "Apache-2.0"})
    assert relabelled.snapshot_id == _descriptor().snapshot_id
    assert relabelled.content_id == _descriptor().content_id
    assert relabelled.descriptor_checksum != _descriptor().descriptor_checksum


def test_two_selections_of_identical_files_share_one_content():
    wide = _descriptor(
        declaration=_declaration(selection=SnapshotSelection(include=("**",)))
    )
    assert wide.content_id == _descriptor().content_id
    assert wide.snapshot_id != _descriptor().snapshot_id
