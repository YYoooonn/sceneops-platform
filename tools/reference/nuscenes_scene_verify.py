#!/usr/bin/env python3
"""nuScenes scene-0061 slice Scenes verified against the raw source (M3 S4, ADR-015 §12).

    derive     the expectations of the raw scene-0061 slice by the documented rules
               (verification/nuscenes_scene.py), summarized; no stack
    selftest   a synthetic platform state built from the derivation must pass, and
               every negative control must be detected; proves the checks, not
               the platform; no stack
    verify     the running platform's Scenes of one SourceSnapshot (FastAPI +
               object store, read-only) against the derivation, then every
               negative control on a copy of what was collected; --unit-key limits
               the units held to the derivation, --dataset adds one DatasetVersion

Inputs: config/reference/source-snapshots.json (the nuscenes-scene-0061 declaration
and local root) and config/reference/nuscenes-scene-0061/scene_build_config.json.
Each command prints one JSON report (sceneops.reference_verification_report/1) on
stdout and exits non-zero on any violation or undetected control.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verification import nuscenes_scene  # noqa: E402
from verification.findings import Findings  # noqa: E402
from verification.observation import HttpApi, S3Store  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_SCHEMA = "sceneops.reference_verification_report/1"
SNAPSHOT = "nuscenes-scene-0061"
CONFIG_PATH = REPO_ROOT / "config/reference/nuscenes-scene-0061/scene_build_config.json"


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _declaration() -> dict[str, Any]:
    snapshots = json.loads(
        (REPO_ROOT / "config/reference/source-snapshots.json").read_text()
    )["snapshots"]
    return snapshots[SNAPSHOT]


def _derive(args: argparse.Namespace):
    declaration = _declaration()
    root = Path(args.source_root or REPO_ROOT / declaration["local_root"])
    raw = nuscenes_scene.read_raw(root, declaration["include"])
    config = json.loads(CONFIG_PATH.read_text())
    unit_keys = None if args.unit_key is None else [args.unit_key]
    return declaration, raw, nuscenes_scene.derive(raw, config, unit_keys)


def _report(command: str, findings: Findings, controls=None, **extra: Any) -> int:
    undetected = [c["control"] for c in controls or [] if not c["detected"]]
    report = {
        "schema": REPORT_SCHEMA,
        "command": command,
        "ok": findings.ok and not undetected,
        "checks": dict(sorted(findings.checked.items())),
        "check_count": sum(findings.checked.values()),
        "violations": findings.violations,
        "negative_controls": controls,
        "undetected_controls": undetected,
        **extra,
    }
    print(json.dumps(report, indent=2))
    for v in findings.violations[:50]:
        _log(f"❌ [{v['code']}] {v.get('unit_key') or ''}: {v['message']}")
    for name in undetected:
        _log(f"❌ negative control {name} was not detected")
    detected = sum(1 for c in controls or [] if c["detected"])
    _log(
        f"{'✅' if report['ok'] else '❌'} {command}: {report['check_count']} checks, "
        f"{len(findings.violations)} violation(s)"
        + (
            f", negative controls {detected}/{len(controls)} detected"
            if controls is not None
            else ""
        )
    )
    return 0 if report["ok"] else 1


def _verify(obs, raw, expected, declaration, all_units: bool) -> Findings:
    f = Findings()
    nuscenes_scene.verify(
        f, obs, raw, expected, declaration=declaration, all_units=all_units
    )
    return f


def cmd_derive(args: argparse.Namespace) -> int:
    _, raw, expected = _derive(args)
    findings = Findings()
    findings.check(
        any(not u.refusals for u in expected.values()),
        "raw_buildable_scene",
        "no scene of the slice has all its files preserved",
    )
    return _report("derive", findings, summary=nuscenes_scene.summary(raw, expected))


def cmd_selftest(args: argparse.Namespace) -> int:
    declaration, raw, expected = _derive(args)
    snapshot = {
        "snapshot_id": "snapshot-" + "5" * 32,
        **{k: declaration[k] for k in ("format", "upstream", "upstream_revision")},
    }
    obs = nuscenes_scene.synthetic_observation(
        raw, expected, snapshot=snapshot, dataset_key="selftest/v1"
    )
    findings = _verify(obs, raw, expected, declaration, all_units=True)
    controls = (
        nuscenes_scene.run_controls(
            obs,
            lambda corrupted: _verify(
                corrupted, raw, expected, declaration, True
            ).codes(),
        )
        if findings.ok
        else []
    )
    return _report(
        "selftest", findings, controls,
        note="synthetic platform state: proves the checks, not the platform",
    )  # fmt: skip


def cmd_verify(args: argparse.Namespace) -> int:
    declaration, raw, expected = _derive(args)
    if args.observation_in:
        obs = json.loads(Path(args.observation_in).read_text())
    else:
        _log("--- collecting the snapshot's Scenes (read-only)")
        obs = nuscenes_scene.collect(
            HttpApi(
                os.environ.get("API_BASE_URL", "http://localhost:8000"),
                os.environ.get("API_PREFIX", "/api/v1"),
            ),
            S3Store.from_env(),
            snapshot_id=args.snapshot_id,
            dataset_key=args.dataset,
        )
    if args.observation_out:
        Path(args.observation_out).write_text(json.dumps(obs, sort_keys=True))
    all_units = args.unit_key is None
    findings = _verify(obs, raw, expected, declaration, all_units)
    controls = None
    if findings.ok:
        controls = nuscenes_scene.run_controls(
            obs,
            lambda corrupted: _verify(
                corrupted, raw, expected, declaration, all_units
            ).codes(),
        )
    else:
        _log("negative controls are run only on a platform state that passes")
    return _report(
        "verify", findings, controls, snapshot_id=args.snapshot_id,
        unit_key=args.unit_key, dataset_version=args.dataset,
        summary=nuscenes_scene.summary(raw, expected),
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source-root", help="the raw nuScenes tree (default: its local_root)"
    )
    parser.add_argument("--unit-key", help="derive and verify one scene token only")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("derive").set_defaults(func=cmd_derive)
    sub.add_parser("selftest").set_defaults(func=cmd_selftest)
    p = sub.add_parser("verify")
    p.add_argument("--snapshot-id", required=True)
    p.add_argument(
        "--dataset", help="<dataset_id>/<version> composed of the verified units"
    )
    p.add_argument("--observation-out")
    p.add_argument("--observation-in")
    p.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
