"""SceneManifest v5: the content variant (ADR-015 §6.5, §6.6; ADR-017).

A Scene binds to a recording segment or to a native unit of a SourceContent;
a preserved file is referenced in place by its sources Logical URI, the same
PayloadRef a recording payload is; native identifiers are verbatim and name
one observation or one group; the loader reads v5 only.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from sceneops_core.artifacts.logical_uri import LogicalNamespace, format_logical_uri
from sceneops_core.artifacts.schemas import PayloadRef
from sceneops_core.provenance import ContentUnitSource, manifest_artifact_id_for
from sceneops_core.scenes.schemas import (
    SceneManifest,
    SceneRecord,
    UnsupportedSceneManifestVersionError,
    load_canonical_scene_manifest,
    project_scene_revision,
    scene_id_for,
)
from sceneops_core.scenes.testing import build_scene_manifest
from sceneops_core.sources import content_id_for

CHECKSUM = "sha256:" + "c" * 64
CONTENT_ID = content_id_for(CHECKSUM)
OTHER_CHECKSUM = "sha256:" + "d" * 64


def _content_source(unit_key: str = "cc8c0bf57f984915a77078b10eb33198"):
    return ContentUnitSource(
        content_id=CONTENT_ID,
        content_checksum=CHECKSUM,
        source_clock="mcap_log_time",
        start_timestamp_ns=0,
        end_timestamp_ns=10_000_000_000,
        unit_key=unit_key,
    )


def _file_ref(path: str, *, content_id: str = CONTENT_ID) -> dict:
    return PayloadRef(
        uri=format_logical_uri(
            LogicalNamespace.SOURCES, f"contents/{content_id}/files/{path}"
        ),
        checksum="sha256:" + hashlib.sha256(path.encode()).hexdigest(),
        size_bytes=len(path),
        media_type="image/jpeg",
    ).model_dump(mode="json")


def _payload(manifest: SceneManifest) -> dict:
    return json.loads(manifest.to_canonical_bytes())


def _with_file_refs(payload: dict, *, content_id: str = CONTENT_ID) -> dict:
    for i, observation in enumerate(payload["observations"]):
        observation["payload"] = _file_ref(f"samples/x/{i}.jpg", content_id=content_id)
    return payload


def test_a_content_scene_references_files_of_its_own_content():
    payload = _with_file_refs(_payload(build_scene_manifest(source=_content_source())))
    manifest = SceneManifest.model_validate(payload)
    assert manifest.lineage.source.source_id == CONTENT_ID
    assert {o.payload.logical_uri().namespace for o in manifest.observations} == {
        LogicalNamespace.SOURCES
    }
    data = manifest.to_canonical_bytes()
    assert load_canonical_scene_manifest(data) == manifest


@pytest.mark.parametrize("scene_source", ["recording", "other_content"])
def test_whose_bytes_a_uri_names_is_not_decided_by_the_schema(scene_source):
    """The schema validates the reference itself; that it names bytes of the
    Scene's own source is checked where the revision is registered
    (sceneops_worker.payload_registration)."""
    if scene_source == "recording":
        payload = _with_file_refs(_payload(build_scene_manifest()))
    else:
        payload = _with_file_refs(
            _payload(build_scene_manifest(source=_content_source())),
            content_id=content_id_for(OTHER_CHECKSUM),
        )
    SceneManifest.model_validate(payload)


@pytest.mark.parametrize("structure", ["observations", "groups"])
def test_a_native_identifier_names_one_structure(structure):
    payload = _payload(build_scene_manifest(source=_content_source()))
    for item in payload[structure]:
        item["native_ref"] = "5ace90b379af485b9dcb1584b01e7212"
    with pytest.raises(ValidationError, match="repeat native_ref"):
        SceneManifest.model_validate(payload)
    for i, item in enumerate(payload[structure]):
        item["native_ref"] = f"token-{i}"
    SceneManifest.model_validate(payload)


def test_a_recording_scene_serializes_absent_native_refs():
    payload = _payload(build_scene_manifest())
    assert payload["schema_version"] == "sceneops.scene_manifest/v5"
    assert {o["native_ref"] for o in payload["observations"]} == {None}
    assert {g["native_ref"] for g in payload["groups"]} == {None}


@pytest.mark.parametrize(
    "version", ["sceneops.scene_manifest/v3", "sceneops.scene_manifest/v4"]
)
def test_earlier_versions_are_not_read(version):
    payload = _payload(build_scene_manifest())
    payload["schema_version"] = version
    with pytest.raises(UnsupportedSceneManifestVersionError):
        load_canonical_scene_manifest(json.dumps(payload).encode())


def test_a_content_revision_names_its_content_and_no_robot_run():
    manifest = build_scene_manifest(source=_content_source())
    source = manifest.lineage.source
    scene_id = scene_id_for(source)
    record = project_scene_revision(
        manifest=manifest,
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=scene_id, manifest_checksum=manifest.checksum()
        ),
        manifest_checksum=manifest.checksum(),
    )
    assert (record.source_kind, record.content_id, record.robot_run_id) == (
        "content",
        CONTENT_ID,
        None,
    )
    unit = record.unit()
    assert (unit.scene_id, unit.source_id) == (scene_id, CONTENT_ID)
    with pytest.raises(ValidationError, match="names its source by content_id only"):
        SceneRecord(
            scene_id=scene_id,
            source_kind="content",
            robot_run_id="run-1",
            content_id=CONTENT_ID,
            unit_key=source.unit_key,
        )
    with pytest.raises(ValidationError, match="is not the unit of content source"):
        SceneRecord(
            scene_id=scene_id,
            source_kind="content",
            content_id=content_id_for(OTHER_CHECKSUM),
            unit_key=source.unit_key,
        )
