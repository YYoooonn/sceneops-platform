"""Read-only acquisition reconciliation (ADR-008 §3.2, §5.1). See ``service``."""

from .classify import ClassificationPolicy, classify_run
from .model import (
    RECONCILIATION_REPORT_SCHEMA_V1,
    AcquisitionState,
    ReconciliationReport,
    RunReport,
)
from .service import (
    PostgresRegistrationFacts,
    RegistrationFactSource,
    RegistrationFactsScope,
    postgres_registration_facts,
    reconcile_once,
    register_robot_run_execution_key,
)

__all__ = [
    "RECONCILIATION_REPORT_SCHEMA_V1",
    "AcquisitionState",
    "ClassificationPolicy",
    "PostgresRegistrationFacts",
    "ReconciliationReport",
    "RegistrationFactSource",
    "RegistrationFactsScope",
    "RunReport",
    "classify_run",
    "postgres_registration_facts",
    "reconcile_once",
    "register_robot_run_execution_key",
]
