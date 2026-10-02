"""Database-free Recording Publisher (ADR-007 §7).

Publishes a finalized local MCAP and its canonical RobotRunManifest to
Object Storage; ``REGISTER_ROBOT_RUN`` later verifies and registers them.
Like the rest of ``sceneops_integrations`` it never imports ``sceneops-db``,
never opens a DB session and never writes ArtifactRecords.
"""

from .facts import (
    SUPPORTED_SOURCE_CLOCKS,
    RecordingFacts,
    RecordingValidationError,
    derive_mcap_facts,
    sha256_checksum,
)
from .publisher import (
    MANIFEST_OBJECT_NAME,
    RECORDING_OBJECT_NAME,
    RecordingPublication,
    RecordingPublicationConflictError,
    RecordingPublicationIntegrityError,
    manifest_uri,
    publish_recording,
    recording_uri,
)

__all__ = [
    "MANIFEST_OBJECT_NAME",
    "RECORDING_OBJECT_NAME",
    "SUPPORTED_SOURCE_CLOCKS",
    "RecordingFacts",
    "RecordingPublication",
    "RecordingPublicationConflictError",
    "RecordingPublicationIntegrityError",
    "RecordingValidationError",
    "derive_mcap_facts",
    "manifest_uri",
    "publish_recording",
    "recording_uri",
    "sha256_checksum",
]
