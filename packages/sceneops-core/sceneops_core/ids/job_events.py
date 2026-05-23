from __future__ import annotations

from uuid import uuid4


def generate_job_event_id() -> str:
    return f"jobevt-{uuid4().hex[:12]}"
