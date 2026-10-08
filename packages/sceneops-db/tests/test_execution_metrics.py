"""The execution-health queries on real PostgreSQL.

Window metrics are asserted exactly over rows placed in a window of their own
(January 2001), which no other test writes into; current-state metrics are
database-wide, so they are asserted as bounds the test's own rows imply.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from sceneops_db.postgres.execution_metrics import PostgresExecutionMetrics

T0 = datetime(2001, 1, 1, tzinfo=UTC)
WINDOW = {"start": T0, "end": T0 + timedelta(hours=1)}


async def _job(session, job_id, *, status="succeeded", wait, run, resent=False,
               generation=1, error=None, type="profile_scene", offset=0.0):  # fmt: skip
    enqueued = T0 + timedelta(seconds=60 + offset)
    locked = enqueued + timedelta(seconds=wait)
    await session.execute(
        text(
            """
            INSERT INTO jobs (job_id, type, status, lease_generation, error,
                              created_at, updated_at, enqueued_at, queued_at,
                              locked_at, started_at, finished_at)
            VALUES (:id, :type, :status, :gen, CAST(:error AS jsonb), :created,
                    :finished, :enqueued, :queued, :locked, :locked, :finished)
            """
        ),
        {
            "id": job_id,
            "type": type,
            "status": status,
            "gen": generation,
            "error": None if error is None else f'{{"type": "{error}"}}',
            "created": enqueued - timedelta(seconds=1),
            "enqueued": enqueued,
            # A resend moves the last dispatch to just before the claim.
            "queued": locked - timedelta(seconds=0.1) if resent else enqueued,
            "locked": locked,
            "finished": locked + timedelta(seconds=run),
        },
    )


async def _event(session, event_id, job_id, type, level, *, at, attempt=None,
                 data="{}", error=None):  # fmt: skip
    await session.execute(
        text(
            """
            INSERT INTO job_events (event_id, job_id, type, level, attempt, data,
                                    error, created_at)
            VALUES (:id, :job, :type, :level, :attempt, CAST(:data AS jsonb),
                    CAST(:error AS jsonb), :at)
            """
        ),
        {
            "id": event_id,
            "job": job_id,
            "type": type,
            "level": level,
            "attempt": attempt,
            "data": data,
            "error": error,
            "at": T0 + timedelta(seconds=at),
        },
    )


async def test_job_window_measures_the_wait_from_enqueued_at_not_the_last_send(
    db_session, unique_id
):
    for wait, kw in ((1.0, {}), (2.0, {"resent": True}), (30.0, {"resent": True})):
        await _job(db_session, unique_id("mjob"), wait=wait, run=10.0, **kw)
    await _job(
        db_session, unique_id("mjob"), status="failed", wait=3.0, run=10.0,
        error="ValueError", generation=2,
    )  # fmt: skip

    (row,) = [
        r
        for r in await PostgresExecutionMetrics(db_session).jobs(**WINDOW)
        if r.job_type == "profile_scene"
    ]

    assert (row.succeeded, row.failed) == (3, 1)
    assert row.failure_rate == pytest.approx(0.25)
    assert row.throughput_per_min == pytest.approx(4 / 60)
    assert row.queue_wait.count == 4
    assert row.queue_wait.p50 == pytest.approx(2.5)
    assert row.queue_wait.max == pytest.approx(30.0)
    assert row.execution.p50 == pytest.approx(10.0)
    assert row.end_to_end.max == pytest.approx(41.0)
    assert row.claimed_more_than_once == 1
    assert row.failures_by_error == {"ValueError": 1}


async def test_recovery_window_counts_actions_and_attributes_reclaims_to_workers(
    db_session, unique_id
):
    reclaimed, failed, resent = (unique_id("mjob") for _ in range(3))
    for job_id in (reclaimed, failed, resent):
        await _job(db_session, job_id, wait=1.0, run=1.0)
    await _event(db_session, unique_id("ev"), reclaimed, "locked", "info", at=100,
                 attempt=1, data='{"worker_node": "jobs-b@host-1"}')  # fmt: skip
    await _event(db_session, unique_id("ev"), reclaimed, "queued", "warning", at=170,
                 data='{"expired_generation": 1}')  # fmt: skip
    await _event(db_session, unique_id("ev"), failed, "locked", "info", at=100,
                 attempt=3, data='{"worker_node": "jobs-b@host-1"}')  # fmt: skip
    await _event(db_session, unique_id("ev"), failed, "failed", "error", at=180,
                 data='{"expired_generation": 3}',
                 error='{"type": "JobLeaseExpired"}')  # fmt: skip
    await _event(db_session, unique_id("ev"), resent, "queued", "warning", at=190,
                 data='{"previous_queued_at": "2001-01-01T00:01:00+00:00"}')  # fmt: skip

    recovery = await PostgresExecutionMetrics(db_session).recovery(**WINDOW)

    assert (recovery.lease_requeues, recovery.lease_failures, recovery.job_resends) == (
        1,
        1,
        1,
    )
    assert recovery.lease_reclaims_by_worker == {"jobs-b@host-1": 2}


async def test_backlog_ages_a_queued_job_from_when_it_entered_the_queue(
    db_session, unique_id
):
    await db_session.execute(
        text(
            """
            INSERT INTO jobs (job_id, type, status, enqueued_at, queued_at)
            VALUES (:id, 'curate_episodes', 'queued',
                    now() - interval '1 hour', now())
            """
        ),
        {"id": unique_id("mjob")},
    )

    (row,) = [
        b
        for b in await PostgresExecutionMetrics(db_session).backlog()
        if b.job_type == "curate_episodes"
    ]

    assert row.queued >= 1
    assert row.oldest_queued_age_s >= 3600


async def test_a_running_run_over_a_finished_job_waits_on_an_advance(
    db_session, unique_id
):
    run_id, job_id = unique_id("mrun"), unique_id("mjob")
    await _job(db_session, job_id, wait=1.0, run=1.0)
    await db_session.execute(
        text(
            """
            INSERT INTO pipeline_runs (pipeline_run_id, type, status, created_at,
                                       updated_at, started_at)
            VALUES (:r, 'episode_learning_data_building', 'running', :t, :t, :t);
            """
        ),
        {"r": run_id, "t": T0},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO pipeline_task_runs (pipeline_task_run_id, pipeline_run_id,
                   pipeline_task_id, pipeline_task_name, task_order, status,
                   job_type, job_id, started_at)
            VALUES (:t, :r, 'align_episode', 'align', 1, 'running',
                    'align_episode', :j, :at)
            """
        ),
        {"t": unique_id("mtask"), "r": run_id, "j": job_id, "at": T0},
    )

    waiting = {
        w.reason: w
        for w in await PostgresExecutionMetrics(db_session).pipelines_waiting()
    }

    advance = waiting["job_finished_awaiting_advance"]
    assert advance.runs >= 1
    # The Job finished in 2001: the run has waited on its advance since then.
    assert advance.oldest_wait_s > 365 * 86400


