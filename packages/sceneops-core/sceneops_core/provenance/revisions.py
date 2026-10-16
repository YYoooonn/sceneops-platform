"""Unit revisions: the immutable canonical interpretations of a unit
(ADR-015 §3, §6.4, MS-7).

Unit identity and revision identity answer different questions::

    unit       unit_id                         which logical unit of which source
                                               (sceneops_core.provenance.identity)
    revision   (unit_id, manifest_checksum)    which canonical interpretation of it;
                                               manifest_checksum = sha256 of the
                                               canonical manifest bytes

    manifest_artifact_id = <domain>-manifest-<sha256(canonical_json({
        "manifest_artifact_id_schema": "sceneops.unit_manifest_artifact_id/v1",
        "unit_id":                     unit_id,
        "manifest_checksum":           manifest_checksum
    }))[:32]>

The manifest ArtifactRecord id is a function of the revision, so a rebuild
of identical bytes converges on one record and two revisions never share
one. Neither identity includes a DatasetVersion or execution context.

The producer fingerprint determines the revision: one unit and one
fingerprint have at most one manifest checksum. A second checksum for the
same fingerprint means the producer is not deterministic, and registering
it fails loudly instead of adding a revision. A different fingerprint
(another channel selection, semantics version or normalization rule) is a
new revision of the same unit.

Registration decision for one candidate revision, given every registered
revision of its unit (``decide_revision_registration``)::

    nothing registered for the unit                     NEW_UNIT
    the candidate's manifest_checksum is registered     EXISTING_REVISION (no-op)
    nothing registered under the candidate fingerprint  NEW_REVISION
    another checksum under the candidate fingerprint    NonDeterministicProducerError

Registration only adds: it never deletes or repoints a revision, and a unit
has no "current" revision. Every consumer addresses a revision.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Final, Protocol

from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN

from .identity import unit_id_domain

MANIFEST_ARTIFACT_ID_SCHEMA_V1: Final = "sceneops.unit_manifest_artifact_id/v1"
_MANIFEST_ARTIFACT_ID_DIGEST_HEX_LENGTH: Final = 32
_CHECKSUM_RE: Final = re.compile(SHA256_CHECKSUM_PATTERN)


class RevisionPinError(ValueError):
    """A revision's manifest artifact id is not the one its
    ``(unit_id, manifest_checksum)`` derives."""


class RevisionConflictError(ValueError):
    """A revision contradicts the revisions registered for its unit."""


class NonDeterministicProducerError(RevisionConflictError):
    """One unit and one producer fingerprint with two manifest checksums."""


class InconsistentRevisionsError(RevisionConflictError):
    """The registered revisions of a unit already violate the revision
    invariants; nothing may be decided on top of them."""


class RevisionDecision(StrEnum):
    NEW_UNIT = "new_unit"
    NEW_REVISION = "new_revision"
    EXISTING_REVISION = "existing_revision"


class UnitRevisionFacts(Protocol):
    """What the revision rules read from a revision: satisfied by the
    domain records that project one manifest revision."""

    @property
    def manifest_artifact_id(self) -> str: ...

    @property
    def manifest_checksum(self) -> str: ...

    @property
    def producer_fingerprint(self) -> str: ...


def _check_checksum(manifest_checksum: str) -> str:
    if not isinstance(manifest_checksum, str) or not _CHECKSUM_RE.fullmatch(
        manifest_checksum
    ):
        raise ValueError(
            f"manifest_checksum must be 'sha256:<64 hex>', got {manifest_checksum!r}"
        )
    return manifest_checksum


def manifest_artifact_id_for(*, unit_id: str, manifest_checksum: str) -> str:
    """The deterministic id of the manifest ArtifactRecord of one revision."""
    domain = unit_id_domain(unit_id)
    document = {
        "manifest_artifact_id_schema": MANIFEST_ARTIFACT_ID_SCHEMA_V1,
        "unit_id": unit_id,
        "manifest_checksum": _check_checksum(manifest_checksum),
    }
    digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    return f"{domain}-manifest-{digest[:_MANIFEST_ARTIFACT_ID_DIGEST_HEX_LENGTH]}"


def check_revision_pin(
    *, unit_id: str, manifest_artifact_id: str, manifest_checksum: str
) -> None:
    """A pin names a revision of ``unit_id`` only if its artifact id is the
    one the revision derives; any other id names another unit's revision,
    or none."""
    expected = manifest_artifact_id_for(
        unit_id=unit_id, manifest_checksum=manifest_checksum
    )
    if manifest_artifact_id != expected:
        raise RevisionPinError(
            f"manifest_artifact_id {manifest_artifact_id!r} is not the revision "
            f"({unit_id}, {manifest_checksum}); expected {expected!r}"
        )


def decide_revision_registration(
    *,
    unit_id: str,
    candidate: UnitRevisionFacts,
    registered: Iterable[UnitRevisionFacts],
) -> RevisionDecision:
    """Decide how registering ``candidate`` changes the revisions of
    ``unit_id``. ``registered`` is every revision registered for that unit;
    each must pin a revision of it (``check_revision_pin``), so a revision
    of another unit is rejected rather than compared.

    Deciding is pure and repeatable: once the candidate is registered the
    same call returns ``EXISTING_REVISION``, which is what makes a retried
    registration converge.
    """
    _check_revision(unit_id, candidate)
    by_checksum: dict[str, str] = {}
    by_fingerprint: dict[str, str] = {}
    for revision in registered:
        _check_revision(unit_id, revision)
        checksum = revision.manifest_checksum
        fingerprint = revision.producer_fingerprint
        if by_checksum.setdefault(checksum, fingerprint) != fingerprint:
            raise InconsistentRevisionsError(
                f"unit {unit_id}: revision {checksum} is registered under two "
                f"producer fingerprints"
            )
        if by_fingerprint.setdefault(fingerprint, checksum) != checksum:
            raise InconsistentRevisionsError(
                f"unit {unit_id}: producer fingerprint {fingerprint} has two "
                f"registered manifest checksums"
            )

    if not by_checksum:
        return RevisionDecision.NEW_UNIT
    registered_fingerprint = by_checksum.get(candidate.manifest_checksum)
    if registered_fingerprint is not None:
        if registered_fingerprint != candidate.producer_fingerprint:
            # The checksum covers the manifest bytes, fingerprint included,
            # so equal checksums with unequal fingerprints cannot both be
            # true descriptions of one manifest.
            raise InconsistentRevisionsError(
                f"unit {unit_id}: revision {candidate.manifest_checksum} is "
                f"registered under producer fingerprint {registered_fingerprint}, "
                f"the candidate claims {candidate.producer_fingerprint}"
            )
        return RevisionDecision.EXISTING_REVISION
    registered_checksum = by_fingerprint.get(candidate.producer_fingerprint)
    if registered_checksum is not None:
        raise NonDeterministicProducerError(
            f"unit {unit_id}: producer fingerprint {candidate.producer_fingerprint} "
            f"already produced revision {registered_checksum}; the candidate "
            f"{candidate.manifest_checksum} differs, so the producer is not "
            f"deterministic"
        )
    return RevisionDecision.NEW_REVISION


def _check_revision(unit_id: str, revision: UnitRevisionFacts) -> None:
    check_revision_pin(
        unit_id=unit_id,
        manifest_artifact_id=revision.manifest_artifact_id,
        manifest_checksum=revision.manifest_checksum,
    )
    if not isinstance(revision.producer_fingerprint, str) or not _CHECKSUM_RE.fullmatch(
        revision.producer_fingerprint
    ):
        raise ValueError(
            f"producer_fingerprint must be 'sha256:<64 hex>', got "
            f"{revision.producer_fingerprint!r}"
        )


__all__ = [
    "MANIFEST_ARTIFACT_ID_SCHEMA_V1",
    "InconsistentRevisionsError",
    "NonDeterministicProducerError",
    "RevisionConflictError",
    "RevisionDecision",
    "RevisionPinError",
    "UnitRevisionFacts",
    "check_revision_pin",
    "decide_revision_registration",
    "manifest_artifact_id_for",
]
