"""Fault-injection acceptance of acquisition recovery (ADR-008 §2, §5, step 12.4).

Real PostgreSQL (row locks, unique constraints), real MinIO, a real Redis and
real Celery workers -- the last two owned by this module so that killing a
worker or stopping the broker cannot disturb the shared local stack. The
reconciler, ``publish-pending`` and the registration handler are the production
code; ``recovery_worker`` only adds faults at exact points of a registration.

The invariant every test ends on::

    one logical acquisition  ->  exactly one RobotRunRecord (+ its two
                                 ArtifactRecords), whatever crashed, was retried
                                 or ran concurrently

and the converse for what must never be repaired: conflicts and integrity
incidents leave every object and row exactly as they were.

Needs ``make local-up`` (PostgreSQL + MinIO) and Docker (a throwaway Redis);
skips otherwise. Run: ``make test-recovery``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domains.robots.reconciliation import (
    AcquisitionState as S,
    RecoveryActionKind,
    RecoveryOutcome,
    RecoveryPolicy,
    PostgresStalledJobControl,
    postgres_registration_facts,
    reconcile_and_recover,
    reconcile_once,
)
from app.domains.robots.reconciliation.cli import build_registration_submitter
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.robots.manifest import (
    load_canonical_robot_run_manifest,
)
from sceneops_core.robots.registration_failures import JOB_ABANDONED_ERROR_TYPE
from sceneops_db.session import get_async_sessionmaker
from sceneops_integrations.recording import publish_pending

from recovery_support import (
    FIXTURE_MCAP,
    RECOVERY_FIXTURES,
    REPO_ROOT,
    WorkerProcess,
    async_wait_until,
    count_rows,
    job_row_snapshot,
    jobs_of,
    make_finalized_capture,
    robot_run_checksum,
    wait_until,
)

pytestmark = pytest.mark.usefixtures(*RECOVERY_FIXTURES)

STALL = timedelta(seconds=30)
ACTIVE = {"pending", "queued", "running"}


def _later() -> datetime:
    """A clock far enough ahead that every Job created so far has shown no
    activity for longer than the stall threshold -- time passing, without
    sleeping."""
    return datetime.now(UTC) + timedelta(hours=1)


# ── helpers ───────────────────────────────────────────────────────────────────


def _policy(**kw) -> RecoveryPolicy:
    return RecoveryPolicy(stall_threshold=kw.pop("stall_threshold", STALL), **kw)


async def recover(env, *, now=None, policy=None, apply=True):
    sessionmaker = get_async_sessionmaker()
    if not apply:
        return await reconcile_once(
            artifact_store=env.store(),
            root_uri=env.robot_run_root,
            registration_facts=postgres_registration_facts(sessionmaker),
            policy=(policy or _policy()).classification(),
            now=now,
        )
    return await reconcile_and_recover(
        artifact_store=env.store(),
        root_uri=env.robot_run_root,
        registration_facts=postgres_registration_facts(sessionmaker),
        submitter=build_registration_submitter(env.api_settings()),
        jobs=PostgresStalledJobControl(sessionmaker),
        policy=policy or _policy(),
        now=now,
    )


def run_report(report, run_id):
    return next(run for run in report.runs if run.run_id == run_id)


def actions(report, run_id):
    return [a for a in report.actions if a.run_id == run_id]


async def settled(run_id: str) -> bool:
    return all(job.status not in ACTIVE for job in await jobs_of(run_id))


async def registered(run_id: str) -> bool:
    return (await count_rows(run_id))["robot_runs"] == 1


async def assert_one_registration(published) -> None:
    """The invariant: one acquisition, one RobotRun, matching its manifest."""
    assert await count_rows(published.run_id) == {"robot_runs": 1, "artifacts": 2}
    assert await robot_run_checksum(published.run_id) == published.manifest_checksum


# ── W3 / W5 / W6: finalized capture -> published -> registered, unattended ───


async def test_finalized_capture_to_registered_runs_through_the_one_shot_commands(
    env, tmp_path
):
    """The whole recovery vertical, by the real commands (subprocesses with
    configuration from the environment, as the polling services run them):
    a finalized capture nothing ever published -> ``publish-pending`` ->
    ``reconcile --once --apply`` -> a worker -> RobotRun. Re-running either
    command changes nothing."""
    run_id = f"rec124-vertical-{uuid.uuid4().hex[:6]}"
    robot_id = f"rec124-robot-{uuid.uuid4().hex[:6]}"
    env.run_ids.append(run_id)
    capture_root = tmp_path / "capture"
    make_finalized_capture(capture_root, run_id, robot_id)
    publisher_env = env.publisher_environment()
    reconciler_env = env.reconciler_environment(stall_threshold_seconds=30)

    def publish_pending_cmd() -> dict:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "sceneops_integrations.recording",
                "publish-pending",
                "--capture-root",
                str(capture_root),
            ],
            capture_output=True,
            text=True,
            env=publisher_env,
            cwd=str(tmp_path),
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    def reconcile_cmd() -> dict:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.domains.robots.reconciliation",
                "--once",
                "--apply",
            ],
            capture_output=True,
            text=True,
            env=reconciler_env,
            cwd=str(REPO_ROOT / "apps" / "api"),
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    # W3: finalized, never published.
    observed = await recover(env, apply=False)
    assert run_id not in {r.run_id for r in observed.runs}

    published = publish_pending_cmd()
    assert published["counts"] == {"published": 1}
    manifest_uri = published["results"][0]["manifest_uri"]
    again = publish_pending_cmd()
    assert again["results"][0]["reason"] == "already_published"
    assert (
        again["results"][0]["manifest_checksum"]
        == published["results"][0]["manifest_checksum"]
    )

    # W6: published, registration never submitted.
    with WorkerProcess(env, "vertical"):
        first = reconcile_cmd()
        (action,) = [a for a in first["actions"] if a["run_id"] == run_id]
        assert action["kind"] == "submit_registration"
        assert action["outcome"] == "submitted"
        assert first["mode"] == "apply"
        assert first["policy"]["stall_threshold_seconds"] == 30.0  # from the env
        await async_wait_until(lambda: registered(run_id), timeout=60)

    second = reconcile_cmd()
    run = next(r for r in second["runs"] if r["run_id"] == run_id)
    assert run["state"] == "registered"
    assert [a for a in second["actions"] if a["run_id"] == run_id] == []
    assert await count_rows(run_id) == {"robot_runs": 1, "artifacts": 2}
    jobs = await jobs_of(run_id)
    assert [(j.status, j.error_type) for j in jobs] == [("succeeded", None)]
    manifest = load_canonical_robot_run_manifest(
        await env.store().read_bytes(manifest_uri)
    )
    assert await robot_run_checksum(run_id) == sha256_checksum(
        await env.store().read_bytes(manifest_uri)
    )
    assert manifest.run_id == run_id


async def test_publish_pending_resumes_after_the_recording_was_uploaded(env, tmp_path):
    """W5 on real MinIO: the recording is in the store, the manifest marker is
    not (a publisher killed between P3 and P5). The pass reuses the recording,
    writes the marker, and the run then registers."""
    run_id = f"rec124-w5-{uuid.uuid4().hex[:6]}"
    env.run_ids.append(run_id)
    capture_root = tmp_path / "capture"
    make_finalized_capture(capture_root, run_id, f"rec124-robot-{uuid.uuid4().hex[:6]}")
    store = env.store()
    recording_uri = f"{env.robot_run_root}/{run_id}/recording.mcap"
    await store.write_bytes(recording_uri, FIXTURE_MCAP.read_bytes())

    observed = await recover(env, apply=False)
    assert run_report(observed, run_id).state == S.PUBLICATION_INCOMPLETE
    assert (
        await store.exists(f"{env.robot_run_root}/{run_id}/robot_run_manifest.json")
        is False
    )

    report = await publish_pending(
        artifact_store=store, root_uri=env.robot_run_root, capture_root=capture_root
    )

    (result,) = report.results
    assert result.outcome.value == "published"
    assert result.recording_written is False and result.manifest_written is True
    with WorkerProcess(env, "w5"):
        await recover(env)
        await async_wait_until(lambda: registered(run_id), timeout=60)
    assert await count_rows(run_id) == {"robot_runs": 1, "artifacts": 2}


# ── W7: a Job nobody will ever run ────────────────────────────────────────────


async def test_a_lost_queue_message_is_recovered_by_replacing_the_job(env):
    published = await env.publish_run("w7")

    first = await recover(env)  # submits; no worker is consuming the queue
    (submit,) = actions(first, published.run_id)
    assert submit.outcome == RecoveryOutcome.SUBMITTED
    (job,) = await jobs_of(published.run_id)
    assert job.status == "queued"

    env.redis.flush()  # the message is gone; the Job row says QUEUED forever

    with WorkerProcess(env, "w7"):
        # Inside the threshold the Job is only "in flight": no action.
        within = await recover(env)
        assert run_report(within, published.run_id).state == S.REGISTRATION_ACTIVE
        assert within.actions == ()

        later = await recover(env, now=_later())
        assert (
            run_report(later, published.run_id).state
            == S.REGISTRATION_STALLED_CANDIDATE
        )
        (replace,) = actions(later, published.run_id)
        assert replace.kind == RecoveryActionKind.REPLACE_STALLED_JOB
        assert replace.abandoned_job_ids == (job.job_id,)
        await async_wait_until(lambda: registered(published.run_id), timeout=60)

    jobs = await jobs_of(published.run_id)
    assert [(j.status, j.error_type) for j in jobs] == [
        ("failed", JOB_ABANDONED_ERROR_TYPE),
        ("succeeded", None),
    ]
    await assert_one_registration(published)


# ── W8: worker killed mid-registration ────────────────────────────────────────


async def test_a_worker_killed_during_registration_is_recovered(env):
    published = await env.publish_run("w8")
    env.set_faults(hold_before_commit=[published.run_id])

    with WorkerProcess(env, "doomed") as doomed:
        report = await recover(env)
        assert actions(report, published.run_id)[0].outcome == RecoveryOutcome.SUBMITTED
        wait_until(
            env.marker("hold_before_commit", published.run_id).exists, timeout=60
        )
        (job,) = await jobs_of(published.run_id)
        assert job.status == "running"
        doomed.kill()  # SIGKILL: no rollback code runs, no ack is sent

    assert await count_rows(published.run_id) == {"robot_runs": 0, "artifacts": 0}
    (job,) = await jobs_of(published.run_id)
    assert job.status == "running"  # F3: nothing will ever finish it

    env.clear_faults()
    with WorkerProcess(env, "healthy"):
        within = await recover(env)
        assert run_report(within, published.run_id).state == S.REGISTRATION_ACTIVE
        assert within.actions == ()

        later = await recover(env, now=_later())
        (replace,) = actions(later, published.run_id)
        assert replace.kind == RecoveryActionKind.REPLACE_STALLED_JOB
        assert replace.outcome == RecoveryOutcome.SUBMITTED
        await async_wait_until(lambda: registered(published.run_id), timeout=60)
        await async_wait_until(lambda: settled(published.run_id), timeout=60)

    jobs = await jobs_of(published.run_id)
    assert [(j.status, j.error_type) for j in jobs] == [
        ("failed", JOB_ABANDONED_ERROR_TYPE),
        ("succeeded", None),
    ]
    await assert_one_registration(published)
    final = await recover(env, now=_later())
    assert run_report(final, published.run_id).state == S.REGISTERED
    assert final.actions == ()


# ── W9: registration committed, completion lost ───────────────────────────────


async def test_a_committed_registration_with_a_lost_completion_is_left_alone(env):
    published = await env.publish_run("w9")
    env.set_faults(hold_after_commit=[published.run_id])

    with WorkerProcess(env, "doomed") as doomed:
        await recover(env)
        wait_until(env.marker("hold_after_commit", published.run_id).exists, timeout=60)
        doomed.kill()  # after the R8 commit, before the Job could be completed

    await assert_one_registration(published)
    (job,) = await jobs_of(published.run_id)
    assert job.status == "running"  # the Job row is stale noise (W9)
    before = await job_row_snapshot(published.run_id)

    # Long after the stall threshold: the RobotRunRecord is the only truth.
    for _ in range(2):
        report = await recover(env, now=_later())
        run = run_report(report, published.run_id)
        assert run.state == S.REGISTERED
        assert report.actions == ()

    assert await job_row_snapshot(published.run_id) == before  # not abandoned
    await assert_one_registration(published)


# ── transient failures across replacement Jobs; the retry budget ──────────────


async def test_transient_failures_share_one_budget_across_replacement_jobs(env):
    recovers, exhausts = (
        await env.publish_run("recovers"),
        await env.publish_run("spent"),
    )
    ids = [recovers.run_id, exhausts.run_id]
    env.set_faults(transient_error=ids)

    async def settle() -> None:
        for run_id in ids:
            await async_wait_until(lambda r=run_id: settled(r), timeout=60)

    with WorkerProcess(env, "flaky"):
        first = await recover(env)  # first Jobs (attempt 1)
        assert {a.kind for a in first.actions} == {
            RecoveryActionKind.SUBMIT_REGISTRATION
        }
        await settle()

        second = await recover(env)  # replacement Jobs (attempt 2)
        assert {a.kind for a in second.actions} == {
            RecoveryActionKind.RETRY_REGISTRATION
        }
        assert {a.attempts_used for a in second.actions} == {1}
        evidence = run_report(second, exhausts.run_id).registration
        assert (evidence.failed_job_count, evidence.attempts_remaining) == (1, 2)
        await settle()

        # The environment heals for one run only, before its last attempt.
        env.set_faults(transient_error=[exhausts.run_id])
        third = await recover(env)  # attempt 3
        assert {a.attempts_used for a in third.actions} == {2}
        await settle()
        await async_wait_until(lambda: registered(recovers.run_id), timeout=60)

        # Success ends recovery; the other run has spent its budget.
        fourth = await recover(env)
        assert run_report(fourth, recovers.run_id).state == S.REGISTERED
        spent = run_report(fourth, exhausts.run_id)
        assert spent.state == S.REGISTRATION_FAILED_PERMANENT
        assert "attempt_budget_exhausted" in spent.reasons
        assert spent.registration.failed_job_count == 3
        assert spent.registration.attempts_remaining == 0
        assert fourth.actions == ()

        for _ in range(2):  # never again, however often it runs
            assert (await recover(env, now=_later())).actions == ()

        jobs_a = await jobs_of(recovers.run_id)
        jobs_b = await jobs_of(exhausts.run_id)
        assert [j.status for j in jobs_a] == ["failed", "failed", "succeeded"]
        assert [j.error_type for j in jobs_a[:2]] == ["OSError", "OSError"]
        assert [j.status for j in jobs_b] == ["failed"] * 3  # not a 4th row
        assert await count_rows(exhausts.run_id) == {"robot_runs": 0, "artifacts": 0}
        await assert_one_registration(recovers)

        # An operator's forced submission is outside the budget.
        env.clear_faults()
        service = build_registration_submitter(env.api_settings())
        await service.submit(exhausts.manifest_uri, force=True)
        await async_wait_until(lambda: registered(exhausts.run_id), timeout=60)
    assert [j.status for j in await jobs_of(exhausts.run_id)] == ["failed"] * 3 + [
        "succeeded"
    ]
    await assert_one_registration(exhausts)


async def test_a_permanent_failure_is_never_retried(env):
    published = await env.publish_run("permanent")
    env.set_faults(permanent_error=[published.run_id])

    with WorkerProcess(env, "w"):
        await recover(env)
        await async_wait_until(lambda: settled(published.run_id), timeout=60)
        report = await recover(env, now=_later())

    run = run_report(report, published.run_id)
    assert run.state == S.REGISTRATION_FAILED_PERMANENT
    assert run.reasons == ("error_type:RecordingVerificationError",)
    assert report.actions == ()
    (job,) = await jobs_of(published.run_id)
    assert (job.status, job.error_type) == ("failed", "RecordingVerificationError")
    assert await count_rows(published.run_id) == {"robot_runs": 0, "artifacts": 0}


# ── Redis / dispatch unavailable, then restored ───────────────────────────────


async def test_dispatch_failure_preserves_the_job_and_recovers_after_redis_returns(env):
    published = await env.publish_run("redis-down")
    env.redis.stop()
    try:
        # Classification needs only the ArtifactStore and PostgreSQL.
        observed = await recover(env, apply=False)
        assert run_report(observed, published.run_id).state == S.REGISTRATION_PENDING

        started = time.monotonic()
        report = await recover(env)
        assert time.monotonic() - started < 60
        (action,) = actions(report, published.run_id)
        assert action.outcome == RecoveryOutcome.DISPATCH_FAILED
        assert action.job_id is not None and action.error
    finally:
        env.redis.start()

    # Preserved, not rolled back and not reported as done.
    (job,) = await jobs_of(published.run_id)
    assert job.job_id == action.job_id
    assert job.status in ("pending", "queued")
    assert await count_rows(published.run_id) == {"robot_runs": 0, "artifacts": 0}

    with WorkerProcess(env, "back"):
        within = await recover(env)
        assert run_report(within, published.run_id).state == S.REGISTRATION_ACTIVE
        assert within.actions == ()

        later = await recover(env, now=_later())
        (replace,) = actions(later, published.run_id)
        assert replace.kind == RecoveryActionKind.REPLACE_STALLED_JOB
        assert replace.outcome == RecoveryOutcome.SUBMITTED
        assert replace.attempts_used == 0 and replace.abandoned_job_ids == (job.job_id,)
        await async_wait_until(lambda: registered(published.run_id), timeout=60)

    assert [(j.status, j.error_type) for j in await jobs_of(published.run_id)] == [
        ("failed", JOB_ABANDONED_ERROR_TYPE),
        ("succeeded", None),
    ]
    await assert_one_registration(published)


async def test_an_unresponsive_broker_does_not_hang_a_pass(env):
    """A paused Redis accepts nothing and answers nothing (not a refused
    connection): the dispatch must give up within its bound so the pass
    completes and the next one retries."""
    published = await env.publish_run("redis-paused")
    env.redis.pause()
    try:
        started = time.monotonic()
        report = await recover(env)
        elapsed = time.monotonic() - started
    finally:
        env.redis.unpause()

    assert elapsed < 90, (
        f"the pass blocked for {elapsed:.0f}s on an unresponsive broker"
    )
    (action,) = actions(report, published.run_id)
    assert action.outcome == RecoveryOutcome.DISPATCH_FAILED
    (job,) = await jobs_of(published.run_id)
    assert job.status in ("pending", "queued")


# ── concurrent reconciliation passes ──────────────────────────────────────────


async def test_concurrent_passes_converge_on_one_registration_per_acquisition(env):
    runs = [await env.publish_run(f"c{index}") for index in range(4)]
    stalled = await env.publish_run("c-stalled")
    policy = _policy(stall_threshold=timedelta(seconds=10))

    # One acquisition has a Job nobody will run (no worker is up yet).
    submitter = build_registration_submitter(env.api_settings())
    (stalled_job,) = [(await submitter.submit(stalled.manifest_uri)).job]
    await asyncio.sleep(11)  # past the stall threshold, for real

    for _ in range(2):
        reports = await asyncio.gather(*(recover(env, policy=policy) for _ in range(6)))
    first_round_actions = [a for r in reports for a in r.actions]
    assert all(a.outcome != RecoveryOutcome.ERROR for a in first_round_actions)

    # Exactly one pass replaced the stalled Job (single winner).
    jobs = await jobs_of(stalled.run_id)
    assert [(j.status, j.error_type) for j in jobs] == [
        ("failed", JOB_ABANDONED_ERROR_TYPE),
        ("queued", None),
    ]
    assert jobs[0].job_id == stalled_job.job_id

    with WorkerProcess(env, "concurrent"):
        for published in (*runs, stalled):
            await async_wait_until(lambda r=published.run_id: registered(r), timeout=90)
            await async_wait_until(lambda r=published.run_id: settled(r), timeout=90)

    for published in (*runs, stalled):
        await assert_one_registration(published)
        statuses = {j.status for j in await jobs_of(published.run_id)}
        assert statuses <= {"succeeded", "failed"}
    # Extra Jobs from racing submissions are allowed; they all converged
    # (the duplicate registration is a no-op) and none failed.
    for published in runs:
        assert {j.status for j in await jobs_of(published.run_id)} == {"succeeded"}
    assert [j.status for j in await jobs_of(stalled.run_id)] == ["failed", "succeeded"]

    final = await asyncio.gather(*(recover(env, policy=policy) for _ in range(4)))
    for report in final:
        assert report.actions == ()
        for published in (*runs, stalled):
            assert run_report(report, published.run_id).state == S.REGISTERED


# ── what must never be repaired ───────────────────────────────────────────────


async def test_conflicts_and_integrity_incidents_are_left_exactly_as_they_are(env):
    conflicted = await env.publish_run("conflict")
    damaged = await env.publish_run("damaged")
    clean = await env.publish_run("clean")

    with WorkerProcess(env, "w"):
        for published in (conflicted, damaged, clean):
            await recover(env)
            await async_wait_until(lambda r=published.run_id: registered(r), timeout=60)
            await async_wait_until(lambda r=published.run_id: settled(r), timeout=60)

    store = env.store()
    # A different (valid) manifest replaces the registered one in the store...
    manifest_uri = conflicted.manifest_uri
    forged = load_canonical_robot_run_manifest(
        await store.read_bytes(manifest_uri)
    ).model_copy(update={"robot_id": "someone-else"})
    await store.write_bytes(manifest_uri, forged.to_canonical_bytes())
    # ...and another run loses the recording its RobotRun points at.
    await store.delete_prefix(f"{env.robot_run_root}/{damaged.run_id}/recording.mcap")

    def listing():
        return store.list_objects(env.robot_run_root)

    objects_before = sorted(
        (o.uri, o.size_bytes, o.last_modified) for o in await listing()
    )
    rows_before = {
        run_id: (await count_rows(run_id), await job_row_snapshot(run_id))
        for run_id in (conflicted.run_id, damaged.run_id, clean.run_id)
    }

    with WorkerProcess(env, "w2"):
        for pass_number in range(3):
            report = await recover(env, now=_later())
            assert run_report(report, conflicted.run_id).state == S.PERMANENT_CONFLICT
            assert run_report(report, damaged.run_id).state == S.INTEGRITY_INCIDENT
            assert run_report(report, clean.run_id).state == S.REGISTERED
            assert report.actions == ()
        await asyncio.sleep(2)  # a worker is up: nothing may have been queued

    assert (
        sorted((o.uri, o.size_bytes, o.last_modified) for o in await listing())
        == objects_before
    )
    for run_id, before in rows_before.items():
        assert (await count_rows(run_id), await job_row_snapshot(run_id)) == before
