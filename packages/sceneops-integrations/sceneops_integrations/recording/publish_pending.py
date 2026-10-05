"""One-shot recovery of publication from finalized captures (ADR-008 §3.2, §5.1,
§8 step 12.4).

``publish_pending`` scans a capture output root and, for every finalized bag
that carries a valid capture receipt and is not completely published, runs
``publish_from_capture`` -- the same code path as ``publish --from-capture`` --
so retries and first publications share one set of correctness guarantees:
write-once keys, identical bytes reused, a differing object a loud conflict.

What it publishes, and what it refuses to touch::

    finalized, receipt valid, nothing in the store          -> publish
    finalized, receipt valid, recording only (no manifest)  -> publish (reuses the recording)
    finalized, receipt valid, valid manifest + recording    -> skip  (already published)
    manifest without recording / malformed manifest /
      manifest contradicted by the objects                  -> skip  (an operator decides;
                                                               never repaired automatically)
    finalized without a receipt (legacy / batch)            -> skip  (explicit-input publish only)
    receipt present but unusable                            -> skip  (an integrity incident)
    capture still unfinished                                -> skip

A publish that raises (conflicting bytes, a receipt that disagrees with the
bytes, an unreadable MCAP) is reported as ``failed`` and the pass continues with
the next run: one bad capture never blocks the others. The command keeps no
state between invocations, so running it again is the retry; it is safe at any
frequency and concurrently (publication is idempotent for identical inputs).

Database-free, like the rest of the Publisher: it needs the capture volume and
the ArtifactStore, nothing else.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.robots.capture_scan import CaptureClass
from sceneops_core.robots.published_scan import (
    PublicationClass,
    classify_publication,
    observe_published_runs,
)

from .capture_scan import scan_capture_volume
from .from_capture import publish_from_capture

logger = logging.getLogger(__name__)

PUBLISH_PENDING_REPORT_SCHEMA_V1: Final = "sceneops.publish_pending_report/v1"


class PendingOutcome(StrEnum):
    PUBLISHED = "published"
    SKIPPED = "skipped"
    FAILED = "failed"


class PendingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    outcome: PendingOutcome
    # Machine-readable code for SKIPPED / FAILED.
    reason: str | None = None
    manifest_uri: str | None = None
    manifest_checksum: str | None = None
    recording_written: bool | None = None
    manifest_written: bool | None = None
    # "ErrorType: message" for FAILED.
    error: str | None = None


class PublishPendingReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["sceneops.publish_pending_report/v1"] = (
        PUBLISH_PENDING_REPORT_SCHEMA_V1
    )
    root_uri: str
    results: tuple[PendingResult, ...]
    # outcome value -> number of runs; keys sorted.
    counts: dict[str, int]

    @property
    def failed(self) -> bool:
        return self.counts.get(PendingOutcome.FAILED.value, 0) > 0


def _error_text(error: BaseException) -> str:
    message = " ".join(str(error).split())
    return f"{type(error).__name__}: {message[:300]}"


_CAPTURE_SKIP_REASONS: Final = {
    CaptureClass.CAPTURE_UNFINISHED: "capture_unfinished",
    CaptureClass.FINALIZED_NO_RECEIPT: "finalized_no_receipt",
    CaptureClass.FINALIZED_RECEIPT_INVALID: "receipt_invalid",
}


async def publish_pending(
    *,
    artifact_store: ArtifactStore,
    root_uri: str,
    capture_root: Path,
) -> PublishPendingReport:
    """Publish every finalized capture under ``capture_root`` that is not yet
    completely published. Raises only when the facts cannot be read (capture
    root missing, store listing failing); a per-run failure is a result."""
    captures = scan_capture_volume(capture_root)
    published = {
        observation.run_id: observation
        for observation in (await observe_published_runs(artifact_store, root_uri)).runs
    }

    results: list[PendingResult] = []
    for capture in captures.runs:
        run_id = capture.run_id
        skip_reason = _CAPTURE_SKIP_REASONS.get(capture.classification)
        if skip_reason is not None:
            results.append(
                PendingResult(
                    run_id=run_id, outcome=PendingOutcome.SKIPPED, reason=skip_reason
                )
            )
            continue

        observation = published.get(run_id)
        if observation is not None:
            assessment = classify_publication(observation)
            if assessment.classification == PublicationClass.PUBLISHED:
                results.append(
                    PendingResult(
                        run_id=run_id,
                        outcome=PendingOutcome.SKIPPED,
                        reason="already_published",
                        manifest_uri=(
                            observation.manifest_object.uri
                            if observation.manifest_object is not None
                            else None
                        ),
                        manifest_checksum=observation.manifest_checksum,
                    )
                )
                continue
            if assessment.classification != PublicationClass.RECORDING_WITHOUT_MANIFEST:
                # A manifest exists but contradicts or lacks its recording:
                # publishing again could only conflict or mask an incident.
                results.append(
                    PendingResult(
                        run_id=run_id,
                        outcome=PendingOutcome.SKIPPED,
                        reason=f"publication_{assessment.classification.value}",
                    )
                )
                continue

        try:
            publication = await publish_from_capture(
                artifact_store=artifact_store,
                root_uri=root_uri,
                capture_dir=capture_root / run_id,
            )
        except Exception as exc:  # noqa: BLE001 - one capture must not block the others
            result = PendingResult(
                run_id=run_id,
                outcome=PendingOutcome.FAILED,
                reason="publish_failed",
                error=_error_text(exc),
            )
        else:
            result = PendingResult(
                run_id=run_id,
                outcome=PendingOutcome.PUBLISHED,
                manifest_uri=publication.manifest_uri,
                manifest_checksum=publication.manifest_checksum,
                recording_written=publication.recording_written,
                manifest_written=publication.manifest_written,
            )
        results.append(result)
        logger.info(
            "acquisition_recovery %s",
            result.model_dump_json(exclude_none=True),
        )

    counts: dict[str, int] = {}
    for result in results:
        counts[result.outcome.value] = counts.get(result.outcome.value, 0) + 1
    return PublishPendingReport(
        root_uri=root_uri, results=tuple(results), counts=dict(sorted(counts.items()))
    )


__all__ = [
    "PUBLISH_PENDING_REPORT_SCHEMA_V1",
    "PendingOutcome",
    "PendingResult",
    "PublishPendingReport",
    "publish_pending",
]
