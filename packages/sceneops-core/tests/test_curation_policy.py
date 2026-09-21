from __future__ import annotations

from sceneops_core.episodes.curation import (
    CurationCandidateFacts,
    CurationEvaluator,
    CurationPolicy,
    CurationRejectionCode,
    curation_policy_hash,
    episode_curation_id,
)
from sceneops_core.episodes.schemas.enums import EpisodeOutcome

_evaluator = CurationEvaluator()


def _facts(**overrides) -> CurationCandidateFacts:
    defaults = dict(
        episode_id="ep-1",
        aligned_artifact_checksum="c" * 64,
        valid=True,
        task="pick",
        outcome=EpisodeOutcome.SUCCESS,
        observation_channels=["camera.front", "state.position"],
        action_channels=["steering"],
        overall_missing_ratio=0.05,
        max_channel_missing_ratio=0.05,
        max_interpolated_ratio=0.0,
        max_abs_sync_delta_us=1_000,
    )
    defaults.update(overrides)
    return CurationCandidateFacts(**defaults)


class TestNoRestrictionPolicy:
    def test_empty_policy_selects_everything(self) -> None:
        decision = _evaluator.evaluate(_facts(), CurationPolicy())
        assert decision.selected is True
        assert decision.reasons == []


class TestSingleRuleFailure:
    def test_missing_required_observation_channel(self) -> None:
        policy = CurationPolicy(required_observation_channels=["camera.rear"])
        decision = _evaluator.evaluate(_facts(), policy)
        assert decision.selected is False
        assert len(decision.reasons) == 1
        assert (
            decision.reasons[0].code
            == CurationRejectionCode.REQUIRED_OBSERVATION_CHANNEL_MISSING
        )
        assert decision.reasons[0].channel == "camera.rear"

    def test_missing_required_action_channel(self) -> None:
        policy = CurationPolicy(required_action_channels=["throttle"])
        decision = _evaluator.evaluate(_facts(), policy)
        assert decision.selected is False
        assert (
            decision.reasons[0].code
            == CurationRejectionCode.REQUIRED_ACTION_CHANNEL_MISSING
        )

    def test_overall_missing_ratio_exceeded(self) -> None:
        policy = CurationPolicy(max_overall_missing_ratio=0.10)
        decision = _evaluator.evaluate(_facts(overall_missing_ratio=0.18), policy)
        assert decision.selected is False
        assert (
            decision.reasons[0].code
            == CurationRejectionCode.MAX_OVERALL_MISSING_RATIO_EXCEEDED
        )
        assert decision.reasons[0].actual == 0.18
        assert decision.reasons[0].expected_max == 0.10

    def test_channel_missing_ratio_exceeded(self) -> None:
        policy = CurationPolicy(max_channel_missing_ratio=0.2)
        decision = _evaluator.evaluate(_facts(max_channel_missing_ratio=0.3), policy)
        assert decision.selected is False
        assert (
            decision.reasons[0].code
            == CurationRejectionCode.MAX_CHANNEL_MISSING_RATIO_EXCEEDED
        )

    def test_interpolated_ratio_exceeded(self) -> None:
        policy = CurationPolicy(max_interpolated_ratio=0.1)
        decision = _evaluator.evaluate(_facts(max_interpolated_ratio=0.2), policy)
        assert decision.selected is False
        assert (
            decision.reasons[0].code
            == CurationRejectionCode.MAX_INTERPOLATED_RATIO_EXCEEDED
        )

    def test_sync_delta_exceeded(self) -> None:
        policy = CurationPolicy(max_abs_sync_delta_us=500)
        decision = _evaluator.evaluate(_facts(max_abs_sync_delta_us=600), policy)
        assert decision.selected is False
        assert (
            decision.reasons[0].code
            == CurationRejectionCode.MAX_ABS_SYNC_DELTA_EXCEEDED
        )

    def test_task_not_allowed(self) -> None:
        policy = CurationPolicy(allowed_tasks=["place"])
        decision = _evaluator.evaluate(_facts(task="pick"), policy)
        assert decision.selected is False
        assert decision.reasons[0].code == CurationRejectionCode.TASK_NOT_ALLOWED

    def test_outcome_not_allowed(self) -> None:
        policy = CurationPolicy(allowed_outcomes=[EpisodeOutcome.SUCCESS])
        decision = _evaluator.evaluate(_facts(outcome=EpisodeOutcome.FAILURE), policy)
        assert decision.selected is False
        assert decision.reasons[0].code == CurationRejectionCode.OUTCOME_NOT_ALLOWED


