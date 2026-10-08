"""Acquisition reconciliation (ADR-008 §3.2, §5.1-§5.3): ``service`` observes and
classifies read-only; ``recovery`` acts, boundedly, on the eligible states."""

from .classify import ClassificationPolicy, classify_run
from .model import (
    RECONCILIATION_REPORT_SCHEMA_V1,
    AcquisitionState,
    ReconciliationReport,
    RecoveryAction,
    RecoveryActionKind,
    RecoveryOutcome,
    RunReport,
)
from .recovery import (
    DEFAULT_MAX_ACTIONS_PER_PASS,
    PostgresStalledJobControl,
    RecoveryPolicy,
    RegistrationSubmitter,
    StalledJobControl,
    plan_recovery,
    reconcile_and_recover,
    recover,
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
    "DEFAULT_MAX_ACTIONS_PER_PASS",
    "RECONCILIATION_REPORT_SCHEMA_V1",
    "AcquisitionState",
    "ClassificationPolicy",
    "PostgresRegistrationFacts",
    "PostgresStalledJobControl",
    "ReconciliationReport",
    "RecoveryAction",
    "RecoveryActionKind",
    "RecoveryOutcome",
    "RecoveryPolicy",
    "RegistrationFactSource",
    "RegistrationFactsScope",
    "RegistrationSubmitter",
    "RunReport",
    "StalledJobControl",
    "classify_run",
    "plan_recovery",
    "postgres_registration_facts",
    "reconcile_and_recover",
    "reconcile_once",
    "recover",
    "register_robot_run_execution_key",
]
