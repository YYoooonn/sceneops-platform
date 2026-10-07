"""The lease a worker holds on the Job it claimed, kept alive while it runs.

A claim holds a Job until ``lease_expires_at`` (PostgreSQL time); job lease
recovery (``lease_recovery``) reclaims a RUNNING Job whose lease has passed.
``JobLeaseKeeper`` renews the lease every third of its duration, so two renewals
in a row can fail (a database blip) before the lease runs out.

The keeper runs on a thread of its own, with its own event loop and database
connection. Handlers run synchronous work on the worker's event loop; a renewal
scheduled on that loop would wait for them, and a healthy worker busy for longer
than its lease would lose it. On its own thread the keeper renews while the
process is alive and reaches PostgreSQL. That is all a renewal proves: not that
the handler makes progress.

A renewal that finds the claim gone (recovery reclaimed the Job, another claim
holds it now) is final: the keeper stops and calls ``on_lost`` once, from its
thread. A renewal that fails for any other reason (a connection error) proves
nothing either way; the keeper tries again at the next interval, and the lease
may expire meanwhile. Safety never depends on the keeper: every write of the
claim is fenced by its ``lease_generation`` in PostgreSQL.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from sceneops_db.config import get_db_settings
from sceneops_db.postgres.jobs import PostgresJobRepository

logger = logging.getLogger(__name__)

# Renewals per lease duration.
RENEWALS_PER_LEASE = 3


class LeaseRenewal(Protocol):
    async def renew(
        self, job_id: str, *, lease_generation: int, lease_seconds: float
    ) -> bool:
        """True when the claim still holds the Job and its lease was extended;
        False when the claim is gone. Raises when it could not tell."""
        ...

    async def close(self) -> None: ...


class PostgresLeaseRenewal:
    """Renews through a one-connection engine of its own: the keeper's event
    loop cannot use the worker's engine, whose connections belong to another
    loop."""

    def __init__(self, database_url: str | None = None) -> None:
        self._engine = create_async_engine(
            database_url or get_db_settings().sceneops_database_url,
            pool_size=1,
            max_overflow=0,
            pool_pre_ping=True,
        )

    async def renew(
        self, job_id: str, *, lease_generation: int, lease_seconds: float
    ) -> bool:
        async with AsyncSession(self._engine) as session:
            expires_at = await PostgresJobRepository(session).renew_lease(
                job_id, lease_generation=lease_generation, lease_seconds=lease_seconds
            )
            await session.commit()
        return expires_at is not None

    async def close(self) -> None:
        await self._engine.dispose()


class JobLeaseKeeper:
    def __init__(
        self,
        *,
        job_id: str,
        lease_generation: int,
        lease_seconds: float,
        renewal_factory: Callable[[], LeaseRenewal],
        on_lost: Callable[[], None],
    ) -> None:
        self.job_id = job_id
        self.lease_generation = lease_generation
        self.lease_seconds = lease_seconds
        self.interval = lease_seconds / RENEWALS_PER_LEASE
        self.renewals = 0
        self.failed_renewals = 0
        self._renewal_factory = renewal_factory
        self._on_lost = on_lost
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"job-lease-{job_id}", daemon=True
        )

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> JobLeaseKeeper:
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop renewing. Waits at most one lease duration for a renewal in
        progress; a renewal that completes later is harmless (it is refused once
        the Job left RUNNING, and only reported while the claim runs)."""
        self._stop.set()
        self._thread.join(timeout=self.lease_seconds)

    def _run(self) -> None:
        renewal: LeaseRenewal | None = None
        with asyncio.Runner() as runner:
            try:
                while not self._stop.wait(self.interval):
                    try:
                        if renewal is None:
                            renewal = self._renewal_factory()
                        held = runner.run(
                            renewal.renew(
                                self.job_id,
                                lease_generation=self.lease_generation,
                                lease_seconds=self.lease_seconds,
                            )
                        )
                    except Exception:  # noqa: BLE001 - unknown is not lost
                        self.failed_renewals += 1
                        logger.warning(
                            "job %s: lease renewal failed; retrying in %.1fs",
                            self.job_id,
                            self.interval,
                            exc_info=True,
                        )
                        continue
                    if not held:
                        self._lost.set()
                        if not self._stop.is_set():
                            self._on_lost()
                        return
                    self.renewals += 1
            finally:
                if renewal is not None:
                    try:
                        runner.run(renewal.close())
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "job %s: closing the lease connection failed",
                            self.job_id,
                            exc_info=True,
                        )
