"""One-shot acquisition operational report (ADR-008 §7, §8 step 12.6).

``acquisition_status_once`` observes the durable facts once -- the same listing,
RobotRunRecords, ArtifactRecords and Jobs the reconciler and the artifact
lifecycle report read -- and derives both views from that one observation, so
the per-run status and the artifact findings cannot disagree about what was
read. It stores nothing: no status table, no cache, no event. Like the two
reports it is built on, it is read-only (PostgreSQL ``READ ONLY`` transactions;
the store is only listed and read) and writes no object, row, Job or log of
record.

``now`` is a required input: ages, the stall threshold and the grace periods
are measured against it and it is part of the report, so the same facts with
the same ``now`` give a byte-identical report.
"""

from __future__ import annotations

from datetime import datetime

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_recording.capture_scan import CaptureScanReport

from sceneops_acquisition.lifecycle import (
    ArtifactLifecyclePolicy,
    classify_reconciled_artifacts,
    default_classification_policy,
)
from sceneops_acquisition.reconciliation import (
    ClassificationPolicy,
    RegistrationFactsScope,
    reconcile_once,
)

from .derive import build_operational_report
from .model import AcquisitionOperationalReport


async def acquisition_status_once(
    *,
    artifact_store: ArtifactStore,
    root_uri: str,
    registration_facts: RegistrationFactsScope,
    now: datetime,
    capture_report: CaptureScanReport | None = None,
    lifecycle_policy: ArtifactLifecyclePolicy | None = None,
    classification_policy: ClassificationPolicy | None = None,
    verify_recording_bytes: bool = False,
    include_runs: bool = True,
) -> AcquisitionOperationalReport:
    """Every run under ``root_uri`` as an ``AcquisitionStatus`` plus the
    aggregates. ``classification_policy`` is the acquisition policy that decides
    stalled and budget-exhausted registrations (default: the one-shot command's
    stall threshold and attempt budget)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    classification_policy = classification_policy or default_classification_policy()

    reconciliation = await reconcile_once(
        artifact_store=artifact_store,
        root_uri=root_uri,
        registration_facts=registration_facts,
        capture_report=capture_report,
        policy=classification_policy,
        now=now,
        verify_registered_recordings=verify_recording_bytes,
        include_database_runs=True,
    )
    lifecycle = await classify_reconciled_artifacts(
        report=reconciliation,
        artifact_store=artifact_store,
        registration_facts=registration_facts,
        now=now,
        policy=lifecycle_policy,
        classification_policy=classification_policy,
        verify_recording_bytes=verify_recording_bytes,
    )
    return build_operational_report(
        reconciliation=reconciliation,
        lifecycle=lifecycle,
        observed_at=now,
        include_runs=include_runs,
    )


__all__ = ["acquisition_status_once"]
