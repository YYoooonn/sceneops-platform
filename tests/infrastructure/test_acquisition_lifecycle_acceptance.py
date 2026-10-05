"""Full-lifecycle reliability acceptance (ADR-008 §8 step 12.6).

One workflow over the whole acquisition lifecycle::

    capture -> finalized capture + receipt -> publication -> registration -> RobotRun

Nine acquisitions share one capture volume, one object store, one PostgreSQL and
one broker, and each is hit by a different failure on the way. Everything that
recovers does so through the production one-shot commands, run as subprocesses
with their configuration from the environment, the way the polling services run
them: ``publish-pending``, ``reconcile --once --apply`` and the read-only
``acquisition_status``. The tests inject faults and never repair anything
themselves.

====  ============  ===========================================================
 A    outage        a clean capture; the broker is down when its registration
                    is first submitted
 B    publisher     the publisher is killed between the recording and the
                    manifest marker (W5)
 C    publish fail  the manifest write fails once
 D    worker kill   the registration worker is SIGKILLed mid-registration (W8)
 F    flaky         registration fails transiently until the retry budget is
                    spent
 G    conflict      registered, then a different manifest appears in the store
 H    damaged       registered, then its recording disappears
 I    unfinished    a capture killed before finalize (only ``.partial``)
 J    legacy        a finalized bag with no receipt
====  ============  ===========================================================

The invariant it ends on: one logical acquisition is one RobotRun matching its
published manifest, or none when recovery is over and a person must decide.

What it proves and what it does not: Capture's finalize and receipt code build
the captures (the Kafka consumption that feeds it is ``make ros2-test`` and the
streaming equivalence journey); the object store is real MinIO, the database real
PostgreSQL, the broker a throwaway Redis, the workers real Celery subprocesses,
and times are real (the stall threshold is 5 s here). It is infrastructure
acceptance, not a product journey. Run: ``make test-recovery``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import text

from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.robots.manifest import load_canonical_robot_run_manifest
from sceneops_db.session import get_async_sessionmaker

from recovery_support import (
    RECOVERY_FIXTURES,
    REPO_ROOT,
    RUN_PREFIX,
    WorkerProcess,
    async_wait_until,
    count_rows,
    job_row_snapshot,
    jobs_of,
    make_finalized_capture,
    make_legacy_capture,
    make_unfinished_capture,
    robot_run_checksum,
    wait_until,
)

pytestmark = pytest.mark.usefixtures(*RECOVERY_FIXTURES)

STALL_SECONDS = 5
# A threshold no Job reaches during the test: a pass with it acts on nothing
# that is merely in flight.
WITHIN_SECONDS = 600
KILLED = 137  # the exit status of ``os._exit(137)`` in recovery_publisher

REGISTERED = ("a", "b", "c", "d", "g", "h")  # acquisitions that end as RobotRuns


# ── commands ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Command:
    label: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def json(self) -> dict:
        return json.loads(self.stdout)

    def records(self, event: str = "acquisition_recovery") -> list[dict]:
        marker = f"{event} {{"
        return [
            json.loads(line[line.index(marker) + len(event) + 1 :])
            for line in self.stderr.splitlines()
            if marker in line
        ]


def _spawn(args, *, env_vars, cwd) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env_vars,
        cwd=str(cwd),
    )


def _finish(label: str, proc: subprocess.Popen) -> Command:
    stdout, stderr = proc.communicate(timeout=180)
    return Command(label, proc.returncode, stdout, stderr)


class Commands:
    """The production commands, configured the way their services are."""

    def __init__(self, env, capture_root: Path) -> None:
        self.env = env
        self.capture_root = capture_root
        self.log: list[Command] = []

    def _record(self, command: Command) -> Command:
        self.log.append(command)
        return command

    def _publish_args(self, module: str) -> list[str]:
        return [
            "-m",
            module,
            "publish-pending",
            "--capture-root",
            str(self.capture_root),
        ]

    def publish_pending(self, *, faulty: bool = False) -> Command:
        """``faulty`` runs the same command through ``recovery_publisher``,
        which only adds the fault points of the control file."""
        module = "recovery_publisher" if faulty else "sceneops_integrations.recording"
        proc = _spawn(
            self._publish_args(module),
            env_vars=self.env.publisher_environment(),
            cwd=self.env.tmp,
        )
        return self._record(_finish("publish-pending", proc))

    def publish_pending_concurrently(self, count: int) -> list[Command]:
        procs = [
            _spawn(
                self._publish_args("sceneops_integrations.recording"),
                env_vars=self.env.publisher_environment(),
                cwd=self.env.tmp,
            )
            for _ in range(count)
        ]
        return [self._record(_finish("publish-pending", proc)) for proc in procs]

    def _reconcile_args(self, stall: float) -> list[str]:
        return [
            "-m",
            "app.domains.robots.reconciliation",
            "--once",
            "--apply",
            "--stall-threshold-seconds",
            str(stall),
        ]

    def reconcile(self, *, stall: float = STALL_SECONDS) -> Command:
        proc = _spawn(
            self._reconcile_args(stall),
            env_vars=self.env.reconciler_environment(stall_threshold_seconds=stall),
            cwd=REPO_ROOT / "apps" / "api",
        )
        return self._record(_finish("reconcile", proc))

    def reconcile_concurrently(self, count: int, *, stall: float) -> list[Command]:
        procs = [
            _spawn(
                self._reconcile_args(stall),
                env_vars=self.env.reconciler_environment(stall_threshold_seconds=stall),
                cwd=REPO_ROOT / "apps" / "api",
            )
            for _ in range(count)
        ]
        return [self._record(_finish("reconcile", proc)) for proc in procs]

    def scan_capture(self) -> Path:
        """``scan-capture`` -> the report file ``--capture-report`` reads."""
        proc = _spawn(
            [
                "-m",
                "sceneops_integrations.recording",
                "scan-capture",
                "--capture-root",
                str(self.capture_root),
            ],
            env_vars=self.env.publisher_environment(),
            cwd=self.env.tmp,
        )
        command = _finish("scan-capture", proc)
        assert command.returncode == 0, command.stderr
        path = self.env.tmp / f"capture-scan-{uuid.uuid4().hex[:6]}.json"
        path.write_text(command.stdout)
        return path

    def status(
        self, *, stall: float = STALL_SECONDS, summary_only: bool = False
    ) -> Command:
        args = [
            "-m",
            "app.domains.robots.acquisition_status",
            "--once",
            "--capture-report",
            str(self.scan_capture()),
            "--stall-threshold-seconds",
            str(stall),
        ]
        if summary_only:
            args.append("--summary-only")
        proc = _spawn(
            args,
            env_vars=self.env.reconciler_environment(stall_threshold_seconds=stall),
            cwd=REPO_ROOT / "apps" / "api",
        )
        command = _finish("acquisition-status", proc)
        assert command.returncode == 0, command.stderr
        return self._record(command)


# ── helpers ───────────────────────────────────────────────────────────────────


def runs_by_id(report: dict) -> dict[str, dict]:
    return {run["run_id"]: run for run in report["runs"]}


async def durable_snapshot(env, run_ids: list[str]):
    """Every durable fact the commands could touch: the objects (with their
    modification times) and each run's rows and Job rows."""
    objects = sorted(
        (o.uri, o.size_bytes, o.last_modified)
        for o in await env.store().list_objects(env.robot_run_root)
    )
    rows = {
        run_id: (await count_rows(run_id), await job_row_snapshot(run_id))
        for run_id in run_ids
    }
    return objects, rows


