from __future__ import annotations

from sceneops_core.sources import RegistrationOutcome

from .base import BaseJobResult


class RegisterSourceSnapshotJobResult(BaseJobResult):
    """The registered declaration and the content it names. ``content`` and
    ``snapshot`` say whether this Job inserted the record or found it
    registered; a Job that finds both is an idempotent retry and wrote
    nothing. ``snapshot_id`` is the execution provenance of this Job
    (ADR-015 §4)."""

    snapshot_id: str
    content_id: str
    content: RegistrationOutcome
    snapshot: RegistrationOutcome
    file_count: int
    total_bytes: int
