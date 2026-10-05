#!/usr/bin/env python
"""Build the IntegrationRequest JSON for a LeRobot EXPORT of one pinned
learning data export.

Runs inside the worker image (``make e2e-episode-learning`` invokes it through
``docker compose run worker-cli``). It reads nothing: the manifest's uri and
checksum come from the ArtifactRecord the API reports, and the projection from
the verified export. The LeRobot integration container receives only this
request and ArtifactStore settings in its environment.

Usage (inside the worker image):
    python /workspace/scripts/e2e/lerobot_build_request.py \\
        --manifest-uri s3://... --manifest-checksum sha256:... \\
        --dataset-id D --dataset-version V \\
        --export-root-uri /data/runs/e2e-lerobot/<repo-id> --repo-id <repo-id> \\
        --observation-channels a,b --action-channels c,d --default-task drive
"""

from __future__ import annotations

import argparse

from sceneops_analytics.external_adapters import (
    ExternalExportConfig,
    UnsupportedSemanticPolicy,
)
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.episodes.learning import FeatureProjection
from sceneops_core.integration_runtime import (
    CanonicalDatasetRef,
    ExternalDatasetRef,
    IntegrationOperation,
    IntegrationRequest,
)


def _channels(value: str) -> list[str]:
    return [c for c in value.split(",") if c]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-uri", required=True)
    parser.add_argument("--manifest-checksum", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--export-root-uri", required=True)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--observation-channels", default="")
    parser.add_argument("--action-channels", default="")
    parser.add_argument(
        "--default-task",
        default=None,
        help="task string for episodes that carry none (canonical Episodes have no "
        "task; LeRobot requires one per frame)",
    )
    args = parser.parse_args()

    request = IntegrationRequest(
        operation=IntegrationOperation.EXPORT,
        external_ref=ExternalDatasetRef(
            format="lerobot",
            format_version="3.0",
            uri=args.export_root_uri,
            external_name=args.repo_id,
        ),
        canonical_ref=CanonicalDatasetRef(
            dataset_id=args.dataset_id, dataset_version=args.dataset_version
        ),
        canonical_inputs={
            "learning_manifest": ArtifactRef(
                kind=ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
                uri=args.manifest_uri,
                checksum=args.manifest_checksum,
            )
        },
        config=ExternalExportConfig(
            projection=FeatureProjection(
                observation_channels=_channels(args.observation_channels),
                action_channels=_channels(args.action_channels),
            ),
            unsupported_semantic_policy=UnsupportedSemanticPolicy.RECORD,
            default_task=args.default_task,
        ).model_dump(mode="json"),
        metadata={"requested_by": "e2e_episode_learning"},
    )
    print(request.model_dump_json(by_alias=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