class TestMultipleRuleFailure:
    def test_candidate_can_fail_multiple_rules_at_once(self) -> None:
        policy = CurationPolicy(
            max_overall_missing_ratio=0.01,
            allowed_tasks=["place"],
            required_observation_channels=["camera.rear"],
        )
        decision = _evaluator.evaluate(_facts(), policy)
        assert decision.selected is False
        codes = {reason.code for reason in decision.reasons}
        assert codes == {
            CurationRejectionCode.MAX_OVERALL_MISSING_RATIO_EXCEEDED,
            CurationRejectionCode.TASK_NOT_ALLOWED,
            CurationRejectionCode.REQUIRED_OBSERVATION_CHANNEL_MISSING,
        }


class TestBoundaryEquality:
    def test_metric_equal_to_max_passes(self) -> None:
        policy = CurationPolicy(
            max_overall_missing_ratio=0.1,
            max_channel_missing_ratio=0.1,
            max_interpolated_ratio=0.1,
            max_abs_sync_delta_us=1_000,
        )
        decision = _evaluator.evaluate(
            _facts(
                overall_missing_ratio=0.1,
                max_channel_missing_ratio=0.1,
                max_interpolated_ratio=0.1,
                max_abs_sync_delta_us=1_000,
            ),
            policy,
        )
        assert decision.selected is True
        assert decision.reasons == []


class TestStructuralInvalidity:
    def test_invalid_candidate_short_circuits_with_single_reason(self) -> None:
        policy = CurationPolicy(max_overall_missing_ratio=0.0)  # would otherwise fail
        decision = _evaluator.evaluate(
            _facts(valid=False, validation_issue_count=3), policy
        )
        assert decision.selected is False
        assert len(decision.reasons) == 1
        assert decision.reasons[0].code == CurationRejectionCode.STRUCTURALLY_INVALID
        assert decision.reasons[0].actual == 3


class TestExplainability:
    def test_every_candidate_appears_exactly_once(self) -> None:
        policy = CurationPolicy(max_overall_missing_ratio=0.1)
        candidates = [
            _facts(episode_id="ep-1", aligned_artifact_checksum="a" * 64),
            _facts(
                episode_id="ep-1",
                aligned_artifact_checksum="b" * 64,
                overall_missing_ratio=0.9,
            ),
            _facts(episode_id="ep-2", aligned_artifact_checksum="c" * 64),
        ]
        decisions = [_evaluator.evaluate(f, policy) for f in candidates]
        keys = [(d.episode_id, d.aligned_artifact_checksum) for d in decisions]
        assert len(keys) == len(set(keys)) == 3
        selected = {d.aligned_artifact_checksum: d.selected for d in decisions}
        assert selected == {"a" * 64: True, "b" * 64: False, "c" * 64: True}

    def test_rejected_candidates_have_deterministic_reason_codes(self) -> None:
        policy = CurationPolicy(max_overall_missing_ratio=0.1)
        facts = _facts(overall_missing_ratio=0.5)
        d1 = _evaluator.evaluate(facts, policy)
        d2 = _evaluator.evaluate(facts, policy)
        assert [r.code for r in d1.reasons] == [r.code for r in d2.reasons]


class TestSameEpisodeMultipleAlignments:
    def test_two_revisions_of_same_episode_are_independently_selectable(self) -> None:
        policy = CurationPolicy(max_overall_missing_ratio=0.1)
        good = _evaluator.evaluate(
            _facts(
                episode_id="ep-1",
                aligned_artifact_checksum="a" * 64,
                overall_missing_ratio=0.05,
            ),
            policy,
        )
        bad = _evaluator.evaluate(
            _facts(
                episode_id="ep-1",
                aligned_artifact_checksum="b" * 64,
                overall_missing_ratio=0.5,
            ),
            policy,
        )
        assert good.selected is True
        assert bad.selected is False
        assert good.episode_id == bad.episode_id == "ep-1"
        assert good.aligned_artifact_checksum != bad.aligned_artifact_checksum


class TestPolicyHashDeterminism:
    def test_equivalent_policies_hash_identically_regardless_of_list_order(
        self,
    ) -> None:
        p1 = CurationPolicy(
            allowed_tasks=["b", "a"], required_observation_channels=["y", "x"]
        )
        p2 = CurationPolicy(
            allowed_tasks=["a", "b"], required_observation_channels=["x", "y"]
        )
        assert curation_policy_hash(p1) == curation_policy_hash(p2)

    def test_different_policies_hash_differently(self) -> None:
        p1 = CurationPolicy(max_overall_missing_ratio=0.1)
        p2 = CurationPolicy(max_overall_missing_ratio=0.2)
        assert curation_policy_hash(p1) != curation_policy_hash(p2)


