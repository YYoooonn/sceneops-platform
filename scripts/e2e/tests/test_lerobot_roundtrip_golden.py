"""Tests for scripts/e2e/lerobot_roundtrip_golden.py (SceneOps V2 Request
4.3): proving the shared golden oracle actually catches real drift, not
just that it passes against already-correct data (which every real E2E run
-- host or containerized -- already exercises).

``verify_export_report`` only reads attributes off an ``ExternalExportReport``
-- no I/O, no lerobot dataset on disk -- so these are cheap, pure-Python
mismatch-detection tests. ``verify_official_readback`` needs a real LeRobot
dataset on disk to read back and is exercised at the full-E2E level
(``make e2e-lerobot``/``make e2e-lerobot-container``) instead; fabricating
a deliberately-wrong on-disk LeRobot dataset purely to unit-test its
mismatch branches was judged lower value than the effort it costs here
(SceneOps V2 Request 4.3 §7 scope decision).

Imports ``lerobot`` transitively (``lerobot_roundtrip_golden`` imports
``OfficialLeRobotDataset`` at module scope even though this file's tests
never call ``verify_official_readback``) -- skipped entirely, not
erroring, when the ``lerobot`` extra isn't installed, exactly like
``test_lerobot_adapter.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("lerobot")

_SCRIPTS_E2E_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_E2E_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_E2E_DIR))

from lerobot_roundtrip_golden import (  # noqa: E402
    EXPECTED_EPISODE_ORDER,
    RoundtripAssertionError,
    verify_export_report,
)
from sceneops_analytics.external_adapters import (  # noqa: E402
    ExternalExportReport,
    MappingKind,
    SemanticField,
    SemanticLoss,
)
from sceneops_core.episodes.learning import FeatureSchema  # noqa: E402

_VALID_SEMANTIC_LOSSES = [
    SemanticLoss(
        field=SemanticField.EPISODE_IDENTITY,
        mapping=MappingKind.LOSSY_EXPLICIT,
        detail="x",
    ),
    SemanticLoss(
        field=SemanticField.TIMESTAMPS, mapping=MappingKind.LOSSY_EXPLICIT, detail="x"
    ),
    SemanticLoss(
        field=SemanticField.TASK_OUTCOME_METADATA,
        mapping=MappingKind.LOSSY_EXPLICIT,
        detail="x",
    ),
    SemanticLoss(
        field=SemanticField.SIGNAL_STATUS, mapping=MappingKind.UNSUPPORTED, detail="x"
    ),
    SemanticLoss(
        field=SemanticField.SOURCE_REVISION_TRACEABILITY,
        mapping=MappingKind.LOSSY_EXPLICIT,
        detail="x",
    ),
]


def _valid_report() -> ExternalExportReport:
    return ExternalExportReport(
        format_name="lerobot",
        format_version="3.0",
        source_dataset_id="test-e2e-interop",
        source_dataset_version="test-v1",
        source_export_id="deadbeef",
        source_episode_refs=list(EXPECTED_EPISODE_ORDER),
        exported_episode_count=3,
        exported_step_count=22,
        feature_schema=FeatureSchema(observation_dim=7, action_dim=4),
        semantic_losses=list(_VALID_SEMANTIC_LOSSES),
    )


def test_valid_report_passes():
    verify_export_report(_valid_report())


def test_wrong_episode_count_is_caught():
    report = _valid_report().model_copy(update={"exported_episode_count": 2})
    with pytest.raises(RoundtripAssertionError, match="exported_episode_count"):
        verify_export_report(report)


def test_wrong_step_count_is_caught():
    report = _valid_report().model_copy(update={"exported_step_count": 21})
    with pytest.raises(RoundtripAssertionError, match="exported_step_count"):
        verify_export_report(report)


def test_wrong_episode_order_is_caught():
    reversed_order = list(reversed(EXPECTED_EPISODE_ORDER))
    report = _valid_report().model_copy(update={"source_episode_refs": reversed_order})
    with pytest.raises(RoundtripAssertionError, match="source_episode_refs"):
        verify_export_report(report)


def test_missing_feature_schema_is_caught():
    report = _valid_report().model_copy(update={"feature_schema": None})
    with pytest.raises(RoundtripAssertionError, match="feature_schema is None"):
        verify_export_report(report)


def test_wrong_observation_dim_is_caught():
    report = _valid_report().model_copy(
        update={"feature_schema": FeatureSchema(observation_dim=8, action_dim=4)}
    )
    with pytest.raises(RoundtripAssertionError, match="observation_dim"):
        verify_export_report(report)


def test_wrong_action_dim_is_caught():
    report = _valid_report().model_copy(
        update={"feature_schema": FeatureSchema(observation_dim=7, action_dim=3)}
    )
    with pytest.raises(RoundtripAssertionError, match="action_dim"):
        verify_export_report(report)


def test_missing_expected_semantic_loss_is_caught():
    losses = [
        loss
        for loss in _VALID_SEMANTIC_LOSSES
        if loss.field is not SemanticField.SIGNAL_STATUS
    ]
    report = _valid_report().model_copy(update={"semantic_losses": losses})
    with pytest.raises(RoundtripAssertionError, match="signal_status"):
        verify_export_report(report)


def test_wrong_semantic_loss_mapping_is_caught():
    losses = [
        loss.model_copy(update={"mapping": MappingKind.UNSUPPORTED})
        if loss.field is SemanticField.TIMESTAMPS
        else loss
        for loss in _VALID_SEMANTIC_LOSSES
    ]
    report = _valid_report().model_copy(update={"semantic_losses": losses})
    with pytest.raises(RoundtripAssertionError, match="timestamps"):
        verify_export_report(report)


def test_unexpected_lossless_field_is_caught():
    extra_loss = SemanticLoss(
        field=SemanticField.STEP_ORDERING,
        mapping=MappingKind.LOSSY_EXPLICIT,
        detail="x",
    )
    report = _valid_report().model_copy(
        update={"semantic_losses": [*_VALID_SEMANTIC_LOSSES, extra_loss]}
    )
    with pytest.raises(RoundtripAssertionError, match="step_ordering"):
        verify_export_report(report)
