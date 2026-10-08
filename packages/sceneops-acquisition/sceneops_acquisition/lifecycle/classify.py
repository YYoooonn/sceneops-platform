"""Pure artifact lifecycle classification (ADR-008 §6, L-9, L-10, L-11).

Input is the 12.3/12.4 reconciliation report (one state per ``run_id``, built
from the store listing, PostgreSQL and Jobs), the ArtifactRecords under the
RobotRun root, and the observation time. Output is one class per object and one
entry per dangling DB reference. Nothing here reads a store, a database or a
clock.

Reference (ADR-008 §6.1): an object is *referenced* iff some ArtifactRecord
carries exactly its URI. Ownership is never inferred from the object's name; the
run prefix is used only to group the objects of one publication and to find the
run's registration facts.

Per object, first match wins:

1. The run is an integrity incident or a permanent conflict (the durable facts
   contradict each other): every recording/manifest object of the run is an
   ``integrity_incident``, referenced or not.
2. Referenced: ``referenced``, unless the record's size or known checksum
   contradicts the object (``referenced_size_mismatch`` / ``referenced_corrupt``).
3. Unreferenced, but the run is registered: incident (the record rows that should
   reference it do not).
4. Unreferenced, registration unfinished (PN-2, PN-4): ``pending`` at any age.
5. Unreferenced, publication incomplete: a recording with no manifest is O1 once
   old enough and no capture source exists or is unobservable (PN-1, PN-3,
   orphan grace); a malformed manifest or a manifest with no recording is an
   incident, never a candidate.
6. Unreferenced, registration failed permanently: O2 once old enough (PN-1,
   orphan grace).
7. Anything else: incident (``unclassifiable_state``). Conservative on purpose.

Grace is measured on the *run*: the age of its newest recording/manifest
object, because the objects of one publication live and die together.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_core.jobs.schemas import JobStatus
from sceneops_acquisition.lifecycle.vocabulary import (
    ACTIVE_REGISTRATION_JOB,
    DANGLING_REFERENCE,
    DEFAULT_ORPHAN_GRACE_SECONDS,
    DEFAULT_PENDING_GRACE_SECONDS,
    ORPHAN_RISK,
    PN1_WITHIN_PENDING_GRACE,
    PN2_REGISTRATION_UNFINISHED,
    PN3_CAPTURE_SOURCE_PRESENT,
    PN3_CAPTURE_SOURCE_UNOBSERVED,
    PN4_ACTIVE_REGISTRATION_JOB,
    REFERENCED_CORRUPT,
    REFERENCED_SIZE_MISMATCH,
    REGISTERED_OBJECT_UNREFERENCED,
    UNCLASSIFIABLE_STATE,
    WITHIN_ORPHAN_GRACE,
    LifecycleClass,
    OrphanReason,
    RiskTier,
)
from sceneops_recording.published_scan import (
    ObservedObject,
    PublicationClass,
    PublishedRunObservation,
    UnrecognizedObject,
)

from sceneops_acquisition.reconciliation import (
    AcquisitionState,
    ReconciliationReport,
    RunReport,
)

from .model import (
    ByteCount,
    EntrySubject,
    IncidentSummary,
    LifecycleEntry,
    LifecycleSummary,
    ObjectRole,
    OrphanSummary,
    PendingSummary,
    ReferencedSummary,
    UnclassifiedObject,
)

# PN-2: registration is unfinished and recoverable, so the platform's only copy
# of the recording stays protected at any age.
_PN2_STATES = frozenset(
    {
        AcquisitionState.REGISTRATION_PENDING,
        AcquisitionState.REGISTRATION_ACTIVE,
        AcquisitionState.REGISTRATION_STALLED_CANDIDATE,
        AcquisitionState.REGISTRATION_FAILED_TRANSIENT,
    }
)
_CONTRADICTED_STATES = frozenset(
    {AcquisitionState.INTEGRITY_INCIDENT, AcquisitionState.PERMANENT_CONFLICT}
)
_ACTIVE_JOB_STATUSES = frozenset(
    {JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING}
)


@dataclass(frozen=True)
class ArtifactLifecyclePolicy:
    """Grace periods (ADR-008 §6.2, L-11). Both are explicit and configurable;
    the defaults are the ADR's initial proposal.

    ``pending_grace``: an unreferenced object younger than this may belong to an
    in-flight write (PN-1).
    ``orphan_grace``: an unreferenced, unprotected object must also be at least
    this old to be an orphan candidate. Between the two it stays pending.
    """

    pending_grace: timedelta = timedelta(seconds=DEFAULT_PENDING_GRACE_SECONDS)
    orphan_grace: timedelta = timedelta(seconds=DEFAULT_ORPHAN_GRACE_SECONDS)

    def __post_init__(self) -> None:
        if self.pending_grace <= timedelta(0):
            raise ValueError("pending_grace must be positive")
        if self.orphan_grace < self.pending_grace:
            raise ValueError("orphan_grace must not be shorter than pending_grace")


@dataclass(frozen=True)
class ClassifiedArtifacts:
    entries: tuple[LifecycleEntry, ...]
    unclassified: tuple[UnclassifiedObject, ...]


def _age_seconds(now: datetime, modified: datetime) -> float:
    # A modification time in the future (clock skew) counts as brand new:
    # protective, never the reverse.
    return max((now - modified).total_seconds(), 0.0)


def run_id_of(root_uri: str, uri: str) -> str | None:
    """The first path segment under the root (the run prefix), if any. Used to
    label entries only, never to decide ownership."""
    prefix = root_uri.rstrip("/") + "/"
    if not uri.startswith(prefix):
        return None
    parts = [part for part in uri[len(prefix) :].split("/") if part]
    return parts[0] if len(parts) > 1 else None


def _publication_objects(
    publication: PublishedRunObservation,
) -> list[tuple[ObjectRole, ObservedObject]]:
    objects: list[tuple[ObjectRole, ObservedObject]] = []
    if publication.recording is not None:
        objects.append((ObjectRole.RECORDING, publication.recording))
    if publication.manifest_object is not None:
        objects.append((ObjectRole.MANIFEST, publication.manifest_object))
    objects.extend((ObjectRole.OTHER, item) for item in publication.unexpected_objects)
    return objects


def _known_checksum(
    role: ObjectRole, publication: PublishedRunObservation
) -> str | None:
    """The object's sha256 when the observation computed it: the manifest's
    when it parsed, the recording's only when its bytes were read."""
    if role == ObjectRole.MANIFEST:
        return publication.manifest_checksum
    if role == ObjectRole.RECORDING:
        return publication.recording_actual_checksum
    return None