class TestCurationIdDeterminism:
    def test_same_inputs_produce_same_curation_id(self) -> None:
        h = curation_policy_hash(CurationPolicy(max_overall_missing_ratio=0.1))
        id1 = episode_curation_id(source_export_checksum="d" * 64, policy_hash=h)
        id2 = episode_curation_id(source_export_checksum="d" * 64, policy_hash=h)
        assert id1 == id2

    def test_different_source_export_changes_curation_id(self) -> None:
        h = curation_policy_hash(CurationPolicy(max_overall_missing_ratio=0.1))
        id1 = episode_curation_id(source_export_checksum="d" * 64, policy_hash=h)
        id2 = episode_curation_id(source_export_checksum="e" * 64, policy_hash=h)
        assert id1 != id2

    def test_sha256_prefix_is_ignored_in_source_export_checksum(self) -> None:
        h = curation_policy_hash(CurationPolicy())
        id1 = episode_curation_id(source_export_checksum="d" * 64, policy_hash=h)
        id2 = episode_curation_id(
            source_export_checksum="sha256:" + "d" * 64, policy_hash=h
        )
        assert id1 == id2


class TestCurationIdAnalysisSemanticsPinning:
    """SceneOps V2 Request 2.6A: curation_id must be sensitive to the
    validation/profile analysis semantics that produced CurationCandidateFacts,
    not just the source export + policy (Request 2.6A §1/§2/§7)."""

    def test_stable_identity_same_everything(self) -> None:
        h = curation_policy_hash(CurationPolicy(max_overall_missing_ratio=0.1))
        id1 = episode_curation_id(
            source_export_checksum="d" * 64,
            policy_hash=h,
            curation_semantics_version="v1",
            validation_semantics_version="v1",
            profile_semantics_version="v1",
        )
        id2 = episode_curation_id(
            source_export_checksum="d" * 64,
            policy_hash=h,
            curation_semantics_version="v1",
            validation_semantics_version="v1",
            profile_semantics_version="v1",
        )
        assert id1 == id2

    def test_profile_semantics_change_changes_curation_id(self) -> None:
        h = curation_policy_hash(CurationPolicy())
        id_v1 = episode_curation_id(
            source_export_checksum="d" * 64,
            policy_hash=h,
            profile_semantics_version="v1",
        )
        id_v2 = episode_curation_id(
            source_export_checksum="d" * 64,
            policy_hash=h,
            profile_semantics_version="v2",
        )
        assert id_v1 != id_v2

    def test_validation_semantics_change_changes_curation_id(self) -> None:
        h = curation_policy_hash(CurationPolicy())
        id_v1 = episode_curation_id(
            source_export_checksum="d" * 64,
            policy_hash=h,
            validation_semantics_version="v1",
        )
        id_v2 = episode_curation_id(
            source_export_checksum="d" * 64,
            policy_hash=h,
            validation_semantics_version="v2",
        )
        assert id_v1 != id_v2

    def test_curation_semantics_change_changes_curation_id(self) -> None:
        h = curation_policy_hash(CurationPolicy())
        id_v1 = episode_curation_id(
            source_export_checksum="d" * 64,
            policy_hash=h,
            curation_semantics_version="v1",
        )
        id_v2 = episode_curation_id(
            source_export_checksum="d" * 64,
            policy_hash=h,
            curation_semantics_version="v2",
        )
        assert id_v1 != id_v2

    def test_policy_change_still_changes_curation_id(self) -> None:
        """Regression: existing Request 2.6 behavior (different policy ->
        different policy_hash -> different curation_id) must survive this
        request's identity extension unchanged."""
        h1 = curation_policy_hash(CurationPolicy(max_overall_missing_ratio=0.1))
        h2 = curation_policy_hash(CurationPolicy(max_overall_missing_ratio=0.2))
        id1 = episode_curation_id(source_export_checksum="d" * 64, policy_hash=h1)
        id2 = episode_curation_id(source_export_checksum="d" * 64, policy_hash=h2)
        assert id1 != id2

    def test_defaults_reflect_currently_installed_analysis_semantics(self) -> None:
        """Calling without explicit validation/profile semantics versions
        must use the real installed sceneops-core constants, not an
        independently-duplicated literal (Request 2.6A §3's "must reflect
        the actual pure validator/profiler implementations used")."""
        from sceneops_core.episodes.alignment import (
            ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION,
            ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION,
        )

        h = curation_policy_hash(CurationPolicy())
        default_id = episode_curation_id(source_export_checksum="d" * 64, policy_hash=h)
        explicit_id = episode_curation_id(
            source_export_checksum="d" * 64,
            policy_hash=h,
            validation_semantics_version=ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION,
            profile_semantics_version=ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION,
        )
        assert default_id == explicit_id
