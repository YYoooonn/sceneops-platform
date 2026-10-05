"""Structured records of ``reconcile --once --apply`` (ADR-008 §7.3, Phase 12.6).

Each action is one ``acquisition_recovery {json}`` record carrying what was
observed before, what was attempted, what happened, what was observed after and
the retry-budget evidence; each pass ends with one ``acquisition_recovery_pass``
record. The records are evidence only: these tests also pin that they are
consistent with the report and that a failure to make the second observation
cannot fail a pass whose actions already happened.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.domains.robots.reconciliation import AcquisitionState
from sceneops_core.jobs.schemas import JobStatus
from sceneops_core.robots.registration_failures import JOB_ABANDONED_ERROR_TYPE
from tests.robots.test_reconciliation import MemoryStore, add_job, publish, register
from tests.robots.test_reconciliation_recovery import (
    LONG_AGO,
    NOW,
    FakeJobControl,
    FakeSubmitter,
    recover_once,
)

LOGGER = "sceneops.acquisition"


@pytest.fixture()
def submitter(facts) -> FakeSubmitter:
    return FakeSubmitter(facts)


@pytest.fixture()
def control(facts) -> FakeJobControl:
    return FakeJobControl(facts)


def records(caplog, event: str = "acquisition_recovery") -> list[dict]:
    marker = f"{event} {{"
    return [
        json.loads(message[message.index(marker) + len(event) + 1 :])
        for message in caplog.messages
        if marker in message
    ]


async def run_pass(store, facts, submitter, control, caplog, **kwargs):
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        report = await recover_once(store, facts, submitter, control, **kwargs)
    return report


async def test_a_submission_logs_the_state_before_and_after(
    store, facts, submitter, control, caplog
):
    publish(store, "run-1")

    report = await run_pass(store, facts, submitter, control, caplog)

    (record,) = records(caplog)
    (action,) = report.actions
    assert record["component"] == "reconcile" and record["stage"] == "register"
    assert (record["action"], record["outcome"]) == ("submit_registration", "submitted")
    assert record["run_id"] == "run-1" and record["job_id"] == action.job_id
    assert record["state_before"] == AcquisitionState.REGISTRATION_PENDING.value
    assert record["state_after"] == AcquisitionState.REGISTRATION_ACTIVE.value
    assert (record["attempts_used"], record["attempt_budget"]) == (0, 3)
    assert record["attempts_remaining"] == 3
    assert record["duration_ms"] >= 0 and "ts" in record
    # The report carries the same evidence the record does.
    assert action.state_before == AcquisitionState.REGISTRATION_PENDING
    assert action.state_after == AcquisitionState.REGISTRATION_ACTIVE


async def test_a_retry_logs_the_failure_class_and_the_budget_left(
    store, facts, submitter, control, caplog
):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.FAILED, error_type="OSError")

    await run_pass(store, facts, submitter, control, caplog)

    (record,) = records(caplog)
    assert record["action"] == "retry_registration"
    assert record["state_before"] == "registration_failed_transient"
    assert record["state_after"] == "registration_active"
    assert record["failure_class"] == "transient" and record["error_type"] == "OSError"
    assert (record["attempts_used"], record["attempts_remaining"]) == (1, 2)
    assert record["attempt"] == 2  # the second attempt of the logical registration


async def test_a_replacement_logs_the_abandoned_job(
    store, facts, submitter, control, caplog
):
    publish(store, "run-1")
    stalled = add_job(facts, "run-1", JobStatus.RUNNING, created_at=LONG_AGO)

    await run_pass(store, facts, submitter, control, caplog)

    (record,) = records(caplog)
    assert record["action"] == "replace_stalled_job"
    assert record["abandoned_job_ids"] == [stalled.job_id]
    assert record["state_before"] == "registration_stalled_candidate"
    assert record["state_after"] == "registration_active"


async def test_the_abandon_that_spends_the_budget_logs_the_failed_state_after(
    store, facts, submitter, control, caplog
):
    publish(store, "run-1")
    for _ in range(2):
        add_job(
            facts,
            "run-1",
            JobStatus.FAILED,
            created_at=LONG_AGO,
            error_type=JOB_ABANDONED_ERROR_TYPE,
        )
    add_job(facts, "run-1", JobStatus.RUNNING, created_at=LONG_AGO)

    await run_pass(store, facts, submitter, control, caplog)

    (record,) = records(caplog)
    assert (record["action"], record["outcome"]) == (
        "abandon_stalled_job",
        "abandoned",
    )
    assert record["reason"] == "attempt_budget_exhausted"
    assert record["state_before"] == "registration_stalled_candidate"
    assert record["state_after"] == "registration_failed_permanent"
    assert record["attempts_used"] == 2 and record["attempts_remaining"] == 1
    assert record["failure_class"] == "transient"  # the newest failure was abandoned


async def test_a_dispatch_failure_is_logged_as_such_and_the_job_is_kept(
    store, facts, submitter, control, caplog
):
    publish(store, "run-1")
    submitter.dispatch_down = True

    await run_pass(store, facts, submitter, control, caplog)

    (record,) = records(caplog)
    assert record["outcome"] == "dispatch_failed" and record["job_id"]
    assert record["error_type"] == "ConnectionRefusedError"
    assert record["state_after"] == "registration_active"  # the Job is preserved


async def test_a_skip_is_logged_without_a_state_after(
    store, facts, submitter, control, caplog
):
    publish(store, "run-1")
    add_job(facts, "run-1", JobStatus.SUCCEEDED)  # a success without a RobotRun

    await run_pass(store, facts, submitter, control, caplog)

    (record,) = records(caplog)
    assert record["outcome"] == "skipped"
    assert record["reason"] == "succeeded_job_without_robot_run"
    assert record["state_before"] == "registration_pending"
    assert "state_after" not in record and record["duration_ms"] == 0


async def test_a_pass_with_nothing_to_do_logs_only_its_summary(
    store, facts, submitter, control, caplog
):
    register(facts, "run-1", publish(store, "run-1"))

    await run_pass(store, facts, submitter, control, caplog)

    assert records(caplog) == []
    (summary,) = records(caplog, "acquisition_recovery_pass")
    assert summary["component"] == "reconcile" and summary["mode"] == "apply"
    assert summary["runs"] == 1 and summary["states"] == {"registered": 1}
    assert "actions" in summary and summary["actions"] == {}
    assert summary["stall_threshold_seconds"] == 900.0
    assert summary["attempt_budget"] == 3 and summary["actions_deferred"] == 0


async def test_the_pass_summary_counts_actions_by_kind_and_outcome(
    store, facts, submitter, control, caplog
):
    publish(store, "a")
    publish(store, "b")
    publish(store, "c")
    add_job(facts, "c", JobStatus.FAILED, error_type="OSError")

    await run_pass(store, facts, submitter, control, caplog)

    (summary,) = records(caplog, "acquisition_recovery_pass")
    assert summary["actions"] == {
        "retry_registration:submitted": 1,
        "submit_registration:submitted": 2,
    }
    assert len(records(caplog)) == 3


class _FailsOnTheSecondListing(MemoryStore):
    """The pass's first observation works; the post-action one does not."""

    def __init__(self, inner: MemoryStore) -> None:
        super().__init__()
        self.objects, self.modified = inner.objects, inner.modified
        self.listings = 0

    async def list_objects(self, uri):
        self.listings += 1
        if self.listings > 1:
            raise ConnectionError("store went away after the actions")
        return await super().list_objects(uri)


async def test_a_failed_second_observation_does_not_fail_the_pass(
    store, facts, control, caplog
):
    publish(store, "run-1")
    flaky = _FailsOnTheSecondListing(store)
    submitter = FakeSubmitter(facts, now=NOW)

    report = await run_pass(flaky, facts, submitter, control, caplog)

    (action,) = report.actions
    assert action.outcome.value == "submitted"  # the action happened and is reported
    assert action.state_after is None
    (record,) = records(caplog)
    assert "state_after" not in record and record["state_before"]
    assert flaky.listings == 2
    assert len(facts.jobs) == 1


def test_publish_pending_state_names_are_the_reconcilers():
    """The Publisher cannot import the API, so it names the two states it acts
    on as strings; they must stay the reconciler's vocabulary."""
    from sceneops_integrations.recording.publish_pending import (
        STATE_PUBLICATION_INCOMPLETE,
        STATE_PUBLISH_PENDING,
    )

    assert STATE_PUBLISH_PENDING == AcquisitionState.PUBLISH_PENDING.value
    assert STATE_PUBLICATION_INCOMPLETE == AcquisitionState.PUBLICATION_INCOMPLETE.value
