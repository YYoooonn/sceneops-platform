"""Shared fixtures of the learning data schema tests.

``temporal_entries`` aligns real EpisodeManifests through the real engine: the
cases where only a clock instance tells two Episodes' identical timestamps apart.

``write_v1_export`` writes what the previous learning data schema left in a
bucket. Its objects are the exact bytes that schema's writer produced for the same
input, so a test can show a reader refusing, and a new export coexisting with, a
real old export. It lives here, and nowhere near production paths, because nothing
but a test may produce such an export.
"""

from __future__ import annotations

import hashlib
import io
import json
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from sceneops_core.artifacts.logical_uri import LogicalNamespace, format_logical_uri
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
    LearningDataShard,
    LearningDataShardIndex,
    ShardEpisodeMember,
    default_shard_policy,
    learning_data_export_id,
)
from sceneops_core.episodes.schemas import EpisodeManifest
from sceneops_core.episodes.testing import (
    DEFAULT_CLOCK,
    action,
    episode_manifest,
    observation,
    state,
)
from sceneops_core.provenance import ContentUnitSource, ProducerInfo
from sceneops_core.sources import content_id_for
from sceneops_core.temporal import frame_time_ns

from ..learning_tables import (
    build_learning_episodes_table,
    build_learning_signals_table,
    build_learning_steps_table,
)
from ..learning_tables_sharded import plan_shards_for_entries
from ..writer import AnalyticsTableWriter

S = 1_000_000_000
NATIVE_CLOCK = "lerobot.frame_time"
RECORDING_CONFIG = TemporalAlignmentConfig(
    target_frequency_hz=1.0, tolerance_us=500_000, max_gap_us=2_000_000
)
NATIVE_CONFIG = TemporalAlignmentConfig(
    target_frequency_hz=10.0, tolerance_us=50_000, max_gap_us=200_000
)
_CONTENT_CHECKSUM = "sha256:" + "c" * 64
_VIDEO_URI = format_logical_uri(
    LogicalNamespace.SOURCES,
    f"contents/{content_id_for(_CONTENT_CHECKSUM)}/files/videos/cam.mp4",
)

Entry = tuple[str, AlignedEpisodeArtifact]


def _recording_manifest(robot_run_id: str, unit_key: str) -> EpisodeManifest:
    return episode_manifest(
        [
            observation("/cam", 0),
            observation("/cam", S),
            state("/odom", 0, x=0.0),
            state("/odom", 2 * S + 700, x=2.0),
            action("/control", 0, steering=0.1),
            action("/control", 2 * S, steering=0.2),
        ],
        robot_run_id=robot_run_id,
        unit_key=unit_key,
        window=(0, 3 * S),
    )


