from __future__ import annotations

import json
from dataclasses import asdict

import typer

from sceneops_db.postgres.execution_metrics import PostgresExecutionMetrics
from sceneops_db.session import get_async_sessionmaker
from sceneops_worker.cli.async_utils import run_cli_async
from sceneops_worker.config import get_settings


def broker_depths() -> dict[str, object]:
    """Messages waiting in the job and pipeline queues, by the broker. Best
    effort: an unreachable broker is reported, not raised -- that is itself the
    answer. The broker counts messages, PostgreSQL counts work: resends make the
    first larger than the second, a lost message makes it smaller."""
    from sceneops_worker.celery_app import celery_app

    from kombu.exceptions import ChannelError

    celery = get_settings().execution.celery
    depths: dict[str, object] = {}
    try:
        with celery_app.connection_for_read() as connection:
            connection.ensure_connection(max_retries=1, timeout=2)
            for queue in (celery.job_queue, celery.pipeline_queue):
                with connection.channel() as channel:
                    try:
                        depths[queue] = channel.queue_declare(
                            queue=queue, passive=True
                        ).message_count
                    except ChannelError:
                        # Redis keeps no key for an empty queue: an empty queue
                        # and one never used both answer NOT_FOUND.
                        depths[queue] = 0
    except Exception as exc:  # noqa: BLE001 - reported as the probe's result
        return {"error": repr(exc)[:300]}
    return depths


def execution_status_command(
    window_seconds: float = typer.Option(
        300.0,
        "--window-seconds",
        min=1,
        help="Window of the throughput, latency, failure and recovery metrics.",
    ),
    broker: bool = typer.Option(
        True, "--broker/--no-broker", help="Also read the broker's queue depths."
    ),
) -> None:
    """Execution health from durable state: backlog and oldest queued Job, running
    Jobs and their heartbeats, what active PipelineRuns wait on, and, over the
    window, throughput, latency percentiles, failures and recovery actions by
    type. Read-only. Prints one JSON document."""

    async def _run():
        async with get_async_sessionmaker()() as session:
            return await PostgresExecutionMetrics(session).snapshot(
                window_seconds=window_seconds
            )

    report = asdict(run_cli_async(_run))
    if broker:
        report["broker_queue_depths"] = broker_depths()
    typer.echo(json.dumps(report, default=str, sort_keys=True))
