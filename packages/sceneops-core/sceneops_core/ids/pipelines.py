from __future__ import annotations

from uuid import uuid4


def generate_pipeline_run_id() -> str:
    return f"pipe-{uuid4().hex[:12]}"


def generate_pipeline_step_run_id() -> str:
    return f"pstep-{uuid4().hex[:12]}"
