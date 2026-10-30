#!/usr/bin/env python3
"""Source-grounded verification of the golden reference contract.

    sources    raw nuScenes tables and files <-> locked recordings <-> corpus lock
               <-> reference label artifacts; then the source negative controls
    derive     expectations from the locked recordings by the documented rules;
               compared with config/reference/<corpus>/reference_expectations.json
               (--write replaces that file; nothing else writes it)
    selftest   a synthetic platform state built from the derived expectations
               must pass, and every platform negative control must be detected;
               proves the checks, not the platform
    verify     the running platform's contract state (FastAPI + object store,
               read-only) against the derived expectations, then every platform
               negative control on a copy of what was collected; --mode limits it
               to one ingestion mode, --fixture to one fixture's RobotRuns and its
               baseline-<fixture> DatasetVersion (the report names both)

Rules: docs/development/reference-verification.md. Each command prints one JSON
report (sceneops.reference_verification_report/1) on stdout and exits non-zero
on any violation or undetected negative control.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verification import negative, observation, source_checks, synthetic  # noqa: E402
from verification.checks import summary_for  # noqa: E402
from verification.contract import (  # noqa: E402
    MODES,
    REPO_ROOT,
    ContractError,
    Derived,
    Sources,
    cached_recording,
    check_expectations,
    derive,
    derive_fixture,
    expectations_document,
    load_sources,
    read_json,
)
from verification.findings import Findings  # noqa: E402
from verification.nuscenes_source import NuScenesScene, NuScenesTables  # noqa: E402
from verification.platform import verify_platform  # noqa: E402
from verification.recording import read_recording_facts, recording_facts_from_bytes  # noqa: E402

REPORT_SCHEMA = "sceneops.reference_verification_report/1"


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _report(
    command: str,
    findings: Findings,
    controls: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> int:
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
        _log(
            f"❌ [{v['code']}] {v.get('mode') or ''} {v.get('fixture_id') or ''}: {v['message']}"
        )
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


def _fixtures(sources: Sources, args: argparse.Namespace) -> list[str]:
    if getattr(args, "fixture", None):
        if args.fixture not in sources.fixture_ids():
            raise ContractError(f"{args.fixture!r} is not a contract fixture")
        return [args.fixture]
    return sources.fixture_ids()


def _cache_root(args: argparse.Namespace) -> Path:
    return Path(
        args.cache_root
        or os.environ.get("REFERENCE_DATA_ROOT")
        or REPO_ROOT / "data/reference"
    )


def _derive_all(
    sources: Sources, cache_root: Path, fixtures: list[str]
) -> dict[str, Derived]:
    out = {}
    for fid in fixtures:
        _log(f"--- deriving {fid}")
        out[fid] = derive_fixture(sources, fid, cache_root)
    return out


def _labels_path(sources: Sources, fixture_id: str, cache_root: Path) -> Path:
    sha = sources.locked(fixture_id)["labels"]["sha256"].removeprefix("sha256:")
    return (
        cache_root
        / sources.contract["corpus"]["corpus_id"]
        / "labels"
        / f"{fixture_id}-{sha[:12]}.labels.json"
    )


# ── sources ──────────────────────────────────────────────────────────────────


def _source_findings(
    state: dict[str, Any], scene: NuScenesScene, fixture_id: str, *, hash_files: bool
) -> Findings:
    f = Findings({"fixture_id": fixture_id})
    source_checks.check_lock(f, state["facts"], state["lock"])
    source_checks.check_recording_against_source(
        f, state["facts"], scene, hash_files=hash_files
    )
    source_checks.check_labels(f, state["labels"], state["labels_lock"], scene)
    return f


def cmd_sources(args: argparse.Namespace) -> int:
    sources = load_sources()
    cache_root = _cache_root(args)
    tables = NuScenesTables(
        Path(args.nuscenes_root), sources.corpus["source"]["version"]
    )
    findings = Findings()
    controls: list[dict[str, Any]] = []
    for i, fid in enumerate(_fixtures(sources, args)):
        _log(f"--- {fid}: raw nuScenes <-> locked recording <-> lock <-> labels")
        unit = next(
            f["source_unit"]
            for f in sources.corpus["fixtures"]
            if f["fixture_id"] == fid
        )
        scene = NuScenesScene(tables, unit)
        recording = cached_recording(sources, fid, cache_root)
        _log(f"    {recording.name}")
        state = {
            "facts": read_recording_facts(recording),
            "lock": sources.locked(fid)["recording"],
            "labels": _labels_path(sources, fid, cache_root).read_bytes(),
            "labels_lock": sources.locked(fid)["labels"],
        }
        result = _source_findings(state, scene, fid, hash_files=not args.no_hash)
        findings.violations += result.violations
        for code, n in result.checked.items():
            findings.checked[code] = findings.checked.get(code, 0) + n
        if i == 0:
            for control in negative.source_controls():
                corrupted = copy.deepcopy(state)
                control.apply(corrupted, {})
                codes = _source_findings(
                    corrupted, scene, fid, hash_files=not args.no_hash
                ).codes()
                controls.append(
                    {"control": control.name, "fault": control.fault, "fixture_id": fid, "expected_codes": sorted(control.codes),
                     "observed_codes": sorted(codes), "detected": bool(codes & control.codes)}
                )  # fmt: skip
    return _report("sources", findings, controls)


# ── derive ───────────────────────────────────────────────────────────────────


def cmd_derive(args: argparse.Namespace) -> int:
    sources = load_sources()
    derived = _derive_all(sources, _cache_root(args), sources.fixture_ids())
    document = expectations_document(sources, derived)
    findings = Findings()
    for fid, d in derived.items():
        findings.scoped(layer="expectations", fixture_id=fid).equal(
            d.facts.checksum,
            sources.locked(fid)["recording"]["sha256"],
            "lock_recording_checksum",
            "derived from the locked recording",
        )
    if args.write:
        if not findings.ok:
            return _report("derive", findings)
        sources.expectations_path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n"
        )
        _log(f"wrote {sources.expectations_path.relative_to(REPO_ROOT)}")
        return _report(
            "derive",
            findings,
            written=str(sources.expectations_path.relative_to(REPO_ROOT)),
        )
    committed = read_json(sources.expectations_path)
    check_expectations(findings, committed, document)
    controls = []
    for control in negative.expectation_controls():
        state = {"committed": copy.deepcopy(committed)}
        control.apply(state, {})
        f = Findings()
        check_expectations(f, state["committed"], document)
        controls.append({"control": control.name, "fault": control.fault, "expected_codes": sorted(control.codes),
                         "observed_codes": sorted(f.codes()), "detected": bool(f.codes() & control.codes)})  # fmt: skip
    return _report("derive", findings, controls)


# ── selftest and verify ──────────────────────────────────────────────────────


def synthetic_observation(
    sources: Sources,
    derived: dict[str, Derived],
    modes: tuple[str, ...] = MODES,
    selection: list[str] | None = None,
) -> dict[str, Any]:
    """The platform state the rules describe, for every run of ``modes`` over the
    fixtures of ``derived``, composed as ``selection`` names it (test double)."""
    obs = synthetic.empty()
    for mode in modes:
        spec = sources.contract["ingestion_modes"][mode]
        pins = []
        for fid, d in derived.items():
            pins += synthetic.add_robot_run(
                obs, robot_run_id=sources.run_id(mode, fid), robot_id=spec["robot_id"], recording=sources.locked(fid)["recording"],
                scene=d.scene, episodes=d.episodes, scene_config=sources.scene_config, episode_config=sources.episode_config,
            )  # fmt: skip
        summary = summary_for(
            [d.scene for d in derived.values()], [d.episodes for d in derived.values()]
        )
        synthetic.add_dataset_version(
            obs, sources.dataset_key(mode, selection), pins, summary
        )
    return obs


def _verify(
    sources: Sources,
    obs: dict[str, Any],
    derived: dict[str, Derived],
    *,
    captured: dict[str, Any],
    deep: bool,
    fixtures: list[str] | None = None,
    modes: tuple[str, ...] = MODES,
    selection: list[str] | None = None,
) -> Findings:
    f = Findings()
    verify_platform(
        f,
        sources,
        obs,
        derived,
        captured=captured,
        derive_captured=lambda facts: derive(sources, facts),
        deep=deep,
        fixtures=fixtures,
        modes=modes,
        selection=selection,
    )
    return f


def control_context(
    sources: Sources, modes: tuple[str, ...], selection: list[str] | None = None
) -> dict[str, Any]:
    """What the platform controls corrupt: the first selected fixture's run of the
    first verified mode (Recording Import when it is verified). The other RobotRun a
    binding is moved to is the same fixture's Streaming Acquisition run when both
    modes are verified, else another contract fixture's run of that mode; a foreign
    member is that streaming run's Episode, or a fabricated unit outside the
    contract when no observed run lies outside the version."""
    selection = selection or sources.fixture_ids()
    first = selection[0]
    other = next(fid for fid in sources.fixture_ids() if fid != first)
    primary = modes[0]
    both = set(modes) == set(MODES)
    return {
        "run_id": sources.run_id(primary, first),
        "other_run_id": sources.run_id("streaming_acquisition", first)
        if both
        else sources.run_id(primary, other),
        "foreign_run_id": sources.run_id("streaming_acquisition", first)
        if both
        else None,
        "dataset_key": sources.dataset_key(primary, selection),
    }


def _controls(
    sources: Sources,
    obs: dict[str, Any],
    derived: dict[str, Derived],
    captured: dict[str, Any],
    deep: bool,
    modes: tuple[str, ...] = MODES,
    selection: list[str] | None = None,
) -> list[dict[str, Any]]:
    fid = (selection or sources.fixture_ids())[0]
    _log(f"--- negative controls on {fid}")
    return negative.run_platform_controls(
        obs,
        control_context(sources, modes, selection),
        lambda corrupted: _verify(
            sources, corrupted, derived, captured=captured, deep=True, fixtures=[fid], modes=modes,
            selection=selection,
        ).codes(),
    )  # fmt: skip


def cmd_selftest(args: argparse.Namespace) -> int:
    sources = load_sources()
    derived = _derive_all(sources, _cache_root(args), sources.fixture_ids())
    obs = synthetic_observation(sources, derived)
    # A synthetic capture is the locked recording itself.
    captured = {
        sources.run_id("streaming_acquisition", fid): d.facts
        for fid, d in derived.items()
    }
    findings = _verify(sources, obs, derived, captured=captured, deep=True)
    controls = _controls(sources, obs, derived, captured, True) if findings.ok else []
    return _report(
        "selftest",
        findings,
        controls,
        note="synthetic platform state: proves the checks, not the platform",
    )


def cmd_verify(args: argparse.Namespace) -> int:
    sources = load_sources()
    modes = tuple(m for m in MODES if m in (args.mode or MODES))
    # One fixture: its RobotRuns and its own baseline-<fixture> composition.
    selection = _fixtures(sources, args)
    partial = selection != sources.fixture_ids()
    derived = _derive_all(sources, _cache_root(args), selection)
    findings = Findings()
    check_expectations(
        findings,
        read_json(sources.expectations_path),
        expectations_document(sources, derived),
        fixtures=selection if partial else None,
    )
    if args.observation_in:
        obs = json.loads(Path(args.observation_in).read_text())
        captured: dict[str, Any] = {}
        _log(
            "captured recordings are not part of a saved observation: streaming content is reported as unverified"
        )
    else:
        captured = {}
        streaming = {
            sources.run_id("streaming_acquisition", fid)
            for fid in selection
            if "streaming_acquisition" in modes
        }

        def sink(run_id: str, data: bytes) -> None:
            if run_id in streaming:
                _log(f"    re-deriving the captured recording of {run_id}")
                captured[run_id] = recording_facts_from_bytes(data)

        _log("--- collecting the contract's platform state (read-only)")
        obs = observation.collect(
            observation.HttpApi(os.environ.get("API_BASE_URL", "http://localhost:8000"), os.environ.get("API_PREFIX", "/api/v1")),
            observation.S3Store.from_env(),
            robot_run_ids=[sources.run_id(m, fid) for m in modes for fid in selection],
            dataset_versions=[tuple(sources.dataset_key(m, selection).split("/", 1)) for m in modes],
            deep=args.deep,
            recording_sink=sink,
        )  # fmt: skip
    if args.observation_out:
        observation.write_observation(obs, Path(args.observation_out))
    platform = _verify(
        sources, obs, derived, captured=captured, deep=args.deep, modes=modes,
        selection=selection,
    )  # fmt: skip
    findings.violations += platform.violations
    for code, n in platform.checked.items():
        findings.checked[code] = findings.checked.get(code, 0) + n
    controls = (
        _controls(sources, obs, derived, captured, args.deep, modes, selection)
        if platform.ok
        else None
    )
    if controls is None:
        _log("negative controls are run only on a platform state that passes")
    # A report of fewer modes or fixtures is not the contract's acceptance; it says so.
    return _report(
        "verify", findings, controls, deep=args.deep, modes=list(modes),
        fixtures=selection, dataset_versions=[sources.dataset_key(m, selection) for m in modes],
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cache-root",
        help="reference data cache (default data/reference or REFERENCE_DATA_ROOT)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sources")
    p.add_argument(
        "--nuscenes-root",
        default=os.environ.get("NUSCENES_DATAROOT")
        or str(REPO_ROOT / "data/raw/nuscenes"),
    )
    p.add_argument("--fixture")
    p.add_argument(
        "--no-hash",
        action="store_true",
        help="skip hashing the source image and point-cloud files",
    )
    p.set_defaults(func=cmd_sources)
    p = sub.add_parser("derive")
    p.add_argument(
        "--write",
        action="store_true",
        help="replace reference_expectations.json with the derivation",
    )
    p.set_defaults(func=cmd_derive)
    sub.add_parser("selftest").set_defaults(func=cmd_selftest)
    p = sub.add_parser("verify")
    p.add_argument(
        "--deep", action="store_true", help="read back and hash every payload object"
    )
    p.add_argument(
        "--observation-out", help="also write the collected observation (JSON)"
    )
    p.add_argument(
        "--observation-in", help="verify a saved observation instead of collecting one"
    )
    p.add_argument(
        "--mode",
        action="append",
        choices=MODES,
        help="verify only this ingestion mode (repeatable; default: every mode)",
    )
    p.add_argument(
        "--fixture",
        help="verify only this fixture's RobotRuns and its baseline-<fixture> "
        "DatasetVersion (default: the whole contract)",
    )
    p.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ContractError as exc:
        _log(f"❌ {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
