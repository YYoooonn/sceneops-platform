from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.scenarios.schemas.records import ScenarioSetRecord
from sceneops_storage.exceptions import ArtifactStoreError


@runtime_checkable
class ScenarioSetStore(Protocol):
    """Minimal protocol satisfied by ScenarioStore and any test fake."""

    async def get(self, scenario_set_id: str) -> ScenarioSetRecord | None: ...


@dataclass(frozen=True)
class ResolvedScenarioSet:
    """Result of resolving a ScenarioSet artifact into concrete scene ID lists.

    All entries in `candidates[]` written by mine_scenarios are scenes that
    passed the candidate filter, so candidate_scene_ids == selected_scene_ids.
    Rejected scenes are counted in rejected_count but are not stored in the
    artifact, so rejected_scene_ids is always empty.
    """

    scenario_set_id: str
    scenario_set_uri: str

    # All scene IDs present in the candidates list (== selected).
    candidate_scene_ids: list[str]
    # Scenes that passed the mine_scenarios filter — use this for detection.
    selected_scene_ids: list[str]
    # Always empty: rejected scenes are not written into the artifact.
    rejected_scene_ids: list[str] = field(default_factory=list)

    candidate_count: int = 0
    selected_count: int = 0
    # Scenes that did not pass the filter (stored as a count in the artifact).
    rejected_count: int = 0


class ScenarioSetSceneResolver:
    """Resolves a scenario_set_id into selected scene IDs for detection.

    Fetch flow:
      scenario_set_id
      → ScenarioSetRecord (DB)
      → scenario_set_uri
      → candidates.json artifact (object storage)
      → list[scene_id]  (all entries in candidates[] are selected)
    """

    def __init__(
        self,
        scenario_store: ScenarioSetStore,
        artifact_store: ArtifactStore,
    ) -> None:
        self._scenario_store = scenario_store
        self._artifact_store = artifact_store

    async def resolve(self, scenario_set_id: str) -> ResolvedScenarioSet:
        record = await self._scenario_store.get(scenario_set_id)
        if record is None:
            raise ValueError(
                f"ScenarioSet not found: {scenario_set_id!r}. "
                "Ensure scenario_curation has completed successfully."
            )

        if not record.scenario_set_uri:
            raise ValueError(
                f"ScenarioSet {scenario_set_id!r} has no scenario_set_uri. "
                "The scenario set record exists but the artifact URI was not recorded."
            )

        try:
            data: Any = await self._artifact_store.read_json(record.scenario_set_uri)
        except ArtifactStoreError as exc:
            raise ValueError(
                f"Failed to load ScenarioSet artifact at {record.scenario_set_uri!r}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise ValueError(
                f"ScenarioSet artifact at {record.scenario_set_uri!r} is not a JSON object"
            )

        if "candidates" not in data:
            raise ValueError(
                f"ScenarioSet artifact at {record.scenario_set_uri!r} is missing 'candidates' key"
            )

        candidates = data["candidates"]
        if not isinstance(candidates, list):
            raise ValueError(
                f"ScenarioSet artifact 'candidates' must be a list, "
                f"got {type(candidates).__name__!r}"
            )

        selected_scene_ids: list[str] = []
        for i, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                raise ValueError(
                    f"Candidate at index {i} in ScenarioSet {scenario_set_id!r} "
                    f"is not a JSON object"
                )
            scene_id = candidate.get("scene_id")
            if not scene_id or not isinstance(scene_id, str):
                raise ValueError(
                    f"Candidate at index {i} in ScenarioSet {scenario_set_id!r} "
                    f"has no valid 'scene_id'"
                )
            selected_scene_ids.append(scene_id)

        n = len(selected_scene_ids)
        artifact_rejected_count = int(data.get("rejected_count", 0))

        return ResolvedScenarioSet(
            scenario_set_id=scenario_set_id,
            scenario_set_uri=record.scenario_set_uri,
            candidate_scene_ids=selected_scene_ids,
            selected_scene_ids=selected_scene_ids,
            rejected_scene_ids=[],
            candidate_count=n,
            selected_count=n,
            rejected_count=artifact_rejected_count,
        )
