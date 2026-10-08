#!/usr/bin/env python3
"""The golden reference contract: which RobotRuns are the platform's reference state.

The contract (config/reference/<corpus>/reference_contract.json) names the exact
RobotRuns that make up the promoted reference state: every fixture of the corpus
scope, ingested once through each ingestion mode, under a deterministic identity.
This module

  * validates the contract against its sources (corpus definition, corpus lock,
    baseline build configuration),
  * evaluates observed platform state against it (pure functions, no I/O),
  * collects that state read-only through FastAPI, the existing baseline
    verifiers and the registered RobotRun manifests,
  * converges the platform on it by composing the existing bootstrap commands.

The contract layer is the only place that states how many RobotRuns, Scenes or
Episodes the reference state has, and which Scene policy produced them. Generic
SceneOps logic knows none of it. Host Python, standard library only.

Subcommands: show | validate | verify | bootstrap | pair | fingerprint
(see docs/development/reference-contract.md)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "config" / "reference" / "nuscenes-mini-v1"
BASELINE_CONFIG_DIR = REPO_ROOT / "config" / "baselines"

CONTRACT_SCHEMA = "sceneops.reference_contract/1"
REPORT_SCHEMA = "sceneops.reference_contract_report/1"
# The ingestion modes a contract states, in bootstrap order. A mode is how a
# recording reaches a RobotRun; preparing the source (nuScenes -> MCAP) is not one.
MODES = ("recording_import", "streaming_acquisition")

# Datasets of the reserved test namespace are derived test state: the fixed, test-owned
# DatasetVersions the L3 journeys and infrastructure tests build from the contract's
# RobotRuns (docs/development/test-matrix.md, REFERENCE_DERIVED). They are never part of
# the contract and never make it invalid; the verifier reports them apart from datasets
# that belong to nobody.
DERIVED_TEST_NAMESPACE = "sceneops-test-"

Violation = dict[str, Any]
Entry = dict[str, str]


class ContractError(Exception):
    """The contract or one of its sources is unreadable or malformed."""


def _violation(
    code: str, message: str, mode: str | None = None, fixture_id: str | None = None
) -> Violation:
    return {"code": code, "mode": mode, "fixture_id": fixture_id, "message": message}


# ── Loading and expansion ────────────────────────────────────────────────────


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read {path}: {exc}") from exc


def load_contract(path: Path | None = None) -> dict[str, Any]:
    contract = _read_json(path or CORPUS_DIR / "reference_contract.json")
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise ContractError(f"unsupported contract schema {contract.get('schema')!r}")
    if sorted(contract.get("ingestion_modes", {})) != sorted(MODES):
        raise ContractError(f"ingestion_modes must be exactly {list(MODES)}")
    return contract


def run_id(contract: dict[str, Any], mode: str, fixture_id: str) -> str:
    spec = contract["ingestion_modes"][mode]
    return spec["run_id_template"].format(
        baseline_id=spec["baseline_id"], fixture_id=fixture_id
    )


def expand(contract: dict[str, Any]) -> list[Entry]:
    """Every (mode, fixture) pair of the contract with its RobotRun identity."""
    return [
        {
            "mode": mode,
            "fixture_id": f["fixture_id"],
            "robot_run_id": run_id(contract, mode, f["fixture_id"]),
        }
        for mode in MODES
        for f in contract["fixtures"]
    ]


def _fixture(contract: dict[str, Any], fixture_id: str) -> dict[str, Any]:
    return next(f for f in contract["fixtures"] if f["fixture_id"] == fixture_id)


def equivalence_pair(
    contract: dict[str, Any],
    corpus: dict[str, Any],
    lock: dict[str, Any],
    *,
    fixture_id: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """The two golden RobotRuns of one fixture, resolved from the contract.

    A fixture is named directly or selected by a corpus scope that contains
    exactly one (``smoke-1`` selects ``scene-0061``). Returns the fixture's
    locked recording facts and, per ingestion mode, the RobotRun and the
    DatasetVersion that holds its Scenes and Episodes; nothing is read from the
    platform.
    """
    if fixture_id is None:
        if scope not in corpus["scopes"]:
            raise ContractError(f"the corpus has no scope {scope!r}")
        selected = _scope_fixture_ids(corpus, scope)
        if len(selected) != 1:
            raise ContractError(
                f"scope {scope!r} selects {len(selected)} fixtures; an equivalence "
                "pair is one fixture (name it with --fixture)"
            )
        fixture_id = selected[0]
    fixture = next(
        (f for f in contract["fixtures"] if f["fixture_id"] == fixture_id), None
    )
    if fixture is None:
        raise ContractError(
            f"{fixture_id!r} is not a fixture of the reference contract"
        )
    recording = lock["fixtures"][fixture_id]["recording"]
    pair: dict[str, Any] = {
        "fixture_id": fixture_id,
        "recording": {
            "sha256": fixture["recording_sha256"],
            "size_bytes": recording["size_bytes"],
            "message_count": fixture["message_count"],
            "topic_counts": recording["topic_counts"],
        },
    }
    for mode in MODES:
        spec = contract["ingestion_modes"][mode]
        pair[mode] = {
            "robot_run_id": run_id(contract, mode, fixture_id),
            "robot_id": spec["robot_id"],
            "dataset_id": spec["dataset_id"],
            "dataset_version": spec["dataset_version"],
        }
    return pair


def _scope_fixture_ids(corpus: dict[str, Any], scope: str) -> list[str]:
    ids = [f["fixture_id"] for f in corpus["fixtures"]]
    selected = corpus["scopes"][scope]
    return ids if selected == ["*"] else list(selected)


# ── The contract against its sources (offline) ───────────────────────────────


def validate_contract(
    contract: dict[str, Any],
    corpus: dict[str, Any],
    lock: dict[str, Any],
    scene_build_config: dict[str, Any],
    baseline_shape: dict[str, Any],
) -> list[Violation]:
    """Internal consistency of the contract and agreement with what it references."""
    out: list[Violation] = []
    fixtures = contract["fixtures"]
    ids = [f["fixture_id"] for f in fixtures]
    if len(set(ids)) != len(ids):
        out.append(
            _violation(
                "duplicate_fixture",
                f"fixtures repeat: {sorted({i for i in ids if ids.count(i) > 1})}",
            )
        )

    entries = expand(contract)
    run_ids = [e["robot_run_id"] for e in entries]
    for dup in sorted({r for r in run_ids if run_ids.count(r) > 1}):
        out.append(
            _violation(
                "duplicate_identity",
                f"RobotRun {dup} is the identity of more than one fixture/mode pair",
            )
        )
    for key in ("baseline_id", "robot_id", "dataset_id"):
        values = [contract["ingestion_modes"][m][key] for m in MODES]
        if len(set(values)) != len(values):
            out.append(
                _violation(
                    "duplicate_identity", f"ingestion modes share {key} {values[0]!r}"
                )
            )

    corpus_ref = contract["corpus"]
    if corpus_ref["corpus_id"] != corpus.get("corpus_id") or corpus_ref[
        "corpus_id"
    ] != lock.get("corpus_id"):
        out.append(
            _violation(
                "corpus_mismatch",
                "the contract, corpus.json and corpus.lock.json name different corpora",
            )
        )
    scope = corpus_ref["scope"]
    if scope not in corpus.get("scopes", {}):
        out.append(_violation("corpus_mismatch", f"corpus has no scope {scope!r}"))
    elif sorted(ids) != sorted(_scope_fixture_ids(corpus, scope)):
        out.append(
            _violation(
                "corpus_mismatch",
                f"contract fixtures differ from corpus scope {scope!r}",
            )
        )

    for f in fixtures:
        locked = lock.get("fixtures", {}).get(f["fixture_id"], {}).get("recording")
        if locked is None:
            out.append(
                _violation(
                    "lock_mismatch",
                    "fixture is not in the corpus lock",
                    fixture_id=f["fixture_id"],
                )
            )
            continue
        observed = (
            locked["sha256"],
            locked["message_count"],
            len(locked["topic_counts"]),
        )
        expected = (f["recording_sha256"], f["message_count"], f["channel_count"])
        if observed != expected:
            out.append(
                _violation(
                    "lock_mismatch",
                    f"contract facts {expected} differ from the lock's {observed}: the lock was re-locked, "
                    "so the contract needs a deliberate refresh",
                    fixture_id=f["fixture_id"],
                )
            )

    units = contract["units"]
    if (
        scene_build_config.get("segmentation", {}).get("policy")
        != units["scene_policy"]
    ):
        out.append(
            _violation(
                "config_mismatch",
                f"config/baselines/scene_build_config.json segments by "
                f"{scene_build_config.get('segmentation', {}).get('policy')!r}, the contract expects {units['scene_policy']!r}",
            )
        )
    if (
        baseline_shape.get("scenes_per_robot_run"),
        baseline_shape.get("episodes_per_robot_run"),
    ) != (
        units["scenes_per_robot_run"],
        units["episodes_per_robot_run"],
    ):
        out.append(
            _violation(
                "config_mismatch",
                "config/baselines/baseline_shape.json differs from the contract's units",
            )
        )

    totals = contract["expected_totals"]
    derived = {
        "fixtures": len(set(ids)),
        "robot_runs": len(set(ids)) * len(MODES),
        "scenes": len(set(ids)) * len(MODES) * units["scenes_per_robot_run"],
        "episodes": len(set(ids)) * len(MODES) * units["episodes_per_robot_run"],
    }
    if totals != derived:
        out.append(
            _violation(
                "totals_mismatch",
                f"expected_totals {totals} differ from what the contract implies {derived}",
            )
        )
    return out


# ── Observed state against the contract (pure) ───────────────────────────────

_KIND_TEMPORARY = re.compile(r"^run-(e2e|test)-")
# `run-ref-*` / `run-stream-ref-*` are the baseline identity rules of
# tools/baselines/canonical/baseline_lib.sh; a heuristic for reporting only.
_KIND_REFERENCE_LIKE = re.compile(r"^run-(stream-)?ref-")


def _classify_non_contract(run: str) -> str:
    if _KIND_TEMPORARY.match(run):
        return "temporary_e2e"
    if _KIND_REFERENCE_LIKE.match(run):
        return "reference_like_baseline"
    return "unclassified"


def _by_run(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row.get("robotRunId", ""), []).append(row)
    return out


def _contract_units(
    contract: dict[str, Any], mode: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    spec = contract["ingestion_modes"][mode]
    return [
        r
        for r in rows
        if r.get("datasetId") == spec["dataset_id"]
        and r.get("datasetVersion") == spec["dataset_version"]
    ]


def conflicts(contract: dict[str, Any], obs: dict[str, Any]) -> list[Violation]:
    """Identities the contract owns that the platform holds in a conflicting shape.

    Nothing here is repairable by a bootstrap: an immutable RobotRun that
    disagrees with the contract, or foreign state on a contract robot / dataset,
    must be resolved deliberately.
    """
    out: list[Violation] = []
    rows = obs["robot_runs"]
    ids = [r["runId"] for r in rows]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        out.append(
            _violation(
                "duplicate_robot_run", f"RobotRun {dup} is listed more than once"
            )
        )
    by_id = {r["runId"]: r for r in rows}
    entries = expand(contract)
    for e in entries:
        spec = contract["ingestion_modes"][e["mode"]]
        row = by_id.get(e["robot_run_id"])
        if row is not None and row["robotId"] != spec["robot_id"]:
            out.append(
                _violation(
                    "conflicting_robot_run",
                    f"{e['robot_run_id']} is registered under robot {row['robotId']!r}, the contract says {spec['robot_id']!r}",
                    e["mode"],
                    e["fixture_id"],
                )
            )
    for mode in MODES:
        spec = contract["ingestion_modes"][mode]
        expected = {e["robot_run_id"] for e in entries if e["mode"] == mode}
        for row in rows:
            if row["robotId"] == spec["robot_id"] and row["runId"] not in expected:
                out.append(
                    _violation(
                        "unexpected_robot_run",
                        f"{row['runId']} is registered under the contract robot {spec['robot_id']!r} but is not a contract RobotRun",
                        mode,
                    )
                )
        for kind, units, key in (
            ("Scene", obs["scenes"], "sceneId"),
            ("Episode", obs["episodes"], "episodeId"),
        ):
            for unit in _contract_units(contract, mode, units):
                if unit["robotRunId"] not in expected:
                    out.append(
                        _violation(
                            "foreign_unit",
                            f"{kind} {unit[key]} in the contract dataset {spec['dataset_id']!r} belongs to non-contract RobotRun {unit['robotRunId']!r}",
                            mode,
                        )
                    )
    return out


def inventory(contract: dict[str, Any], obs: dict[str, Any]) -> dict[str, Any]:
    """Contract vs non-contract state; informational, never a violation."""
    contract_ids = {e["robot_run_id"] for e in expand(contract)}
    present = {r["runId"] for r in obs["robot_runs"]}
    non_contract = [
        {
            "robot_run_id": r["runId"],
            "robot_id": r["robotId"],
            "kind": _classify_non_contract(r["runId"]),
            "registered_at": r.get("registeredAt"),
        }
        for r in sorted(obs["robot_runs"], key=lambda r: r["runId"])
        if r["runId"] not in contract_ids
    ]
    contract_datasets = {
        (
            contract["ingestion_modes"][m]["dataset_id"],
            contract["ingestion_modes"][m]["dataset_version"],
        )
        for m in MODES
    }

    def outside(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            u
            for u in units
            if (u["datasetId"], u["datasetVersion"]) not in contract_datasets
        ]

    def orphans(units: list[dict[str, Any]], key: str) -> list[str]:
        return sorted(u[key] for u in units if u["robotRunId"] not in present)

    kinds: dict[str, int] = {}
    for r in non_contract:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    non_contract_datasets = sorted(
        d for d in obs.get("datasets", []) if d not in {x for x, _ in contract_datasets}
    )

    def derived(units: list[dict[str, Any]]) -> int:
        return sum(
            1 for u in units if u["datasetId"].startswith(DERIVED_TEST_NAMESPACE)
        )

    return {
        "contract_robot_runs": len(contract_ids & present),
        "non_contract_robot_runs": non_contract,
        "non_contract_robot_runs_by_kind": dict(sorted(kinds.items())),
        "derived_test_datasets": [
            d for d in non_contract_datasets if d.startswith(DERIVED_TEST_NAMESPACE)
        ],
        "derived_test_scenes": derived(obs["scenes"]),
        "derived_test_episodes": derived(obs["episodes"]),
        "foreign_datasets": [
            d for d in non_contract_datasets if not d.startswith(DERIVED_TEST_NAMESPACE)
        ],
        "scenes_outside_contract_datasets": len(outside(obs["scenes"])),
        "episodes_outside_contract_datasets": len(outside(obs["episodes"])),
        "orphan_scenes": orphans(obs["scenes"], "sceneId"),
        "orphan_episodes": orphans(obs["episodes"], "episodeId"),
    }


def evaluate(
    contract: dict[str, Any],
    lock: dict[str, Any],
    obs: dict[str, Any],
    require_pristine: bool = False,
) -> dict[str, Any]:
    """The contract report for an observation.

    obs: robot_runs / scenes / episodes (FastAPI rows, all of them), datasets
    (ids), facts ({run_id: registered manifest facts} of the registered contract
    RobotRuns) and baselines ({mode: {ok, summary, error}} from the existing
    baseline verifiers).

    Validity is the contract's alone: the 20 RobotRuns, their Scenes and Episodes. State
    outside it is reported, never a violation, so the contract stays valid while fixed
    derived test datasets (DERIVED_TEST_NAMESPACE) exist on the platform.

    require_pristine: the platform holds nothing but the contract, derived test state
    included: every non-contract RobotRun, derived test dataset and foreign dataset is a
    violation. Off, they are only reported (a general development platform may hold any).
    """
    violations = conflicts(contract, obs)
    units = contract["units"]
    by_id = {r["runId"]: r for r in obs["robot_runs"]}
    entries = expand(contract)
    mode_report: dict[str, Any] = {}
    run_rows: list[dict[str, Any]] = []
    totals = {
        "fixtures": len({e["fixture_id"] for e in entries}),
        "robot_runs": 0,
        "scenes": 0,
        "episodes": 0,
    }

    for mode in MODES:
        spec = contract["ingestion_modes"][mode]
        mode_entries = [e for e in entries if e["mode"] == mode]
        scenes = _by_run(_contract_units(contract, mode, obs["scenes"]))
        episodes = _by_run(_contract_units(contract, mode, obs["episodes"]))
        counts = {"robot_runs": 0, "scenes": 0, "episodes": 0}

        for e in mode_entries:
            fid, rid = e["fixture_id"], e["robot_run_id"]
            fx = _fixture(contract, fid)
            locked = lock["fixtures"][fid]["recording"]
            registered = rid in by_id
            run_rows.append({**e, "registered": registered})
            if not registered:
                violations.append(
                    _violation(
                        "missing_robot_run",
                        f"contract RobotRun {rid} is not registered",
                        mode,
                        fid,
                    )
                )
                continue
            counts["robot_runs"] += 1

            run_scenes, run_episodes = scenes.get(rid, []), episodes.get(rid, [])
            if len(run_scenes) != units["scenes_per_robot_run"] or any(
                s["unitKey"] != units["scene_unit_key"] for s in run_scenes
            ):
                violations.append(
                    _violation(
                        "scene_shape",
                        f"{rid} has Scenes {[s['unitKey'] for s in run_scenes]}, the contract expects "
                        f"{units['scenes_per_robot_run']} {units['scene_policy']} Scene(s) with unit key {units['scene_unit_key']!r}",
                        mode,
                        fid,
                    )
                )
            if len(run_episodes) != units["episodes_per_robot_run"]:
                violations.append(
                    _violation(
                        "episode_shape",
                        f"{rid} has {len(run_episodes)} Episode(s), the contract expects {units['episodes_per_robot_run']}",
                        mode,
                        fid,
                    )
                )
            counts["scenes"] += len(run_scenes)
            counts["episodes"] += len(run_episodes)

            facts = obs.get("facts", {}).get(rid)
            if facts is None:
                violations.append(
                    _violation(
                        "facts_unavailable",
                        f"no registered manifest facts for {rid}",
                        mode,
                        fid,
                    )
                )
                continue
            if (
                facts["robot_id"] != spec["robot_id"]
                or facts["capture_source"] != spec["capture_source"]
            ):
                violations.append(
                    _violation(
                        "provenance",
                        f"{rid} manifest says robot {facts['robot_id']!r}, source {facts['capture_source']}; "
                        f"the contract says robot {spec['robot_id']!r}, source {spec['capture_source']}",
                        mode,
                        fid,
                    )
                )
            locked_recording = facts["recording"]["checksum"] == fx["recording_sha256"]
            if locked_recording != (spec["recording"] == "locked"):
                violations.append(
                    _violation(
                        "provenance",
                        f"{rid} pins the {'locked' if locked_recording else 'a different'} recording; "
                        f"this mode registers the {spec['recording']} recording",
                        mode,
                        fid,
                    )
                )
            if (
                facts["message_count"] != fx["message_count"]
                or len(facts["channel_counts"]) != fx["channel_count"]
                or facts["channel_counts"] != locked["topic_counts"]
            ):
                violations.append(
                    _violation(
                        "recording_facts",
                        f"{rid} registered {facts['message_count']} messages on {len(facts['channel_counts'])} channels; "
                        f"the locked corpus has {fx['message_count']} on {fx['channel_count']}, per channel as locked",
                        mode,
                        fid,
                    )
                )

        _check_baseline_summary(contract, mode, mode_entries, obs, violations)
        mode_report[mode] = {
            "role": spec["role"],
            "baseline_id": spec["baseline_id"],
            "robot_id": spec["robot_id"],
            "dataset": f"{spec['dataset_id']}/{spec['dataset_version']}",
            "baseline_verify": "ok"
            if obs.get("baselines", {}).get(mode, {}).get("ok")
            else "failed",
            **counts,
        }
        for key, value in counts.items():
            totals[key] += value

    if totals != contract["expected_totals"]:
        violations.append(
            _violation(
                "totals",
                f"observed {totals}, the contract expects {contract['expected_totals']}",
            )
        )
    inv = inventory(contract, obs)
    state = {
        "contract_robot_runs": inv["contract_robot_runs"],
        "non_contract_robot_runs": len(inv["non_contract_robot_runs"]),
        "contract_scenes": totals["scenes"],
        "contract_episodes": totals["episodes"],
        "derived_test_datasets": len(inv["derived_test_datasets"]),
        "foreign_datasets": len(inv["foreign_datasets"]),
        "pristine": not inv["non_contract_robot_runs"]
        and not inv["derived_test_datasets"]
        and not inv["foreign_datasets"],
    }
    if require_pristine:
        for r in inv["non_contract_robot_runs"]:
            violations.append(
                _violation(
                    "non_contract_robot_run",
                    f"RobotRun {r['robot_run_id']} ({r['kind']}) is not part of the contract; "
                    "a pristine environment holds only the contract (make local-reset, then reference-contract-bootstrap)",
                )
            )
        for dataset in inv["derived_test_datasets"]:
            violations.append(
                _violation(
                    "derived_test_dataset",
                    f"Dataset {dataset} is derived test state; a pristine environment holds none "
                    "(make local-reset, then reference-contract-bootstrap)",
                )
            )
        for dataset in inv["foreign_datasets"]:
            violations.append(
                _violation(
                    "foreign_dataset",
                    f"Dataset {dataset} is neither a contract dataset nor derived test state; "
                    "a pristine environment holds only the contract",
                )
            )
    return {
        "schema": REPORT_SCHEMA,
        "ok": not violations,
        "contract": {
            "version": contract["contract_version"],
            "corpus": contract["corpus"]["corpus_id"],
            "scope": contract["corpus"]["scope"],
            "scene_policy": units["scene_policy"],
        },
        "totals": {"expected": contract["expected_totals"], "observed": totals},
        "modes": mode_report,
        "state": state,
        "contract_robot_runs": run_rows,
        "inventory": inv,
        "violations": violations,
    }


def _check_baseline_summary(
    contract: dict[str, Any],
    mode: str,
    mode_entries: list[Entry],
    obs: dict[str, Any],
    out: list[Violation],
) -> None:
    """The existing baseline verifier's own summary, read as contract evidence."""
    spec = contract["ingestion_modes"][mode]
    baseline = obs.get("baselines", {}).get(mode)
    if baseline is None or not baseline.get("ok"):
        out.append(
            _violation(
                "baseline_verify_failed",
                f"the {mode} baseline verifier failed: {(baseline or {}).get('error') or 'did not run'}",
                mode,
            )
        )
        return
    summary = baseline["summary"]
    identity = (
        summary["baseline_id"],
        summary["robot_id"],
        summary["dataset_id"],
        summary["dataset_version"],
    )
    if identity != (
        spec["baseline_id"],
        spec["robot_id"],
        spec["dataset_id"],
        spec["dataset_version"],
    ):
        out.append(
            _violation(
                "identity",
                f"the verified baseline is {identity}, not the contract's",
                mode,
            )
        )
    mapping: dict[str, list[str]] = {}
    for f in summary["fixtures"]:
        mapping.setdefault(f["fixture_id"], []).append(f["robot_run_id"])
    for fid, runs in sorted(mapping.items()):
        if len(runs) > 1:
            out.append(
                _violation(
                    "duplicate_fixture_mapping", f"fixture maps to {runs}", mode, fid
                )
            )
    expected = {e["fixture_id"]: e["robot_run_id"] for e in mode_entries}
    for fid, rid in expected.items():
        if mapping.get(fid) != [rid]:
            out.append(
                _violation(
                    "wrong_run_identity",
                    f"fixture maps to {mapping.get(fid)}, the contract says [{rid!r}]",
                    mode,
                    fid,
                )
            )
    for fid in sorted(set(mapping) - set(expected)):
        out.append(
            _violation(
                "unexpected_fixture", "fixture is not in the contract", mode, fid
            )
        )
    transport = spec.get("transport")
    if summary.get("transport") != transport:
        out.append(
            _violation(
                "provenance",
                f"transport {summary.get('transport')!r}, the contract says {transport!r}",
                mode,
            )
        )
    for f in summary["fixtures"]:
        fid = f["fixture_id"]
        if fid not in expected:
            continue
        fx = _fixture(contract, fid)
        if (
            f["recording_sha256"] != fx["recording_sha256"]
            or f["message_count"] != fx["message_count"]
        ):
            out.append(
                _violation(
                    "recording_facts",
                    "the verified locked recording differs from the contract's",
                    mode,
                    fid,
                )
            )
        if (
            f["scenes_ready"] != f["scene_count"]
            or f["episodes_ready"] != f["episode_count"]
        ):
            out.append(
                _violation(
                    "readiness",
                    "a Scene or Episode is not validated, profiled and ready",
                    mode,
                    fid,
                )
            )


