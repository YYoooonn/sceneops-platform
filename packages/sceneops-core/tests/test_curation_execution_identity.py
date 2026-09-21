"""CURATE_EPISODES execution-key identity (SceneOps V2 Request 2.6A §6/§7).

params_for_execution_key(JobType.CURATE_EPISODES, ...) must inject the
installed validation/profile analysis-semantics versions into the hashed
payload -- these are not job input params, so without this they would never
participate in execution-key dedup at all, meaning a package upgrade that
changes either version could silently reuse a stale pre-upgrade Job.
"""

from __future__ import annotations

import sceneops_core.executions.key as key_module
from sceneops_core.executions import compute_execution_key, params_for_execution_key
from sceneops_core.jobs.schemas import JobType


def _curate_key(params: dict) -> str:
    return compute_execution_key(
        kind="job",
        type=JobType.CURATE_EPISODES.value,
        dataset_id="d1",
        dataset_version="v1",
        params=params_for_execution_key(JobType.CURATE_EPISODES, params),
    )


_BASE_PARAMS = {
    "dataset_id": "d1",
    "dataset_version": "v1",
    "learning_data_export_manifest_artifact_id": "art-export-1",
    "learning_data_export_manifest_checksum": "d" * 64,
    "policy": {"max_overall_missing_ratio": 0.1},
}


class TestValidationProfileSemanticsParticipateInExecutionKey:
    def test_validation_semantics_version_change_changes_execution_key(
        self, monkeypatch
    ) -> None:
        key_v1 = _curate_key(dict(_BASE_PARAMS))
        monkeypatch.setattr(
            key_module, "ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION", "v2"
        )
        key_v2 = _curate_key(dict(_BASE_PARAMS))
        assert key_v1 != key_v2

    def test_profile_semantics_version_change_changes_execution_key(
        self, monkeypatch
    ) -> None:
        key_v1 = _curate_key(dict(_BASE_PARAMS))
        monkeypatch.setattr(
            key_module, "ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION", "v2"
        )
        key_v2 = _curate_key(dict(_BASE_PARAMS))
        assert key_v1 != key_v2

    def test_stable_key_for_identical_params_and_semantics(self) -> None:
        assert _curate_key(dict(_BASE_PARAMS)) == _curate_key(dict(_BASE_PARAMS))

    def test_manifest_artifact_id_lineage_field_excluded(self) -> None:
        params_a = {
            **_BASE_PARAMS,
            "learning_data_export_manifest_artifact_id": "art-A",
        }
        params_b = {
            **_BASE_PARAMS,
            "learning_data_export_manifest_artifact_id": "art-B",
        }
        assert _curate_key(params_a) == _curate_key(params_b)

    def test_different_checksum_changes_key(self) -> None:
        params_a = {**_BASE_PARAMS, "learning_data_export_manifest_checksum": "d" * 64}
        params_b = {**_BASE_PARAMS, "learning_data_export_manifest_checksum": "e" * 64}
        assert _curate_key(params_a) != _curate_key(params_b)


class TestUnrelatedJobTypesUnaffected:
    """Regression: this request must not change dedup behavior for any
    JobType other than CURATE_EPISODES."""

    def test_export_learning_data_transform_unchanged(self) -> None:
        params = {
            "dataset_id": "d1",
            "dataset_version": "v1",
            "inputs": [
                {
                    "episode_id": "ep-1",
                    "aligned_artifact_id": "art-1",
                    "aligned_artifact_checksum": "a" * 64,
                },
                {
                    "episode_id": "ep-2",
                    "aligned_artifact_id": "art-2",
                    "aligned_artifact_checksum": "b" * 64,
                },
            ],
        }
        transformed = params_for_execution_key(JobType.EXPORT_LEARNING_DATA, params)
        assert "validation_semantics_version" not in transformed
        assert "profile_semantics_version" not in transformed
        assert transformed["inputs"] == [
            {"episode_id": "ep-1", "aligned_artifact_checksum": "a" * 64},
            {"episode_id": "ep-2", "aligned_artifact_checksum": "b" * 64},
        ]

    def test_validate_aligned_episode_transform_unchanged(self) -> None:
        params = {"episode_id": "ep-1", "aligned_artifact_id": "art-1"}
        transformed = params_for_execution_key(JobType.VALIDATE_ALIGNED_EPISODE, params)
        assert transformed == {"episode_id": "ep-1"}

    def test_no_op_job_type_unaffected(self) -> None:
        params = {"episode_id": "ep-1"}
        assert params_for_execution_key(JobType.PROFILE_EPISODE, params) == params
