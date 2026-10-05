"""One-shot, read-only artifact lifecycle report (ADR-008 §6, §8 step 12.5).

``artifact_lifecycle_once`` is built on ``reconcile_once``; it adds no scanner of
its own. The facts are the ones the acquisition reconciler reads:

1. The ArtifactStore listing of the RobotRun root (and each manifest object).
2. PostgreSQL in ``READ ONLY`` transactions: RobotRunRecords (including those
   whose objects are gone), the ArtifactRecords under the root -- the reference
   side of ADR-008 §6.1 -- and the registration Jobs.
3. Optionally the capture report (PN-3 / O1) and, with
   ``verify_recording_bytes``, the recording bytes of registered runs.

The store is listed before PostgreSQL is read, so an object is never reported
unreferenced because a registration committed after the references were read.
The opposite race -- a publication and registration landing between the listing
and the database read -- would make a fresh record look dangling; every
dangling finding is therefore confirmed with ``ArtifactStore.exists`` and a
finding whose object has appeared is dropped and counted
(``unconfirmed_findings``). Classification can still go stale between this
report and any later action; re-verifying before acting is a precondition of
any future deletion (ADR-008 §6.3).

``now`` is injected: grace periods are measured against it, it is part of the
report, and the same facts with the same ``now`` give the same report. Nothing
is written anywhere (L-2, L-10).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.robots.capture_scan import CaptureScanReport
from sceneops_core.robots.published_scan import (
    MANIFEST_OBJECT_NAME,
    RECORDING_OBJECT_NAME,
)
from sceneops_core.robots.registration_failures import (
    DEFAULT_STALL_THRESHOLD_SECONDS,
    REGISTRATION_ATTEMPT_BUDGET,
)

from app.domains.robots.reconciliation import (
    ClassificationPolicy,
    RegistrationFactsScope,
    RunReport,
    reconcile_once,
)

from .classify import ArtifactLifecyclePolicy, classify_artifact_lifecycle, summarize
from .model import ArtifactLifecycleReport, LifecyclePolicyFacts


def _expected_uris(store: ArtifactStore, root_uri: str, run: RunReport) -> set[str]:
    uris = {
        store.join_uri(root_uri, run.run_id, RECORDING_OBJECT_NAME),
        store.join_uri(root_uri, run.run_id, MANIFEST_OBJECT_NAME),
    }
    for artifact in (
        run.registration.recording_artifact,
        run.registration.manifest_artifact,
    ):
        if artifact is not None:
            uris.add(artifact.uri)
    return uris


async def artifact_lifecycle_once(
    *,
    artifact_store: ArtifactStore,
    root_uri: str,
    registration_facts: RegistrationFactsScope,
    now: datetime,
    capture_report: CaptureScanReport | None = None,
    policy: ArtifactLifecyclePolicy | None = None,
    classification_policy: ClassificationPolicy | None = None,
    verify_recording_bytes: bool = False,
) -> ArtifactLifecycleReport:
    """Classify every object under ``root_uri`` and every database reference to
    one. ``classification_policy`` is the acquisition policy that decides stalled
    and budget-exhausted registrations; it defaults to the one-shot command's
    (the stall threshold and attempt budget the reconciler applies)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    policy = policy or ArtifactLifecyclePolicy()
    classification_policy = classification_policy or ClassificationPolicy(
        stall_candidate_after=timedelta(seconds=DEFAULT_STALL_THRESHOLD_SECONDS),
        attempt_budget=REGISTRATION_ATTEMPT_BUDGET,
    )

    report = await reconcile_once(
        artifact_store=artifact_store,
        root_uri=root_uri,
        registration_facts=registration_facts,
        capture_report=capture_report,
        policy=classification_policy,
        now=now,
        verify_registered_recordings=verify_recording_bytes,
        include_database_runs=True,
    )
    async with registration_facts() as facts:
        records = await facts.artifact_records_under(root_uri)

    listed = {
        item.uri
        for run in report.runs
        if run.publication is not None
        for item in (
            run.publication.recording,
            run.publication.manifest_object,
            *run.publication.unexpected_objects,
        )
        if item is not None
    } | {entry.item.uri for entry in report.unrecognized_objects}

    unconfirmed = 0
    absent: set[str] = set()
    for uri in sorted({record.uri for record in records} - listed):
        if await artifact_store.exists(uri):
            unconfirmed += 1
        else:
            absent.add(uri)

    excluded: set[str] = set()
    for run in report.runs:
        if run.publication is None and run.registration.robot_run is not None:
            for uri in sorted(_expected_uris(artifact_store, root_uri, run)):
                if await artifact_store.exists(uri):
                    excluded.add(run.run_id)
                    unconfirmed += 1
                    break

    classified = classify_artifact_lifecycle(
        report=report,
        records=records,
        absent_record_uris=frozenset(absent),
        excluded_run_ids=frozenset(excluded),
        policy=policy,
        now=now,
    )
    return ArtifactLifecycleReport(
        root_uri=root_uri,
        observed_at=now,
        policy=LifecyclePolicyFacts(
            pending_grace_seconds=policy.pending_grace.total_seconds(),
            orphan_grace_seconds=policy.orphan_grace.total_seconds(),
            stall_threshold_seconds=(
                classification_policy.stall_candidate_after.total_seconds()
                if classification_policy.stall_candidate_after is not None
                else 0.0
            ),
            attempt_budget=classification_policy.attempt_budget or 0,
            capture_observed=report.capture_observed,
            registered_recordings_hashed=verify_recording_bytes,
        ),
        summary=summarize(classified),
        entries=classified.entries,
        unclassified=classified.unclassified,
        unconfirmed_findings=unconfirmed,
    )


__all__ = ["artifact_lifecycle_once"]