# ── Bootstrap: state snapshots ───────────────────────────────────────────────


def snapshot(contract: dict[str, Any], obs: dict[str, Any]) -> dict[str, Any]:
    """Immutable-record fingerprints of the contract's RobotRuns, Scenes and Episodes."""
    contract_ids = {e["robot_run_id"] for e in expand(contract)}
    return {
        "robot_runs": {
            r["runId"]: [r["robotId"], r.get("registeredAt"), r.get("manifestChecksum")]
            for r in obs["robot_runs"]
            if r["runId"] in contract_ids
        },
        "units": {
            u[key]: [u["robotRunId"], u.get("manifestChecksum"), u.get("updatedAt")]
            for units, key in (
                (obs["scenes"], "sceneId"),
                (obs["episodes"], "episodeId"),
            )
            for u in units
            if u["robotRunId"] in contract_ids
        },
    }


def platform_fingerprint(state: dict[str, Any]) -> dict[str, Any]:
    """Every RobotRun, Dataset, Scene and Episode the platform holds, as the
    identity plus the fields that change when a record is rewritten. Two
    fingerprints are equal exactly when no record was added, removed or changed."""
    return {
        "counts": {
            "robot_runs": len(state["robot_runs"]),
            "datasets": len(state["datasets"]),
            "scenes": len(state["scenes"]),
            "episodes": len(state["episodes"]),
        },
        "robot_runs": {
            r["runId"]: [r["robotId"], r.get("registeredAt"), r.get("manifestChecksum")]
            for r in state["robot_runs"]
        },
        "datasets": sorted(state["datasets"]),
        "scenes": {
            u["sceneId"]: [
                u["robotRunId"],
                u.get("manifestChecksum"),
                u.get("updatedAt"),
            ]
            for u in state["scenes"]
        },
        "episodes": {
            u["episodeId"]: [
                u["robotRunId"],
                u.get("manifestChecksum"),
                u.get("updatedAt"),
            ]
            for u in state["episodes"]
        },
    }


