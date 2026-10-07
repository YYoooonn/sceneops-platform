"""Registered recording facts of RobotRuns, read from their RobotRunManifests.

Runs INSIDE the recording-publisher container (worker image: SceneOps core,
ArtifactStore settings, no database). The host passes the manifest URIs it read
from the FastAPI control plane (``/artifacts/<manifestArtifactId>``).

stdin (JSON)::

    {"manifests": {"<run_id>": "<manifest uri>", ...}}

stdout: one JSON object, ``{"<run_id>": <facts>}``, deterministic for a
registered RobotRun. The manifest is canonical and was verified against the
recording bytes at registration (REGISTER_ROBOT_RUN re-derives the same facts),
so these are the registered recording's own facts, not a second source of truth.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from sceneops_core.robots.manifest import load_canonical_robot_run_manifest
from sceneops_storage import create_artifact_store


def facts_of(manifest: Any) -> dict[str, Any]:
    counts = {c.topic: c.message_count for c in manifest.channels}
    return {
        "robot_id": manifest.robot_id,
        "source_clock": manifest.capture.source_clock,
        "capture_source": {
            "kind": manifest.capture.source.kind.value,
            "topics": manifest.capture.source.topics,
        },
        "recording": {
            "checksum": manifest.recording.checksum,
            "size_bytes": manifest.recording.size_bytes,
        },
        "message_count": sum(counts.values()),
        "channel_counts": dict(sorted(counts.items())),
    }


async def read_facts(manifests: dict[str, str]) -> dict[str, Any]:
    from sceneops_integrations.recording.cli import RecordingPublisherSettings

    store = create_artifact_store(RecordingPublisherSettings().artifact)
    out: dict[str, Any] = {}
    for run_id, uri in sorted(manifests.items()):
        manifest = load_canonical_robot_run_manifest(await store.read_bytes(uri))
        if manifest.run_id != run_id:
            raise ValueError(f"manifest {uri} is for {manifest.run_id}, not {run_id}")
        out[run_id] = facts_of(manifest)
    return out


def main() -> int:
    request = json.load(sys.stdin)
    print(json.dumps(asyncio.run(read_facts(request["manifests"])), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
