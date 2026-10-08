"""Database-free Recording Publisher (ADR-007 §7).

Publishes a finalized local MCAP and its canonical RobotRunManifest to
Object Storage, either from explicit inputs (``publish_recording``) or from a
finalized capture's receipt (``publish_from_capture``); ``REGISTER_ROBOT_RUN``
later verifies and registers them.
``check_l1_recording`` is the L1 conformance suite every recording writer's
output is tested against (ADR-007 §29.5), and ``iter_recording_messages`` /
``Ros2Decoder`` are the one reader canonicalization uses (§29.6).
Like the rest of ``sceneops_recording`` it never imports ``sceneops-db``,
never opens a DB session and never writes ArtifactRecords.
"""

from .capture_scan import PARTIAL_DIRNAME, scan_capture_volume
from .conformance import (
    ACQUISITION_ORIGIN_METADATA,
    ChannelReport,
    ConformanceReport,
    ConformanceViolation,
    check_l1_recording,
)
from .equivalence import (
    EquivalenceReport,
    compare_recording_contents,
    compare_recordings,
    semantic_recording_content,
)
from .facts import (
    SUPPORTED_SOURCE_CLOCKS,
    RecordingFacts,
    RecordingValidationError,
    derive_mcap_facts,
    sha256_checksum,
)
from .reader import (
    RecordingMessage,
    RecordingReadError,
    Ros2Decoder,
    header_stamps,
    iter_recording_messages,
    stamp_ns,
)
from .from_capture import (
    CaptureReceiptMismatchError,
    CaptureReceiptMissingError,
    publish_from_capture,
    read_capture_receipt,
)
from .publish_pending import (
    PendingOutcome,
    PendingResult,
    PublishPendingReport,
    publish_pending,
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
    "ACQUISITION_ORIGIN_METADATA",
    "MANIFEST_OBJECT_NAME",
    "PARTIAL_DIRNAME",
    "RECORDING_OBJECT_NAME",
    "SUPPORTED_SOURCE_CLOCKS",
    "CaptureReceiptMismatchError",
    "CaptureReceiptMissingError",
    "ChannelReport",
    "EquivalenceReport",
    "compare_recording_contents",
    "compare_recordings",
    "semantic_recording_content",
    "ConformanceReport",
    "ConformanceViolation",
    "RecordingFacts",
    "RecordingMessage",
    "RecordingReadError",
    "Ros2Decoder",
    "header_stamps",
    "iter_recording_messages",
    "stamp_ns",
    "RecordingPublication",
    "RecordingPublicationConflictError",
    "RecordingPublicationIntegrityError",
    "RecordingValidationError",
    "check_l1_recording",
    "derive_mcap_facts",
    "manifest_uri",
    "PendingOutcome",
    "PendingResult",
    "PublishPendingReport",
    "publish_from_capture",
    "publish_pending",
    "publish_recording",
    "read_capture_receipt",
    "recording_uri",
    "scan_capture_volume",
    "sha256_checksum",
]