def _reference_problems(
    role: ObjectRole,
    size_bytes: int,
    references: Sequence[ArtifactRecord],
    checksum: str | None,
) -> list[str]:
    problems: list[str] = []
    for record in references:
        if record.size_bytes is not None and record.size_bytes != size_bytes:
            problems.append(REFERENCED_SIZE_MISMATCH)
        if (
            record.checksum is not None
            and checksum is not None
            and record.checksum != checksum
        ):
            problems.append(REFERENCED_CORRUPT)
    return problems


@dataclass(frozen=True)
class _Verdict:
    lifecycle_class: LifecycleClass
    reasons: tuple[str, ...]
    orphan_reason: OrphanReason | None = None
    risk: RiskTier | None = None


def _incident(*reasons: str) -> _Verdict:
    return _Verdict(LifecycleClass.INTEGRITY_INCIDENT, tuple(sorted(set(reasons))))


def _protected_by_age(run_age: float, policy: ArtifactLifecyclePolicy) -> list[str]:
    if run_age < policy.pending_grace.total_seconds():
        return [PN1_WITHIN_PENDING_GRACE]
    if run_age < policy.orphan_grace.total_seconds():
        return [WITHIN_ORPHAN_GRACE]
    return []


def _unreferenced_verdict(
    run: RunReport,
    *,
    run_age: float,
    capture_observed: bool,
    policy: ArtifactLifecyclePolicy,
) -> _Verdict:
    state = run.state
    active_job = any(
        job.status in _ACTIVE_JOB_STATUSES for job in run.registration.jobs
    )

    if run.registration.robot_run is not None:
        return _incident(REGISTERED_OBJECT_UNREFERENCED)

    if state in _PN2_STATES:
        reasons = [PN2_REGISTRATION_UNFINISHED]
        if active_job:
            reasons.append(PN4_ACTIVE_REGISTRATION_JOB)
        return _Verdict(LifecycleClass.PENDING, tuple(sorted(reasons)))

    if state == AcquisitionState.PUBLICATION_INCOMPLETE:
        assert run.publication_assessment is not None
        if (
            run.publication_assessment.classification
            == PublicationClass.RECORDING_WITHOUT_MANIFEST
        ):
            reasons = _protected_by_age(run_age, policy)
            # PN-3: a capture bag or receipt that still exists can resume the
            # publication. Not knowing is not the same as not existing.
            if run.capture is not None:
                reasons.append(PN3_CAPTURE_SOURCE_PRESENT)
            elif not capture_observed:
                reasons.append(PN3_CAPTURE_SOURCE_UNOBSERVED)
            if reasons:
                return _Verdict(LifecycleClass.PENDING, tuple(sorted(reasons)))
            orphan = OrphanReason.RECORDING_WITHOUT_MANIFEST
            return _Verdict(
                LifecycleClass.ORPHAN_CANDIDATE,
                (orphan.value,),
                orphan,
                ORPHAN_RISK[orphan],
            )
        # Malformed manifest, or a manifest whose recording is gone: the
        # publication contradicts itself. Conservative: never a candidate.
        extra = [ACTIVE_REGISTRATION_JOB] if active_job else []
        return _incident(*run.reasons, *extra)

    if state == AcquisitionState.REGISTRATION_FAILED_PERMANENT:
        reasons = _protected_by_age(run_age, policy)
        if reasons:
            return _Verdict(LifecycleClass.PENDING, tuple(sorted(reasons)))
        orphan = OrphanReason.RECORDING_MANIFEST_PERMANENTLY_FAILED
        return _Verdict(
            LifecycleClass.ORPHAN_CANDIDATE,
            (orphan.value,),
            orphan,
            ORPHAN_RISK[orphan],
        )

    return _incident(UNCLASSIFIABLE_STATE)


