"""Failure classes of ``REGISTER_ROBOT_RUN`` (ADR-008 §5.2).

A failed Job records only the exception *class name* (``Job.error.type``), so
the platform classifies a failure by that name. A permanent failure cannot be
fixed by running the same registration again (the manifest, the recording or
canonical state must change first); everything else is transient. An unknown
type is transient: the retry budget, not a guess, bounds it (12.4).

The names are strings because the exception classes live in the worker
(``sceneops_worker.robots.registration``) and core cannot import it; the
worker's tests assert that every registration exception is classified here and
that every name here resolves to a real class.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class RegistrationFailureClass(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


PERMANENT_REGISTRATION_ERROR_TYPES: Final[frozenset[str]] = frozenset(
    {
        # Worker registration (sceneops_worker.robots.registration)
        "RobotRunRegistrationConflictError",
        "RobotPlatformConflictError",
        "RecordingVerificationError",
        "PublishedArtifactMissingError",
        "InconsistentCanonicalStateError",
        # Manifest parsing (sceneops_core.robots.manifest)
        "RobotRunManifestError",
        "UnsupportedRobotRunManifestVersionError",
        "NonCanonicalRobotRunManifestError",
        # Job parameter validation
        "ValidationError",
    }
)


def classify_registration_failure(error_type: str | None) -> RegistrationFailureClass:
    if error_type in PERMANENT_REGISTRATION_ERROR_TYPES:
        return RegistrationFailureClass.PERMANENT
    return RegistrationFailureClass.TRANSIENT


__all__ = [
    "PERMANENT_REGISTRATION_ERROR_TYPES",
    "RegistrationFailureClass",
    "classify_registration_failure",
]
