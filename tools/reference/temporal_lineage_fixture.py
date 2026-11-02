"""A synthetic platform state for the temporal lineage verifier: real canonical
manifests, aligned by the real engine, exported by the real writers into a local
store. Used by `temporal_lineage_verify.py selftest` and its unit tests; it proves
the verifier's checks, not the platform.

Every kind of clock instance the contract distinguishes is here:

```text
episode      source                    clock                 origin
rec-a-0      RobotRun run-a, segment 0 mcap_log_time         source
rec-a-1      RobotRun run-a, segment 1 mcap_log_time         source   comparable with rec-a-0
rec-b-0      RobotRun run-b            mcap_log_time         source   same times, not comparable
native-0/-1  one content, two units    lerobot.frame_time    unit     same times, not comparable
epoch-0      a content                 x.epoch               unix_epoch

scene        source                    window / lidar and keyframes / camera clock
scene-a      RobotRun run-a, segment 0 mcap_log_time / lidar.header_stamp / camera.header_stamp
scene-a-1    RobotRun run-a, segment 1 the same clocks and times, comparable with scene-a
scene-b      RobotRun run-b            the same clocks and times, not comparable
scene-native a content                 x.clock (unix_epoch)
```

A Scene of run-a and an Episode of run-a share ``mcap_log_time``; the base export holds
the first four Episodes and the incremental export adds the last two over its shards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl
from temporal_lineage import sha256

from sceneops_analytics import (
    AnalyticsTableWriter,
    SceneRevisionContent,
    build_keyframes_table,
    build_learning_episodes_table,
    build_observations_table,
    build_scenes_table,
    write_incremental_sharded_learning_tables,
    write_sharded_learning_tables,
)
from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.common.derived_ids import (
    aligned_episode_artifact_id,
    derived_artifact_id,
)
from sceneops_core.episodes.alignment import (
    AlignedEpisodeArtifact,
    EpisodeSourceRevision,
    TemporalAlignmentConfig,
    TemporalSourceContext,
    align_episode,
)
from sceneops_core.episodes.learning_export import (
    LEARNING_DATA_LAYOUT_VERSION_SHARDED,
    LEARNING_DATA_SCHEMA_VERSION,
    AlignedArtifactRevision,
    LearningDataExportConfig,
    LearningDataExportManifest,
    default_shard_policy,
    learning_data_export_id,
    plan_incremental_export,
)
from sceneops_core.episodes.schemas import (
    EpisodeManifest,
    episode_id_for,
    project_episode_revision,
)
from sceneops_core.episodes.testing import action, episode_manifest, observation, state
from sceneops_core.provenance import (
    ContentUnitSource,
    ProducerInfo,
    manifest_artifact_id_for,
)
from sceneops_core.scenes.schemas import (
    SceneManifest,
    project_scene_revision,
    scene_id_for,
)
from sceneops_core.scenes.testing import build_scene_manifest, recording_source
from sceneops_core.sources import content_id_for
from sceneops_core.temporal import frame_time_ns
from sceneops_storage import LocalArtifactStore

S = 1_000_000_000
DATASET = "temporal-selftest"
VERSION = "v1"
RECORDING_CLOCK = "mcap_log_time"
NATIVE_CLOCK = "lerobot.frame_time"
EPOCH_CLOCK = "x.epoch"
CONTENT = "sha256:" + "c" * 64
EPOCH_CONTENT = "sha256:" + "e" * 64
FPS = 10
RECORDING_CONFIG = TemporalAlignmentConfig(
    target_frequency_hz=1.0, tolerance_us=500_000, max_gap_us=2_000_000
)
NATIVE_CONFIG = TemporalAlignmentConfig(
    target_frequency_hz=10.0, tolerance_us=50_000, max_gap_us=200_000
)


def recording_episode(run: str, unit_key: str) -> EpisodeManifest:
    """Equal times for every RobotRun and segment: only the instance tells them apart."""
    return episode_manifest(
        [
            state("/odom", 0, x=0.0),
            state("/odom", S, x=1.0),
            state("/odom", 2 * S, x=2.0),
            action("/cmd", 0, steering=0.0),
            action("/cmd", 2 * S, steering=0.5),
            observation("/camera", S),
        ],
        robot_run_id=run,
        unit_key=unit_key,
        clock=RECORDING_CLOCK,
        window=(0, 2 * S + 1),
    )


def native_episode(
    unit_key: str,
    *,
    content: str = CONTENT,
    origin: str = "unit",
    clock: str = NATIVE_CLOCK,
) -> EpisodeManifest:
    """A LeRobot-style Episode built without transition features, on a frame grid."""
    frames = 5
    offset = 1_600_000_000 * S if origin == "unix_epoch" else 0
    source = ContentUnitSource(
        content_id=content_id_for(content),
        content_checksum=content,
        source_clock=clock,
        start_timestamp_ns=offset,
        end_timestamp_ns=offset + frame_time_ns(frames - 1, FPS) + 1,
        unit_key=unit_key,
    )
    producer = ProducerInfo.create(
        producer_id="sceneops.lerobot_episode_builder",
        semantics_version=1,
        build_config={"features": []},
        source=source.source_revision(),
    )

    def occurrence(topic: str, k: int) -> dict[str, Any]:
        return {
            "occurrence_id": f"{topic}-{k:08d}",
            "topic": topic,
            "timestamp_ns": offset + frame_time_ns(k, FPS),
            "values": {"value": [float(k)]},
        }

    return EpisodeManifest.model_validate(
        {
            "schema_version": "sceneops.episode_manifest/v4",
            "lineage": {
                "source": source.model_dump(mode="json"),
                "producer": producer.model_dump(mode="json"),
            },
            "clock_domains": [{"clock": clock, "origin": origin, "resolution_ns": 1}],
            "frame_timeline": (
                {"clock": clock, "fps": FPS, "frame_count": frames}
                if origin == "unit"
                else None
            ),
            "streams": [
                {
                    "topic": topic,
                    "role": role,
                    "schema_name": "float32",
                    "source_clock": clock,
                    "fields": [{"name": "value", "path": topic}],
                }
                for topic, role in (
                    ("action", "action"),
                    ("observation.state", "state"),
                )
            ],
            "states": [occurrence("observation.state", k) for k in range(frames)],
            "actions": [occurrence("action", k) for k in range(frames)],
        }
    )


def recording_scene(run: str, unit_key: str = "segment-0000") -> SceneManifest:
    return build_scene_manifest(
        source=recording_source(robot_run_id=run, unit_key=unit_key),
        source_clock="lidar.header_stamp",
        camera_clock="camera.header_stamp",
    )


def native_scene() -> SceneManifest:
    source = ContentUnitSource(
        content_id=content_id_for(EPOCH_CONTENT),
        content_checksum=EPOCH_CONTENT,
        source_clock="x.clock",
        start_timestamp_ns=0,
        end_timestamp_ns=10 * S,
        unit_key="scene-native",
    )
    payload = json.loads(build_scene_manifest(source=source).to_canonical_bytes())
    for domain in payload["clock_domains"]:
        domain["origin"] = "unix_epoch"
    return SceneManifest.model_validate(payload)


class _ReadOnlyStore:
    def __init__(self, store: LocalArtifactStore) -> None:
        self._store = store

    async def read_bytes(self, uri: str) -> bytes:
        return await self._store.read_bytes(uri)

    async def read_range(self, uri: str, offset: int, length: int) -> bytes:
        return await self._store.read_range(uri, offset, length)


@dataclass
class World:
    """The synthetic state and the ``LineageSource`` that reads it."""

    root: Path
    store: _ReadOnlyStore
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    versions: dict[tuple[str, str, str], list[dict[str, Any]]] = field(
        default_factory=dict
    )
    episodes: dict[str, EpisodeManifest] = field(default_factory=dict)
    scenes: dict[str, SceneManifest] = field(default_factory=dict)
    aligned: dict[str, str] = field(default_factory=dict)
    aligned_entries: dict[str, tuple[str, AlignedEpisodeArtifact]] = field(
        default_factory=dict
    )
    base_export: str = ""
    incremental_export: str = ""
    scene_tables: dict[str, str] = field(default_factory=dict)

    def artifact_record(self, artifact_id: str) -> dict[str, Any] | None:
        return self.records.get(artifact_id)

    def members(
        self, domain: str, dataset_id: str, dataset_version: str
    ) -> list[dict[str, Any]] | None:
        return self.versions.get((domain, dataset_id, dataset_version))

    def episode_id(self, name: str) -> str:
        return episode_id_for(self.episodes[name].lineage.source)

    def scene_id(self, name: str) -> str:
        return scene_id_for(self.scenes[name].lineage.source)


EPISODES = ("rec-a-0", "rec-a-1", "rec-b-0", "native-0", "native-1", "epoch-0")
BASE_EXPORT_EPISODES = ("rec-a-0", "rec-a-1", "rec-b-0", "native-0")
DELTA_EPISODES = ("native-1", "epoch-0")


async def build_world(root: Path) -> World:
    backend = LocalArtifactStore(root_uri=str(root))
    world = World(root=root, store=_ReadOnlyStore(backend))
    world.episodes = {
        "rec-a-0": recording_episode("run-a", "segment-0000"),
        "rec-a-1": recording_episode("run-a", "segment-0001"),
        "rec-b-0": recording_episode("run-b", "segment-0000"),
        "native-0": native_episode("0"),
        "native-1": native_episode("1"),
        "epoch-0": native_episode(
            "0", content=EPOCH_CONTENT, origin="unix_epoch", clock=EPOCH_CLOCK
        ),
    }
    world.scenes = {
        "scene-a": recording_scene("run-a"),
        "scene-a-1": recording_scene("run-a", "segment-0001"),
        "scene-b": recording_scene("run-b"),
        "scene-native": native_scene(),
    }

    episode_members = []
    for name, manifest in world.episodes.items():
        unit_id = episode_id_for(manifest.lineage.source)
        artifact_id, uri, checksum = await _publish_manifest(
            world, backend, "episode", unit_id, manifest, ArtifactKind.EPISODE_MANIFEST
        )
        episode_members.append(
            project_episode_revision(
                manifest=manifest,
                manifest_artifact_id=artifact_id,
                manifest_checksum=checksum,
            ).model_dump()
        )
        clock = manifest.declared_window().source_clock
        config = RECORDING_CONFIG if name.startswith("rec") else NATIVE_CONFIG
        aligned = align_episode(
            manifest,
            config,
            TemporalSourceContext(source_clock=clock),
            episode_id=unit_id,
        )
        artifact = AlignedEpisodeArtifact(
            source_revision=EpisodeSourceRevision(
                episode_id=unit_id,
                episode_manifest_uri=uri,
                source_artifact_id=artifact_id,
                source_manifest_sha256=checksum.removeprefix("sha256:"),
            ),
            aligned_episode=aligned,
        )
        data = json.dumps(
            artifact.to_artifact_dict(), sort_keys=True, separators=(",", ":")
        ).encode()
        aligned_checksum = sha256(data)
        aligned_id = aligned_episode_artifact_id(
            episode_id=unit_id, checksum=aligned_checksum
        )
        aligned_uri = str(root / "aligned" / f"{unit_id}-{aligned_checksum[7:19]}.json")
        await backend.write_bytes(aligned_uri, data)
        world.records[aligned_id] = {
            "artifact_id": aligned_id,
            "kind": ArtifactKind.ALIGNED_EPISODE_MANIFEST.value,
            "uri": aligned_uri,
            "checksum": aligned_checksum,
            "owner_id": unit_id,
            "metadata": {
                "episode_id": unit_id,
                "source_artifact_id": artifact_id,
                "source_manifest_sha256": checksum.removeprefix("sha256:"),
                "source_clock": aligned.source_clock,
            },
        }
        world.aligned[name] = aligned_id
        world.aligned_entries[name] = (aligned_checksum, artifact)
    world.versions[("episode", DATASET, VERSION)] = episode_members

    writer = AnalyticsTableWriter(
        artifact_store=backend, root_uri=str(root / "analytics")
    )
    world.base_export = await _export(world, writer, BASE_EXPORT_EPISODES, base=None)
    world.incremental_export = await _export(
        world, writer, DELTA_EPISODES, base=world.base_export
    )
    await _scene_snapshot(world, backend, writer)
    return world


async def _publish_manifest(
    world: World,
    backend: LocalArtifactStore,
    domain: str,
    unit_id: str,
    manifest: EpisodeManifest | SceneManifest,
    kind: ArtifactKind,
) -> tuple[str, str, str]:
    data = manifest.to_canonical_bytes()
    checksum = manifest.checksum()
    artifact_id = manifest_artifact_id_for(unit_id=unit_id, manifest_checksum=checksum)
    uri = str(world.root / "canonical" / domain / f"{unit_id}-{checksum[7:19]}.json")
    await backend.write_bytes(uri, data)
    world.records[artifact_id] = {
        "artifact_id": artifact_id,
        "kind": kind.value,
        "uri": uri,
        "checksum": checksum,
        "owner_id": unit_id,
    }
    return artifact_id, uri, checksum


def _revision(world: World, name: str) -> AlignedArtifactRevision:
    checksum, artifact = world.aligned_entries[name]
    return AlignedArtifactRevision(
        episode_id=artifact.aligned_episode.episode_id,
        aligned_artifact_id=world.aligned[name],
        aligned_artifact_checksum=checksum,
    )


async def _export(
    world: World,
    writer: AnalyticsTableWriter,
    names: tuple[str, ...],
    *,
    base: str | None,
) -> str:
    """What EXPORT_LEARNING_DATA does with these writers, base shards reused verbatim."""
    base_manifest = None
    base_export_id = None
    if base is not None:
        data = await world.store.read_bytes(world.records[base]["uri"])
        base_manifest = LearningDataExportManifest.model_validate_json(data)
        base_export_id = base_manifest.export_id
    entries = [world.aligned_entries[name] for name in names]
    revisions = [_revision(world, name) for name in names]
    all_revisions = [*(base_manifest.inputs if base_manifest else []), *revisions]
    config = LearningDataExportConfig()
    export_id = learning_data_export_id(
        aligned_checksums=[r.aligned_artifact_checksum for r in all_revisions],
        export_config=config,
        schema_version=LEARNING_DATA_SCHEMA_VERSION,
        base_export_id=base_export_id,
    )
    keys = dict(dataset_id=DATASET, dataset_version=VERSION, export_id=export_id)
    frame = build_learning_episodes_table(entries=entries, **keys)
    if base_manifest is not None:
        reused = await writer.read_learning_table(
            "learning_episodes", base_manifest.table_uris["learning_episodes"]
        )
        frame = pl.concat([reused, frame], how="vertical")
    episodes = await writer.write_learning_table("learning_episodes", frame, **keys)
    if base_manifest is None:
        shard_index = await write_sharded_learning_tables(
            writer, entries=entries, policy=default_shard_policy(), **keys
        )
    else:
        shard_index = await write_incremental_sharded_learning_tables(
            writer,
            delta_entries=entries,
            policy=default_shard_policy(),
            plan=plan_incremental_export(base_manifest, all_revisions),
            **keys,
        )
    manifest = LearningDataExportManifest(
        schema_version=LEARNING_DATA_SCHEMA_VERSION,
        inputs=all_revisions,
        export_config=config,
        table_uris={"learning_episodes": episodes.uri},
        table_checksums={"learning_episodes": episodes.checksum},
        row_counts={
            "learning_episodes": frame.height,
            "learning_steps": sum(s.row_count for s in shard_index.learning_steps),
            "learning_signals": sum(s.row_count for s in shard_index.learning_signals),
        },
        layout_version=LEARNING_DATA_LAYOUT_VERSION_SHARDED,
        shard_index=shard_index,
        episode_count=len(all_revisions),
        base_export_id=base_export_id,
        **keys,
    )
    written = await writer.write_learning_export_manifest(manifest, **keys)
    artifact_id = derived_artifact_id(
        prefix="lexport", logical_id=export_id, checksum=written.checksum
    )
    assert written.checksum == sha256(await world.store.read_bytes(written.uri))
    world.records[artifact_id] = {
        "artifact_id": artifact_id,
        "kind": ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST.value,
        "uri": written.uri,
        "checksum": written.checksum,
    }
    return artifact_id


async def _scene_snapshot(
    world: World, backend: LocalArtifactStore, writer: AnalyticsTableWriter
) -> None:
    contents = []
    members = []
    for manifest in world.scenes.values():
        unit_id = scene_id_for(manifest.lineage.source)
        artifact_id, _, checksum = await _publish_manifest(
            world, backend, "scene", unit_id, manifest, ArtifactKind.SCENE_MANIFEST
        )
        record = project_scene_revision(
            manifest=manifest,
            manifest_artifact_id=artifact_id,
            manifest_checksum=checksum,
        )
        contents.append(SceneRevisionContent(record=record, manifest=manifest))
        members.append(record.model_dump())
    world.versions[("scene", DATASET, VERSION)] = members
    keys = dict(dataset_id=DATASET, dataset_version=VERSION)
    for table_name, builder in (
        ("scenes", build_scenes_table),
        ("observations", build_observations_table),
        ("keyframes", build_keyframes_table),
    ):
        written = await writer.write_table(
            table_name, builder(scenes=contents, **keys), **keys
        )
        artifact_id = derived_artifact_id(
            prefix="analyticstable",
            logical_id=f"{DATASET}:{VERSION}:{table_name}",
            checksum=written.checksum,
        )
        world.records[artifact_id] = {
            "artifact_id": artifact_id,
            "kind": ArtifactKind.ANALYTICS_TABLE.value,
            "uri": written.uri,
            "checksum": written.checksum,
        }
        world.scene_tables[table_name] = written.uri
