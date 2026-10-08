"""Derived acquisition status and operational report (ADR-008 §7, §8 step 12.6):
a per-run ``AcquisitionStatus`` and the aggregates over every run, computed on
demand from the reconciliation and artifact lifecycle facts. Nothing is stored."""

from .derive import (
    build_operational_report,
    derive_health,
    derive_stage,
    derive_status,
    summary_record,
)
from .model import (
    ACQUISITION_OPERATIONAL_REPORT_SCHEMA_V1,
    AcquisitionHealth,
    AcquisitionOperationalReport,
    AcquisitionStage,
    AcquisitionStatus,
)
from .service import acquisition_status_once

__all__ = [
    "ACQUISITION_OPERATIONAL_REPORT_SCHEMA_V1",
    "AcquisitionHealth",
    "AcquisitionOperationalReport",
    "AcquisitionStage",
    "AcquisitionStatus",
    "acquisition_status_once",
    "build_operational_report",
    "derive_health",
    "derive_stage",
    "derive_status",
    "summary_record",
]
