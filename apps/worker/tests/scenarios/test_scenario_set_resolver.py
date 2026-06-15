"""Unit tests for ScenarioSetSceneResolver.

The resolver converts a scenario_set_id into selected scene IDs by:
  1. Fetching ScenarioSetRecord from DB (via ScenarioStore.get)
  2. Loading candidates.json from scenario_set_uri via ArtifactStore.read_json
  3. Returning all scene IDs in candidates[] as selected_scene_ids

Key invariant confirmed from mine_scenarios.py:
  - candidates[] only contains scenes that PASSED the filter (all are selected)
  - rejected scenes are counted in rejected_count but NOT stored in the artifact
  - candidates[].status is the scene's SceneStatus (validated/profiled/etc), not
    "selected"/"rejected"
"""

from __future__ import annotations

import pytest

from sceneops_core.scenarios.schemas.records import ScenarioSetRecord
from sceneops_storage.exceptions import ArtifactNotFoundError
from sceneops_worker.scenarios.resolver import (
    ResolvedScenarioSet,
    ScenarioSetSceneResolver,
)


# ── fakes ─────────────────────────────────────────────────────────────────────


class FakeScenarioStore:
    def __init__(self, record: ScenarioSetRecord | None) -> None:
        self._record = record

    async def get(self, scenario_set_id: str) -> ScenarioSetRecord | None:
        return self._record


class FakeArtifactStore:
    def __init__(self, payloads: dict[str, object]) -> None:
        self._payloads = payloads

    async def read_json(self, uri: str) -> object:
        if uri not in self._payloads:
            raise ArtifactNotFoundError(uri)
        return self._payloads[uri]


# ── helpers ───────────────────────────────────────────────────────────────────

_SCENARIO_SET_URI = "file:///artifacts/scenario_mining/run-001/candidates.json"


def _record(uri: str | None = _SCENARIO_SET_URI) -> ScenarioSetRecord:
    return ScenarioSetRecord(
        scenario_set_id="scset-001",
        scenario_set_uri=uri,
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        scenario_count=2,
    )


def _candidates_payload(candidates: list[dict]) -> dict:
    return {
        "scenario_set_id": "scset-001",
        "dataset_id": "nuscenes",
        "dataset_version": "v1.0-mini",
        "candidate_count": len(candidates),
        "selected_count": len(candidates),
        "rejected_count": 5,
        "candidates": candidates,
    }


def _resolver(
    record: ScenarioSetRecord | None,
    payload: object | None = None,
    uri: str = _SCENARIO_SET_URI,
) -> ScenarioSetSceneResolver:
    payloads: dict[str, object] = {}
    if payload is not None:
        payloads[uri] = payload
    return ScenarioSetSceneResolver(
        scenario_store=FakeScenarioStore(record),
        artifact_store=FakeArtifactStore(payloads),
    )


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_returns_selected_scene_ids():
    candidates = [
        {
            "candidate_id": "scene-a",
            "scene_id": "scene-a",
            "status": "validated",
            "annotation_count": 10,
            "has_ground_truth": True,
        },
        {
            "candidate_id": "scene-b",
            "scene_id": "scene-b",
            "status": "profiled",
            "annotation_count": 5,
            "has_ground_truth": True,
        },
    ]
    resolver = _resolver(_record(), _candidates_payload(candidates))
    result = await resolver.resolve("scset-001")

    assert isinstance(result, ResolvedScenarioSet)
    assert result.scenario_set_id == "scset-001"
    assert result.scenario_set_uri == _SCENARIO_SET_URI
    assert result.selected_scene_ids == ["scene-a", "scene-b"]
    assert result.candidate_scene_ids == ["scene-a", "scene-b"]
    assert result.rejected_scene_ids == []
    assert result.selected_count == 2
    assert result.candidate_count == 2
    assert result.rejected_count == 5


@pytest.mark.asyncio
async def test_resolve_missing_record_raises():
    resolver = _resolver(record=None)
    with pytest.raises(ValueError, match="ScenarioSet not found.*scset-missing"):
        await resolver.resolve("scset-missing")


@pytest.mark.asyncio
async def test_resolve_missing_scenario_set_uri_raises():
    resolver = _resolver(_record(uri=None))
    with pytest.raises(ValueError, match="has no scenario_set_uri"):
        await resolver.resolve("scset-001")


@pytest.mark.asyncio
async def test_resolve_artifact_not_found_raises():
    # payload dict is empty → ArtifactNotFoundError will be raised by FakeArtifactStore
    resolver = _resolver(_record(), payload=None)
    with pytest.raises(ValueError, match="Failed to load ScenarioSet artifact"):
        await resolver.resolve("scset-001")


@pytest.mark.asyncio
async def test_resolve_missing_candidates_key_raises():
    bad_payload = {"scenario_set_id": "scset-001"}  # no "candidates" key
    resolver = _resolver(_record(), bad_payload)
    with pytest.raises(ValueError, match="missing 'candidates' key"):
        await resolver.resolve("scset-001")


@pytest.mark.asyncio
async def test_resolve_candidate_without_scene_id_raises():
    bad_candidates = [{"candidate_id": "no-scene-id", "annotation_count": 5}]
    resolver = _resolver(_record(), _candidates_payload(bad_candidates))
    with pytest.raises(ValueError, match="no valid 'scene_id'"):
        await resolver.resolve("scset-001")


@pytest.mark.asyncio
async def test_resolve_selected_candidate_included():
    candidates = [{"scene_id": "scene-x", "status": "validated", "annotation_count": 3}]
    resolver = _resolver(_record(), _candidates_payload(candidates))
    result = await resolver.resolve("scset-001")
    assert "scene-x" in result.selected_scene_ids


@pytest.mark.asyncio
async def test_resolve_empty_candidates_produces_empty_selected():
    resolver = _resolver(_record(), _candidates_payload([]))
    result = await resolver.resolve("scset-001")
    assert result.selected_scene_ids == []
    assert result.candidate_count == 0
    assert result.selected_count == 0
    assert result.rejected_count == 5  # still preserved from artifact