def _native_manifest(
    unit_key: str, *, frames: int = 3, fps: int = 10
) -> EpisodeManifest:
    """A LeRobot-style Episode on a frame grid, with frame references and no
    ``next.*`` feature: frame 0 is the unit's own origin, so every Episode of the
    content has the same frame times."""
    source = ContentUnitSource(
        content_id=content_id_for(_CONTENT_CHECKSUM),
        content_checksum=_CONTENT_CHECKSUM,
        source_clock=NATIVE_CLOCK,
        start_timestamp_ns=0,
        end_timestamp_ns=frame_time_ns(frames - 1, fps) + 1,
        unit_key=unit_key,
    )
    producer = ProducerInfo.create(
        producer_id="sceneops.lerobot_episode_builder",
        semantics_version=1,
        build_config={"features": []},
        source=source.source_revision(),
    )

    def occurrence(topic: str, k: int, values: dict[str, Any], **extra: Any) -> dict:
        return {
            "occurrence_id": f"{topic}-{k:08d}",
            "topic": topic,
            "timestamp_ns": frame_time_ns(k, fps),
            "values": values,
            "native_ref": str(k),
            **extra,
        }

    return EpisodeManifest.model_validate(
        {
            "schema_version": "sceneops.episode_manifest/v4",
            "lineage": {
                "source": source.model_dump(mode="json"),
                "producer": producer.model_dump(mode="json"),
            },
            "clock_domains": [
                {"clock": NATIVE_CLOCK, "origin": "unit", "resolution_ns": 1}
            ],
            "frame_timeline": {
                "clock": NATIVE_CLOCK,
                "fps": fps,
                "frame_count": frames,
            },
            "streams": [
                {
                    "topic": "action",
                    "role": "action",
                    "schema_name": "float32",
                    "source_clock": NATIVE_CLOCK,
                    "fields": [{"name": "value", "path": "action"}],
                },
                {
                    "topic": "observation.image",
                    "role": "observation",
                    "schema_name": "video",
                    "source_clock": NATIVE_CLOCK,
                    "has_payload": True,
                },
                {
                    "topic": "observation.state",
                    "role": "state",
                    "schema_name": "float32",
                    "source_clock": NATIVE_CLOCK,
                    "fields": [{"name": "value", "path": "observation.state"}],
                },
            ],
            "observations": [
                occurrence(
                    "observation.image",
                    k,
                    {},
                    payload={
                        "uri": _VIDEO_URI,
                        "checksum": "sha256:" + "e" * 64,
                        "size_bytes": 1000,
                        "media_type": "video/mp4",
                        "selector": {
                            "kind": "video_frame",
                            "timestamp_ns": frame_time_ns(k, fps),
                        },
                    },
                )
                for k in range(frames)
            ],
            "states": [
                occurrence("observation.state", k, {"value": [float(k), -float(k)]})
                for k in range(frames)
            ],
            "actions": [
                occurrence("action", k, {"value": [float(k) * 0.5]})
                for k in range(frames)
            ],
        }
    )


def _entry(
    episode_id: str,
    manifest: EpisodeManifest,
    config: TemporalAlignmentConfig,
    clock: str,
) -> Entry:
    aligned = align_episode(
        manifest,
        config,
        TemporalSourceContext(source_clock=clock),
        episode_id=episode_id,
    )
    artifact = AlignedEpisodeArtifact(
        source_revision=EpisodeSourceRevision(
            episode_id=episode_id,
            episode_manifest_uri=f"mem://episodes/{episode_id}.json",
            source_artifact_id=f"src-{episode_id}",
            source_manifest_sha256=hashlib.sha256(episode_id.encode()).hexdigest(),
        ),
        aligned_episode=aligned,
    )
    document = json.dumps(
        artifact.to_artifact_dict(), sort_keys=True, separators=(",", ":")
    ).encode()
    return f"sha256:{hashlib.sha256(document).hexdigest()}", artifact


def temporal_entries() -> dict[str, Entry]:
    """Five Episodes, keyed by what makes each one a case:

    run-a-0, run-a-1  two segments of one RobotRun: one ``source`` instance
    run-b-0           another RobotRun, same clock, same timestamps
    native-0, native-1  two LeRobot Episodes of one content, same frame times
    """
    return {
        "run-a-0": _entry(
            "ep-run-a-0",
            _recording_manifest("run-a", "segment-0"),
            RECORDING_CONFIG,
            DEFAULT_CLOCK,
        ),
        "run-a-1": _entry(
            "ep-run-a-1",
            _recording_manifest("run-a", "segment-1"),
            RECORDING_CONFIG,
            DEFAULT_CLOCK,
        ),
        "run-b-0": _entry(
            "ep-run-b-0",
            _recording_manifest("run-b", "segment-0"),
            RECORDING_CONFIG,
            DEFAULT_CLOCK,
        ),
        "native-0": _entry(
            "ep-native-0", _native_manifest("0"), NATIVE_CONFIG, NATIVE_CLOCK
        ),
        "native-1": _entry(
            "ep-native-1", _native_manifest("1"), NATIVE_CONFIG, NATIVE_CLOCK
        ),
    }


def _v1_parquet(df) -> bytes:
    buffer = io.BytesIO()
    df.write_parquet(buffer)
    return buffer.getvalue()


