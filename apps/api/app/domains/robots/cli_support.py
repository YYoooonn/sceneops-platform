"""Pieces the one-shot acquisition commands (reconciliation, artifact lifecycle,
acquisition status) share: reading a capture report and fixing the observation
time."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from sceneops_core.robots.capture_scan import CaptureScanReport


def load_capture_report(source: str | None) -> CaptureScanReport | None:
    """The JSON printed by ``recording scan-capture``; ``-`` reads stdin."""
    if source is None:
        return None
    raw = sys.stdin.read() if source == "-" else Path(source).read_text("utf-8")
    return CaptureScanReport.model_validate_json(raw)


def parse_observed_at(value: str | None) -> datetime:
    """``--observed-at``: an ISO-8601 timestamp with a UTC offset, default now."""
    if value is None:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--observed-at needs a UTC offset (e.g. 2026-10-06T00:00:00Z)")
    return parsed