def golden_fingerprint(
    contract: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """The contract's own records: its RobotRuns and the Scenes and Episodes of its
    DatasetVersions, in the shape of `platform_fingerprint`. Derived test state, which
    consumes the same RobotRuns from Datasets of its own, is not part of it, so the
    fingerprint is equal before and after a derived workflow exactly when that workflow
    left the contract untouched."""
    contract_ids = {e["robot_run_id"] for e in expand(contract)}
    fingerprint = platform_fingerprint(state)
    in_contract = {
        key: {
            u[key_name]
            for mode in MODES
            for u in _contract_units(contract, mode, state[key])
        }
        for key, key_name in (("scenes", "sceneId"), ("episodes", "episodeId"))
    }
    return {
        "counts": {
            "robot_runs": len(contract_ids & set(fingerprint["robot_runs"])),
            "scenes": len(in_contract["scenes"]),
            "episodes": len(in_contract["episodes"]),
        },
        "robot_runs": {
            k: v for k, v in fingerprint["robot_runs"].items() if k in contract_ids
        },
        "scenes": {
            k: v for k, v in fingerprint["scenes"].items() if k in in_contract["scenes"]
        },
        "episodes": {
            k: v
            for k, v in fingerprint["episodes"].items()
            if k in in_contract["episodes"]
        },
    }


def plan(contract: dict[str, Any], obs: dict[str, Any]) -> list[dict[str, Any]]:
    """Per contract RobotRun, what the platform holds now (before a bootstrap)."""
    by_id = {r["runId"] for r in obs["robot_runs"]}
    units = contract["units"]
    out = []
    for e in expand(contract):
        spec = contract["ingestion_modes"][e["mode"]]
        rid = e["robot_run_id"]
        if rid not in by_id:
            state = "missing"
        else:
            scenes = [
                s
                for s in _contract_units(contract, e["mode"], obs["scenes"])
                if s["robotRunId"] == rid
            ]
            episodes = [
                s
                for s in _contract_units(contract, e["mode"], obs["episodes"])
                if s["robotRunId"] == rid
            ]
            complete = (
                len(scenes) == units["scenes_per_robot_run"]
                and len(episodes) == units["episodes_per_robot_run"]
            )
            state = "registered" if complete else "registered_incomplete"
        out.append({**e, "dataset": spec["dataset_id"], "state": state})
    return out


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Reused (identical), created (new) and changed (a violation) records."""
    result: dict[str, Any] = {}
    for kind in ("robot_runs", "units"):
        reused = sorted(
            k for k in before[kind] if after[kind].get(k) == before[kind][k]
        )
        created = sorted(set(after[kind]) - set(before[kind]))
        changed = sorted(
            k
            for k in before[kind]
            if k in after[kind] and after[kind][k] != before[kind][k]
        )
        removed = sorted(set(before[kind]) - set(after[kind]))
        result[kind] = {
            "reused": len(reused),
            "created": len(created),
            "changed": changed,
            "removed": removed,
        }
    return result


# ── Collection (read-only I/O) ───────────────────────────────────────────────


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _api(path: str) -> Any:
    url = f"{_env('API_BASE_URL', 'http://localhost:8000')}{_env('API_PREFIX', '/api/v1')}{path}"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"SceneOps API request {url} failed: {exc}") from exc


def _paged(path: str, key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    limit = 500
    while True:
        sep = "&" if "?" in path else "?"
        page = _api(f"{path}{sep}limit={limit}&offset={len(rows)}")[key]
        rows += page
        if len(page) < limit:
            return rows


def collect_state() -> dict[str, Any]:
    """RobotRuns, Scenes, Episodes and Datasets through the FastAPI control plane."""
    return {
        "robot_runs": _paged("/robot-runs", "robotRuns"),
        "scenes": _paged("/scenes", "scenes"),
        "episodes": _paged("/episodes", "episodes"),
        "datasets": [d["datasetId"] for d in _paged("/datasets", "datasets")],
    }


def collect_facts(registered_run_ids: list[str]) -> dict[str, Any]:
    """Registered manifest facts (channel counts, capture source) of RobotRuns."""
    if not registered_run_ids:
        return {}
    manifests = {}
    for rid in registered_run_ids:
        manifest_id = _api(f"/robot-runs/{rid}")["robotRun"]["manifestArtifactId"]
        manifests[rid] = _api(f"/artifacts/{manifest_id}")["artifact"]["uri"]
    command = [
        "docker", "compose", "--env-file", _env("ENV_FILE", ".env.local"), "--profile", "acquisition",
        "run", "--rm", "-T", "-v", f"{REPO_ROOT}/tools/baselines/streaming:/workspace/streaming:ro",
        "--entrypoint", "python", "recording-publisher", "/workspace/streaming/manifest_facts.py",
    ]  # fmt: skip
    done = subprocess.run(
        command,
        input=json.dumps({"manifests": manifests}),
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if done.returncode != 0:
        raise ContractError(
            f"could not read the RobotRun manifests: {done.stderr.strip()[-400:]}"
        )
    return json.loads(done.stdout)


def run_baseline_verifier(contract: dict[str, Any], mode: str) -> dict[str, Any]:
    """The existing read-only baseline verifier of a mode, with the contract's identity."""
    spec = contract["ingestion_modes"][mode]
    script = {
        "recording_import": REPO_ROOT / "tools/baselines/canonical/canonical_verify.sh",
        "streaming_acquisition": REPO_ROOT
        / "tools/baselines/streaming/streaming_verify.sh",
    }[mode]
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in ("FIXTURE", "BASELINE_ID", "DATASET_ID", "DATASET_VERSION", "ROBOT_ID")
    }
    env.update(
        REFERENCE_SCOPE=contract["corpus"]["scope"], BASELINE_ID=spec["baseline_id"]
    )
    done = subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if done.returncode != 0:
        lines = [line for line in done.stderr.splitlines() if line.strip()]
        return {
            "ok": False,
            "summary": None,
            "error": lines[-1] if lines else f"exit {done.returncode}",
        }
    return {"ok": True, "summary": json.loads(done.stdout), "error": None}


