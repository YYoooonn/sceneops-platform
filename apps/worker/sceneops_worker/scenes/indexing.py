"""Derived Scene index entries.

An index entry is a snapshot of one SceneRecord at its current revision:
it pins ``manifest_artifact_id`` + ``manifest_checksum`` and carries the
location of those immutable bytes. Readers verify the checksum before use
(``SceneArtifactStore.read_pinned_manifest``), so a derived index never
needs to re-read manifests to be built.
"""

from __future__ import annotations

from sceneops_core.datasets.schemas import DatasetSceneIndexEntry
from sceneops_core.scenes.schemas import SceneRecord

from sceneops_worker.core.context import WorkerContext
from sceneops_worker.scenes.resolver import InconsistentSceneStateError

# Upper bound of SceneRecords one derived index covers; a DatasetVersion
# with more members fails loudly instead of producing a partial index.
MAX_INDEXED_SCENES = 10_000


async def list_dataset_version_scenes(
    context: WorkerContext, *, dataset_id: str, dataset_version: str
) -> list[SceneRecord]:
    scenes = await context.scene_store.list(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        limit=MAX_INDEXED_SCENES + 1,
    )
    if len(scenes) > MAX_INDEXED_SCENES:
        raise ValueError(
            f"{dataset_id}/{dataset_version} has more than {MAX_INDEXED_SCENES} "
            "registered Scenes; derived Scene indexes do not page yet"
        )
    return scenes


async def index_entry_for(
    context: WorkerContext, record: SceneRecord
) -> DatasetSceneIndexEntry:
    artifact = await context.artifact_record_store.get(record.manifest_artifact_id)
    if artifact is None or artifact.checksum != record.manifest_checksum:
        raise InconsistentSceneStateError(
            f"Scene {record.scene_id} pins manifest artifact "
            f"{record.manifest_artifact_id} ({record.manifest_checksum}), which is "
            "missing or has a different checksum"
        )
    return DatasetSceneIndexEntry(
        scene_id=record.scene_id,
        manifest_artifact_id=record.manifest_artifact_id,
        manifest_checksum=record.manifest_checksum,
        manifest_uri=artifact.uri,
        keyframe_count=record.keyframe_count,
        observation_count=record.observation_count,
        annotation_count=record.annotation_count,
        observed_channels=list(record.observed_channels),
        window_clock=record.window_clock,
        window_start_timestamp_ns=record.window_start_timestamp_ns,
        window_end_timestamp_ns=record.window_end_timestamp_ns,
    )