async def test_pipeline_stages_split_a_finished_run_into_wait_run_and_handoff(
    db_session, unique_id
):
    run_id, job_id = unique_id("mrun"), unique_id("mjob")
    await _job(db_session, job_id, wait=4.0, run=6.0, type="align_episode")
    job_finished = T0 + timedelta(seconds=70)
    await db_session.execute(
        text(
            """
            INSERT INTO pipeline_runs (pipeline_run_id, type, status, created_at,
                                       updated_at, started_at, finished_at)
            VALUES (:r, 'episode_learning_data_building', 'succeeded', :t0, :end,
                    :t0, :end)
            """
        ),
        {"r": run_id, "t0": T0, "end": job_finished + timedelta(seconds=2)},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO pipeline_task_runs (pipeline_task_run_id, pipeline_run_id,
                   pipeline_task_id, pipeline_task_name, task_order, status,
                   job_type, job_id, started_at, finished_at)
            VALUES (:t, :r, 'align_episode', 'align', 1, 'succeeded',
                    'align_episode', :j, :t0, :settled)
            """
        ),
        {
            "t": unique_id("mtask"),
            "r": run_id,
            "j": job_id,
            "t0": T0,
            "settled": job_finished + timedelta(seconds=0.5),
        },
    )

    metrics = PostgresExecutionMetrics(db_session)
    (stage,) = await metrics.pipeline_stages(**WINDOW)
    (run,) = await metrics.pipelines(**WINDOW)

    assert stage.queue_wait.p50 == pytest.approx(4.0)
    assert stage.execution.p50 == pytest.approx(6.0)
    assert stage.handoff.p50 == pytest.approx(0.5)
    assert (run.succeeded, run.end_to_end.p50) == (1, pytest.approx(72.0))
