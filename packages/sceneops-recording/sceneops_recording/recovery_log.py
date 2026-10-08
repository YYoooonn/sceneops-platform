"""Structured records of acquisition recovery (ADR-008 §7.3).

Every resumer action -- a publication by ``publish-pending``, a submission,
retry or replacement by ``reconcile --once --apply`` -- is logged as one line::

    acquisition_recovery {"action": ..., "outcome": ..., "run_id": ..., ...}

and every pass ends with one summary line::

    acquisition_recovery_pass {"component": ..., "counts": ..., ...}

The JSON object is a record of what a command observed and did. It is evidence
for an operator and for log aggregation, never a source of truth: nothing reads
these lines back, and every decision is recomputed from durable facts (L-1).
Records are written to ``stderr`` so a command's ``stdout`` stays the one JSON
report it documents.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, Final

RECOVERY_LOGGER_NAME: Final = "sceneops.acquisition"
ACTION_EVENT: Final = "acquisition_recovery"
PASS_EVENT: Final = "acquisition_recovery_pass"
STATUS_EVENT: Final = "acquisition_status"

recovery_logger = logging.getLogger(RECOVERY_LOGGER_NAME)


def format_record(event: str, fields: dict[str, Any]) -> str:
    """``<event> <compact JSON>``; absent (``None``) fields are omitted and keys
    are sorted, so a record is a stable function of its fields."""
    payload = {key: value for key, value in fields.items() if value is not None}
    payload["ts"] = datetime.now(UTC).isoformat(timespec="milliseconds")
    return f"{event} {json.dumps(payload, sort_keys=True, default=str)}"


def log_event(event: str, **fields: Any) -> None:
    recovery_logger.info("%s", format_record(event, fields))


def configure_cli_logging() -> None:
    """Send acquisition records to ``stderr`` for a one-shot command.

    Only the acquisition logger is raised to INFO: turning the root logger to
    INFO would also enable SQLAlchemy's statement logging. Idempotent."""
    if any(
        getattr(handler, "_sceneops_cli", False) for handler in recovery_logger.handlers
    ):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._sceneops_cli = True  # type: ignore[attr-defined]
    recovery_logger.addHandler(handler)
    recovery_logger.setLevel(logging.INFO)
    recovery_logger.propagate = False


__all__ = [
    "ACTION_EVENT",
    "PASS_EVENT",
    "RECOVERY_LOGGER_NAME",
    "STATUS_EVENT",
    "configure_cli_logging",
    "format_record",
    "log_event",
    "recovery_logger",
]
