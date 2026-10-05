"""Batch-vs-streaming equivalence verifier (ADR-007 §29.12, I-35).

Runs INSIDE the recording-publisher container (worker image: SceneOps core +
integrations, ArtifactStore settings, the shared recordings volume). It
needs no database and no host-side credentials: the host passes artifact
URIs it read from the FastAPI control plane.

stdin (JSON)::

    {"batch":  {"recording": "/recordings/a.mcap",
                "scene_manifest_uris": [...], "episode_manifest_uris": [...]},
     "stream": {...same shape, the capture output...}}

It checks, and prints one JSON report:

1. L1 conformance of the streamed recording, and that its timing is the
   recorder's: log_time is wall-clock receive time, every channel carries a
   sequence, and source observation times (inside payloads) are untouched.
2. Semantic recording equivalence (§29.12): same channels, same per-channel
   message multisets, same per-channel sequence order.
3. Canonical equivalence (I-35): for every Scene / Episode unit key, the
   semantic projection (``semantic_scene_content`` /
   ``semantic_episode_content``) of the batch and streamed manifests is
   equal; and provenance really differs (so equality is not vacuous).

Exit 0 only when everything holds.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from sceneops_core.episodes import EpisodeManifest, load_canonical_episode_manifest
from sceneops_core.episodes.testing import semantic_episode_content
from sceneops_core.scenes import SceneManifest, load_canonical_scene_manifest
from sceneops_core.scenes.testing import semantic_scene_content
from sceneops_storage import create_artifact_store

from sceneops_integrations.recording import (
    check_l1_recording,
    compare_recordings,
    iter_recording_messages,
)

RECOGNIZABLY_NOT_NOW_NS = 1_600_000_000_000_000_000  # 2020-09: the source data is 2018


def first_difference(a: Any, b: Any, path: str = "$") -> str | None:
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                return f"{path}.{key}: present in only one"
            found = first_difference(a[key], b[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} vs {len(b)}"
        for index, (x, y) in enumerate(zip(a, b)):
            found = first_difference(x, y, f"{path}[{index}]")
            if found:
                return found
        return None
    return None if a == b else f"{path}: {a!r} vs {b!r}"


def timing_report(path: Path) -> dict[str, Any]:
    report = check_l1_recording(path)
    messages = list(iter_recording_messages(path))
    log_times = [m.log_time_ns for m in messages]
    return {
        # A7 §32.2: a streamed message's publish_time is the transport ingest
        # time, taken before capture received it; a batch writer sets it to log_time.
        "publish_not_after_log": all(
            m.publish_time_ns <= m.log_time_ns for m in messages
        ),
        "publish_equals_log": all(m.publish_time_ns == m.log_time_ns for m in messages),
        "first_publish_time_ns": min(m.publish_time_ns for m in messages),
        "conforms": report.conforms,
        "violations": [v.detail for v in report.violations],
        "message_count": report.message_count,
        "all_channels_sequenced": all(c.sequenced for c in report.channels.values()),
        "first_log_time_ns": min(log_times),
        "last_log_time_ns": max(log_times),
        "log_time_non_decreasing": log_times == sorted(log_times),
        "channels": sorted(report.channels),
    }


def mission_source_times(path: Path) -> list[int]:
    return [
        int(json.loads(_string_data(m.data))["source_timestamp_ns"])
        for m in iter_recording_messages(path, topics=["/mission/status"])
    ]


def _string_data(cdr: bytes) -> str:
    # std_msgs/String CDR: 4-byte encapsulation header, uint32 length (incl. NUL), bytes.
    length = int.from_bytes(cdr[4:8], "little")
    return cdr[8 : 8 + length - 1].decode()


async def load_manifests(
    store: Any, uris: list[str], loader: Any, project: Any, key: Any
):
    out = {}
    for uri in uris:
        manifest = loader(await store.read_bytes(uri))
        out[key(manifest)] = (manifest, project(manifest))
    return out


def unit_key(manifest: SceneManifest | EpisodeManifest) -> str:
    return manifest.lineage.source.unit_key


async def verify(request: dict[str, Any]) -> dict[str, Any]:
    batch, stream = request["batch"], request["stream"]
    batch_path, stream_path = Path(batch["recording"]), Path(stream["recording"])
    failures: list[str] = []
    report: dict[str, Any] = {}

    stream_timing = timing_report(stream_path)
    batch_timing = timing_report(batch_path)
    report["stream_recording"] = stream_timing
    if not stream_timing["conforms"]:
        failures.append(
            f"streamed recording is not L1-conformant: {stream_timing['violations']}"
        )
    if not stream_timing["all_channels_sequenced"]:
        failures.append("a streamed channel carries no sequence")
    if not stream_timing["log_time_non_decreasing"]:
        failures.append("streamed log_time decreases in write order")
    if not stream_timing["publish_not_after_log"]:
        failures.append("a streamed publish_time is later than its log_time")
    if stream_timing["publish_equals_log"]:
        failures.append(
            "streamed publish_time equals log_time: transport ingest time was lost"
        )
    if stream_timing["first_publish_time_ns"] < RECOGNIZABLY_NOT_NOW_NS:
        failures.append(
            "streamed publish_time is not a wall-clock transport ingest time"
        )
    if not batch_timing["publish_equals_log"]:
        failures.append("batch publish_time differs from its simulated receive time")
    now_ns = time.time_ns()
    if not (RECOGNIZABLY_NOT_NOW_NS < stream_timing["first_log_time_ns"] <= now_ns):
        failures.append("streamed log_time is not a wall-clock receive time")
    if batch_timing["first_log_time_ns"] >= RECOGNIZABLY_NOT_NOW_NS:
        failures.append("batch log_time is not the simulated source-timeline time")
    stream_missions = mission_source_times(stream_path)
    batch_missions = mission_source_times(batch_path)
    report["mission_source_times_ns"] = stream_missions
    if stream_missions != batch_missions or len(stream_missions) != 2:
        failures.append(
            f"mission event times differ: {batch_missions} vs {stream_missions}"
        )
    if any(t >= RECOGNIZABLY_NOT_NOW_NS for t in stream_missions):
        failures.append("a mission event carries a wall-clock / replay time")

    recordings = compare_recordings(batch_path, stream_path)
    report["recording_equivalence"] = recordings.to_dict()
    failures.extend(recordings.differences)

    store = create_artifact_store(_artifact_settings())
    for name, loader, project in (
        ("scene", load_canonical_scene_manifest, semantic_scene_content),
        ("episode", load_canonical_episode_manifest, semantic_episode_content),
    ):
        a = await load_manifests(
            store, batch[f"{name}_manifest_uris"], loader, project, unit_key
        )
        b = await load_manifests(
            store, stream[f"{name}_manifest_uris"], loader, project, unit_key
        )
        section: dict[str, Any] = {"unit_keys": sorted(a), "count": len(a)}
        if not a:
            failures.append(f"no batch {name} manifests")
        if set(a) != set(b):
            failures.append(f"{name} unit keys differ: {sorted(a)} vs {sorted(b)}")
        for key in sorted(set(a) & set(b)):
            difference = first_difference(a[key][1], b[key][1])
            if difference:
                failures.append(
                    f"{name} {key}: semantic content differs at {difference}"
                )
            if (
                a[key][0].lineage.source.robot_run_id
                == b[key][0].lineage.source.robot_run_id
            ):
                failures.append(f"{name} {key}: both come from one RobotRun")
            if (
                a[key][0].lineage.producer.producer_fingerprint
                == b[key][0].lineage.producer.producer_fingerprint
            ):
                failures.append(
                    f"{name} {key}: provenance did not change (vacuous equality)"
                )
        section["observations"] = sum(len(m.observations) for m, _ in a.values())
        report[name] = section

    report["equivalent"] = not failures
    report["failures"] = failures
    return report


def _artifact_settings():
    # The publisher container's ArtifactStore settings (SCENEOPS_PUBLISHER_ARTIFACT__*).
    from sceneops_integrations.recording.cli import RecordingPublisherSettings

    return RecordingPublisherSettings().artifact


def main() -> int:
    report = asyncio.run(verify(json.load(sys.stdin)))
    print(json.dumps(report, sort_keys=True))
    for failure in report["failures"]:
        print(f"NOT EQUIVALENT: {failure}", file=sys.stderr)
    return 0 if report["equivalent"] else 1


if __name__ == "__main__":
    sys.exit(main())
