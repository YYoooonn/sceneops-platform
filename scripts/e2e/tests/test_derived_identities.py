"""Static guards for REFERENCE_DERIVED workflows (docs/development/test-matrix.md).

A derived workflow writes into a fixed, test-owned Dataset so that running it again
reuses its state. These tests read the journeys' scripts and the infrastructure
tests' helpers; they run no workflow. The convergence itself is proven by running
each journey twice against the live stack and comparing state snapshots.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
E2E = REPO_ROOT / "scripts" / "e2e"
INFRASTRUCTURE = REPO_ROOT / "tests" / "infrastructure"

# A Dataset id that depends on time, a counter or a random value grows with every run.
GROWING = re.compile(r"\$\(date|\$\$|\$RANDOM|uuid|SUFFIX", re.IGNORECASE)


def _dataset_id_default(script: str) -> str:
    """The default of `export DATASET_ID="${DATASET_ID:-<default>}"` in a journey."""
    text = (E2E / script).read_text()
    match = re.search(r'export DATASET_ID="\$\{DATASET_ID:-([^}]*)\}"', text)
    assert match, f"{script} states no default DATASET_ID"
    return match.group(1)


def test_scene_ml_has_a_fixed_default_identity():
    text = (E2E / "e2e_scene_ml.sh").read_text()
    assert _dataset_id_default("e2e_scene_ml.sh") == "$DEFAULT_DATASET_ID"
    assert 'mock) DEFAULT_DATASET_ID="sceneops-test-scene-ml"' in text
    assert (
        'grounding_dino) DEFAULT_DATASET_ID="sceneops-test-scene-ml-grounding-dino"'
        in text
    )


def test_episode_learning_has_a_fixed_default_identity():
    assert (
        _dataset_id_default("e2e_episode_learning.sh")
        == "sceneops-test-episode-learning"
    )


def test_journey_identities_do_not_grow_with_time_or_randomness():
    for script in ("e2e_scene_ml.sh", "e2e_episode_learning.sh"):
        text = (E2E / script).read_text()
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"DATASET_ID|_RUN_ID|SCENARIO_SET_ID|LABEL_SET_ID", line):
                assert not GROWING.search(line), f"{script}: {line.strip()}"


def test_scene_ml_derived_records_carry_ids_derived_from_the_dataset():
    text = (E2E / "e2e_scene_ml.sh").read_text()
    for variable, prefix in (
        ("SCENARIO_SET_ID", "scset-"),
        ("INFERENCE_RUN_ID", "infer-"),
        ("EVALUATION_RUN_ID", "eval-"),
    ):
        assert re.search(rf'^{variable}="{prefix}\$DATASET_ID', text, re.MULTILINE), (
            variable
        )


def test_infrastructure_tests_own_fixed_datasets():
    spec = importlib.util.spec_from_file_location(
        "infra_support", INFRASTRUCTURE / "infra_support.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.INFRA_PIPELINES_DATASET == "sceneops-test-infra-pipelines"


def test_infrastructure_tests_derive_no_identity_from_randomness():
    for name in (
        "infra_support.py",
        "test_pipeline_execution.py",
    ):
        text = (INFRASTRUCTURE / name).read_text()
        assert "uuid" not in text.lower(), f"{name} derives an identity from a UUID"
        assert "new_dataset_version" not in text, (
            f"{name} creates a DatasetVersion per run"
        )
