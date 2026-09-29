from __future__ import annotations

from enum import StrEnum


class TelemetryEnvelopeVersion(StrEnum):
    """Streaming-envelope schema version -- deliberately independent of
    DatasetVersion, source-format version, and MCAP schema version (Phase
    6.1 request §4). Only ``V1`` exists today; an envelope carrying any
    other value fails Pydantic validation, which is what gives the
    consumer's "unknown envelope version" rejection for free."""

    V1 = "v1"


class EnvelopeEncoding(StrEnum):
    """How ``TelemetryEnvelope.payload`` bytes are encoded. Transport-level
    only -- the envelope never decodes the payload itself, this just tells
    a downstream consumer which decoder to reach for."""

    ROS2_CDR = "ros2-cdr"
    JSON = "json"
    RAW = "raw"