def _v1_shard(df, row_group_sizes: list[int]) -> bytes:
    arrow_table = df.to_arrow()
    sink = pa.BufferOutputStream()
    with pq.ParquetWriter(
        sink, arrow_table.schema, compression="zstd", compression_level=3
    ) as writer:
        offset = 0
        for size in row_group_sizes:
            writer.write_table(arrow_table.slice(offset, size), row_group_size=size)
            offset += size
    return sink.getvalue().to_pybytes()


async def write_v1_export(
    writer: AnalyticsTableWriter,
    *,
    dataset_id: str,
    dataset_version: str,
    entries: list[Entry],
) -> tuple[str, bytes]:
    """Writes one sharded export of the previous schema: no ``clock_origin`` or
    ``clock_scope`` column, no schema stamp in any footer, ``"schemaVersion":
    "v1"`` in its manifest, at the location its export id named. Returns
    ``(manifest uri, manifest bytes)``."""
    store = writer.artifact_store
    config = LearningDataExportConfig()
    export_id = learning_data_export_id(
        aligned_checksums=[checksum for checksum, _ in entries],
        export_config=config,
        schema_version="v1",
    )
    scope = dict(
        dataset_id=dataset_id, dataset_version=dataset_version, export_id=export_id
    )
    policy = default_shard_policy()

    episodes = build_learning_episodes_table(**scope, entries=entries).drop(
        "clock_origin", "clock_scope"
    )
    episodes_data = _v1_parquet(episodes)
    episodes_uri = writer.learning_table_uri(**scope, table_name="learning_episodes")
    await store.write_bytes(episodes_uri, episodes_data)

    builders = {
        "learning_steps": build_learning_steps_table,
        "learning_signals": build_learning_signals_table,
    }
    shards: dict[str, list[LearningDataShard]] = {name: [] for name in builders}
    for table_name, builder in builders.items():
        for index, shard_entries in enumerate(plan_shards_for_entries(entries, policy)):
            df = builder(**scope, entries=shard_entries)
            counts = (
                df.group_by("aligned_artifact_checksum", maintain_order=True)
                .len()["len"]
                .to_list()
            )
            data = _v1_shard(df, counts)
            uri = writer.learning_table_shard_uri(
                **scope, table_name=table_name, shard_index=index
            )
            await store.write_bytes(uri, data)
            shards[table_name].append(
                LearningDataShard(
                    shard_index=index,
                    uri=uri,
                    checksum=f"sha256:{hashlib.sha256(data).hexdigest()}",
                    size_bytes=len(data),
                    row_count=df.height,
                    episodes=[
                        ShardEpisodeMember(
                            episode_ref={
                                "episode_id": artifact.aligned_episode.episode_id,
                                "aligned_artifact_checksum": checksum,
                            },
                            row_group_index=position,
                            row_count=count,
                        )
                        for position, ((checksum, artifact), count) in enumerate(
                            zip(shard_entries, counts)
                        )
                    ],
                )
            )

    manifest = LearningDataExportManifest(
        schema_version=LEARNING_DATA_SCHEMA_VERSION,
        export_id=export_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        inputs=[
            AlignedArtifactRevision(
                episode_id=artifact.aligned_episode.episode_id,
                aligned_artifact_id=f"art-{artifact.aligned_episode.episode_id}",
                aligned_artifact_checksum=checksum,
            )
            for checksum, artifact in entries
        ],
        export_config=config,
        table_uris={"learning_episodes": episodes_uri},
        table_checksums={
            "learning_episodes": f"sha256:{hashlib.sha256(episodes_data).hexdigest()}"
        },
        row_counts={
            "learning_episodes": episodes.height,
            "learning_steps": sum(s.row_count for s in shards["learning_steps"]),
            "learning_signals": sum(s.row_count for s in shards["learning_signals"]),
        },
        layout_version=LEARNING_DATA_LAYOUT_VERSION_SHARDED,
        shard_index=LearningDataShardIndex(shard_policy=policy, **shards),
        episode_count=len({a.aligned_episode.episode_id for _, a in entries}),
    )
    document = manifest.to_artifact_dict()
    document["schemaVersion"] = "v1"
    data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    uri = writer.learning_export_manifest_uri(**scope)
    await store.write_bytes(uri, data)
    return uri, data