def collect_observation(contract: dict[str, Any]) -> dict[str, Any]:
    obs = collect_state()
    registered = {r["runId"] for r in obs["robot_runs"]}
    obs["facts"] = collect_facts(
        [e["robot_run_id"] for e in expand(contract) if e["robot_run_id"] in registered]
    )
    obs["baselines"] = {mode: run_baseline_verifier(contract, mode) for mode in MODES}
    return obs


# ── Commands ─────────────────────────────────────────────────────────────────


def _sources() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]
]:
    return (
        load_contract(),
        _read_json(CORPUS_DIR / "corpus.json"),
        _read_json(CORPUS_DIR / "corpus.lock.json"),
        _read_json(BASELINE_CONFIG_DIR / "scene_build_config.json"),
        _read_json(BASELINE_CONFIG_DIR / "baseline_shape.json"),
    )


def _emit(document: Any) -> None:
    print(json.dumps(document, indent=2, sort_keys=False))


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _require_valid() -> tuple[dict[str, Any], dict[str, Any]]:
    contract, _corpus, lock = _load_valid()
    return contract, lock


def _load_valid() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    contract, corpus, lock, scene_config, shape = _sources()
    problems = validate_contract(contract, corpus, lock, scene_config, shape)
    for p in problems:
        _log(f"❌ [{p['code']}] {p['message']}")
    if problems:
        raise ContractError(
            f"the reference contract is not valid ({len(problems)} problem(s))"
        )
    return contract, corpus, lock


