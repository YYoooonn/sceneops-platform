"""Batch-vs-streaming equivalence verifier (ADR-007 §29.12, I-35).

Runs INSIDE the recording-publisher container (worker image: SceneOps core +
integrations, ArtifactStore settings, the shared recordings volume and the
read-only reference cache). It needs no database and no host-side credentials:
the host passes artifact URIs it read from the FastAPI control plane.

Both arms acquire one logical source: the locked reference MCAP. The batch arm
is that MCAP itself (published as the reference baseline's RobotRun); the
streaming arm is what ROS 2 replay -> bridge -> Kafka -> capture recorded from
it. The verifier therefore proves *transport preservation*.

stdin (JSON)::

    {"batch":  {"recording": "/reference/.../scene-0061-<key>.mcap",
                "scene_manifest_uris": [...], "episode_manifest_uris": [...]},
     "stream": {...same shape, the capture output...}}

It checks, and prints one JSON report:

1. Streamed recording timing: L1 conformance, log_time is wall-clock receive
   time, publish_time is transport ingest time, every channel carries a
   sequence; the batch recording's timeline is the simulated source timeline.
2. Acquisition equivalence (§29.12): same channels, same message types and
   encodings, same per-channel payload sequences (sha256 multiset and per-channel
   sequence order), same source observation times (every ``Header.stamp`` the
   payloads carry, and the ``/mission/status`` event times), ``/tf_static``
   preserved. Container bytes, receive times, schema text and cross-channel
   write order are not compared.
3. Canonical equivalence (I-35): for every Scene / Episode unit key, the
   semantic projection (``semantic_scene_content`` /
   ``semantic_episode_content``) of the batch and streamed manifests is equal;
   provenance really differs (so equality is not vacuous).
4. Negative controls: one real, minimal perturbation of the loaded data per
   comparison (a dropped message, a 1 ns source-time shift, a flipped payload
   checksum) must each be detected.

Exit 0 only when everything holds.
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from sceneops_core.episodes import EpisodeManifest, load_canonical_episode_manifest
from sceneops_core.episodes.testing import semantic_episode_content
from sceneops_core.scenes import SceneManifest, load_canonical_scene_manifest
from sceneops_core.scenes.testing import semantic_scene_content
from sceneops_storage import create_artifact_store

from sceneops_integrations.recording import (
    check_l1_recording,
    compare_recording_contents,
    iter_recording_messages,
    semantic_recording_content,
)
from sceneops_integrations.recording.reader import (
    Ros2Decoder,
    header_stamps,
    stamp_ns,
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


def source_stamps(path: Path) -> dict[str, list[int]]:
    """Every ``Header.stamp`` the payloads of each channel carry (one per
    transform for ``/tf``), sorted: the source observation times."""
    decoder = Ros2Decoder()
    stamps: dict[str, list[int]] = {}
    for message in iter_recording_messages(path):
        decoded = decoder.decode(message)
        for header in header_stamps(decoded, message.schema_name):
            stamps.setdefault(message.topic, []).append(stamp_ns(header.stamp))
    return {topic: sorted(values) for topic, values in sorted(stamps.items())}


def _differs(a: Any, b: Any) -> bool:
    return first_difference(a, b) is not None


def negative_controls(
    batch_content: Any,
    batch_stamps: dict[str, list[int]],
    scenes: dict[str, Any],
    episodes: dict[str, Any],
) -> dict[str, bool]:
    """One minimal, real perturbation of the loaded batch data per comparison;
    each must be reported as a difference. ``True`` means detected."""
    results: dict[str, bool] = {}

    topic = next(
        t for t, c in sorted(batch_content.channels.items()) if c.message_count > 1
    )
    channel = batch_content.channels[topic]
    payloads = Counter(channel.payloads)
    payloads.subtract([next(iter(payloads))])
    dropped = replace(
        batch_content,
        channels={
            **batch_content.channels,
            topic: replace(
                channel,
                message_count=channel.message_count - 1,
                payloads=+payloads,
            ),
        },
    )
    results["one message dropped from a channel"] = not compare_recording_contents(
        batch_content, dropped
    ).equivalent

    shifted = copy.deepcopy(batch_stamps)
    stamp_topic = next(t for t, v in sorted(shifted.items()) if v)
    shifted[stamp_topic][0] += 1
    results["one Header.stamp shifted by 1 ns"] = _differs(batch_stamps, shifted)

    for key, projection in scenes.items():
        changed = copy.deepcopy(projection)
        changed["observations"][0]["timestamp_ns"] += 1
        results["Scene observation time shifted by 1 ns"] = _differs(
            projection, changed
        )
        changed = copy.deepcopy(projection)
        changed["observations"][0]["payload"]["checksum"] = "sha256:" + "0" * 64
        results["Scene observation payload checksum changed"] = _differs(
            projection, changed
        )
        break
    for key, projection in episodes.items():
        for kind in ("observations", "states", "actions"):
            if not projection[kind]:
                continue
            changed = copy.deepcopy(projection)
            changed[kind][0]["timestamp_ns"] += 1
            results[f"Episode {kind[:-1]} time shifted by 1 ns"] = _differs(
                projection, changed
            )
        changed = copy.deepcopy(projection)
        with_payload = next(o for o in changed["observations"] if o["payload"])
        with_payload["payload"]["checksum"] = "sha256:" + "0" * 64
        results["Episode observation payload checksum changed"] = _differs(
            projection, changed
        )
        break
    return results


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
    if stream_missions != batch_missions:
        failures.append(
            f"mission event times differ: {batch_missions} vs {stream_missions}"
        )
    if any(t >= RECOGNIZABLY_NOT_NOW_NS for t in stream_missions):
        failures.append("a mission event carries a wall-clock / replay time")

    batch_content = semantic_recording_content(batch_path)
    stream_content = semantic_recording_content(stream_path)
    recordings = compare_recording_contents(batch_content, stream_content)
    report["recording_equivalence"] = recordings.to_dict()
    failures.extend(recordings.differences)
    report["channels"] = {
        topic: {
            "message_type": c.schema_name,
            "encodings": [c.schema_encoding, c.message_encoding],
            "message_count": c.message_count,
        }
        for topic, c in sorted(stream_content.channels.items())
    }
    locked_counts = request.get("locked_topic_counts")
    if locked_counts is not None:
        stream_counts = {t: c.message_count for t, c in stream_content.channels.items()}
        if stream_counts != locked_counts:
            failures.append(
                "streamed per-channel counts differ from the locked recording"
            )
    tf_static = {
        side: content.channels.get("/tf_static")
        for side, content in (("batch", batch_content), ("stream", stream_content))
    }
    if any(c is None or c.message_count < 1 for c in tf_static.values()):
        failures.append("/tf_static is missing from a recording")
    report["tf_static_messages"] = {
        side: (c.message_count if c else 0) for side, c in tf_static.items()
    }

    batch_stamps = source_stamps(batch_path)
    stream_stamps = source_stamps(stream_path)
    report["source_stamp_channels"] = {t: len(v) for t, v in stream_stamps.items()}
    if batch_stamps != stream_stamps:
        differing = sorted(
            t
            for t in set(batch_stamps) | set(stream_stamps)
            if batch_stamps.get(t) != stream_stamps.get(t)
        )
        failures.append(f"source observation times differ on {differing}")
    report["mission_source_times_ns"] = stream_missions

    store = create_artifact_store(_artifact_settings())
    projections: dict[str, dict[str, Any]] = {}
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
        projections[name] = {k: v[1] for k, v in a.items()}

    if all(projections.get(n) for n in ("scene", "episode")):
        controls = negative_controls(
            batch_content, batch_stamps, projections["scene"], projections["episode"]
        )
        report["negative_controls"] = controls
        failures.extend(
            f"negative control not detected: {name}"
            for name, detected in controls.items()
            if not detected
        )

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
