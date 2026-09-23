#!/usr/bin/env python
"""LeRobot containerized round-trip E2E, Step 3 (SceneOps V2 Request 4.3):
verify the ``IntegrationResult`` the LeRobot integration container
produced, reconstruct its ``ExternalExportReport`` from
``result_metadata``, and reopen the exported LeRobot dataset with
LeRobot's own official reader -- checking every claim against the exact
same frozen golden expectations ``scripts/e2e/e2e_lerobot_export.py``
already checks (``scripts/e2e/lerobot_roundtrip_golden.py``, shared
unchanged).

Runs ONLY inside tools/lerobot-integration's isolated venv (imports
``lerobot``) -- the SAME environment the host E2E's own Step 2
(``e2e_lerobot_export.py``) already uses for this exact read-back purpose
(SceneOps V2 Request 4.3 §4: reusing what already exists rather than
standing up a second "read-back container"/generic service layer).

The LeRobot EXPORT itself already happened inside the LeRobot integration
container (``tools/lerobot-integration/Dockerfile``, SceneOps V2 Request
4.2) -- this script never re-exports, never opens a SceneOpsDataset, and
never touches Postgres/sceneops-db; it only reads the container's own
``IntegrationResult`` JSON (``--result-file``) plus the LeRobot dataset
files it wrote to the host-mounted export directory (``--export-root-
host``).

Usage (always from within tools/lerobot-integration/, see
scripts/e2e/e2e_lerobot_container_roundtrip.sh):
    uv run python ../../scripts/e2e/e2e_lerobot_container_verify.py \\
        --result-file /path/to/result.json \\
        --export-root-host /path/to/host/export/dir \\
        --export-root-container /data/runs/.../<repo-id> \\
        --dataset-id test-e2e-interop --dataset-version test-v1
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from lerobot_roundtrip_golden import (
    RoundtripAssertionError,
    check,
    verify_export_report,
    verify_official_readback,
)
from sceneops_analytics.external_adapters import ExternalExportReport
from sceneops_core.integration_runtime import IntegrationOperation, IntegrationResult


def _verify_integration_result(
    result: IntegrationResult,
    *,
    expected_dataset_id: str,
    expected_dataset_version: str,
    expected_export_root_container: str,
) -> None:
    """Everything checkable from the container's ``IntegrationResult``
    alone (SceneOps V2 Request 4.3 §11), before touching the LeRobot
    output on disk -- direction, canonical/external identity, and the
    frozen "never duplicate the export target as an ArtifactRef" rule
    (Request 4.1A §5 / 4.2 §2)."""
    check(
        result.operation is IntegrationOperation.EXPORT,
        f"operation={result.operation.value!r}, expected 'export'",
    )
    check(
        result.canonical_ref.dataset_id == expected_dataset_id,
        f"canonical_ref.dataset_id={result.canonical_ref.dataset_id!r}, "
        f"expected {expected_dataset_id!r}",
    )
    check(
        result.canonical_ref.dataset_version == expected_dataset_version,
        f"canonical_ref.dataset_version={result.canonical_ref.dataset_version!r}, "
        f"expected {expected_dataset_version!r}",
    )
    check(
        result.external_ref.format == "lerobot",
        f"external_ref.format={result.external_ref.format!r}, expected 'lerobot'",
    )
    check(
        result.external_ref.uri == expected_export_root_container,
        f"external_ref.uri={result.external_ref.uri!r}, expected "
        f"{expected_export_root_container!r}",
    )
    check(
        result.produced_artifacts == {},
        f"produced_artifacts={result.produced_artifacts!r}, expected {{}} -- "
        "the LeRobot dataset root must never be duplicated as an ArtifactRef "
        "(Request 4.1A §5)",
    )


def _run(args: argparse.Namespace) -> int:
    print("--- 1. Load IntegrationResult (container output) ---", file=sys.stderr)
    payload = json.loads(Path(args.result_file).read_text())
    result = IntegrationResult.model_validate(payload)
    print(f"  external_ref.uri={result.external_ref.uri}", file=sys.stderr)

    print("--- 2. Verify IntegrationResult ---", file=sys.stderr)
    _verify_integration_result(
        result,
        expected_dataset_id=args.dataset_id,
        expected_dataset_version=args.dataset_version,
        expected_export_root_container=args.export_root_container,
    )
    print("  OK", file=sys.stderr)

    print("--- 3. Reconstruct + verify ExternalExportReport ---", file=sys.stderr)
    # result_metadata is opaque JSON at the IntegrationResult contract
    # level (SceneOps V2 Request 4.1) -- reconstructing the real,
    # strongly-typed ExternalExportReport from it here, once, is exactly
    # what lets verify_export_report() (Request 3.4's own oracle) run
    # unchanged against a container-produced result.
    report = ExternalExportReport.model_validate(result.result_metadata)
    verify_export_report(report)
    print("  OK", file=sys.stderr)

    print("--- 4. Reopen with official LeRobot reader + verify ---", file=sys.stderr)
    repo_id = result.external_ref.external_name or Path(args.export_root_host).name
    summary = verify_official_readback(Path(args.export_root_host), repo_id=repo_id)
    print("  OK", file=sys.stderr)

    print(
        json.dumps(
            {
                "ok": True,
                "export_root": args.export_root_host,
                "integration_result": {
                    "operation": result.operation.value,
                    "external_ref": result.external_ref.model_dump(mode="json"),
                    "canonical_ref": result.canonical_ref.model_dump(mode="json"),
                    "produced_artifacts": result.produced_artifacts,
                },
                "report": {
                    "exported_episode_count": report.exported_episode_count,
                    "exported_step_count": report.exported_step_count,
                    "source_episode_refs": [
                        {
                            "episode_id": r.episode_id,
                            "aligned_artifact_checksum": r.aligned_artifact_checksum,
                        }
                        for r in report.source_episode_refs
                    ],
                },
                "readback": summary,
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-file", required=True)
    parser.add_argument(
        "--export-root-host",
        required=True,
        help="LeRobot dataset root as a real filesystem path from THIS process.",
    )
    parser.add_argument(
        "--export-root-container",
        required=True,
        help="Expected external_ref.uri exactly as the container saw it.",
    )
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-version", required=True)
    args = parser.parse_args()

    try:
        return _run(args)
    except RoundtripAssertionError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- top-level: always fail clearly, never a bare traceback exit
        traceback.print_exc(file=sys.stderr)
        print(
            f"❌ LeRobot container round-trip verification failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