def cmd_show(_: argparse.Namespace) -> int:
    contract = load_contract()
    _emit(
        {
            "contract_version": contract["contract_version"],
            "robot_runs": expand(contract),
        }
    )
    return 0


def cmd_validate(_: argparse.Namespace) -> int:
    contract, _lock = _require_valid()
    _log(
        f"✅ reference contract v{contract['contract_version']} is consistent with the corpus lock and baseline configuration"
    )
    return 0


def cmd_pair(args: argparse.Namespace) -> int:
    """Both golden RobotRuns of one fixture; reads no platform state."""
    contract, corpus, lock = _load_valid()
    _emit(
        equivalence_pair(
            contract,
            corpus,
            lock,
            fixture_id=args.fixture,
            scope=None if args.fixture else args.scope,
        )
    )
    return 0


def cmd_fingerprint(args: argparse.Namespace) -> int:
    """The platform's records through FastAPI, one compact JSON line; read-only.
    `--golden` restricts it to the contract's own records."""
    state = collect_state()
    document = (
        golden_fingerprint(_require_valid()[0], state)
        if args.golden
        else platform_fingerprint(state)
    )
    print(json.dumps(document, sort_keys=True))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    contract, lock = _require_valid()
    report = evaluate(
        contract,
        lock,
        collect_observation(contract),
        require_pristine=getattr(args, "require_pristine", False),
    )
    _emit(report)
    _report_log(report)
    return 0 if report["ok"] else 1


