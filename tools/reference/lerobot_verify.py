#!/usr/bin/env python3
"""LeRobot pusht Episodes verified against the raw source (M3 S3, ADR-015 §12).

    derive     the expectations of the pinned pusht tree by the documented rules
               (verification/lerobot.py), summarized; no stack
    selftest   a synthetic platform state built from the derivation must pass, and
               every negative control must be detected; proves the checks, not
               the platform; no stack
    verify     the running platform's Episodes of one SourceSnapshot (FastAPI +
               object store, read-only) against the derivation, then every
               negative control on a copy of what was collected; --episode limits
               the units held to the derivation, --dataset adds one DatasetVersion

Inputs: config/reference/source-snapshots.json (the lerobot-pusht declaration and
local root) and config/reference/lerobot-pusht/episode_build_config.json. Each
command prints one JSON report (sceneops.reference_verification_report/1) on
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

from verification import lerobot  # noqa: E402
from verification.findings import Findings  # noqa: E402
from verification.observation import HttpApi, S3Store  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_SCHEMA = "sceneops.reference_verification_report/1"
SNAPSHOT = "lerobot-pusht"
CONFIG_PATH = REPO_ROOT / "config/reference/lerobot-pusht/episode_build_config.json"


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
    raw = lerobot.read_raw(root, declaration["include"])
    config = json.loads(CONFIG_PATH.read_text())
    episodes = None if args.episode is None else [args.episode]
    return declaration, raw, lerobot.derive(raw, config, episodes)


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
    lerobot.verify(f, obs, raw, expected, declaration=declaration, all_units=all_units)
    return f


def cmd_derive(args: argparse.Namespace) -> int:
    _, raw, expected = _derive(args)
    findings = Findings()
    for unit in expected.values():
        findings.check(
            not unit.refusals,
            "raw_frame_grid",
            f"episode {unit.unit_key}: {unit.refusals[:3]}",
        )
    return _report("derive", findings, summary=lerobot.summary(raw, expected))


def cmd_selftest(args: argparse.Namespace) -> int:
    declaration, raw, expected = _derive(args)
    snapshot = {
        "snapshot_id": "snapshot-" + "5" * 32,
        **{k: declaration[k] for k in ("format", "upstream", "upstream_revision")},
    }
    obs = lerobot.synthetic_observation(
        raw, expected, snapshot=snapshot, dataset_key="selftest/v1"
    )
    findings = _verify(obs, raw, expected, declaration, all_units=True)
    controls = (
        lerobot.run_controls(
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
        _log("--- collecting the snapshot's Episodes (read-only)")
        obs = lerobot.collect(
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
    all_units = args.episode is None
    findings = _verify(obs, raw, expected, declaration, all_units)
    controls = None
    if findings.ok:
        controls = lerobot.run_controls(
            obs,
            lambda corrupted: _verify(
                corrupted, raw, expected, declaration, all_units
            ).codes(),
        )
    else:
        _log("negative controls are run only on a platform state that passes")
    return _report(
        "verify", findings, controls, snapshot_id=args.snapshot_id, episode=args.episode,
        dataset_version=args.dataset, summary=lerobot.summary(raw, expected),
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source-root", help="the raw pusht tree (default: its local_root)"
    )
    parser.add_argument(
        "--episode", type=int, help="derive and verify one episode only"
    )
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
