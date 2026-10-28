#!/usr/bin/env python3
"""A mixed-source DatasetVersion verified against the raw inputs (M3 S5, ADR-015 §7, §12).

    derive     the expected composition of config/reference/mixed-sources/composition.json
               (verification/mixed_source.py): every member, content pin and summary,
               from the locked recording, the raw nuScenes tree and the raw pusht tree;
               compared with config/reference/mixed-sources/expectations.json (--write
               replaces that file; nothing else writes it); no stack
    selftest   a synthetic platform state built from the derivation, composed by pins
               and by selector, must pass, and every negative control must be
               detected; proves the checks, not the platform; no stack
    verify     the running platform's DatasetVersion --dataset (FastAPI + object store,
               read-only) against the derivation, then every negative control on a
               copy of what was collected

Rules: docs/development/reference-verification.md ("Mixed-source DatasetVersions"). Each
command prints one JSON report (sceneops.reference_verification_report/1) on stdout and
exits non-zero on any violation or undetected control.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verification import mixed_source  # noqa: E402
from verification.contract import REPO_ROOT  # noqa: E402
from verification.findings import Findings  # noqa: E402
from verification.observation import HttpApi, S3Store  # noqa: E402

REPORT_SCHEMA = "sceneops.reference_verification_report/1"


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _derive(args: argparse.Namespace) -> mixed_source.MixedExpectation:
    cache_root = Path(
        args.cache_root
        or os.environ.get("REFERENCE_DATA_ROOT")
        or REPO_ROOT / "data/reference"
    )
    _log("--- deriving the mixed-source composition from the raw inputs")
    return mixed_source.derive(mixed_source.load_spec(), cache_root=cache_root)


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
        _log(f"❌ [{v['code']}] {v.get('part') or ''} {v.get('unit_key') or ''}: {v['message']}")
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


def _verify(obs: dict[str, Any], expected: mixed_source.MixedExpectation) -> Findings:
    f = Findings()
    mixed_source.verify(f, obs, expected)
    return f


def _committed() -> dict[str, Any] | None:
    path = mixed_source.EXPECTATIONS_PATH
    return json.loads(path.read_text()) if path.exists() else None


def cmd_derive(args: argparse.Namespace) -> int:
    expected = _derive(args)
    document = mixed_source.expectations_document(expected)
    if args.write:
        mixed_source.EXPECTATIONS_PATH.write_text(json.dumps(document, indent=2) + "\n")
        _log(f"wrote {mixed_source.EXPECTATIONS_PATH.relative_to(REPO_ROOT)}")
    findings = Findings()
    mixed_source.check_expectations(findings, _committed(), document)
    return _report("derive", findings, summary=document["summary"])


def cmd_selftest(args: argparse.Namespace) -> int:
    expected = _derive(args)
    findings = Findings()
    controls: list[dict[str, Any]] = []
    for with_selector in (False, True):
        obs = mixed_source.synthetic_observation(
            expected, dataset_key="selftest/mixed", with_selector=with_selector
        )
        scope = findings.scoped(composition="selector" if with_selector else "pins")
        mixed_source.verify(scope, obs, expected)
        if findings.ok:
            for control in mixed_source.run_controls(
                obs, lambda corrupted: _verify(corrupted, expected).codes()
            ):
                controls.append(
                    {**control, "control": f"{control['control']}@{'selector' if with_selector else 'pins'}"}
                )
    return _report(
        "selftest", findings, controls,
        note="synthetic platform state: proves the checks, not the platform",
    )  # fmt: skip


def cmd_verify(args: argparse.Namespace) -> int:
    expected = _derive(args)
    findings = Findings()
    mixed_source.check_expectations(
        findings, _committed(), mixed_source.expectations_document(expected)
    )
    if args.observation_in:
        obs = json.loads(Path(args.observation_in).read_text())
    else:
        _log(f"--- collecting DatasetVersion {args.dataset} (read-only)")
        parts = expected.parts.values()
        obs = mixed_source.collect(
            HttpApi(
                os.environ.get("API_BASE_URL", "http://localhost:8000"),
                os.environ.get("API_PREFIX", "/api/v1"),
            ),
            S3Store.from_env(),
            dataset_key=args.dataset,
            content_ids=[p.source_id for p in parts if p.source_kind == "content"],
            robot_run_ids=[p.source_id for p in parts if p.source_kind == "recording"],
        )
    if args.observation_out:
        Path(args.observation_out).write_text(json.dumps(obs, sort_keys=True))
    platform = _verify(obs, expected)
    findings.violations += platform.violations
    for code, n in platform.checked.items():
        findings.checked[code] = findings.checked.get(code, 0) + n
    controls = None
    if platform.ok:
        controls = mixed_source.run_controls(
            obs, lambda corrupted: _verify(corrupted, expected).codes()
        )
    else:
        _log("negative controls are run only on a platform state that passes")
    version = (obs.get("dataset_version") or {}).get("version") or {}
    return _report(
        "verify", findings, controls, dataset_version=args.dataset,
        composed_by="selector" if version.get("selector") else "pins",
        summary=expected.summary(),
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cache-root",
        help="the reference data root holding the locked recordings (default data/reference)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("derive")
    p.add_argument("--write", action="store_true")
    p.set_defaults(func=cmd_derive)
    sub.add_parser("selftest").set_defaults(func=cmd_selftest)
    p = sub.add_parser("verify")
    p.add_argument("--dataset", required=True, help="<dataset_id>/<version>")
    p.add_argument("--observation-out")
    p.add_argument("--observation-in")
    p.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
