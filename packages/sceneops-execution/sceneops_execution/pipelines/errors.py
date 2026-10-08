from __future__ import annotations


class PipelineQualityBlocked(Exception):
    """Raised when a task completed but its result blocks the pipeline."""

    def __init__(self, message: str, *, code: str = "quality_gate_blocked") -> None:
        super().__init__(message)
        self.code = code