def _report_log(report: dict[str, Any]) -> None:
    obs, exp = report["totals"]["observed"], report["totals"]["expected"]
    inv, state = report["inventory"], report["state"]
    for v in report["violations"]:
        _log(
            f"❌ [{v['code']}] {v['mode'] or ''} {v['fixture_id'] or ''}: {v['message']}"
        )
    _log(
        f"{'✅' if report['ok'] else '❌'} reference contract: RobotRuns {obs['robot_runs']}/{exp['robot_runs']}, "
        f"Scenes {obs['scenes']}/{exp['scenes']}, Episodes {obs['episodes']}/{exp['episodes']}; "
        f"{len(inv['non_contract_robot_runs'])} non-contract RobotRun(s) {inv['non_contract_robot_runs_by_kind']}, "
        f"{state['derived_test_datasets']} derived test dataset(s), {state['foreign_datasets']} foreign dataset(s); "
        f"environment {'pristine' if state['pristine'] else 'NOT pristine'}"
    )


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Converge the platform on the contract by composing the existing bootstraps."""
    contract, lock = _require_valid()
    before = collect_state()
    clash = conflicts(contract, before)
    if clash:
        for v in clash:
            _log(f"❌ [{v['code']}] {v['message']}")
        raise ContractError(
            "the platform holds contract identities in a conflicting shape; nothing was changed"
        )
    held = plan(contract, before)
    summary = {
        s: sum(1 for p in held if p["state"] == s)
        for s in ("registered", "registered_incomplete", "missing")
    }
    _log(f"reference contract state before bootstrap: {summary}")
    pre = snapshot(contract, before)

    make = os.environ.get("MAKE", "make")
    targets = {
        "recording_import": "canonical-bootstrap",
        "streaming_acquisition": "streaming-bootstrap",
    }
    for mode in MODES:
        spec = contract["ingestion_modes"][mode]
        _log(f"=== {mode}: make {targets[mode]} BASELINE_ID={spec['baseline_id']}")
        # Identity always comes from the contract; FIXTURE= clears any selection from the environment.
        done = subprocess.run(
            [make, "--no-print-directory", targets[mode], f"REFERENCE_SCOPE={contract['corpus']['scope']}",
             f"BASELINE_ID={spec['baseline_id']}", "FIXTURE="],
            cwd=REPO_ROOT, stdout=sys.stderr, check=False,
        )  # fmt: skip
        if done.returncode != 0:
            raise ContractError(
                f"`make {targets[mode]}` failed for {mode}; contract identities were not replaced"
            )

    observation = collect_observation(contract)
    changes = diff_snapshots(pre, snapshot(contract, observation))
    report = evaluate(
        contract,
        lock,
        observation,
        require_pristine=getattr(args, "require_pristine", False),
    )
    for kind in ("robot_runs", "units"):
        for key in changes[kind]["changed"] + changes[kind]["removed"]:
            report["violations"].append(
                _violation(
                    "immutable_changed",
                    f"{kind[:-1]} {key} changed or vanished during the bootstrap",
                )
            )
    report["ok"] = not report["violations"]
    report["bootstrap"] = {"before": summary, "changes": changes}
    _emit(report)
    _report_log(report)
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (
        ("show", cmd_show),
        ("validate", cmd_validate),
        ("verify", cmd_verify),
        ("bootstrap", cmd_bootstrap),
        ("pair", cmd_pair),
        ("fingerprint", cmd_fingerprint),
    ):
        command = sub.add_parser(name)
        command.set_defaults(func=fn)
        if name in ("verify", "bootstrap"):
            command.add_argument(
                "--require-pristine",
                action="store_true",
                help="also fail on any non-contract RobotRun, derived test dataset or foreign dataset",
            )
        if name == "fingerprint":
            command.add_argument(
                "--golden",
                action="store_true",
                help="only the contract's RobotRuns and the Scenes / Episodes of its DatasetVersions",
            )
        if name == "pair":
            command.add_argument("--fixture", help="a fixture of the contract")
            command.add_argument(
                "--scope",
                default="smoke-1",
                help="a corpus scope that selects one fixture (default smoke-1)",
            )
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ContractError as exc:
        _log(f"❌ {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