def classify_artifact_lifecycle(
    *,
    report: ReconciliationReport,
    records: Sequence[ArtifactRecord],
    absent_record_uris: frozenset[str],
    excluded_run_ids: frozenset[str] = frozenset(),
    policy: ArtifactLifecyclePolicy | None = None,
    now: datetime,
) -> ClassifiedArtifacts:
    """Classify every object and dangling reference under the report's root.

    ``report`` must have been made with ``include_database_runs=True`` for the
    reverse checks to see registered runs whose objects are gone.
    ``absent_record_uris`` are the ArtifactRecord URIs confirmed absent from the
    store; only those are dangling. ``excluded_run_ids`` are runs the caller
    could not confirm (see the service)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    policy = policy or ArtifactLifecyclePolicy()

    by_uri: dict[str, list[ArtifactRecord]] = defaultdict(list)
    for record in sorted(records, key=lambda r: r.artifact_id):
        by_uri[record.uri].append(record)

    entries: list[LifecycleEntry] = []
    unclassified: list[UnclassifiedObject] = []
    listed_uris: set[str] = set()

    def object_entry(
        *,
        run: RunReport | None,
        run_id: str | None,
        role: ObjectRole,
        item: ObservedObject,
        verdict: _Verdict,
        references: Sequence[ArtifactRecord],
    ) -> None:
        entries.append(
            LifecycleEntry(
                subject=EntrySubject.OBJECT,
                run_id=run_id,
                uri=item.uri,
                role=role,
                artifact_ids=tuple(r.artifact_id for r in references),
                lifecycle_class=verdict.lifecycle_class,
                reasons=verdict.reasons,
                orphan_reason=verdict.orphan_reason,
                risk=verdict.risk,
                size_bytes=item.size_bytes,
                last_modified=item.last_modified,
                age_seconds=_age_seconds(now, item.last_modified),
                acquisition_state=run.state.value if run is not None else None,
                acquisition_reasons=run.reasons if run is not None else (),
            )
        )

    for run in report.runs:
        if run.run_id in excluded_run_ids:
            continue
        publication = run.publication

        if publication is None:
            # No object under the run prefix. A RobotRunRecord that is still
            # there is a registration whose bytes are gone (never silently
            # healthy). Capture-only runs have nothing in the store.
            if run.registration.robot_run is not None:
                entries.append(
                    LifecycleEntry(
                        subject=EntrySubject.ROBOT_RUN_RECORD,
                        run_id=run.run_id,
                        uri=None,
                        role=None,
                        artifact_ids=tuple(
                            artifact.artifact_id
                            for artifact in (
                                run.registration.recording_artifact,
                                run.registration.manifest_artifact,
                            )
                            if artifact is not None
                        ),
                        lifecycle_class=LifecycleClass.INTEGRITY_INCIDENT,
                        reasons=run.reasons,
                        orphan_reason=None,
                        risk=None,
                        size_bytes=None,
                        last_modified=None,
                        age_seconds=None,
                        acquisition_state=run.state.value,
                        acquisition_reasons=run.reasons,
                    )
                )
            continue

        publication_items = [
            item
            for role, item in _publication_objects(publication)
            if role != ObjectRole.OTHER
        ]
        run_age = (
            min(_age_seconds(now, item.last_modified) for item in publication_items)
            if publication_items
            else 0.0
        )

        for role, item in _publication_objects(publication):
            listed_uris.add(item.uri)
            references = by_uri.get(item.uri, [])
            problems = _reference_problems(
                role, item.size_bytes, references, _known_checksum(role, publication)
            )

            if role == ObjectRole.OTHER:
                if references:
                    verdict = (
                        _incident(*problems)
                        if problems
                        else _Verdict(LifecycleClass.REFERENCED, ())
                    )
                    object_entry(
                        run=run,
                        run_id=run.run_id,
                        role=role,
                        item=item,
                        verdict=verdict,
                        references=references,
                    )
                else:
                    unclassified.append(
                        UnclassifiedObject(
                            uri=item.uri,
                            size_bytes=item.size_bytes,
                            last_modified=item.last_modified,
                            age_seconds=_age_seconds(now, item.last_modified),
                            run_id=run.run_id,
                            reason="unrecognized_object_under_run_prefix",
                        )
                    )
                continue

            if run.state in _CONTRADICTED_STATES:
                verdict = _incident(*run.reasons, *problems)
            elif references:
                verdict = (
                    _incident(*problems)
                    if problems
                    else _Verdict(LifecycleClass.REFERENCED, ())
                )
            else:
                verdict = _unreferenced_verdict(
                    run,
                    run_age=run_age,
                    capture_observed=report.capture_observed,
                    policy=policy,
                )
            object_entry(
                run=run,
                run_id=run.run_id,
                role=role,
                item=item,
                verdict=verdict,
                references=references,
            )

    stray: list[UnrecognizedObject] = list(report.unrecognized_objects)
    for entry in stray:
        item = entry.item
        listed_uris.add(item.uri)
        references = by_uri.get(item.uri, [])
        run_id = run_id_of(report.root_uri, item.uri)
        if references:
            problems = _reference_problems(
                ObjectRole.OTHER, item.size_bytes, references, None
            )
            object_entry(
                run=None,
                run_id=run_id,
                role=ObjectRole.OTHER,
                item=item,
                verdict=(
                    _incident(*problems)
                    if problems
                    else _Verdict(LifecycleClass.REFERENCED, ())
                ),
                references=references,
            )
        else:
            unclassified.append(
                UnclassifiedObject(
                    uri=item.uri,
                    size_bytes=item.size_bytes,
                    last_modified=item.last_modified,
                    age_seconds=_age_seconds(now, item.last_modified),
                    run_id=run_id,
                    reason=entry.reason,
                )
            )

    # Reverse check (ADR-008 §6.1): an ArtifactRecord under the root whose
    # object is not in the store is a dangling reference, never healthy.
    for record in sorted(records, key=lambda r: r.artifact_id):
        if record.uri in listed_uris or record.uri not in absent_record_uris:
            continue
        entries.append(
            LifecycleEntry(
                subject=EntrySubject.ARTIFACT_RECORD,
                run_id=run_id_of(report.root_uri, record.uri),
                uri=record.uri,
                role=None,
                artifact_ids=(record.artifact_id,),
                lifecycle_class=LifecycleClass.INTEGRITY_INCIDENT,
                reasons=(DANGLING_REFERENCE,),
                orphan_reason=None,
                risk=None,
                size_bytes=record.size_bytes,
                last_modified=None,
                age_seconds=None,
                acquisition_state=None,
                acquisition_reasons=(),
            )
        )

    subjects = list(EntrySubject)
    entries.sort(
        key=lambda e: (
            subjects.index(e.subject),
            e.run_id or "",
            e.uri or "",
            e.artifact_ids,
        )
    )
    unclassified.sort(key=lambda u: u.uri)
    return ClassifiedArtifacts(tuple(entries), tuple(unclassified))


def summarize(classified: ClassifiedArtifacts) -> LifecycleSummary:
    referenced = [
        e for e in classified.entries if e.lifecycle_class == LifecycleClass.REFERENCED
    ]
    pending = [
        e for e in classified.entries if e.lifecycle_class == LifecycleClass.PENDING
    ]
    candidates = [
        e
        for e in classified.entries
        if e.lifecycle_class == LifecycleClass.ORPHAN_CANDIDATE
    ]
    incidents = [
        e
        for e in classified.entries
        if e.lifecycle_class == LifecycleClass.INTEGRITY_INCIDENT
    ]

    def total(items: Sequence[LifecycleEntry]) -> int:
        return sum(e.size_bytes or 0 for e in items)

    def grouped(items: Sequence[LifecycleEntry], keys) -> dict[str, ByteCount]:
        buckets: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for entry in items:
            for key in keys(entry):
                buckets[key][0] += 1
                buckets[key][1] += entry.size_bytes or 0
        return {
            key: ByteCount(count=count, bytes=size)
            for key, (count, size) in sorted(buckets.items())
        }

    reason_counts: dict[str, int] = defaultdict(int)
    subject_counts: dict[str, int] = defaultdict(int)
    for entry in incidents:
        subject_counts[entry.subject.value] += 1
        for reason in entry.reasons:
            reason_counts[reason] += 1

    return LifecycleSummary(
        referenced=ReferencedSummary(count=len(referenced), bytes=total(referenced)),
        pending=PendingSummary(
            count=len(pending),
            bytes=total(pending),
            oldest_age_seconds=max(
                (e.age_seconds for e in pending if e.age_seconds is not None),
                default=None,
            ),
            by_reason=grouped(pending, lambda e: e.reasons),
        ),
        orphan_candidates=OrphanSummary(
            count=len(candidates),
            bytes=total(candidates),
            by_reason=grouped(
                candidates, lambda e: [e.orphan_reason.value] if e.orphan_reason else []
            ),
            by_risk=grouped(candidates, lambda e: [e.risk.value] if e.risk else []),
        ),
        integrity_incidents=IncidentSummary(
            count=len(incidents),
            object_bytes=sum(
                e.size_bytes or 0 for e in incidents if e.subject == EntrySubject.OBJECT
            ),
            by_subject=dict(sorted(subject_counts.items())),
            by_reason=dict(sorted(reason_counts.items())),
        ),
        unclassified=ByteCount(
            count=len(classified.unclassified),
            bytes=sum(u.size_bytes for u in classified.unclassified),
        ),
    )


__all__ = [
    "ArtifactLifecyclePolicy",
    "ClassifiedArtifacts",
    "classify_artifact_lifecycle",
    "run_id_of",
    "summarize",
]
