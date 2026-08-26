from __future__ import annotations

from sceneops_core.executions import compute_execution_key


def _key(**overrides):
    base = dict(
        kind="job",
        type="export_analytics_snapshot",
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        params={"tables": ["scenes"]},
    )
    base.update(overrides)
    return compute_execution_key(**base)


def test_same_inputs_produce_same_key():
    assert _key() == _key()


def test_different_dataset_version_changes_key():
    assert _key() != _key(dataset_version="v1.0-trainval")


def test_different_params_changes_key():
    assert _key() != _key(params={"tables": ["scenes", "samples"]})


def test_param_key_order_does_not_change_key():
    a = compute_execution_key(
        kind="job",
        type="t",
        dataset_id="d",
        dataset_version="v",
        params={"a": 1, "b": 2},
    )
    b = compute_execution_key(
        kind="job",
        type="t",
        dataset_id="d",
        dataset_version="v",
        params={"b": 2, "a": 1},
    )
    assert a == b


def test_different_kind_changes_key():
    assert _key(kind="job") != _key(kind="pipeline_run")


def test_different_type_changes_key():
    assert _key() != _key(type="evaluate_detection")


def test_model_fields_affect_key():
    assert _key(model_id="gdino") != _key(model_id="gdino", model_version="v2")


def test_key_is_prefixed_with_type():
    key = _key(type="predict_detection")
    assert key.startswith("predict_detection:")
