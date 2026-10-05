"""The definitions the API exposes are exactly the four first-class pipelines,
and anything else is refused at the request boundary."""

from __future__ import annotations

import pytest

FINAL = {
    "recording_scene_building": [
        "build_recording_scenes",
        "register_scenes",
        "validate_scene",
        "profile_scene",
    ],
    "recording_episode_building": [
        "build_recording_episodes",
        "register_episodes",
        "validate_episode",
        "profile_episode",
    ],
    "scene_ml_evaluation": [
        "build_scene_sample_views",
        "mine_scenarios",
        "score_scenario_readiness",
        "predict_detection",
        "evaluate_detection",
    ],
    "episode_learning_data_building": ["align_episode", "export_learning_data"],
}
REMOVED = (
    "scenario_curation",
    "detection_evaluation",
    "aligned_episode_building",
    "dataset_scene_ingestion",
    "raw_log_scene_building",
    "raw_log_episode_building",
    "scene_registration",
)


def test_exactly_the_four_pipelines_are_listed_with_their_task_chains(api):
    body = api.get("/pipelines/definitions")
    assert body["count"] == len(FINAL)
    listed = {
        d["type"]: [t["pipelineTaskId"] for t in sorted(d["tasks"], key=lambda t: t["order"])]
        for d in body["definitions"]
    }
    assert listed == FINAL


@pytest.mark.parametrize("pipeline_type", REMOVED)
def test_removed_pipelines_cannot_be_created(api, pipeline_type):
    dataset = api.new_dataset_version("surface")
    response = api.post(
        "/pipelines/runs",
        {"type": pipeline_type, "dataset_id": dataset[0], "dataset_version": dataset[1]},
    )
    assert response.status_code in (400, 422), response.text
