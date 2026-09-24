#!/usr/bin/env python
"""Build a real IntegrationRequest JSON for the nuScenes integration
container's smoke test (SceneOps V2 Request 4.5) -- Step 1 of
scripts/e2e/nuscenes_container_smoke.sh.

Unlike scripts/e2e/lerobot_container_build_request.py (an EXPORT, which
must resolve an already-registered canonical artifact via sceneops-db
before it can build a request), a nuScenes INGEST request names no
pre-existing canonical artifact at all -- canonical_ref is bare identity
(SceneOps V2 Request 4.1A), and canonical_inputs is legitimately empty for
this format today. So this script needs no DB session, no sceneops-db
import, and no async fixture resolution: it just prints one
IntegrationRequest (sceneops_core.integration_runtime, Request 4.1/4.1A) as
JSON to stdout, exactly like the container itself expects via
--request-file.

Usage:
    uv run python scripts/e2e/nuscenes_container_build_request.py \\
        --source-root-uri /data/raw/nuscenes \\
        --dataset-id test-e2e-nuscenes-container-smoke \\
        --dataset-version test-v1 \\
        --source-format-version v1.0-mini \\
        --max-source-sequences 2
"""

from __future__ import annotations

import argparse

from sceneops_core.datasets.schemas.external import ExternalDatasetRef
from sceneops_core.integration_runtime import (
    CanonicalDatasetRef,
    IntegrationOperation,
    IntegrationRequest,
)


def build_request(
    *,
    source_root_uri: str,
    source_format_version: str,
    dataset_id: str,
    dataset_version: str,
    max_source_sequences: int | None,
) -> IntegrationRequest:
    config: dict = {}
    if max_source_sequences is not None:
        config["max_source_sequences"] = max_source_sequences

    return IntegrationRequest(
        operation=IntegrationOperation.INGEST,
        external_ref=ExternalDatasetRef(
            format="nuscenes",
            format_version=source_format_version,
            uri=source_root_uri,
        ),
        canonical_ref=CanonicalDatasetRef(
            dataset_id=dataset_id, dataset_version=dataset_version
        ),
        config=config,
        metadata={"requested_by": "nuscenes_container_smoke"},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root-uri",
        required=True,
        help="nuScenes dataroot path as seen INSIDE the container "
        "(e.g. /data/raw/nuscenes).",
    )
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--source-format-version", default="v1.0-mini")
    parser.add_argument("--max-source-sequences", type=int, default=None)
    args = parser.parse_args()

    request = build_request(
        source_root_uri=args.source_root_uri,
        source_format_version=args.source_format_version,
        dataset_id=args.dataset_id,
        dataset_version=args.dataset_version,
        max_source_sequences=args.max_source_sequences,
    )
    print(request.model_dump_json(by_alias=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
