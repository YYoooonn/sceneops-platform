"""Selection of a real local tree and its inventory."""

from __future__ import annotations

import os

import pytest
from tests.tiny_source import INCLUDE, SELECTED, TREE, expected_content_id, sha

from sceneops_core.sources import SnapshotSelection
from sceneops_sources import (
    SourceFileChangedError,
    SourceTreeError,
    build_inventory,
    select_files,
)
from sceneops_sources import tree as tree_module

SELECTION = SnapshotSelection(include=INCLUDE)


def test_selection_is_sorted_and_excludes_what_is_not_declared(make_tree):
    assert select_files(make_tree(), SELECTION) == tuple(SELECTED)


def test_selection_is_independent_of_creation_order(make_tree):
    reversed_tree = dict(reversed(list(TREE.items())))
    assert select_files(make_tree(reversed_tree, "b"), SELECTION) == select_files(
        make_tree(name="a"), SELECTION
    )


def test_inventory_matches_an_independent_hash_of_the_tree(make_tree):
    root = make_tree()
    inventory = build_inventory(root, select_files(root, SELECTION))
    assert [(e.path, e.size_bytes, e.sha256) for e in inventory.files] == [
        (p, len(TREE[p]), sha(TREE[p])) for p in SELECTED
    ]
    assert inventory.content_id == expected_content_id({p: TREE[p] for p in SELECTED})
    assert inventory.total_bytes == sum(len(TREE[p]) for p in SELECTED)


def test_identical_bytes_in_two_trees_are_one_content(make_tree):
    first, second = make_tree(name="a"), make_tree(name="b")
    assert (
        build_inventory(first, select_files(first, SELECTION)).content_id
        == build_inventory(second, select_files(second, SELECTION)).content_id
    )


def test_a_pattern_matching_nothing_fails(make_tree):
    with pytest.raises(SourceTreeError, match="vidoes"):
        select_files(make_tree(), SnapshotSelection(include=("data/**", "vidoes/**")))


def test_a_selected_symlink_fails_and_is_never_followed(make_tree, tmp_path):
    root = make_tree()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"not preserved")
    os.symlink(outside, root / "data" / "link.bin")
    with pytest.raises(SourceTreeError, match="data/link.bin"):
        select_files(root, SELECTION)


def test_a_selected_symlinked_directory_fails(make_tree, tmp_path):
    root = make_tree()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "x.bin").write_bytes(b"x")
    os.symlink(tmp_path / "elsewhere", root / "meta" / "linked")
    with pytest.raises(SourceTreeError, match="meta/linked"):
        select_files(root, SELECTION)


def test_an_unselected_symlink_is_ignored(make_tree, tmp_path):
    root = make_tree()
    os.symlink(root / "README.md", root / "notes" / "readme-link")
    assert select_files(root, SELECTION) == tuple(SELECTED)


@pytest.mark.parametrize(
    "name", ["trailing-space.bin ", "back\\slash.bin", "line\nbreak.bin"]
)
def test_a_selected_path_without_one_spelling_fails(make_tree, name):
    root = make_tree()
    (root / "data" / name).write_bytes(b"x")
    with pytest.raises(SourceTreeError, match="cannot be preserved"):
        select_files(root, SELECTION)


@pytest.mark.parametrize(
    "name",
    [
        " leading-space.bin",
        "n015-2018-07-24-11-22-45+0800__CAM_FRONT__1.jpg",
        "파일.bin",
    ],
)
def test_selected_names_are_kept_verbatim(make_tree, name):
    root = make_tree()
    (root / "data" / name).write_bytes(b"x")
    assert f"data/{name}" in select_files(root, SELECTION)


def test_hidden_files_are_selected_like_any_other(make_tree):
    """Preservation is what the declaration selects; a stray `.DS_Store` under a
    selected directory becomes part of the content."""
    root = make_tree()
    (root / "data" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    assert "data/.DS_Store" in select_files(root, SELECTION)


def test_root_must_be_a_directory(tmp_path):
    with pytest.raises(SourceTreeError, match="not a directory"):
        select_files(tmp_path / "missing", SELECTION)


def test_a_file_changing_while_hashed_fails(make_tree, monkeypatch):
    root = make_tree()
    real_hash = tree_module.hash_file

    def hash_then_append(path):
        result = real_hash(path)
        with path.open("ab") as handle:
            handle.write(b"late write")
        return result

    monkeypatch.setattr(tree_module, "hash_file", hash_then_append)
    with pytest.raises(SourceFileChangedError, match="changed while hashing"):
        build_inventory(root, ["README.md"])


def test_a_selected_file_removed_before_hashing_fails(make_tree):
    root = make_tree()
    paths = select_files(root, SELECTION)
    (root / "meta" / "info.json").unlink()
    with pytest.raises(SourceFileChangedError, match="disappeared"):
        build_inventory(root, paths)
