"""Boundary invariants between Acquisition, Dataset Integration and canonical
building inside sceneops-core (ADR-015 §2, §4, §10; MS-1, MS-2, MS-13, MS-14).

    sceneops_core.robots      Acquisition output      RobotRunManifest, RobotRunRecord
    sceneops_core.sources     Dataset Integration     inventory, declaration, descriptor
                              output
    sceneops_core.provenance  canonical boundary      source bindings and revisions,
                                                      pinned builder inputs, fingerprint

Neither ingress contract depends on the other or on the canonical layer; the
canonical layer reads both and decides nothing about either.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import sceneops_core
from sceneops_core.episodes.schemas import EpisodeManifest
from sceneops_core.episodes.testing import episode_manifest, observation
from sceneops_core.provenance import (
    ContentUnitSource,
    PinnedContent,
    PinnedRecording,
    ProducerInfo,
)
from sceneops_core.scenes.schemas import SceneManifest
from sceneops_core.scenes.testing import build_scene_manifest
from sceneops_core.sources import (
    SnapshotDeclaration,
    SnapshotSelection,
    SourceContentInventory,
    SourceFileEntry,
    SourceSnapshotDescriptor,
)

CORE_ROOT = Path(sceneops_core.__file__).parent
CANONICAL_AND_COMPOSITION = {"scenes", "episodes", "datasets", "provenance"}


def _core_imports(subpackage: str) -> set[str]:
    """First-level ``sceneops_core.<x>`` modules imported by a subpackage."""
    found: set[str] = set()
    for path in (CORE_ROOT / subpackage).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                parts = name.split(".")
                if parts[0] == "sceneops_core" and len(parts) > 1:
                    found.add(parts[1])
    return found - {subpackage}


# --- module direction --------------------------------------------------------------


def test_dataset_integration_contracts_depend_on_no_acquisition_or_canonical_module():
    assert _core_imports("sources") <= {"common"}


def test_acquisition_contracts_depend_on_no_dataset_integration_or_canonical_module():
    imports = _core_imports("robots")
    assert not imports & ({"sources"} | CANONICAL_AND_COMPOSITION)


def test_canonical_provenance_depends_on_no_domain_or_composition_module():
    assert not _core_imports("provenance") & {"scenes", "episodes", "datasets"}


# --- builder inputs ----------------------------------------------------------------


@pytest.mark.parametrize("model", [PinnedRecording, PinnedContent])
def test_builder_inputs_name_no_dataset_version_snapshot_or_location(model):
    forbidden = {
        "dataset_id",
        "dataset_version",
        "snapshot_id",
        "format",
        "upstream",
        "uri",
        "local_path",
        "job_id",
    }
    assert not forbidden & set(model.model_fields)


def _inventory() -> SourceContentInventory:
    return SourceContentInventory.create(
        [
            SourceFileEntry(
                path="v1.0-mini/scene.json", size_bytes=10, sha256="sha256:" + "1" * 64
            ),
            SourceFileEntry(
                path="samples/CAM_FRONT/a.jpg",
                size_bytes=20,
                sha256="sha256:" + "2" * 64,
            ),
        ]
    )


def _snapshot(
    revision: str, inventory: SourceContentInventory
) -> SourceSnapshotDescriptor:
    return SourceSnapshotDescriptor(
        declaration=SnapshotDeclaration(
            format="nuscenes",
            upstream="nuscenes",
            upstream_revision=revision,
            selection=SnapshotSelection(include=("samples/**", "v1.0-mini/**")),
        ),
        content_id=inventory.content_id,
    )


def test_units_bind_to_content_not_to_the_snapshot_that_declared_it():
    """Two declarations of the same bytes yield one binding per unit: a
    builder reads PinnedContent, which no descriptor field reaches (R6)."""
    inventory = _inventory()
    first, second = (
        _snapshot("v1.0-mini", inventory),
        _snapshot("v1.0-mini-r2", inventory),
    )
    assert first.snapshot_id != second.snapshot_id

    def built(descriptor: SourceSnapshotDescriptor) -> ContentUnitSource:
        assert descriptor.content_id == inventory.content_id
        return PinnedContent(inventory=inventory).unit_source(
            source_clock="nuscenes.timestamp",
            start_timestamp_ns=1_532_402_927_604_844_000,
            end_timestamp_ns=1_532_402_947_198_048_001,
            unit_key="cc8c0bf57f984915a77078b10eb33198",
        )

    a, b = built(first), built(second)
    assert a == b
    serialized = a.model_dump_json()
    for value in (first.snapshot_id, second.snapshot_id, 'nuscenes"', "v1.0-mini"):
        assert value not in serialized


def test_content_bindings_need_no_robot_run():
    source = PinnedContent(inventory=_inventory()).unit_source(
        source_clock="nuscenes.timestamp",
        start_timestamp_ns=1,
        end_timestamp_ns=2,
        unit_key="scene-token",
    )
    assert "robot_run_id" not in source.model_dump()
    assert "recording" not in json.dumps(source.model_dump(mode="json"))


# --- canonical manifests -----------------------------------------------------------


def _content_lineage(lineage: dict) -> dict:
    """The manifest's lineage with a valid content binding and a producer
    whose fingerprint re-derives from it, so only the source kind differs."""
    source = PinnedContent(inventory=_inventory()).unit_source(
        source_clock=lineage["source"]["source_clock"],
        start_timestamp_ns=lineage["source"]["start_timestamp_ns"],
        end_timestamp_ns=lineage["source"]["end_timestamp_ns"],
        unit_key=lineage["source"]["unit_key"],
    )
    producer = ProducerInfo.create(
        producer_id=lineage["producer"]["producer_id"],
        semantics_version=lineage["producer"]["semantics_version"],
        build_config=lineage["producer"]["build_config"],
        source=source.source_revision(),
    )
    return {
        "source": source.model_dump(mode="json"),
        "producer": producer.model_dump(mode="json"),
    }


def test_scene_manifests_admit_content_bindings_only_with_a_content_builder():
    """No dormant branch: until a content Scene builder produces
    ContentUnitSource lineage, Scene registration and every Scene consumer see
    recording sources only."""
    payload = json.loads(build_scene_manifest().to_canonical_bytes())
    SceneManifest.model_validate(payload)
    with pytest.raises(ValidationError) as rejected:
        SceneManifest.model_validate(
            {**payload, "lineage": _content_lineage(payload["lineage"])}
        )
    assert ("lineage", "source", "source_kind") in {
        error["loc"] for error in rejected.value.errors()
    }


def test_episode_manifests_admit_either_source_binding():
    """The LeRobot Episode builder produces ContentUnitSource lineage, so the
    Episode schema admits both bindings of the union."""
    payload = json.loads(
        episode_manifest(
            occurrences=[observation("/camera/front", 10)]
        ).to_canonical_bytes()
    )
    content = EpisodeManifest.model_validate(
        {**payload, "lineage": _content_lineage(payload["lineage"])}
    )
    assert content.lineage.source.source_kind == "content"
