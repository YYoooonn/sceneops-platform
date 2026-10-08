"""Bounded registration recovery (ADR-008 §5.2, §5.3, Phase 12.4).

Over in-memory doubles: which states are acted on and which never are, the
abandon-then-replace protocol, the logical-registration attempt budget across
replacement Jobs, dispatch failure, the per-pass bound and the single-winner
replacement. The real PostgreSQL / Redis / Celery behavior is exercised by
``tests/infrastructure/test_acquisition_recovery.py``.

The doubles' store raises on any write, so "recovery writes no bytes" (L-2) is
structural; its only mutations are the Job rows the double submitter and job
control change.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from sceneops_acquisition.reconciliation import (
    AcquisitionState,
    RecoveryActionKind,
    RecoveryOutcome,
    RecoveryPolicy,
    plan_recovery,
    reconcile_and_recover,
    reconcile_once,
    recover,
)
from sceneops_acquisition.registration import RegistrationDispatchError
from sceneops_acquisition.registration import RegisterRobotRunResponse
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
)
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_acquisition.registration_failures import (
    JOB_ABANDONED_ERROR_TYPE,
    REGISTRATION_ATTEMPT_BUDGET,
)
from tests.test_reconciliation import (
    ROOT,
    FakeFacts,
    _T0,
    add_job,
    manifest_uri,
    publish,
    reconcile,
    register,
    register_robot_run_execution_key,
)

S = AcquisitionState
THRESHOLD = timedelta(minutes=15)
NOW = _T0 + timedelta(hours=2)
LONG_AGO = NOW - timedelta(hours=1)  # well beyond THRESHOLD
JUST_NOW = NOW - timedelta(minutes=1)  # well within THRESHOLD


def _policy(**overrides) -> RecoveryPolicy:
    return RecoveryPolicy(stall_threshold=THRESHOLD, **overrides)


class FakeSubmitter:
    """``RobotRunRegistrationService`` over the in-memory Job list: ``force``
    skips only the reuse of a succeeded Job, a dispatch can be made to fail, and
    every call is kept."""

    def __init__(self, facts: FakeFacts, *, now: datetime = NOW) -> None:
        self.facts = facts
        self.now = now
        self.calls: list[tuple[str, bool]] = []
        self.dispatch_down = False
        self.raise_for: dict[str, Exception] = {}

    async def submit(self, manifest_uri: str, *, force: bool = False):
        self.calls.append((manifest_uri, force))
        if manifest_uri in self.raise_for:
            raise self.raise_for[manifest_uri]
        key = register_robot_run_execution_key(manifest_uri)
        reusable = {JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING}
        if not force:
            reusable.add(JobStatus.SUCCEEDED)
        for job in self.facts.jobs:
            if job.execution_key == key and job.status in reusable:
                return RegisterRobotRunResponse(job=job, execution=None)
        job = JobManifest(
            job_id=f"job-{len(self.facts.jobs):04d}",
            type=JobType.REGISTER_ROBOT_RUN,
            status=JobStatus.PENDING,
            params={"manifest_uri": manifest_uri},
            execution_key=key,
            created_at=self.now,
            queued_at=self.now,
        )
        self.facts.jobs.append(job)
        if self.dispatch_down:
            # Committed before dispatch, exactly like the real service: the Job
            # stays and only the broker call failed.
            raise RegistrationDispatchError(job) from ConnectionRefusedError(
                "broker down"
            )
        self.facts.jobs[-1] = job.model_copy(update={"status": JobStatus.QUEUED})
        return RegisterRobotRunResponse(
            job=self.facts.jobs[-1],
            execution=ExecutionDispatchResult(
                execution_id=f"exec-{job.job_id}",
                execution_backend=ExecutionBackend.CELERY,
                execution_kind=ExecutionKind.JOB_RUN,
                resource_id=job.job_id,
            ),
        )


class FakeJobControl:
    """``PostgresStalledJobControl`` over the in-memory Job list, with the same
    predicate as the conditional UPDATE: in flight AND inactive since the
    cutoff. ``lose`` makes a job look already changed by someone else."""

    def __init__(self, facts: FakeFacts) -> None:
        self.facts = facts
        self.lose: set[str] = set()
        self.abandon_calls: list[str] = []

    async def abandon(self, job_id, *, inactive_since, error):
        self.abandon_calls.append(job_id)
        for index, job in enumerate(self.facts.jobs):
            if job.job_id != job_id:
                continue
            last = max(
                stamp
                for stamp in (
                    job.created_at,
                    job.queued_at,
                    job.started_at,
                    job.heartbeat_at,
                )
                if stamp is not None
            )
            if (
                job_id in self.lose
                or job.status
                not in (JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING)
                or not last < inactive_since
            ):
                return None
            failed = job.model_copy(update={"status": JobStatus.FAILED, "error": error})
            self.facts.jobs[index] = failed
            return failed
        return None


@pytest.fixture()
def submitter(facts) -> FakeSubmitter:
    return FakeSubmitter(facts)


@pytest.fixture()
def control(facts) -> FakeJobControl:
    return FakeJobControl(facts)


async def recover_once(store, facts, submitter, control, *, now=NOW, **policy):
    return await reconcile_and_recover(
        artifact_store=store,
        root_uri=ROOT,
        registration_facts=facts.scope(),
        submitter=submitter,
        jobs=control,
        policy=_policy(**policy),
        now=now,
    )


def actions_of(report, run_id):
    return [a for a in report.actions if a.run_id == run_id]


def run_of(report, run_id):
    return next(run for run in report.runs if run.run_id == run_id)


# ── the policy ────────────────────────────────────────────────────────────────


def test_the_policy_rejects_values_that_would_disable_the_safety_margins():
    with pytest.raises(ValueError):
        RecoveryPolicy(stall_threshold=timedelta(0))
    with pytest.raises(ValueError):
        _policy(attempt_budget=0)
    with pytest.raises(ValueError):
        _policy(max_actions=0)


def test_the_default_budget_is_the_adr_budget():
    assert _policy().attempt_budget == REGISTRATION_ATTEMPT_BUDGET == 3


# ── registration_pending / transient failure ──────────────────────────────────


async def test_a_published_run_with_no_job_is_submitted(
    store, facts, submitter, control
):
    publish(store, "run-1")

    report = await recover_once(store, facts, submitter, control)

    assert report.mode == "apply"
    (action,) = actions_of(report, "run-1")
    assert action.kind == RecoveryActionKind.SUBMIT_REGISTRATION
    assert action.outcome == RecoveryOutcome.SUBMITTED
    assert action.job_id == facts.jobs[0].job_id
    assert submitter.calls == [(manifest_uri("run-1"), False)]
    # The report states what was observed before acting.
    assert run_of(report, "run-1").state == S.REGISTRATION_PENDING


async def test_a_transient_failure_is_retried_while_budget_remains(
    store, facts, submitter, control
):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.FAILED, error_type="ConnectionError")

    report = await recover_once(store, facts, submitter, control)

    (action,) = actions_of(report, "run-1")
    assert action.kind == RecoveryActionKind.RETRY_REGISTRATION
    assert action.outcome == RecoveryOutcome.SUBMITTED
    assert (action.attempts_used, action.attempt_budget) == (1, 3)
    assert submitter.calls == [(manifest_uri("run-1"), False)]


async def test_transient_failures_across_jobs_exhaust_the_budget(
    store, facts, submitter, control
):
    publish(store, "run-1")
    for _ in range(3):
        add_job(facts, "run-1", JobStatus.FAILED, error_type="ConnectionError")

    report = await recover_once(store, facts, submitter, control)

    run = run_of(report, "run-1")
    assert run.state == S.REGISTRATION_FAILED_PERMANENT
    assert "attempt_budget_exhausted" in run.reasons
    assert run.registration.failed_job_count == 3
    assert run.registration.attempt_budget == 3
    assert run.registration.attempts_remaining == 0
    assert submitter.calls == []
    assert report.actions == ()


async def test_a_permanent_failure_is_never_retried(store, facts, submitter, control):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.FAILED, error_type="RecordingVerificationError")

    report = await recover_once(store, facts, submitter, control)

    assert run_of(report, "run-1").state == S.REGISTRATION_FAILED_PERMANENT
    assert submitter.calls == []
    assert report.actions == ()


# ── states that are never touched ─────────────────────────────────────────────


async def test_registered_runs_are_not_touched_whatever_a_stale_job_says(
    store, facts, submitter, control
):
    checksum = publish(store, "run-1")
    register(facts, "run-1", checksum)
    add_job(facts, "run-1", JobStatus.RUNNING, created_at=LONG_AGO)

    report = await recover_once(store, facts, submitter, control)

    assert run_of(report, "run-1").state == S.REGISTERED
    assert submitter.calls == [] and control.abandon_calls == []
    assert facts.jobs[0].status == JobStatus.RUNNING


async def test_conflicts_and_integrity_incidents_are_never_repaired(
    store, facts, submitter, control
):
    publish(store, "run-conflict")
    register(facts, "run-conflict", "sha256:" + "0" * 64)
    publish(store, "run-orphan-artifacts")
    checksum = publish(store, "run-incident")
    register(facts, "run-incident", checksum)
    del store.objects[f"{ROOT}/run-incident/recording.mcap"]

    report = await recover_once(store, facts, submitter, control)

    assert run_of(report, "run-conflict").state == S.PERMANENT_CONFLICT
    assert run_of(report, "run-incident").state == S.INTEGRITY_INCIDENT
    # run-orphan-artifacts has no records: it is plain registration_pending.
    assert [a.run_id for a in report.actions] == ["run-orphan-artifacts"]


async def test_publication_states_are_not_acted_on(store, facts, submitter, control):
    store.objects[f"{ROOT}/run-1/recording.mcap"] = b"only the recording"
    publish(store, "run-2")
    del store.objects[f"{ROOT}/run-2/recording.mcap"]

    report = await recover_once(store, facts, submitter, control)

    assert {r.state for r in report.runs} == {S.PUBLICATION_INCOMPLETE}
    assert submitter.calls == [] and report.actions == ()


async def test_a_job_within_the_threshold_is_left_alone(
    store, facts, submitter, control
):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.RUNNING, created_at=JUST_NOW)
    add_job(facts, "run-1", JobStatus.QUEUED, created_at=JUST_NOW)

    report = await recover_once(store, facts, submitter, control)

    assert run_of(report, "run-1").state == S.REGISTRATION_ACTIVE
    assert report.actions == ()
    assert [j.status for j in facts.jobs] == [JobStatus.RUNNING, JobStatus.QUEUED]


async def test_a_heartbeat_keeps_an_old_job_from_stalling(
    store, facts, submitter, control
):
    publish(store, "run-1")
    add_job(
        facts,
        "run-1",
        JobStatus.RUNNING,
        created_at=LONG_AGO,
        started_at=LONG_AGO,
        heartbeat_at=JUST_NOW,
    )

    report = await recover_once(store, facts, submitter, control)

    assert run_of(report, "run-1").state == S.REGISTRATION_ACTIVE
    assert report.actions == ()


async def test_a_success_without_a_robot_run_is_reported_not_retried(
    store, facts, submitter, control
):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.SUCCEEDED)
    publish(store, "run-2")
    add_job(facts, "run-2", JobStatus.CANCELLED)

    report = await recover_once(store, facts, submitter, control)

    skipped = {a.run_id: a for a in report.actions}
    assert {a.outcome for a in skipped.values()} == {RecoveryOutcome.SKIPPED}
    assert skipped["run-1"].reason == "succeeded_job_without_robot_run"
    assert skipped["run-2"].reason == "latest_job_cancelled"
    assert submitter.calls == []


# ── stalled jobs: abandon, then replace ───────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "started"),
    [
        (JobStatus.PENDING, None),
        (JobStatus.QUEUED, None),
        (JobStatus.RUNNING, LONG_AGO),
    ],
)
async def test_a_stalled_job_is_abandoned_then_replaced_by_a_forced_job(
    store, facts, submitter, control, status, started
):
    publish(store, "run-1")
    stalled = add_job(facts, "run-1", status, created_at=LONG_AGO, started_at=started)

    report = await recover_once(store, facts, submitter, control)

    (action,) = actions_of(report, "run-1")
    assert run_of(report, "run-1").state == S.REGISTRATION_STALLED_CANDIDATE
    assert action.kind == RecoveryActionKind.REPLACE_STALLED_JOB
    assert action.outcome == RecoveryOutcome.SUBMITTED
    assert action.abandoned_job_ids == (stalled.job_id,)
    old, new = facts.jobs
    assert old.status == JobStatus.FAILED
    assert old.error is not None and old.error.type == JOB_ABANDONED_ERROR_TYPE
    assert new.job_id == action.job_id
    assert new.status == JobStatus.QUEUED
    # Same logical registration: the replacement carries the same key.
    assert new.execution_key == old.execution_key
    # Abandon happened before the replacement was created, and was forced.
    assert submitter.calls == [(manifest_uri("run-1"), True)]


async def test_abandoned_jobs_consume_the_logical_registration_budget(
    store, facts, submitter, control
):
    """A job stalls, is replaced, the replacement stalls too, and so on: three
    attempts in total, then recovery stops. New Job rows never reset the count."""
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.RUNNING, created_at=_T0, started_at=_T0)

    clock = _T0
    created = []
    for round_number in range(8):
        clock = clock + timedelta(hours=1)
        submitter.now = clock
        report = await recover_once(store, facts, submitter, control, now=clock)
        created.append(len(facts.jobs))
        run = run_of(report, "run-1")
        if run.registration.failed_job_count >= 3:
            break

    final = await recover_once(
        store, facts, submitter, control, now=clock + timedelta(hours=1)
    )
    run = run_of(final, "run-1")
    assert run.state == S.REGISTRATION_FAILED_PERMANENT
    assert "attempt_budget_exhausted" in run.reasons
    # Exactly the budget's worth of Jobs exist (1 original + 2 replacements);
    # the third abandon spent the last attempt and created nothing.
    assert len(facts.jobs) == 3
    assert [j.status for j in facts.jobs] == [JobStatus.FAILED] * 3
    assert all(
        j.error is not None and j.error.type == JOB_ABANDONED_ERROR_TYPE
        for j in facts.jobs
    )
    assert run.registration.abandoned_job_count == 3
    assert run.registration.attempts_remaining == 0
    submits_before = len(submitter.calls)
    await recover_once(store, facts, submitter, control, now=clock + timedelta(hours=2))
    assert len(submitter.calls) == submits_before  # nothing more, ever


async def test_the_abandon_that_spends_the_budget_creates_no_replacement(
    store, facts, submitter, control
):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.FAILED, error_type="ConnectionError")
    add_job(facts, "run-1", JobStatus.FAILED, error_type=JOB_ABANDONED_ERROR_TYPE)
    last = add_job(facts, "run-1", JobStatus.RUNNING, created_at=LONG_AGO)

    report = await recover_once(store, facts, submitter, control)

    (action,) = actions_of(report, "run-1")
    assert action.kind == RecoveryActionKind.ABANDON_STALLED_JOB
    assert action.outcome == RecoveryOutcome.ABANDONED
    assert action.abandoned_job_ids == (last.job_id,)
    assert submitter.calls == []
    assert len(facts.jobs) == 3 and facts.jobs[-1].status == JobStatus.FAILED


async def test_a_stalled_job_beyond_the_budget_is_left_to_the_operator(
    store, facts, submitter, control
):
    """An operator-forced Job after exhaustion is outside the automatic budget:
    the reconciler neither abandons nor replaces it."""
    publish(store, "run-1")
    for _ in range(3):
        add_job(facts, "run-1", JobStatus.FAILED, error_type="ConnectionError")
    forced = add_job(facts, "run-1", JobStatus.RUNNING, created_at=LONG_AGO)

    report = await recover_once(store, facts, submitter, control)

    (action,) = actions_of(report, "run-1")
    assert action.outcome == RecoveryOutcome.SKIPPED
    assert action.reason == "attempt_budget_exhausted"
    assert control.abandon_calls == [] and submitter.calls == []
    assert facts.jobs[-1].job_id == forced.job_id
    assert facts.jobs[-1].status == JobStatus.RUNNING


async def test_losing_the_abandon_race_creates_no_replacement(
    store, facts, submitter, control
):
    publish(store, "run-1")
    stalled = add_job(facts, "run-1", JobStatus.RUNNING, created_at=LONG_AGO)
    control.lose.add(stalled.job_id)  # another reconciler won it first

    report = await recover_once(store, facts, submitter, control)

    (action,) = actions_of(report, "run-1")
    assert action.outcome == RecoveryOutcome.LOST_RACE
    assert action.abandoned_job_ids == ()
    assert submitter.calls == []
    assert len(facts.jobs) == 1


async def test_two_concurrent_passes_replace_one_stalled_job_once(
    store, facts, submitter, control
):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.RUNNING, created_at=LONG_AGO)

    first = await recover_once(store, facts, submitter, control)
    # The second pass observed the same stalled Job (as concurrent passes do),
    # but its conditional abandon finds the row already FAILED.
    stale_view = first
    second_actions, _ = await recover(
        stale_view,
        submitter=submitter,
        jobs=control,
        policy=_policy(),
        now=NOW,
    )

    assert [a.outcome for a in second_actions] == [RecoveryOutcome.LOST_RACE]
    assert len(facts.jobs) == 2  # the original (failed) and one replacement
    assert len(submitter.calls) == 1


# ── dispatch failure ──────────────────────────────────────────────────────────


async def test_a_dispatch_failure_preserves_the_job_and_a_later_pass_recovers_it(
    store, facts, submitter, control
):
    publish(store, "run-1")
    submitter.dispatch_down = True

    first = await recover_once(store, facts, submitter, control)

    (action,) = actions_of(first, "run-1")
    assert action.outcome == RecoveryOutcome.DISPATCH_FAILED
    assert action.reason == "dispatch_failed"
    assert action.error is not None and "broker down" in action.error
    # The Job is durable and honestly PENDING; nothing was rolled back or faked.
    assert [j.status for j in facts.jobs] == [JobStatus.PENDING]
    assert action.job_id == facts.jobs[0].job_id

    # The broker is back, but the Job is still within the stall threshold.
    submitter.dispatch_down = False
    submitter.now = NOW
    second = await recover_once(store, facts, submitter, control, now=NOW)
    assert run_of(second, "run-1").state == S.REGISTRATION_ACTIVE
    assert second.actions == ()

    # Beyond the threshold the lifecycle contract takes over: abandon + replace.
    later = NOW + THRESHOLD + timedelta(minutes=1)
    submitter.now = later
    third = await recover_once(store, facts, submitter, control, now=later)
    (recovered,) = actions_of(third, "run-1")
    assert recovered.kind == RecoveryActionKind.REPLACE_STALLED_JOB
    assert recovered.outcome == RecoveryOutcome.SUBMITTED
    assert [j.status for j in facts.jobs] == [JobStatus.FAILED, JobStatus.QUEUED]


async def test_classification_does_not_need_the_broker(
    store, facts, submitter, control
):
    """The observe-only pass has no submitter at all: a broker outage cannot
    stop classification."""
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.QUEUED, created_at=LONG_AGO)

    report = await reconcile_once(
        artifact_store=store,
        root_uri=ROOT,
        registration_facts=facts.scope(),
        policy=_policy().classification(),
        now=NOW,
    )

    assert run_of(report, "run-1").state == S.REGISTRATION_STALLED_CANDIDATE
    assert report.mode == "observe" and report.actions == ()


# ── bounds and isolation ──────────────────────────────────────────────────────


async def test_one_pass_performs_at_most_max_actions(store, facts, submitter, control):
    for index in range(5):
        publish(store, f"run-{index}")

    report = await recover_once(store, facts, submitter, control, max_actions=2)

    assert len(report.actions) == 2
    assert report.actions_deferred == 3
    assert len(submitter.calls) == 2

    # Stateless: the next pass picks up the rest, never repeating the done ones.
    second = await recover_once(store, facts, submitter, control, max_actions=2)
    assert len(second.actions) == 2 and second.actions_deferred == 1


async def test_one_failing_action_does_not_block_the_others(
    store, facts, submitter, control
):
    publish(store, "run-1")
    publish(store, "run-2")
    submitter.raise_for[manifest_uri("run-1")] = ValueError("bad params")

    report = await recover_once(store, facts, submitter, control)

    by_run = {a.run_id: a for a in report.actions}
    assert by_run["run-1"].outcome == RecoveryOutcome.ERROR
    assert by_run["run-1"].error is not None and "bad params" in by_run["run-1"].error
    assert by_run["run-2"].outcome == RecoveryOutcome.SUBMITTED


async def test_recovery_is_idempotent_across_passes(store, facts, submitter, control):
    publish(store, "run-1")

    await recover_once(store, facts, submitter, control)
    second = await recover_once(store, facts, submitter, control)

    # The Job from the first pass is in flight: nothing else is submitted.
    assert run_of(second, "run-1").state == S.REGISTRATION_ACTIVE
    assert second.actions == ()
    assert len(facts.jobs) == 1


# ── the pure planner ──────────────────────────────────────────────────────────


async def test_the_planner_acts_on_no_state_but_the_three_eligible_ones(store, facts):
    publish(store, "run-pending")
    publish(store, "run-transient")
    add_job(facts, "run-transient", JobStatus.FAILED, error_type="OSError")
    publish(store, "run-stalled")
    add_job(facts, "run-stalled", JobStatus.QUEUED, created_at=LONG_AGO)
    publish(store, "run-active")
    add_job(facts, "run-active", JobStatus.QUEUED, created_at=JUST_NOW)
    publish(store, "run-permanent")
    add_job(facts, "run-permanent", JobStatus.FAILED, error_type="ValidationError")
    checksum = publish(store, "run-done")
    register(facts, "run-done", checksum)

    report = await reconcile_once(
        artifact_store=store,
        root_uri=ROOT,
        registration_facts=facts.scope(),
        policy=_policy().classification(),
        now=NOW,
    )

    planned = {run.run_id: plan_recovery(run, _policy()) for run in report.runs}
    assert planned == {
        "run-pending": (RecoveryActionKind.SUBMIT_REGISTRATION, None),
        "run-transient": (RecoveryActionKind.RETRY_REGISTRATION, None),
        "run-stalled": (RecoveryActionKind.REPLACE_STALLED_JOB, None),
        "run-active": None,
        "run-permanent": None,
        "run-done": None,
    }


# ── the report carries the evidence ───────────────────────────────────────────


async def test_the_report_shows_attempt_evidence_and_the_policy(
    store, facts, submitter, control
):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.FAILED, error_type=JOB_ABANDONED_ERROR_TYPE)
    add_job(facts, "run-1", JobStatus.FAILED, error_type="OSError")

    report = await recover_once(store, facts, submitter, control)

    registration = run_of(report, "run-1").registration
    assert registration.failed_job_count == 2
    assert registration.abandoned_job_count == 1
    assert registration.attempt_budget == 3
    assert registration.attempts_remaining == 1
    assert report.policy is not None
    assert report.policy.stall_threshold_seconds == THRESHOLD.total_seconds()
    assert report.policy.attempt_budget == 3
    assert report.policy.max_actions == 100
    dumped = report.to_json_dict()
    assert dumped["mode"] == "apply"
    assert dumped["actions"][0]["kind"] == "retry_registration"


async def test_an_observe_only_report_has_no_recovery_content(store, facts):
    publish(store, "run-1")

    report = await reconcile(store, facts)

    assert report.mode == "observe"
    assert report.actions == () and report.actions_deferred == 0
    assert report.policy is None
