"""Canonical semantic compatibility of the batch and the streaming reference baselines.

Runs INSIDE the recording-publisher container (SceneOps core + ArtifactStore
settings, no database). The host passes the manifest URIs it read from FastAPI.

stdin (JSON)::

    {"fixtures": {"<fixture_id>": {
        "batch":  {"scene_manifest_uris": [...], "episode_manifest_uris": [...]},
        "stream": {"scene_manifest_uris": [...], "episode_manifest_uris": [...]}}}}

For every fixture and for Scenes and Episodes: the same unit keys, and per unit
key an equal semantic projection (``semantic_scene_content`` /
``semantic_episode_content``, ADR-007 I-35): the same canonical content with
transport and provenance removed. Equality must not be vacuous: the two sides
come from different RobotRuns and carry different producer fingerprints.

Manifests only: no recording payload is loaded. Payload-level equivalence of the
two acquisitions is `make e2e-streaming-equivalence`'s.

Prints one JSON report; exit 0 only when every fixture is compatible.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

sys.path.insert(0, "/workspace/e2e")

from sceneops_core.episodes import load_canonical_episode_manifest  # noqa: E402
from sceneops_core.episodes.testing import semantic_episode_content  # noqa: E402
from sceneops_core.scenes import load_canonical_scene_manifest  # noqa: E402
from sceneops_core.scenes.testing import semantic_scene_content  # noqa: E402
from sceneops_storage import create_artifact_store  # noqa: E402
from streaming_equivalence_verify import (  # noqa: E402
    _artifact_settings,
    first_difference,
    load_manifests,
    unit_key,
)


async def compare(request: dict[str, Any]) -> dict[str, Any]:
    store = create_artifact_store(_artifact_settings())
    failures: list[str] = []
    fixtures: dict[str, Any] = {}
    for fixture_id, sides in sorted(request["fixtures"].items()):
        entry: dict[str, Any] = {}
        for name, loader, project in (
            ("scene", load_canonical_scene_manifest, semantic_scene_content),
            ("episode", load_canonical_episode_manifest, semantic_episode_content),
        ):
            a = await load_manifests(
                store,
                sides["batch"][f"{name}_manifest_uris"],
                loader,
                project,
                unit_key,
            )
            b = await load_manifests(
                store,
                sides["stream"][f"{name}_manifest_uris"],
                loader,
                project,
                unit_key,
            )
            equal = bool(a) and set(a) == set(b)
            if not a:
                failures.append(f"{fixture_id}: no batch {name} manifests")
            if set(a) != set(b):
                failures.append(
                    f"{fixture_id}: {name} unit keys differ: {sorted(a)} vs {sorted(b)}"
                )
            for key in sorted(set(a) & set(b)):
                difference = first_difference(a[key][1], b[key][1])
                if difference:
                    equal = False
                    failures.append(
                        f"{fixture_id}: {name} {key}: semantic content differs at {difference}"
                    )
                ma, mb = a[key][0], b[key][0]
                if ma.lineage.source.robot_run_id == mb.lineage.source.robot_run_id:
                    equal = False
                    failures.append(
                        f"{fixture_id}: {name} {key}: both come from one RobotRun"
                    )
                if (
                    ma.lineage.producer.producer_fingerprint
                    == mb.lineage.producer.producer_fingerprint
                ):
                    equal = False
                    failures.append(
                        f"{fixture_id}: {name} {key}: provenance did not change (vacuous equality)"
                    )
            entry[name] = {
                "unit_keys": sorted(a),
                "count": len(a),
                "observations": sum(len(m.observations) for m, _ in a.values()),
                "semantically_equal": equal,
            }
        fixtures[fixture_id] = entry
    return {"equivalent": not failures, "fixtures": fixtures, "failures": failures}


def main() -> int:
    report = asyncio.run(compare(json.load(sys.stdin)))
    print(json.dumps(report, sort_keys=True))
    for failure in report["failures"]:
        print(f"NOT COMPATIBLE: {failure}", file=sys.stderr)
    return 0 if report["equivalent"] else 1


if __name__ == "__main__":
    sys.exit(main())
