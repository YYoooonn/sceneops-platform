"""Recording Import vs Streaming Acquisition equivalence, read from durable state
(ADR-007 §29.12, I-35).

Runs INSIDE the recording-publisher container (worker image: SceneOps core +
integrations, ArtifactStore settings; the capture volume is mounted read-only
and is not used). It needs no database and no host-side credentials: the host
passes the URIs it read from the FastAPI control plane.

Both arms are RobotRuns of the golden reference contract, registered in the
ArtifactStore: the Recording Import RobotRun pins the locked reference MCAP, the
Streaming Acquisition RobotRun pins what ROS 2 replay -> bridge -> Kafka ->
capture recorded from it. Each recording is read from the ArtifactStore into the
container's own temporary directory, verified against its registered checksum,
and removed with the container. Nothing is written to the platform.

stdin (JSON)::

    {"locked": {"sha256": ..., "message_count": ..., "topic_counts": {...}},
     "recording_import":      {"robot_run_id": ..., "manifest_uri": ...,
                               "recording": {"uri": ..., "checksum": ..., "size_bytes": ...},
                               "scene_manifest_uris": [...], "episode_manifest_uris": [...]},
     "streaming_acquisition": {...same shape...}}

It checks, and prints one JSON report:

1. Identity: each registered recording equals its RobotRunManifest's recording;
   the Recording Import RobotRun is the locked recording from a file source; the
   Streaming Acquisition RobotRun is a different recording captured from Kafka;
   both carry the locked message and per-channel counts.
2. Streamed recording timing: L1 conformance, log_time is wall-clock receive
   time, publish_time is transport ingest time, every channel carries a
   sequence; the imported recording's timeline is the simulated source timeline.
3. Acquisition equivalence (§29.12): same channels, same message types and
   encodings, same per-channel payload sequences (sha256 multiset and per-channel
   sequence order), same source observation times (every ``Header.stamp`` the
   payloads carry, and the ``/mission/status`` event times), ``/tf_static``
   preserved. Container bytes, receive times, schema text and cross-channel
   write order are not compared.
4. Canonical equivalence (I-35): for every Scene / Episode unit key, the
   semantic projection (``semantic_scene_content`` /
   ``semantic_episode_content``) of the two arms' manifests is equal;
   provenance really differs (so equality is not vacuous).
5. Negative controls: one real, minimal perturbation of the loaded data per
   comparison (a dropped message, a 1 ns source-time shift, a flipped payload
   checksum, a Scene / Episode semantic change) must each be detected. They act
   on in-memory copies only.

Exit 0 only when everything holds.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
import tempfile
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from sceneops_core.episodes import EpisodeManifest, load_canonical_episode_manifest
from sceneops_core.episodes.testing import semantic_episode_content
from sceneops_core.robots.manifest import load_canonical_robot_run_manifest
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

ARMS = ("recording_import", "streaming_acquisition")
# One ArtifactStore range request; bounds the memory of reading a recording.
READ_CHUNK_BYTES = 32 * 1024 * 1024


class RegisteredRecordingError(RuntimeError):
    """A registered recording cannot be read as registered."""


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
    publish_not_after_log = publish_equals_log = log_time_non_decreasing = True
    first_publish = first_log = last_log = None
    for m in iter_recording_messages(path):
        # A7 §32.2: a streamed message's publish_time is the transport ingest
        # time, taken before capture received it; a batch writer sets it to log_time.
        publish_not_after_log &= m.publish_time_ns <= m.log_time_ns
        publish_equals_log &= m.publish_time_ns == m.log_time_ns
        log_time_non_decreasing &= last_log is None or m.log_time_ns >= last_log
        first_publish = (
            m.publish_time_ns
            if first_publish is None
            else min(first_publish, m.publish_time_ns)
        )
        first_log = (
            m.log_time_ns if first_log is None else min(first_log, m.log_time_ns)
        )
        last_log = m.log_time_ns if last_log is None else max(last_log, m.log_time_ns)
    return {
        "publish_not_after_log": publish_not_after_log,
        "publish_equals_log": publish_equals_log,
        "first_publish_time_ns": first_publish,
        "conforms": report.conforms,
        "violations": [v.detail for v in report.violations],
        "message_count": report.message_count,
        "all_channels_sequenced": all(c.sequenced for c in report.channels.values()),
        "first_log_time_ns": first_log,
        "last_log_time_ns": last_log,
        "log_time_non_decreasing": log_time_non_decreasing,
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


async def materialize_recording(
    store: Any, recording: dict[str, Any], destination: Path
) -> None:
    """Read a registered recording from the ArtifactStore into ``destination`` and
    require the bytes to be exactly what is registered."""
    digest = hashlib.sha256()
    offset, size = 0, int(recording["size_bytes"])
    with open(destination, "wb") as out:
        while offset < size:
            chunk = await store.read_range(
                recording["uri"], offset, min(READ_CHUNK_BYTES, size - offset)
            )
            digest.update(chunk)
            out.write(chunk)
            offset += len(chunk)
    if f"sha256:{digest.hexdigest()}" != recording["checksum"]:
        raise RegisteredRecordingError(
            f"{recording['uri']} does not hash to its registered checksum {recording['checksum']}"
        )


def manifest_facts(manifest: Any) -> dict[str, Any]:
    counts = {c.topic: c.message_count for c in manifest.channels}
    return {
        "run_id": manifest.run_id,
        "capture_source_kind": manifest.capture.source.kind.value,
        "recording": {
            "checksum": manifest.recording.checksum,
            "size_bytes": manifest.recording.size_bytes,
        },
        "message_count": sum(counts.values()),
        "channel_counts": dict(sorted(counts.items())),
    }


def identity_failures(
    arm: str, request: dict[str, Any], facts: dict[str, Any], locked: dict[str, Any]
) -> list[str]:
    """What the registered RobotRun must be, from its manifest and the lock."""
    failures: list[str] = []
    registered = request["recording"]
    if facts["run_id"] != request["robot_run_id"]:
        failures.append(
            f"{arm}: manifest is for {facts['run_id']}, not {request['robot_run_id']}"
        )
    if facts["recording"] != {
        "checksum": registered["checksum"],
        "size_bytes": registered["size_bytes"],
    }:
        failures.append(f"{arm}: the registered recording differs from its manifest's")
    if (facts["message_count"], facts["channel_counts"]) != (
        locked["message_count"],
        locked["topic_counts"],
    ):
        failures.append(
            f"{arm}: message count / per-channel counts differ from the locked recording's"
        )
    pins_lock = registered["checksum"] == locked["sha256"]
    if arm == "recording_import":
        if facts["capture_source_kind"] != "file":
            failures.append(
                f"{arm}: capture source is {facts['capture_source_kind']}, not a file"
            )
        if not pins_lock:
            failures.append(f"{arm}: the RobotRun does not pin the locked recording")
    else:
        if facts["capture_source_kind"] != "kafka":
            failures.append(
                f"{arm}: capture source is {facts['capture_source_kind']}, not kafka"
            )
        if pins_lock:
            failures.append(
                f"{arm}: the RobotRun pins the locked recording itself: it was imported, not streamed"
            )
    return failures


def _arm_report(request: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "robot_run_id": request["robot_run_id"],
        "checksum": request["recording"]["checksum"],
        "size_bytes": request["recording"]["size_bytes"],
        "message_count": facts["message_count"],
        "capture_source_kind": facts["capture_source_kind"],
    }


def timing_failures(
    imported: dict[str, Any], streamed: dict[str, Any], missions: dict[str, list[int]]
) -> list[str]:
    failures: list[str] = []
    if not streamed["conforms"]:
        failures.append(
            f"streamed recording is not L1-conformant: {streamed['violations']}"
        )
    if not streamed["all_channels_sequenced"]:
        failures.append("a streamed channel carries no sequence")
    if not streamed["log_time_non_decreasing"]:
        failures.append("streamed log_time decreases in write order")
    if not streamed["publish_not_after_log"]:
        failures.append("a streamed publish_time is later than its log_time")
    if streamed["publish_equals_log"]:
        failures.append(
            "streamed publish_time equals log_time: transport ingest time was lost"
        )
    if streamed["first_publish_time_ns"] < RECOGNIZABLY_NOT_NOW_NS:
        failures.append(
            "streamed publish_time is not a wall-clock transport ingest time"
        )
    if not imported["publish_equals_log"]:
        failures.append("imported publish_time differs from its simulated receive time")
    if not (RECOGNIZABLY_NOT_NOW_NS < streamed["first_log_time_ns"] <= time.time_ns()):
        failures.append("streamed log_time is not a wall-clock receive time")
    if imported["first_log_time_ns"] >= RECOGNIZABLY_NOT_NOW_NS:
        failures.append("imported log_time is not the simulated source-timeline time")
    if missions["recording_import"] != missions["streaming_acquisition"]:
        failures.append(
            f"mission event times differ: {missions['recording_import']} vs {missions['streaming_acquisition']}"
        )
    if any(t >= RECOGNIZABLY_NOT_NOW_NS for t in missions["streaming_acquisition"]):
        failures.append("a mission event carries a wall-clock / replay time")
    return failures


async def verify(request: dict[str, Any]) -> dict[str, Any]:
    locked = request["locked"]
    store = create_artifact_store(_artifact_settings())
    failures: list[str] = []
    report: dict[str, Any] = {"recordings": {}}

    with tempfile.TemporaryDirectory(prefix="equivalence-") as scratch:
        paths: dict[str, Path] = {}
        unreadable = False
        for arm in ARMS:
            arm_request = request[arm]
            manifest = load_canonical_robot_run_manifest(
                await store.read_bytes(arm_request["manifest_uri"])
            )
            facts = manifest_facts(manifest)
            failures.extend(identity_failures(arm, arm_request, facts, locked))
            report["recordings"][arm] = _arm_report(arm_request, facts)
            paths[arm] = Path(scratch) / f"{arm}.mcap"
            try:
                await materialize_recording(store, arm_request["recording"], paths[arm])
            except RegisteredRecordingError as exc:
                failures.append(f"{arm}: {exc}")
                unreadable = True
        if unreadable:
            return _finish(report, failures)

        timing = {arm: timing_report(path) for arm, path in paths.items()}
        report["timing"] = timing
        missions = {arm: mission_source_times(path) for arm, path in paths.items()}
        failures.extend(
            timing_failures(
                timing["recording_import"], timing["streaming_acquisition"], missions
            )
        )
        report["mission_source_times_ns"] = missions["streaming_acquisition"]

        content = {arm: semantic_recording_content(path) for arm, path in paths.items()}
        recordings = compare_recording_contents(
            content["recording_import"], content["streaming_acquisition"]
        )
        report["recording_equivalence"] = recordings.to_dict()
        failures.extend(recordings.differences)
        report["channels"] = {
            topic: {
                "message_type": c.schema_name,
                "encodings": [c.schema_encoding, c.message_encoding],
                "message_count": c.message_count,
            }
            for topic, c in sorted(content["streaming_acquisition"].channels.items())
        }
        for arm, c in content.items():
            if {t: ch.message_count for t, ch in c.channels.items()} != locked[
                "topic_counts"
            ]:
                failures.append(
                    f"{arm}: the recording's per-channel counts differ from the locked recording's"
                )
        tf_static = {arm: c.channels.get("/tf_static") for arm, c in content.items()}
        if any(c is None or c.message_count < 1 for c in tf_static.values()):
            failures.append("/tf_static is missing from a recording")
        report["tf_static_messages"] = {
            arm: (c.message_count if c else 0) for arm, c in tf_static.items()
        }

        stamps = {arm: source_stamps(path) for arm, path in paths.items()}
        report["source_stamp_channels"] = {
            t: len(v) for t, v in stamps["streaming_acquisition"].items()
        }
        if stamps["recording_import"] != stamps["streaming_acquisition"]:
            differing = sorted(
                t
                for t in set(stamps["recording_import"])
                | set(stamps["streaming_acquisition"])
                if stamps["recording_import"].get(t)
                != stamps["streaming_acquisition"].get(t)
            )
            failures.append(f"source observation times differ on {differing}")

    projections: dict[str, dict[str, Any]] = {}
    for name, loader, project in (
        ("scene", load_canonical_scene_manifest, semantic_scene_content),
        ("episode", load_canonical_episode_manifest, semantic_episode_content),
    ):
        a, b = [
            await load_manifests(
                store, request[arm][f"{name}_manifest_uris"], loader, project, unit_key
            )
            for arm in ARMS
        ]
        failures.extend(canonical_failures(name, a, b))
        report[name] = {
            "unit_keys": sorted(a),
            "count": len(a),
            "observations": sum(len(m.observations) for m, _ in a.values()),
        }
        projections[name] = {k: v[1] for k, v in a.items()}

    if all(projections.get(n) for n in ("scene", "episode")):
        controls = negative_controls(
            content["recording_import"],
            stamps["recording_import"],
            projections["scene"],
            projections["episode"],
        )
        report["negative_controls"] = controls
        failures.extend(
            f"negative control not detected: {name}"
            for name, detected in controls.items()
            if not detected
        )
    return _finish(report, failures)


def canonical_failures(
    name: str, imported: dict[str, Any], streamed: dict[str, Any]
) -> list[str]:
    """Scene / Episode semantic equality per unit key, with provenance that
    really differs (equality of identical records would be vacuous)."""
    failures: list[str] = []
    if not imported:
        failures.append(f"no recording_import {name} manifests")
    if set(imported) != set(streamed):
        failures.append(
            f"{name} unit keys differ: {sorted(imported)} vs {sorted(streamed)}"
        )
    for key in sorted(set(imported) & set(streamed)):
        difference = first_difference(imported[key][1], streamed[key][1])
        if difference:
            failures.append(f"{name} {key}: semantic content differs at {difference}")
        source_a, source_b = (
            imported[key][0].lineage.source,
            streamed[key][0].lineage.source,
        )
        if source_a.robot_run_id == source_b.robot_run_id:
            failures.append(f"{name} {key}: both come from one RobotRun")
        if (
            imported[key][0].lineage.producer.producer_fingerprint
            == streamed[key][0].lineage.producer.producer_fingerprint
        ):
            failures.append(
                f"{name} {key}: provenance did not change (vacuous equality)"
            )
    return failures


def _finish(report: dict[str, Any], failures: list[str]) -> dict[str, Any]:
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
