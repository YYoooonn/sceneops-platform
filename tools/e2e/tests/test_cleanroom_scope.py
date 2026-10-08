"""Static guards for the cleanroom acceptance (docs/development/test-matrix.md).

The cleanroom proves reconstruction reproducibility only: it composes the reference
commands and the two derived journeys and keeps no verifier or identity of its own. These
tests read the script; they run no workflow (the cleanroom run is the validation).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
E2E = REPO_ROOT / "tools" / "e2e"
CLEANROOM = E2E / "e2e_cleanroom.sh"

# Acceptance surfaces with their own commands; the cleanroom must not repeat them.
OTHER_SURFACES = (
    "e2e-streaming-equivalence",
    "test-integration",
    "SUITE=",
    "test-infrastructure",
    "acceptance-grounding-dino",
    "benchmark",
)


def _code(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def test_cleanroom_owns_no_second_golden_state_verifier():
    assert not (E2E / "cleanroom_verify.sh").exists()
    assert "cleanroom_verify" not in _code(CLEANROOM)


def test_cleanroom_reconstructs_through_the_reference_commands():
    code = _code(CLEANROOM)
    for command in (
        "local-reset",
        "reference-data-verify",
        "reference-contract-bootstrap",
        "reference-contract-verify",
        "e2e-scene-ml",
        "e2e-episode-learning",
    ):
        assert command in code, command
    assert "REQUIRE_PRISTINE=1" in code


def test_cleanroom_does_not_run_other_acceptance_surfaces():
    invocations = [
        line
        for line in _code(CLEANROOM).splitlines()
        if re.search(r"\bmake\b|MAKE_Q", line) and not line.lstrip().startswith("echo")
    ]
    for surface in OTHER_SURFACES:
        assert not [line for line in invocations if surface in line], surface


def test_cleanroom_derived_datasets_are_the_journeys_fixed_identities():
    code = CLEANROOM.read_text()
    scene_ml = re.search(r'SCENE_ML_DATASET="([^"]+)"', code)
    episode = re.search(r'EPISODE_LEARNING_DATASET="([^"]+)"', code)
    assert scene_ml and episode
    assert (
        f'mock) DEFAULT_DATASET_ID="{scene_ml.group(1)}"'
        in (E2E / "e2e_scene_ml.sh").read_text()
    )
    assert (
        f'DATASET_ID:-{episode.group(1)}}}"'
        in (E2E / "e2e_episode_learning.sh").read_text()
    )


def test_cleanroom_identities_do_not_grow_with_time_or_randomness():
    code = _code(CLEANROOM)
    assert "$(date" not in code
    assert "RANDOM" not in code
    assert not re.search(r"sceneops-test-cleanroom", code)
