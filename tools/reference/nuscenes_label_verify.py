#!/usr/bin/env python3
"""Native nuScenes labels of the scene-0061 slice verified against the raw source (M3 S6, ADR-015 §9, §12).

    derive     the expected label set of the raw scene-0061 slice by the documented
               rules (verification/nuscenes_labels.py), summarized; no stack
    selftest   a synthetic platform state built from the derivation (the label set,
               the Scene as the Scene rules derive it, a CAM_FRONT- and a
               LIDAR_TOP-anchored view) must pass, and every negative control must
               be detected; proves the checks, not the platform; no stack
    verify     the running platform's label set revision, the Scene of the unit and
               the named sample views (FastAPI + object store, read-only) against
               the derivation, then every negative control on a copy of what was
               collected

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

from verification import nuscenes_labels, nuscenes_scene  # noqa: E402
from verification.findings import Findings  # noqa: E402
from verification.observation import HttpApi, S3Store  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_SCHEMA = "sceneops.reference_verification_report/1"
SNAPSHOT = "nuscenes-scene-0061"
SCENE_0061 = "cc8c0bf57f984915a77078b10eb33198"
LABEL_SET_ID = "nuscenes-scene-0061"
CONFIG_PATH = REPO_ROOT / "config/reference/nuscenes-scene-0061/scene_build_config.json"


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _derive(args: argparse.Namespace):
    declaration = json.loads(
        (REPO_ROOT / "config/reference/source-snapshots.json").read_text()
    )["snapshots"][SNAPSHOT]
    root = Path(args.source_root or REPO_ROOT / declaration["local_root"])
    raw = nuscenes_scene.read_raw(root, declaration["include"])
    expected = nuscenes_labels.derive(
        raw, label_set_id=args.label_set_id, unit_keys=[args.unit_key]
    )
    return raw, expected


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
        _log(f"❌ [{v['code']}] {v['message']}")
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


def _verify(obs, expected) -> Findings:
    f = Findings()
    nuscenes_labels.verify(f, obs, expected)
    return f


def _checked(command: str, obs, expected, **extra: Any) -> int:
    findings = _verify(obs, expected)
    controls = None
    if findings.ok:
        controls = nuscenes_labels.run_controls(
            obs, lambda corrupted: _verify(corrupted, expected).codes()
        )
    else:
        _log("negative controls are run only on a platform state that passes")
    return _report(
        command, findings, controls, summary=nuscenes_labels.summary(expected), **extra
    )


def cmd_derive(args: argparse.Namespace) -> int:
    _, expected = _derive(args)
    findings = Findings()
    findings.check(
        not expected.refusals,
        "raw_label_rules",
        f"the raw source violates the label rules: {expected.refusals[:3]}",
    )
    return _report("derive", findings, summary=nuscenes_labels.summary(expected))


def cmd_selftest(args: argparse.Namespace) -> int:
    raw, expected = _derive(args)
    config = json.loads(CONFIG_PATH.read_text())
    (unit,) = nuscenes_scene.derive(raw, config, [args.unit_key]).values()
    obs = nuscenes_labels.synthetic_observation(
        expected, unit.manifest, scene_id=unit.unit_id
    )
    return _checked(
        "selftest", obs, expected,
        note="synthetic platform state: proves the checks, not the platform",
    )  # fmt: skip


def cmd_verify(args: argparse.Namespace) -> int:
    _, expected = _derive(args)
    if args.observation_in:
        obs = json.loads(Path(args.observation_in).read_text())
    else:
        _log("--- collecting the label set, Scene and views (read-only)")
        obs = nuscenes_labels.collect(
            HttpApi(
                os.environ.get("API_BASE_URL", "http://localhost:8000"),
                os.environ.get("API_PREFIX", "/api/v1"),
            ),
            S3Store.from_env(),
            snapshot_id=args.snapshot_id,
            unit_key=args.unit_key,
            label_set_artifact_id=args.label_set_artifact,
            view_artifact_ids=args.view_artifact or [],
        )
    if args.observation_out:
        Path(args.observation_out).write_text(json.dumps(obs, sort_keys=True))
    return _checked(
        "verify", obs, expected, snapshot_id=args.snapshot_id,
        label_set_artifact=args.label_set_artifact, views=args.view_artifact or [],
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source-root", help="the raw nuScenes tree (default: its local_root)"
    )
    parser.add_argument("--unit-key", default=SCENE_0061, help="the scene token")
    parser.add_argument("--label-set-id", default=LABEL_SET_ID)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("derive").set_defaults(func=cmd_derive)
    sub.add_parser("selftest").set_defaults(func=cmd_selftest)
    p = sub.add_parser("verify")
    p.add_argument("--snapshot-id", required=True)
    p.add_argument("--label-set-artifact", required=True)
    p.add_argument(
        "--view-artifact", action="append",
        help="a sample view of the unit's Scene that pins the label set (repeatable)",
    )  # fmt: skip
    p.add_argument("--observation-out")
    p.add_argument("--observation-in")
    p.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