async def robot_run_count(run_ids: list[str]) -> tuple[int, int]:
    async with get_async_sessionmaker()() as session:
        runs, artifacts = (
            await session.execute(
                text(
                    "SELECT"
                    " (SELECT count(*) FROM robot_runs WHERE run_id = ANY(:ids)),"
                    " (SELECT count(*) FROM artifacts WHERE owner_id = ANY(:ids))"
                ),
                {"ids": run_ids},
            )
        ).one()
        return runs, artifacts


async def registered(run_id: str) -> bool:
    return (await count_rows(run_id))["robot_runs"] == 1


def failed_jobs(jobs) -> list:
    return [job for job in jobs if job.status == "failed"]


# ── the acceptance ────────────────────────────────────────────────────────────


async def test_full_acquisition_lifecycle_recovers_through_the_production_commands(
    env, tmp_path
):
    tag = uuid.uuid4().hex[:6]
    labels = {
        "a": "a-outage",
        "b": "b-pubkill",
        "c": "c-pubfail",
        "d": "d-workerkill",
        "f": "f-flaky",
        "g": "g-conflict",
        "h": "h-damaged",
        "i": "i-unfinished",
        "j": "j-legacy",
    }
    ids = {key: f"{RUN_PREFIX}-acc-{label}-{tag}" for key, label in labels.items()}
    robots = {key: f"{RUN_PREFIX}-robot-{label}-{tag}" for key, label in labels.items()}
    env.run_ids.extend(ids.values())
    all_ids = list(ids.values())
    store = env.store()
    capture_root = tmp_path / "capture"
    commands = Commands(env, capture_root)

    def manifest_uri(key: str) -> str:
        return f"{env.robot_run_root}/{ids[key]}/robot_run_manifest.json"

    def recording_uri(key: str) -> str:
        return f"{env.robot_run_root}/{ids[key]}/recording.mcap"

    # ── capture: nine acquisitions, as Capture leaves them ───────────────────
    for key in "abcdfgh":
        make_finalized_capture(capture_root, ids[key], robots[key])
    make_unfinished_capture(capture_root, ids["i"])  # killed before finalize
    make_legacy_capture(capture_root, ids["j"])  # finalized, no receipt

    # ── 1. what the status says before anything has run ──────────────────────
    before = await durable_snapshot(env, all_ids)
    first = commands.status()
    assert await durable_snapshot(env, all_ids) == before  # derived, not stored
    report = first.json
    assert report["runs_total"] == 9 and report["runs_included"] is True
    statuses = runs_by_id(report)
    for key in "abcdfgh":
        run = statuses[ids[key]]
        assert (run["stage"], run["classification"], run["health"]) == (
            "finalized",
            "publish_pending",
            "pending",
        )
        assert run["operator_required"] is False
        # What the receipt knows, before any manifest exists.
        assert run["robot_id"] == robots[key]
        assert run["finalization_reason"] == "explicit_run_end"
        assert run["recording_bytes"] and run["message_count"]
    unfinished, legacy = statuses[ids["i"]], statuses[ids["j"]]
    assert (unfinished["stage"], unfinished["classification"]) == (
        "capture_unfinished",
        "capture_unfinished",
    )
    assert (legacy["classification"], legacy["operator_required"]) == (
        "finalized_no_receipt",
        True,
    )
    assert [item["run_id"] for item in report["attention"]] == [ids["j"]]
    (summary,) = first.records("acquisition_status")
    assert summary["runs"] == 9 and summary["operator_required"] == 1
    assert "runs_included" not in summary  # a log record carries no per-run status

    # ── 2. publication: the publisher dies, a write fails, a duplicate runs ──
    env.set_faults(kill_after_recording=[ids["b"]], fail_manifest_write=[ids["c"]])
    killed = commands.publish_pending(faulty=True)
    assert killed.returncode == KILLED and killed.stdout == ""
    assert await store.exists(manifest_uri("a"))  # published before the kill
    assert await store.exists(recording_uri("b"))  # P3 done
    assert not await store.exists(manifest_uri("b"))  # P5 never happened
    assert not await store.exists(recording_uri("c"))  # never reached
    (a_record,) = killed.records()
    assert a_record["run_id"] == ids["a"] and a_record["outcome"] == "published"
    assert (a_record["state_before"], a_record["state_after"]) == (
        "publish_pending",
        "published",
    )
    assert killed.records("acquisition_recovery_pass") == []  # it never got to end

    interrupted = runs_by_id(commands.status().json)[ids["b"]]
    assert interrupted["classification"] == "publication_incomplete"
    assert (interrupted["stage"], interrupted["health"]) == ("finalized", "pending")
    assert interrupted["operator_required"] is False  # the capture can resume it
    assert "resumable_from_capture" in interrupted["reasons"]

    env.set_faults(fail_manifest_write=[ids["c"]])
    retried = commands.publish_pending(faulty=True)
    assert retried.returncode == 2  # one publish failed; the report is still printed
    results = {r["run_id"]: r for r in retried.json["results"]}
    assert results[ids["a"]]["reason"] == "already_published"
    assert results[ids["b"]]["outcome"] == "published"
    assert results[ids["b"]]["recording_written"] is False  # reused, not re-uploaded
    assert results[ids["b"]]["manifest_written"] is True
    assert results[ids["c"]]["outcome"] == "failed"
    assert results[ids["c"]]["reason"] == "publish_failed"
    assert {results[ids[key]]["outcome"] for key in "dfgh"} == {"published"}
    assert results[ids["i"]]["reason"] == "capture_unfinished"
    assert results[ids["j"]]["reason"] == "finalized_no_receipt"
    assert retried.json["counts"] == {"failed": 1, "published": 5, "skipped": 3}
    by_run = {r["run_id"]: r for r in retried.records()}
    assert by_run[ids["b"]]["state_before"] == "publication_incomplete"
    failed_publish = by_run[ids["c"]]
    assert failed_publish["outcome"] == "failed"
    assert failed_publish["state_before"] == failed_publish["state_after"]
    assert failed_publish["error_type"] == "OSError"
    assert ids["i"] not in by_run and ids["j"] not in by_run  # skips are not actions
    assert not await store.exists(manifest_uri("c"))  # the failure left no marker

    env.clear_faults()
    duplicates = commands.publish_pending_concurrently(2)
    assert [c.returncode for c in duplicates] == [0, 0]
    seen_c = {
        r["manifest_checksum"]
        for c in duplicates
        for r in c.json["results"]
        if r["run_id"] == ids["c"]
    }
    assert len(seen_c) == 1  # both processes agree on the one manifest
    assert any(
        r["outcome"] == "published" and r["run_id"] == ids["c"]
        for c in duplicates
        for r in c.json["results"]
    )
    published_checksums: dict[str, str] = {}
    for key in "abcdfgh":
        data = await store.read_bytes(manifest_uri(key))
        manifest = load_canonical_robot_run_manifest(data)
        # Everything the manifest says about the acquisition came from the
        # receipt and the bytes: nobody typed a robot id or a topic.
        assert manifest.run_id == ids[key] and manifest.robot_id == robots[key]
        assert manifest.capture.source.topics == ["sceneops.robot.telemetry.v1"]
        published_checksums[key] = sha256_checksum(data)
    for key in "ij":
        assert not await store.exists(manifest_uri(key))
        assert not await store.exists(recording_uri(key))  # never published

    published = commands.status().json
    assert published["by_classification"] == {
        "capture_unfinished": 1,
        "finalized_no_receipt": 1,
        "registration_pending": 7,
    }
    assert published["registration"]["pending"] == 7

    # ── 3. the broker is down when registration is first submitted ───────────
    env.redis.stop()
    outage = commands.reconcile(stall=WITHIN_SECONDS)
    assert outage.returncode == 0, outage.stderr
    actions = {a["run_id"]: a for a in outage.json["actions"]}
    assert set(actions) == {ids[key] for key in "abcdfgh"}
    for key in "abcdfgh":
        action = actions[ids[key]]
        assert (action["kind"], action["outcome"]) == (
            "submit_registration",
            "dispatch_failed",
        )
        assert action["job_id"]
        assert action["state_before"] == "registration_pending"
        assert action["state_after"] == "registration_active"  # the Job is kept
        (job,) = await jobs_of(ids[key])
        assert job.job_id == action["job_id"] and job.status in ("pending", "queued")
    assert await robot_run_count(all_ids) == (0, 0)  # delayed, nothing registered
    (pass_record,) = outage.records("acquisition_recovery_pass")
    assert pass_record["actions"] == {"submit_registration:dispatch_failed": 7}
    assert {r["outcome"] for r in outage.records()} == {"dispatch_failed"}
    # Observation needs no broker: the status still answers with Redis down.
    during = commands.status(stall=WITHIN_SECONDS).json
    assert during["by_classification"]["registration_active"] == 7
    assert during["registration"]["active"] == 7
    env.redis.start()

    # ── 4. the broker is back; stalled Jobs are replaced once, boundedly ─────
    inside = commands.reconcile(stall=WITHIN_SECONDS)
    assert inside.json["actions"] == []  # in flight inside the threshold: untouched
    time.sleep(STALL_SECONDS + 1)

    wave = commands.reconcile_concurrently(4, stall=STALL_SECONDS)
    assert [c.returncode for c in wave] == [0] * 4
    replacements = [
        a for c in wave for a in c.json["actions"] if a["kind"] == "replace_stalled_job"
    ]
    assert all(a["outcome"] != "error" for a in replacements)
    for key in "abcdfgh":
        per_run = [a for a in replacements if a["run_id"] == ids[key]]
        # Four concurrent passes, one winner: the others lost the abandon race.
        assert [a["outcome"] for a in per_run].count("submitted") == 1, per_run
        assert {a["outcome"] for a in per_run} <= {"submitted", "lost_race"}
        assert [(j.status, j.error_type) for j in await jobs_of(ids[key])] == [
            ("failed", "JobAbandoned"),
            ("queued", None),
        ]

    # Now workers: D's registration will hang and be killed, F's keeps failing.
    env.set_faults(hold_before_commit=[ids["d"]], transient_error=[ids["f"]])
    first_worker = WorkerProcess(env, "first").start()
    try:
        for key in "abcgh":
            await async_wait_until(lambda k=key: registered(ids[k]), timeout=120)
        wait_until(env.marker("hold_before_commit", ids["d"]).exists, timeout=60)
        await async_wait_until(
            lambda: _has_failed_with(ids["f"], "OSError"), timeout=60
        )
        (running,) = [j for j in await jobs_of(ids["d"]) if j.status == "running"]
        first_worker.kill()  # SIGKILL: no rollback, no acknowledgement
    finally:
        first_worker.stop()
    assert await count_rows(ids["d"]) == {"robot_runs": 0, "artifacts": 0}
    assert any(
        j.job_id == running.job_id and j.status == "running"
        for j in await jobs_of(ids["d"])
    )  # nothing will ever finish it

    env.set_faults(transient_error=[ids["f"]])  # D's replacement is not held
    with WorkerProcess(env, "second"):
        deadline = time.monotonic() + 240
        while True:
            commands.reconcile(stall=STALL_SECONDS)
            done_d = await registered(ids["d"])
            f_jobs = await jobs_of(ids["f"])
            if done_d and len(failed_jobs(f_jobs)) >= 3:
                break
            assert time.monotonic() < deadline, "recovery did not converge"
            time.sleep(3)
        for _ in range(2):  # converged: further passes act on nothing
            quiet = commands.reconcile(stall=STALL_SECONDS)
            assert quiet.json["actions"] == [], quiet.json["actions"]

    # D: abandoned twice (outage, killed worker), then registered. F: three
    # attempts without success, then recovery stops.
    assert [(j.status, j.error_type) for j in await jobs_of(ids["d"])] == [
        ("failed", "JobAbandoned"),
        ("failed", "JobAbandoned"),
        ("succeeded", None),
    ]
    assert [(j.status, j.error_type) for j in await jobs_of(ids["f"])] == [
        ("failed", "JobAbandoned"),
        ("failed", "OSError"),
        ("failed", "OSError"),
    ]
    for key in "abcgh":
        assert [(j.status, j.error_type) for j in await jobs_of(ids[key])] == [
            ("failed", "JobAbandoned"),
            ("succeeded", None),
        ]
    assert await count_rows(ids["f"]) == {"robot_runs": 0, "artifacts": 0}

    retries = [
        r
        for c in commands.log
        if c.label == "reconcile"
        for r in c.records()
        if r["run_id"] == ids["f"] and r["action"] == "retry_registration"
    ]
    # Attempt 1 was the outage's abandoned Job and attempt 2 the replacement the
    # first worker failed; the one retry is the third and last attempt.
    assert [(r["attempt"], r["attempts_remaining"]) for r in retries] == [(3, 1)]
    assert {r["failure_class"] for r in retries} == {"transient"}
    replaced = [
        r
        for c in commands.log
        if c.label == "reconcile"
        for r in c.records()
        if r["run_id"] == ids["d"]
        and r["action"] == "replace_stalled_job"
        and r["outcome"] == "submitted"
    ]
    assert len(replaced) == 2 and all(
        r["state_before"] == "registration_stalled_candidate" for r in replaced
    )

    # ── 5. damage the registered runs; nothing may be repaired ───────────────
    forged = load_canonical_robot_run_manifest(
        await store.read_bytes(manifest_uri("g"))
    ).model_copy(update={"robot_id": "someone-else"})
    await store.write_bytes(manifest_uri("g"), forged.to_canonical_bytes())
    await store.delete_prefix(recording_uri("h"))
    damaged = await durable_snapshot(env, all_ids)

    after_damage = commands.publish_pending()
    assert after_damage.returncode == 0, after_damage.stderr
    skipped = {r["run_id"]: r["reason"] for r in after_damage.json["results"]}
    # The capture of H still holds the bytes, and publish-pending leaves it be.
    assert skipped[ids["h"]] == "publication_manifest_without_recording"
    assert skipped[ids["g"]] == "already_published"  # it does not know registration
    assert after_damage.json["counts"] == {"skipped": 9}
    passes = [commands.reconcile(stall=STALL_SECONDS) for _ in range(3)]
    for command in passes:
        assert command.json["actions"] == []
        assert command.records() == []  # nothing was attempted, so nothing logged
        run = runs_by_id(command.json)
        assert run[ids["g"]]["state"] == "permanent_conflict"
        assert run[ids["h"]]["state"] == "integrity_incident"
        assert run[ids["f"]]["state"] == "registration_failed_permanent"
        assert "attempt_budget_exhausted" in run[ids["f"]]["reasons"]
    assert await durable_snapshot(env, all_ids) == damaged  # byte-for-byte untouched
    assert not await store.exists(recording_uri("h"))
    assert after_damage.records() == []

    # ── 6. the final picture, derived from the durable facts alone ───────────
    final = commands.status()
    assert await durable_snapshot(env, all_ids) == damaged
    report = final.json
    assert report["runs_total"] == 9
    assert report["by_classification"] == {
        "capture_unfinished": 1,
        "finalized_no_receipt": 1,
        "integrity_incident": 1,
        "permanent_conflict": 1,
        "registered": 4,
        "registration_failed_permanent": 1,
    }
    assert report["by_health"] == {
        "failed": 1,
        "inconsistent": 2,
        "ok": 4,
        "pending": 2,
    }
    assert report["by_stage"] == {
        "capture_unfinished": 1,
        "finalized": 1,
        "published": 1,
        "registered": 6,
    }
    assert report["operator_required"] == 4  # F, G, H and the receipt-less J
    assert {item["run_id"] for item in report["attention"]} == {
        ids[key] for key in "fghj"
    }
    registration = report["registration"]
    assert (registration["failed_permanent"], registration["budget_exhausted"]) == (
        1,
        1,
    )
    assert registration["attempts_used"] == {"3": 1}
    assert registration["abandoned_jobs"] == 1  # F's, from the outage
    assert report["incidents"]["runs"] == 2
    assert report["incidents"]["by_reason"]["manifest_checksum_differs"] == 1
    assert report["artifacts"]["integrity_incidents"]["count"] >= 1
    assert report["recordings"]["total_bytes"] > 0
    assert set(report["oldest"]) == {"capture_unfinished", "finalized_no_receipt"}

    final_runs = runs_by_id(report)
    for key in "abcd":
        run = final_runs[ids[key]]
        assert (run["stage"], run["health"], run["failure"]) == (
            "registered",
            "ok",
            None,
        )
        assert run["robot_id"] == robots[key]
        assert run["registration"]["registered_at"]
        assert run["durations"]["publish_to_register_seconds"] is not None
        assert run["durations"]["finalize_to_register_seconds"] is not None
        assert run["artifacts"]["referenced_objects"] == 2
    d_run = final_runs[ids["d"]]
    assert d_run["registration"]["abandoned_job_count"] == 2
    assert d_run["registration"]["failed_job_count"] == 2
    f_run = final_runs[ids["f"]]
    assert (f_run["stage"], f_run["health"], f_run["operator_required"]) == (
        "published",
        "failed",
        True,
    )
    assert f_run["failure"]["failure_class"] == "transient"
    assert f_run["failure"]["error_type"] == "OSError"
    assert f_run["failure"]["attempts"] == 3
    assert f_run["failure"]["attempts_remaining"] == 0
    g_run = final_runs[ids["g"]]
    assert (g_run["stage"], g_run["health"]) == ("registered", "inconsistent")
    h_run = final_runs[ids["h"]]
    assert (h_run["stage"], h_run["health"]) == ("registered", "inconsistent")
    assert h_run["findings"]  # the lifecycle entries behind it

    # One logical acquisition, one RobotRun that matches its published manifest.
    assert await robot_run_count([ids[key] for key in REGISTERED]) == (6, 12)
    for key in REGISTERED:
        assert await count_rows(ids[key]) == {"robot_runs": 1, "artifacts": 2}
        assert await robot_run_checksum(ids[key]) == published_checksums[key]
    # The forged manifest registered nothing: G's RobotRun is the original.
    assert await robot_run_checksum(ids["g"]) != sha256_checksum(
        forged.to_canonical_bytes()
    )
    for key in "fij":
        assert await count_rows(ids[key]) == {"robot_runs": 0, "artifacts": 0}


async def _has_failed_with(run_id: str, error_type: str) -> bool:
    return any(
        job.status == "failed" and job.error_type == error_type
        for job in await jobs_of(run_id)
    )
