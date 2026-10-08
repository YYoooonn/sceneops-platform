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


# Attempts without success the platform makes automatically for one logical
# registration (one execution key) before it stops and an operator decides
# (ADR-008 §5.2). Replacement Jobs share the budget; creating a Job row never
# resets it. An internal constant, not public configuration.
REGISTRATION_ATTEMPT_BUDGET: Final = 3

# How long a REGISTER_ROBOT_RUN Job may show no activity before it is a stall
# candidate. Derived from measured registration latency, not chosen (ADR-008
# §5.3): execution is linear in recording size at ~5 ms/MB (1.07 GB: 5.4 s;
# ~26 s extrapolated to the 5 GB single-PUT limit, B9), queue wait under a 5-way
# burst was 2.2 s, and ``heartbeat_at`` moves only at claim and finish. 900 s is
# >150x the slowest measurement and covers a 5 GB recording at ~6 MB/s. The cost
# is asymmetric: a premature abandon spends one of three attempts of a healthy
# registration, a late one only delays recovery.
DEFAULT_STALL_THRESHOLD_SECONDS: Final = 900.0

# ``Job.error.type`` of a Job the reconciler moved to FAILED because it was
# neither finished nor progressing (ADR-008 §5.3). Transient, and one attempt.
JOB_ABANDONED_ERROR_TYPE: Final = "JobAbandoned"


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
    "DEFAULT_STALL_THRESHOLD_SECONDS",
    "JOB_ABANDONED_ERROR_TYPE",
    "PERMANENT_REGISTRATION_ERROR_TYPES",
    "REGISTRATION_ATTEMPT_BUDGET",
    "RegistrationFailureClass",
    "classify_registration_failure",
]
