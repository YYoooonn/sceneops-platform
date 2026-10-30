"""Unit tests of the golden reference contract: pure evaluation on synthetic state.

The real persistent baseline is never touched; every failure case corrupts a
synthetic observation of the real contract.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "reference_contract.py"
spec = importlib.util.spec_from_file_location("reference_contract", SCRIPT)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

CONTRACT = rc.load_contract()
LOCK = json.loads((rc.CORPUS_DIR / "corpus.lock.json").read_text())
EXPECTATIONS = rc.load_expectations(CONTRACT)

GOLDEN_FIXTURES = [
    "scene-0061", "scene-0103", "scene-0553", "scene-0655", "scene-0757",
    "scene-0796", "scene-0916", "scene-1077", "scene-1094", "scene-1100",
]  # fmt: skip


# ── Synthetic observation of a contract ──────────────────────────────────────


def build_obs(contract=CONTRACT, lock=LOCK):
    """A clean, complete observation: every contract RobotRun, its Scene, Episode and facts."""
    units = contract["units"]
    obs = {
        "robot_runs": [],
        "scenes": [],
        "episodes": [],
        "datasets": [],
        "members": {},
        "facts": {},
        "baselines": {},
    }
    for mode in rc.MODES:
        spec_ = contract["ingestion_modes"][mode]
        obs["datasets"].append(spec_["dataset_id"])
        key = f"{spec_['dataset_id']}/{spec_['dataset_version']}"
        obs["members"][key] = {"scenes": [], "episodes": []}
        summary_fixtures = []
        for fx in contract["fixtures"]:
            fid, rid = fx["fixture_id"], rc.run_id(contract, mode, fx["fixture_id"])
            topics = lock["fixtures"][fid]["recording"]["topic_counts"]
            obs["robot_runs"].append(
                {
                    "runId": rid,
                    "robotId": spec_["robot_id"],
                    "registeredAt": "t0",
                    "manifestChecksum": f"sha256:{rid}",
                }
            )
            ids = _ids(contract, mode, fid)
            for kind, key_name, count in (
                ("scene", "sceneId", units["scenes_per_robot_run"]),
                ("episode", "episodeId", units["episodes_per_robot_run"]),
            ):
                for n in range(count):
                    uid = ids[kind][n] if ids else f"{kind}-{rid}-{n}"
                    obs[f"{kind}s"].append(_unit(rid, key_name, uid, f"{kind}-key-{n}"))
                    obs["members"][key][f"{kind}s"].append(uid)
            locked = spec_["recording"] == "locked"
            obs["facts"][rid] = {
                "robot_id": spec_["robot_id"],
                "capture_source": copy.deepcopy(spec_["capture_source"]),
                "recording": {
                    "checksum": fx["recording_sha256"] if locked else "sha256:captured"
                },
                "message_count": fx["message_count"],
                "channel_counts": dict(topics),
            }
            summary_fixtures.append(
                {
                    "fixture_id": fid, "robot_run_id": rid,
                    "recording_sha256": fx["recording_sha256"], "message_count": fx["message_count"],
                    "scene_count": units["scenes_per_robot_run"], "scenes_ready": units["scenes_per_robot_run"],
                    "episode_count": units["episodes_per_robot_run"], "episodes_ready": units["episodes_per_robot_run"],
                }
            )  # fmt: skip
        summary = {
            "baseline_id": spec_["baseline_id"], "robot_id": spec_["robot_id"],
            "dataset_id": spec_["dataset_id"], "dataset_version": spec_["dataset_version"],
            "fixtures": summary_fixtures,
        }  # fmt: skip
        if "transport" in spec_:
            summary["transport"] = spec_["transport"]
        obs["baselines"][mode] = {"ok": True, "summary": summary, "error": None}
    return obs


def _ids(contract, mode, fixture_id):
    """The derived unit ids when the contract is the real one."""
    if contract is not CONTRACT:
        return None
    return rc.expected_unit_ids(EXPECTATIONS, mode, fixture_id)


def _unit(rid, key, uid, unit_key):
    return {
        key: uid, "robotRunId": rid, "unitKey": unit_key, "sourceKind": "recording", "registeredAt": "t0",
        "revisions": [{"manifestChecksum": f"sha256:{uid}", "registeredAt": "t0"}],
    }  # fmt: skip


def _member(obs, dataset_key, row):
    kind = "scenes" if "sceneId" in row else "episodes"
    obs["members"].setdefault(dataset_key, {"scenes": [], "episodes": []})[kind].append(rc._unit_id(row))


RI_KEY = "sceneops-ref-nuscenes-mini-full-10/baseline"


def codes(report):
    return sorted({v["code"] for v in report["violations"]})


def evaluate(obs, contract=CONTRACT, lock=LOCK):
    return rc.evaluate(contract, lock, obs, expectations=EXPECTATIONS if contract is CONTRACT else None)


# ── The contract itself ──────────────────────────────────────────────────────


def test_the_contract_agrees_with_corpus_lock_shape_and_derived_expectations():
    contract, corpus, lock, shape, expectations, lock_checksum = rc._sources()
    assert rc.validate_contract(contract, corpus, lock, shape, expectations, lock_checksum) == []


def test_the_twenty_golden_identities():
    entries = rc.expand(CONTRACT)
    assert len(entries) == 20
    assert [(e["mode"], e["fixture_id"], e["robot_run_id"]) for e in entries] == [
        (mode, f, f"run-{baseline}-{f}")
        for mode, baseline in (
            ("recording_import", "ref-nuscenes-mini-full-10"),
            ("streaming_acquisition", "stream-ref-nuscenes-mini-full-10"),
        )
        for f in GOLDEN_FIXTURES
    ]
    assert len({e["robot_run_id"] for e in entries}) == 20


def test_contract_validation_rejects_duplicates_and_drift():
    contract, corpus, lock, shape, expectations, lock_checksum = rc._sources()
    scene_config = None  # noqa: F841 -- the Scene policy is no longer a contract field
    dup_fixture = copy.deepcopy(contract)
    dup_fixture["fixtures"].append(dict(dup_fixture["fixtures"][0]))
    assert "duplicate_fixture" in codes(
        {
            "violations": rc.validate_contract(dup_fixture, corpus, lock, shape)
        }
    )

    same_baseline = copy.deepcopy(contract)
    same_baseline["ingestion_modes"]["streaming_acquisition"]["baseline_id"] = (
        same_baseline["ingestion_modes"]["recording_import"]["baseline_id"]
    )
    found = rc.validate_contract(same_baseline, corpus, lock, shape)
    assert "duplicate_identity" in codes({"violations": found})

    relocked = copy.deepcopy(lock)
    relocked["fixtures"]["scene-0061"]["recording"]["sha256"] = "sha256:other"
    assert "lock_mismatch" in codes(
        {
            "violations": rc.validate_contract(contract, corpus, relocked, shape)
        }
    )

    two_episodes = copy.deepcopy(expectations)
    two_episodes["fixtures"]["scene-0061"]["units"]["recording_import"]["episode"].append("episode-x")
    assert "config_mismatch" in codes(
        {"violations": rc.validate_contract(contract, corpus, lock, shape, two_episodes, lock_checksum)}
    )
    assert "expectations_stale" in codes(
        {"violations": rc.validate_contract(contract, corpus, lock, shape, expectations, "sha256:other-lock")}
    )
    assert "expectations_stale" in codes(
        {"violations": rc.validate_contract(contract, corpus, relocked, shape, expectations, lock_checksum)}
    )

    wrong_totals = copy.deepcopy(contract)
    wrong_totals["expected_totals"]["robot_runs"] = 21
    assert "totals_mismatch" in codes(
        {
            "violations": rc.validate_contract(wrong_totals, corpus, lock, shape)
        }
    )


# ── Observed state ───────────────────────────────────────────────────────────


def test_clean_twenty_run_state_passes():
    report = evaluate(build_obs())
    assert report["violations"] == []
    assert report["ok"]
    assert report["totals"]["observed"] == {
        "fixtures": 10,
        "robot_runs": 20,
        "scenes": 20,
        "episodes": 20,
    }
    assert report["inventory"]["non_contract_robot_runs"] == []


@pytest.mark.parametrize("mode", rc.MODES)
def test_missing_robot_run_fails(mode):
    obs = build_obs()
    victim = rc.run_id(CONTRACT, mode, "scene-0553")
    obs["robot_runs"] = [r for r in obs["robot_runs"] if r["runId"] != victim]
    report = evaluate(obs)
    assert not report["ok"]
    assert any(
        v["code"] == "missing_robot_run"
        and v["mode"] == mode
        and v["fixture_id"] == "scene-0553"
        for v in report["violations"]
    )
    assert report["totals"]["observed"]["robot_runs"] == 19


def test_a_temporary_run_of_the_same_fixture_does_not_stand_in_for_a_contract_run():
    obs = build_obs()
    victim = rc.run_id(CONTRACT, "recording_import", "scene-0061")
    obs["robot_runs"] = [r for r in obs["robot_runs"] if r["runId"] != victim]
    obs["robot_runs"].append(
        {"runId": "run-e2e-batch-123-1-scene-0061", "robotId": "robot-e2e-batch-123-1"}
    )
    report = evaluate(obs)
    assert not report["ok"]
    assert "missing_robot_run" in codes(report)
    assert [
        r["robot_run_id"] for r in report["inventory"]["non_contract_robot_runs"]
    ] == ["run-e2e-batch-123-1-scene-0061"]


def test_wrong_fixture_to_run_identity_fails():
    obs = build_obs()
    fixtures = obs["baselines"]["recording_import"]["summary"]["fixtures"]
    fixtures[0]["robot_run_id"] = fixtures[1]["robot_run_id"]
    report = evaluate(obs)
    assert "wrong_run_identity" in codes(report)


def test_duplicate_fixture_mapping_fails():
    obs = build_obs()
    fixtures = obs["baselines"]["streaming_acquisition"]["summary"]["fixtures"]
    fixtures.append(dict(fixtures[0]))
    assert "duplicate_fixture_mapping" in codes(evaluate(obs))


def test_duplicate_robot_run_rows_fail():
    obs = build_obs()
    obs["robot_runs"].append(dict(obs["robot_runs"][0]))
    assert "duplicate_robot_run" in codes(evaluate(obs))


@pytest.mark.parametrize("extra", [False, True])
def test_unexpected_segmentation_shape_fails(extra):
    obs = build_obs()
    scene = next(
        s
        for s in obs["scenes"]
        if s["robotRunId"] == rc.run_id(CONTRACT, "recording_import", "scene-0103")
    )
    if extra:  # a second Scene of the same RobotRun: segmentation yielded 2 where 1 is the contract
        extra_scene = {**scene, "sceneId": "scene-extra", "unitKey": "fixed_duration-x/0"}
        obs["scenes"].append(extra_scene)
        _member(obs, RI_KEY, extra_scene)
    else:  # another segmentation: another unit id than the derived one
        old = scene["sceneId"]
        scene["sceneId"] = "scene-0000000000000000000000000000000a"
        members = obs["members"][RI_KEY]["scenes"]
        members[members.index(old)] = scene["sceneId"]
    report = evaluate(obs)
    assert "scene_shape" in codes(report)


def test_a_second_revision_of_a_contract_unit_fails():
    obs = build_obs()
    obs["episodes"][0]["revisions"].append({"manifestChecksum": "sha256:second", "registeredAt": "t1"})
    assert "revision_count" in codes(evaluate(obs))


def test_a_failed_source_grounded_verification_fails_the_contract():
    obs = build_obs()
    obs["source_grounded"] = {"ok": False, "error": "manifest content differs", "summary": None}
    assert "source_grounded" in codes(evaluate(obs))
    obs["source_grounded"] = {"ok": True, "error": None, "summary": {}}
    assert evaluate(obs)["ok"]


def test_missing_episode_fails():
    obs = build_obs()
    obs["episodes"].pop()
    assert "episode_shape" in codes(evaluate(obs))


def test_a_unit_outside_the_contract_version_does_not_count():
    """Membership is what the DatasetVersion pins, not which RobotRun a unit names."""
    obs = build_obs()
    obs["members"][RI_KEY]["episodes"].pop()
    assert "episode_shape" in codes(evaluate(obs))


def test_contract_identity_registered_under_another_robot_is_a_conflict():
    obs = build_obs()
    obs["robot_runs"][0]["robotId"] = "robot-someone-else"
    report = evaluate(obs)
    assert "conflicting_robot_run" in codes(report)
    assert rc.conflicts(CONTRACT, obs)


def test_foreign_run_on_a_contract_robot_is_a_conflict_not_a_replacement():
    obs = build_obs()
    obs["robot_runs"].append(
        {
            "runId": "run-ref-nuscenes-mini-full-10-scene-0061-v2",
            "robotId": "robot-ref-nuscenes-mini-full-10",
        }
    )
    report = evaluate(obs)
    assert "unexpected_robot_run" in codes(report)
    assert (
        report["totals"]["observed"]["robot_runs"] == 20
    )  # never counted as a 21st contract run


def test_foreign_unit_in_a_contract_dataset_is_a_conflict():
    obs = build_obs()
    stray = dict(
        obs["scenes"][0], sceneId="scene-stray", robotRunId="run-e2e-x-scene-0061"
    )
    obs["scenes"].append(stray)
    _member(obs, RI_KEY, stray)
    assert "foreign_unit" in codes(evaluate(obs))


def test_unrelated_temporary_runs_are_reported_and_leave_the_contract_unchanged():
    obs = build_obs()
    obs["robot_runs"] += [
        {
            "runId": "run-e2e-scene-ml-1-2-scene-0061",
            "robotId": "robot-e2e-scene-ml-1-2",
            "registeredAt": "t1",
        },
        {
            "runId": "run-ref-smoke-1-scene-0061",
            "robotId": "robot-ref-smoke-1",
            "registeredAt": "t1",
        },
    ]
    obs["datasets"].append("sceneops-ref-smoke-1")
    smoke = _unit("run-ref-smoke-1-scene-0061", "sceneId", "s-smoke", "recording")
    obs["scenes"].append(smoke)
    _member(obs, "sceneops-ref-smoke-1/baseline", smoke)
    report = evaluate(obs)
    assert report["ok"], report["violations"]
    assert report["totals"]["observed"]["robot_runs"] == 20
    inventory = report["inventory"]
    assert inventory["contract_robot_runs"] == 20
    assert inventory["non_contract_robot_runs_by_kind"] == {
        "reference_like_baseline": 1,
        "temporary_e2e": 1,
    }
    assert inventory["foreign_datasets"] == ["sceneops-ref-smoke-1"]
    assert inventory["derived_test_datasets"] == []
    assert inventory["scenes_outside_contract_datasets"] == 1
    assert inventory["orphan_scenes"] == []


def test_state_summarizes_the_reference_environment():
    report = evaluate(build_obs())
    assert report["state"] == {
        "contract_robot_runs": 20,
        "non_contract_robot_runs": 0,
        "contract_scenes": 20,
        "contract_episodes": 20,
        "derived_test_datasets": 0,
        "foreign_datasets": 0,
        "pristine": True,
    }


def _polluted_obs():
    obs = build_obs()
    obs["robot_runs"].append(
        {"runId": "run-test-batch-1-scene-0061", "robotId": "robot-test-batch-1", "registeredAt": "t1"}
    )  # fmt: skip
    obs["datasets"].append("sceneops-test-scene-ml-1")
    return obs


def _with_derived_test_state(obs):
    """The fixed test-owned DatasetVersions the L3 journeys and infrastructure tests keep,
    holding Scenes / Episodes of a contract RobotRun."""
    run = rc.run_id(CONTRACT, "recording_import", "scene-0061")
    for dataset in ("sceneops-test-scene-ml", "sceneops-test-infra-pipelines"):
        obs["datasets"].append(dataset)
        scene = _unit(run, "sceneId", f"s-{dataset}", "recording")
        episode = _unit(run, "episodeId", f"e-{dataset}", "recording")
        obs["scenes"].append(scene)
        obs["episodes"].append(episode)
        _member(obs, f"{dataset}/baseline", scene)
        _member(obs, f"{dataset}/baseline", episode)
    return obs


def test_derived_test_state_is_reported_apart_and_leaves_the_contract_valid():
    report = evaluate(_with_derived_test_state(build_obs()))
    assert report["ok"], report["violations"]
    assert report["state"]["derived_test_datasets"] == 2
    assert report["state"]["foreign_datasets"] == 0
    assert report["state"]["pristine"] is False
    inventory = report["inventory"]
    assert inventory["derived_test_datasets"] == [
        "sceneops-test-infra-pipelines",
        "sceneops-test-scene-ml",
    ]
    assert (inventory["derived_test_scenes"], inventory["derived_test_episodes"]) == (
        2,
        2,
    )
    assert inventory["foreign_datasets"] == []
    # The contract itself is unaffected: still 20 / 20 / 20.
    assert report["state"]["contract_robot_runs"] == 20
    assert report["totals"]["observed"] == report["totals"]["expected"]


def test_pristine_additionally_requires_zero_derived_test_state():
    obs = _with_derived_test_state(build_obs())
    strict = rc.evaluate(CONTRACT, LOCK, obs, require_pristine=True)
    assert not strict["ok"]
    assert codes(strict) == ["derived_test_dataset"]
    assert len(strict["violations"]) == 2
    assert strict["state"]["contract_robot_runs"] == 20
    assert strict["totals"]["observed"]["scenes"] == 20


def test_non_contract_state_is_reported_but_only_fails_a_pristine_environment():
    obs = _polluted_obs()
    report = evaluate(obs)
    assert report["ok"], report["violations"]
    assert report["state"]["pristine"] is False
    assert report["state"]["non_contract_robot_runs"] == 1
    assert report["state"]["derived_test_datasets"] == 1
    assert report["inventory"]["non_contract_robot_runs_by_kind"] == {
        "temporary_e2e": 1
    }

    strict = rc.evaluate(CONTRACT, LOCK, obs, require_pristine=True)
    assert not strict["ok"]
    assert codes(strict) == ["derived_test_dataset", "non_contract_robot_run"]
    # The contract itself is unaffected: still 20 / 20 / 20.
    assert strict["state"]["contract_robot_runs"] == 20
    assert strict["totals"]["observed"]["robot_runs"] == 20


def test_a_dataset_that_is_neither_contract_nor_derived_test_state_is_foreign():
    obs = build_obs()
    obs["datasets"].append("some-other-dataset")
    assert evaluate(obs)["state"]["foreign_datasets"] == 1
    strict = rc.evaluate(CONTRACT, LOCK, obs, require_pristine=True)
    assert codes(strict) == ["foreign_dataset"]


def test_a_pristine_environment_passes_the_strict_check():
    report = rc.evaluate(CONTRACT, LOCK, build_obs(), require_pristine=True)
    assert report["ok"]
    assert report["state"]["pristine"] is True


def test_the_former_require_clean_flag_no_longer_exists():
    with pytest.raises(SystemExit):
        rc.main(["verify", "--require-clean"])


@pytest.mark.parametrize("target", ["reference-contract-verify", "reference-contract-bootstrap"])
def test_both_contract_commands_give_the_source_grounded_verifier_its_object_store(target):
    # Both evaluate through run_source_grounded_verifier, which reads the store
    # with MINIO_ROOT_USER / MINIO_ROOT_PASSWORD; without them it fails on a
    # KeyError after the bootstrap has already converged. Read as text: a recipe
    # that names $(MAKE) runs even under `make -n`.
    makefile = (rc.REPO_ROOT / "makefiles/reference.mk").read_text()
    store_env = re.search(r"^REFERENCE_VERIFY_ENV :=(.*?)\n\n", makefile, re.M | re.S).group(1)
    for variable in ("MINIO_ENDPOINT_URL=", "MINIO_ROOT_USER=", "MINIO_ROOT_PASSWORD="):
        assert variable in store_env
    recipe = re.search(rf"^{target}:\n((?:\t.*\n)+)", makefile, re.M).group(1)
    assert "$(REFERENCE_VERIFY_ENV)" in recipe, f"`make {target}` does not pass the store"


def test_orphan_units_are_inventoried():
    obs = build_obs()
    obs["scenes"].append(_unit("run-vanished", "sceneId", "s-orphan", "recording"))
    assert evaluate(obs)["inventory"]["orphan_scenes"] == ["s-orphan"]


# ── Provenance and recording facts ───────────────────────────────────────────


def test_streamed_run_pinning_the_locked_recording_fails():
    obs = build_obs()
    rid = rc.run_id(CONTRACT, "streaming_acquisition", "scene-0061")
    obs["facts"][rid]["recording"]["checksum"] = _fixture_sha("scene-0061")
    assert "provenance" in codes(evaluate(obs))


def test_imported_run_not_pinning_the_locked_recording_fails():
    obs = build_obs()
    rid = rc.run_id(CONTRACT, "recording_import", "scene-0061")
    obs["facts"][rid]["recording"]["checksum"] = "sha256:something-else"
    assert "provenance" in codes(evaluate(obs))


def test_wrong_capture_source_fails():
    obs = build_obs()
    rid = rc.run_id(CONTRACT, "recording_import", "scene-0103")
    obs["facts"][rid]["capture_source"] = {
        "kind": "kafka",
        "topics": ["sceneops.robot.telemetry.v1"],
    }
    assert "provenance" in codes(evaluate(obs))


def test_wrong_transport_fails():
    obs = build_obs()
    obs["baselines"]["streaming_acquisition"]["summary"]["transport"] = "something_else"
    assert "provenance" in codes(evaluate(obs))


def test_message_or_channel_facts_differing_from_the_lock_fail():
    obs = build_obs()
    rid = rc.run_id(CONTRACT, "streaming_acquisition", "scene-0655")
    obs["facts"][rid]["message_count"] -= 1
    assert "recording_facts" in codes(evaluate(obs))
    obs = build_obs()
    channels = obs["facts"][rc.run_id(CONTRACT, "recording_import", "scene-0655")][
        "channel_counts"
    ]
    topic = sorted(channels)[0]
    channels[topic] += 1
    channels[sorted(channels)[1]] -= 1  # same total, different distribution
    assert "recording_facts" in codes(evaluate(obs))


def test_a_failed_baseline_verifier_fails_the_contract():
    obs = build_obs()
    obs["baselines"]["streaming_acquisition"] = {
        "ok": False,
        "summary": None,
        "error": "Scene X is not ready",
    }
    report = evaluate(obs)
    assert "baseline_verify_failed" in codes(report)
    assert report["modes"]["streaming_acquisition"]["baseline_verify"] == "failed"


def test_not_ready_unit_fails():
    obs = build_obs()
    obs["baselines"]["recording_import"]["summary"]["fixtures"][0]["scenes_ready"] = 0
    assert "readiness" in codes(evaluate(obs))


# ── Generic capability stays generic ─────────────────────────────────────────


def test_the_evaluator_knows_neither_twenty_nor_whole_recording():
    """Another corpus size, another Scene policy and several Scenes per RobotRun are
    expressible: the restriction lives only in the contract data."""
    contract = copy.deepcopy(CONTRACT)
    contract["fixtures"] = contract["fixtures"][:2]
    contract["units"] = {"scenes_per_robot_run": 3, "episodes_per_robot_run": 2, "revisions_per_unit": 1}
    contract["expected_totals"] = {
        "fixtures": 2,
        "robot_runs": 4,
        "scenes": 12,
        "episodes": 8,
    }
    obs = build_obs(contract)
    report = evaluate(obs, contract)
    assert report["ok"], report["violations"]
    assert report["totals"]["observed"] == contract["expected_totals"]

    corpus, shape = (
        {"corpus_id": "nuscenes-mini-v1", "fixtures": [{"fixture_id": f["fixture_id"]} for f in contract["fixtures"]],
         "scopes": {contract["corpus"]["scope"]: ["*"]}},
        {"scenes_per_robot_run": 3, "episodes_per_robot_run": 2},
    )  # fmt: skip
    assert rc.validate_contract(contract, corpus, LOCK, shape) == []


# ── Bootstrap convergence ────────────────────────────────────────────────────


def test_snapshot_diff_of_an_unchanged_platform_creates_and_changes_nothing():
    obs = build_obs()
    before = rc.snapshot(CONTRACT, obs)
    diff = rc.diff_snapshots(before, rc.snapshot(CONTRACT, copy.deepcopy(obs)))
    assert diff["robot_runs"] == {
        "reused": 20,
        "created": 0,
        "changed": [],
        "removed": [],
    }
    assert diff["units"] == {"reused": 40, "created": 0, "changed": [], "removed": []}


def test_snapshot_diff_flags_a_mutated_immutable_record():
    obs = build_obs()
    before = rc.snapshot(CONTRACT, obs)
    obs["robot_runs"][3]["manifestChecksum"] = "sha256:replaced"
    diff = rc.diff_snapshots(before, rc.snapshot(CONTRACT, obs))
    assert diff["robot_runs"]["changed"] == [obs["robot_runs"][3]["runId"]]


def test_plan_names_missing_and_incomplete_runs():
    obs = build_obs()
    obs["robot_runs"] = [
        r
        for r in obs["robot_runs"]
        if not r["runId"].endswith("scene-1100") or "stream" not in r["runId"]
    ]
    obs["episodes"] = [
        e
        for e in obs["episodes"]
        if e["robotRunId"] != rc.run_id(CONTRACT, "recording_import", "scene-0061")
    ]
    states = {(p["mode"], p["fixture_id"]): p["state"] for p in rc.plan(CONTRACT, obs)}
    assert states[("streaming_acquisition", "scene-1100")] == "missing"
    assert states[("recording_import", "scene-0061")] == "registered_incomplete"
    assert states[("recording_import", "scene-0103")] == "registered"


class _Done:
    returncode = 0


def _patch_bootstrap(monkeypatch, states):
    """collect_state returns the first of `states`, then the next; make calls are recorded."""
    calls = []
    queue = list(states)
    monkeypatch.setattr(rc, "_require_valid", lambda: (CONTRACT, LOCK))
    monkeypatch.setattr(rc, "collect_state", lambda: queue.pop(0))
    monkeypatch.setattr(rc, "collect_observation", lambda contract: queue.pop(0))
    monkeypatch.setattr(
        rc.subprocess, "run", lambda command, **kw: calls.append(command) or _Done()
    )
    return calls


def test_repeat_bootstrap_on_a_complete_platform_reuses_everything(monkeypatch, capsys):
    obs = build_obs()
    calls = _patch_bootstrap(monkeypatch, [obs, copy.deepcopy(obs)])
    monkeypatch.setenv(
        "BASELINE_ID", "e2e-batch-999-1"
    )  # a stray environment never names a contract identity
    assert rc.cmd_bootstrap(None) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"]
    assert report["bootstrap"]["before"] == {
        "registered": 20,
        "registered_incomplete": 0,
        "missing": 0,
    }
    assert report["bootstrap"]["changes"]["robot_runs"] == {
        "reused": 20,
        "created": 0,
        "changed": [],
        "removed": [],
    }
    # Only the existing bootstraps run, each with the contract's identity.
    assert [c[2:] for c in calls] == [
        [
            "canonical-bootstrap",
            "REFERENCE_SCOPE=nuscenes-mini-full-10",
            "BASELINE_ID=ref-nuscenes-mini-full-10",
            "FIXTURE=",
        ],
        [
            "streaming-bootstrap",
            "REFERENCE_SCOPE=nuscenes-mini-full-10",
            "BASELINE_ID=stream-ref-nuscenes-mini-full-10",
            "FIXTURE=",
        ],
    ]


def test_bootstrap_stops_before_changing_anything_on_a_conflict(monkeypatch):
    obs = build_obs()
    obs["robot_runs"].append(
        {
            "runId": "run-stream-ref-nuscenes-mini-full-10-scene-9999",
            "robotId": "robot-stream-ref-nuscenes-mini-full-10",
        }
    )
    calls = _patch_bootstrap(monkeypatch, [obs])
    with pytest.raises(rc.ContractError):
        rc.cmd_bootstrap(None)
    assert calls == []


def test_bootstrap_reports_created_records_and_fails_if_an_existing_one_changed(
    monkeypatch, capsys
):
    before = build_obs()
    before["robot_runs"] = [
        r for r in before["robot_runs"] if not r["runId"].endswith("scene-1100")
    ]
    before["scenes"] = [
        s for s in before["scenes"] if not s["robotRunId"].endswith("scene-1100")
    ]
    before["episodes"] = [
        s for s in before["episodes"] if not s["robotRunId"].endswith("scene-1100")
    ]
    after = build_obs()
    _patch_bootstrap(monkeypatch, [before, after])
    assert rc.cmd_bootstrap(None) == 0
    changes = json.loads(capsys.readouterr().out)["bootstrap"]["changes"]
    assert (
        changes["robot_runs"]["created"] == 2 and changes["robot_runs"]["reused"] == 18
    )

    after = build_obs()
    after["robot_runs"][0]["manifestChecksum"] = "sha256:replaced"
    _patch_bootstrap(monkeypatch, [build_obs(), after])
    assert rc.cmd_bootstrap(None) == 1
    assert "immutable_changed" in codes(json.loads(capsys.readouterr().out))


def _fixture_sha(fixture_id):
    return next(
        f["recording_sha256"]
        for f in CONTRACT["fixtures"]
        if f["fixture_id"] == fixture_id
    )


# ── Equivalence pair and platform fingerprint ────────────────────────────────

CORPUS = json.loads((rc.CORPUS_DIR / "corpus.json").read_text())


def test_smoke_scope_resolves_to_the_two_golden_runs_of_scene_0061():
    pair = rc.equivalence_pair(CONTRACT, CORPUS, LOCK, scope="smoke-1")
    assert pair["fixture_id"] == "scene-0061"
    assert pair["recording_import"]["robot_run_id"] == rc.run_id(
        CONTRACT, "recording_import", "scene-0061"
    )
    assert pair["streaming_acquisition"]["robot_run_id"] == rc.run_id(
        CONTRACT, "streaming_acquisition", "scene-0061"
    )
    for mode in rc.MODES:
        spec_ = CONTRACT["ingestion_modes"][mode]
        assert pair[mode]["dataset_id"] == spec_["dataset_id"]
        assert pair[mode]["robot_id"] == spec_["robot_id"]


def test_the_pair_carries_the_locks_recording_facts():
    pair = rc.equivalence_pair(CONTRACT, CORPUS, LOCK, fixture_id="scene-0103")
    locked = LOCK["fixtures"]["scene-0103"]["recording"]
    assert pair["recording"] == {
        "sha256": _fixture_sha("scene-0103"),
        "size_bytes": locked["size_bytes"],
        "message_count": locked["message_count"],
        "topic_counts": locked["topic_counts"],
    }


def test_a_pair_is_one_fixture_of_the_contract():
    with pytest.raises(rc.ContractError, match="selects 10 fixtures"):
        rc.equivalence_pair(CONTRACT, CORPUS, LOCK, scope="nuscenes-mini-full-10")
    with pytest.raises(rc.ContractError, match="not a fixture"):
        rc.equivalence_pair(CONTRACT, CORPUS, LOCK, fixture_id="scene-9999")
    with pytest.raises(rc.ContractError, match="no scope"):
        rc.equivalence_pair(CONTRACT, CORPUS, LOCK, scope="nope")


def test_fingerprint_of_an_unchanged_platform_is_equal():
    state = build_obs()
    assert rc.platform_fingerprint(state) == rc.platform_fingerprint(
        copy.deepcopy(state)
    )
    assert rc.platform_fingerprint(state)["counts"] == {
        "robot_runs": 20,
        "datasets": 2,
        "scenes": 20,
        "episodes": 20,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["robot_runs"].append(
            {"runId": "run-test-x", "robotId": "robot-test-x"}
        ),
        lambda s: s["robot_runs"].pop(),
        lambda s: s["datasets"].append("sceneops-test-x"),
        lambda s: s["scenes"][0]["revisions"][0].update(manifestChecksum="sha256:rewritten"),
        lambda s: s["episodes"][0]["revisions"].append({"manifestChecksum": "sha256:other", "registeredAt": "t1"}),
        lambda s: s["scenes"].pop(),
    ],
    ids=[
        "run added",
        "run removed",
        "dataset added",
        "scene revision rewritten",
        "episode revision added",
        "scene removed",
    ],
)
def test_fingerprint_detects_any_added_removed_or_rewritten_record(mutate):
    before = build_obs()
    after = copy.deepcopy(before)
    mutate(after)
    assert rc.platform_fingerprint(before) != rc.platform_fingerprint(after)


# ── Golden fingerprint ───────────────────────────────────────────────────────


def _with_derived_state(state):
    """The same platform after a derived workflow: a Dataset of its own holding a Scene and
    an Episode built from a contract RobotRun."""
    state = copy.deepcopy(state)
    rid = state["robot_runs"][0]["runId"]
    state["datasets"].append("sceneops-test-scene-ml")
    scene = _unit(rid, "sceneId", "scene-derived", "fixed_duration-x/0")
    episode = _unit(rid, "episodeId", "episode-derived", "fixed_duration-x/0")
    state["scenes"].append(scene)
    state["episodes"].append(episode)
    # The derived journey pins a contract unit as well as its own.
    _member(state, "sceneops-test-scene-ml/baseline", scene)
    _member(state, "sceneops-test-scene-ml/baseline", state["episodes"][0])
    return state


def test_golden_fingerprint_covers_exactly_the_contracts_records():
    golden = rc.golden_fingerprint(CONTRACT, build_obs())
    assert golden["counts"] == {"robot_runs": 20, "scenes": 20, "episodes": 20}
    assert len(golden["robot_runs"]) == 20


def test_golden_fingerprint_ignores_derived_state_consuming_the_contract():
    before = build_obs()
    after = _with_derived_state(before)
    assert rc.platform_fingerprint(before) != rc.platform_fingerprint(after)
    assert rc.golden_fingerprint(CONTRACT, before) == rc.golden_fingerprint(
        CONTRACT, after
    )


def test_golden_fingerprint_excludes_non_contract_robot_runs():
    state = build_obs()
    state["robot_runs"].append({"runId": "run-test-x", "robotId": "robot-test-x"})
    assert "run-test-x" not in rc.golden_fingerprint(CONTRACT, state)["robot_runs"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["robot_runs"].pop(),
        lambda s: s["robot_runs"][0].update(manifestChecksum="sha256:other"),
        lambda s: s["scenes"][0]["revisions"][0].update(manifestChecksum="sha256:rewritten"),
        lambda s: s["episodes"][0]["revisions"].append({"manifestChecksum": "sha256:other", "registeredAt": "t1"}),
        lambda s: s["scenes"].pop(),
    ],
    ids=[
        "run removed",
        "run re-pinned",
        "scene revision rewritten",
        "episode revision added",
        "scene removed",
    ],
)
def test_golden_fingerprint_detects_a_changed_contract_record(mutate):
    before = build_obs()
    after = copy.deepcopy(before)
    mutate(after)
    assert rc.golden_fingerprint(CONTRACT, before) != rc.golden_fingerprint(
        CONTRACT, after
    )
