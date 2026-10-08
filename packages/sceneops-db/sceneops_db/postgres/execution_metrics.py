"""Execution health derived from durable state: read-only SQL over jobs,
job_events, pipeline_runs and pipeline_task_runs.

Two kinds of reading, with different costs:

* **Current state** (backlog, leases, active runs) reads only in-flight rows
  through the status indexes, so its cost follows the amount of work in flight,
  not the amount of history.
* **Window** metrics (throughput, latency percentiles, failures, recovery) read
  the rows that finished, or the events recorded, inside ``[start, end)``,
  through the ``finished_at`` / ``created_at`` indexes: their cost follows the
  window.

Definitions (per Job; a window aggregates the Jobs that finished in it):

    queue wait     locked_at - enqueued_at     the wait the finishing claim ended
    execution      finished_at - locked_at     the finishing claim's run
    end to end     finished_at - created_at    creation to terminal state, lost
                                               claims and every wait included
    oldest queued  now - min(enqueued_at)      over QUEUED Jobs
    heartbeat age  now - min(heartbeat_at)     over RUNNING Jobs; a renewal every
                                               third of the lease keeps it below that

``enqueued_at``, not ``queued_at``: execution recovery moves ``queued_at`` on
every resend, so a wait measured from it never exceeds the resend threshold.
A Job reclaimed after a lost worker is measured by its last claim; the lost time
shows in end to end and in ``claimed_more_than_once``.

Every duration is in seconds and is computed by PostgreSQL. Timestamps written
by a worker use the worker's clock and those written by a conditional UPDATE use
PostgreSQL's; on one host they agree, across hosts a difference is clock skew.

Nothing here writes, and nothing here is a contract other code may act on: the
numbers describe the system for an operator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_PERCENTILES = "ARRAY[0.5, 0.95, 0.99]"


@dataclass(frozen=True)
class Latency:
    count: int
    p50: float | None
    p95: float | None
    p99: float | None
    max: float | None

    @classmethod
    def of(cls, count: int, percentiles: list[float] | None, maximum: float | None):
        p50, p95, p99 = percentiles or (None, None, None)
        return cls(count=count, p50=p50, p95=p95, p99=p99, max=maximum)


@dataclass(frozen=True)
class JobTypeBacklog:
    job_type: str
    queued: int
    oldest_queued_age_s: float | None
    running: int
    lease_expired: int
    oldest_heartbeat_age_s: float | None


@dataclass(frozen=True)
class JobTypeWindow:
    job_type: str
    succeeded: int
    failed: int
    failure_rate: float | None
    throughput_per_min: float
    queue_wait: Latency
    execution: Latency
    end_to_end: Latency
    claimed_more_than_once: int
    failures_by_error: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RecoveryWindow:
    lease_requeues: int
    lease_failures: int
    job_resends: int
    # Reclaims (requeues and budget failures) by the worker process that held the
    # expired claim (``worker_node`` of that claim's LOCKED event; "unknown" when
    # it names none).
    lease_reclaims_by_worker: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineWaiting:
    reason: str
    runs: int
    oldest_wait_s: float | None


@dataclass(frozen=True)
class PipelineTypeWindow:
    pipeline_type: str
    succeeded: int
    failed: int
    blocked: int
    end_to_end: Latency


@dataclass(frozen=True)
class PipelineStage:
    pipeline_type: str
    pipeline_task_id: str
    task_order: int
    queue_wait: Latency
    execution: Latency
    handoff: Latency


@dataclass(frozen=True)
class ExecutionSnapshot:
    measured_at: datetime
    window_start: datetime
    window_end: datetime
    backlog: list[JobTypeBacklog]
    jobs: list[JobTypeWindow]
    recovery: RecoveryWindow
    pipelines_waiting: list[PipelineWaiting]
    pipelines: list[PipelineTypeWindow]
    pipeline_stages: list[PipelineStage]


# ── current state ────────────────────────────────────────────────────────────

# In-flight Jobs only: the status predicate is served by the status indexes.
_BACKLOG = text(
    """
    SELECT type,
           count(*) FILTER (WHERE status = 'queued') AS queued,
           extract(epoch FROM now() - min(enqueued_at) FILTER (WHERE status = 'queued'))::float8,
           count(*) FILTER (WHERE status = 'running') AS running,
           count(*) FILTER (WHERE status = 'running' AND lease_expires_at < now()),
           extract(epoch FROM now() - min(heartbeat_at) FILTER (WHERE status = 'running'))::float8
      FROM jobs
     WHERE status IN ('queued', 'running')
     GROUP BY type
     ORDER BY type
    """
)

# What each active run waits on. After every committed orchestration step a
# RUNNING run waits on exactly one RUNNING task, whose Job is in flight or
# terminal (then the run waits on an ``advance``).
_PIPELINES_WAITING = text(
    """
    WITH waiting AS (
        SELECT CASE
                 WHEN r.status = 'queued' THEN 'run_queued'
                 WHEN j.job_id IS NULL THEN 'no_task_job'
                 WHEN j.status = 'queued' THEN 'job_queued'
                 WHEN j.status = 'pending' THEN 'job_pending'
                 WHEN j.status = 'running' THEN 'job_running'
                 ELSE 'job_finished_awaiting_advance'
               END AS reason,
               CASE
                 WHEN r.status = 'queued' THEN r.updated_at
                 WHEN j.status = 'queued' THEN j.enqueued_at
                 WHEN j.status = 'running' THEN j.locked_at
                 WHEN j.job_id IS NOT NULL THEN coalesce(j.finished_at, j.updated_at)
                 ELSE r.updated_at
               END AS since
          FROM pipeline_runs r
          LEFT JOIN pipeline_task_runs t
                 ON t.pipeline_run_id = r.pipeline_run_id AND t.status = 'running'
          LEFT JOIN jobs j ON j.job_id = t.job_id
         WHERE r.status IN ('queued', 'running')
    )
    SELECT reason, count(*), extract(epoch FROM now() - min(since))::float8
      FROM waiting
     GROUP BY reason
     ORDER BY reason
    """
)

# ── window ───────────────────────────────────────────────────────────────────

_JOBS_WINDOW = text(
    f"""
    WITH finished AS (
        SELECT type, status, lease_generation, error,
               extract(epoch FROM locked_at - enqueued_at)::float8  AS queue_wait,
               extract(epoch FROM finished_at - locked_at)::float8  AS execution,
               extract(epoch FROM finished_at - created_at)::float8 AS end_to_end
          FROM jobs
         WHERE finished_at >= :start AND finished_at < :end
           AND status IN ('succeeded', 'failed')
    )
    SELECT type,
           count(*) FILTER (WHERE status = 'succeeded'),
           count(*) FILTER (WHERE status = 'failed'),
           count(queue_wait),
           percentile_cont({_PERCENTILES}) WITHIN GROUP (ORDER BY queue_wait),
           max(queue_wait),
           count(execution),
           percentile_cont({_PERCENTILES}) WITHIN GROUP (ORDER BY execution),
           max(execution),
           count(end_to_end),
           percentile_cont({_PERCENTILES}) WITHIN GROUP (ORDER BY end_to_end),
           max(end_to_end),
           count(*) FILTER (WHERE lease_generation > 1),
           (SELECT coalesce(jsonb_object_agg(k, n), '{{}}'::jsonb)
              FROM (SELECT coalesce(f2.error->>'type', 'unknown') AS k, count(*) AS n
                      FROM finished f2
                     WHERE f2.type = finished.type AND f2.status = 'failed'
                     GROUP BY 1) e)
      FROM finished
     GROUP BY type
     ORDER BY type
    """
)

# Recovery actions are recorded as JobEvents by the recovery pass itself.
_RECOVERY_WINDOW = text(
    """
    SELECT count(*) FILTER (WHERE type = 'queued' AND data ? 'expired_generation'),
           count(*) FILTER (WHERE type = 'failed' AND error->>'type' = 'JobLeaseExpired'),
           count(*) FILTER (WHERE type = 'queued' AND data ? 'previous_queued_at')
      FROM job_events
     WHERE created_at >= :start AND created_at < :end
       AND type IN ('queued', 'failed')
       AND level IN ('warning', 'error')
    """
)

_RECLAIMS_BY_WORKER = text(
    """
    SELECT coalesce(l.data->>'worker_node', 'unknown'), count(*)
      FROM job_events r
      LEFT JOIN job_events l
             ON l.job_id = r.job_id AND l.type = 'locked'
            AND l.attempt = (r.data->>'expired_generation')::int
     WHERE r.created_at >= :start AND r.created_at < :end
       AND r.type IN ('queued', 'failed')
       AND r.level IN ('warning', 'error')
       AND r.data ? 'expired_generation'
     GROUP BY 1
     ORDER BY 1
    """
)

_PIPELINES_WINDOW = text(
    f"""
    SELECT type,
           count(*) FILTER (WHERE status = 'succeeded'),
           count(*) FILTER (WHERE status = 'failed'),
           count(*) FILTER (WHERE status = 'blocked'),
           count(*),
           percentile_cont({_PERCENTILES})
             WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - created_at)::float8),
           max(extract(epoch FROM finished_at - created_at)::float8)
      FROM pipeline_runs
     WHERE finished_at >= :start AND finished_at < :end
       AND status IN ('succeeded', 'failed', 'blocked')
     GROUP BY type
     ORDER BY type
    """
)

# Where a finished run's time went, task by task: waiting for a worker, running,
# and the handoff from the Job's terminal state to the orchestration step that
# observed it (the ``advance`` message and the pipeline worker).
_PIPELINE_STAGES = text(
    f"""
    WITH stages AS (
        SELECT r.type, t.pipeline_task_id, t.task_order,
               extract(epoch FROM j.locked_at - j.enqueued_at)::float8 AS queue_wait,
               extract(epoch FROM j.finished_at - j.locked_at)::float8 AS execution,
               extract(epoch FROM t.finished_at - j.finished_at)::float8 AS handoff
          FROM pipeline_runs r
          JOIN pipeline_task_runs t ON t.pipeline_run_id = r.pipeline_run_id
          JOIN jobs j ON j.job_id = t.job_id
         WHERE r.finished_at >= :start AND r.finished_at < :end
           AND r.status IN ('succeeded', 'failed', 'blocked')
    )
    SELECT type, pipeline_task_id, task_order,
           count(queue_wait),
           percentile_cont({_PERCENTILES}) WITHIN GROUP (ORDER BY queue_wait),
           max(queue_wait),
           count(execution),
           percentile_cont({_PERCENTILES}) WITHIN GROUP (ORDER BY execution),
           max(execution),
           count(handoff),
           percentile_cont({_PERCENTILES}) WITHIN GROUP (ORDER BY handoff),
           max(handoff)
      FROM stages
     GROUP BY type, pipeline_task_id, task_order
     ORDER BY type, task_order
    """
)


def _latency(row: Any, at: int) -> Latency:
    return Latency.of(row[at], row[at + 1], row[at + 2])


class PostgresExecutionMetrics:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def now(self) -> datetime:
        return (await self._session.execute(text("SELECT now()"))).scalar_one()

    async def backlog(self) -> list[JobTypeBacklog]:
        rows = (await self._session.execute(_BACKLOG)).all()
        return [JobTypeBacklog(*row) for row in rows]

    async def pipelines_waiting(self) -> list[PipelineWaiting]:
        rows = (await self._session.execute(_PIPELINES_WAITING)).all()
        return [PipelineWaiting(*row) for row in rows]

    async def jobs(self, *, start: datetime, end: datetime) -> list[JobTypeWindow]:
        minutes = max((end - start).total_seconds() / 60.0, 1e-9)
        rows = (await self._session.execute(_JOBS_WINDOW, _window(start, end))).all()
        result = []
        for row in rows:
            succeeded, failed = row[1], row[2]
            finished = succeeded + failed
            result.append(
                JobTypeWindow(
                    job_type=row[0],
                    succeeded=succeeded,
                    failed=failed,
                    failure_rate=failed / finished if finished else None,
                    throughput_per_min=finished / minutes,
                    queue_wait=_latency(row, 3),
                    execution=_latency(row, 6),
                    end_to_end=_latency(row, 9),
                    claimed_more_than_once=row[12],
                    failures_by_error=dict(row[13] or {}),
                )
            )
        return result

    async def recovery(self, *, start: datetime, end: datetime) -> RecoveryWindow:
        row = (await self._session.execute(_RECOVERY_WINDOW, _window(start, end))).one()
        workers = (
            await self._session.execute(_RECLAIMS_BY_WORKER, _window(start, end))
        ).all()
        return RecoveryWindow(*row, lease_reclaims_by_worker=dict(workers))

    async def pipelines(
        self, *, start: datetime, end: datetime
    ) -> list[PipelineTypeWindow]:
        rows = (
            await self._session.execute(_PIPELINES_WINDOW, _window(start, end))
        ).all()
        return [
            PipelineTypeWindow(
                pipeline_type=row[0],
                succeeded=row[1],
                failed=row[2],
                blocked=row[3],
                end_to_end=_latency(row, 4),
            )
            for row in rows
        ]

    async def pipeline_stages(
        self, *, start: datetime, end: datetime
    ) -> list[PipelineStage]:
        rows = (
            await self._session.execute(_PIPELINE_STAGES, _window(start, end))
        ).all()
        return [
            PipelineStage(
                pipeline_type=row[0],
                pipeline_task_id=row[1],
                task_order=row[2],
                queue_wait=_latency(row, 3),
                execution=_latency(row, 6),
                handoff=_latency(row, 9),
            )
            for row in rows
        ]

    async def snapshot(
        self, *, window_seconds: float, end: datetime | None = None
    ) -> ExecutionSnapshot:
        """Current state now, window metrics over the ``window_seconds`` before
        ``end`` (default: now, by PostgreSQL's clock)."""
        measured_at = await self.now()
        window_end = end or measured_at
        window_start = window_end - timedelta(seconds=window_seconds)
        return ExecutionSnapshot(
            measured_at=measured_at,
            window_start=window_start,
            window_end=window_end,
            backlog=await self.backlog(),
            jobs=await self.jobs(start=window_start, end=window_end),
            recovery=await self.recovery(start=window_start, end=window_end),
            pipelines_waiting=await self.pipelines_waiting(),
            pipelines=await self.pipelines(start=window_start, end=window_end),
            pipeline_stages=await self.pipeline_stages(
                start=window_start, end=window_end
            ),
        )


def _window(start: datetime, end: datetime) -> dict[str, datetime]:
    return {"start": start, "end": end}


__all__ = [
    "ExecutionSnapshot",
    "JobTypeBacklog",
    "JobTypeWindow",
    "Latency",
    "PipelineStage",
    "PipelineTypeWindow",
    "PipelineWaiting",
    "PostgresExecutionMetrics",
    "RecoveryWindow",
]
