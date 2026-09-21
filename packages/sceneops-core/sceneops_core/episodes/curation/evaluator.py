from __future__ import annotations

from .policy import (
    CurationCandidateFacts,
    CurationDecision,
    CurationPolicy,
    CurationReason,
    CurationRejectionCode,
)


class CurationEvaluator:
    """Pure candidate facts + CurationPolicy -> CurationDecision (SceneOps V2
    Request 2.6 §2/§8). No DB/ArtifactStore/network access anywhere in this
    class -- gathering CurationCandidateFacts is the worker handler's job,
    not this evaluator's.

    Boundary semantics: every "max_*" bound is a ``<=`` check -- a candidate
    whose metric exactly equals the configured maximum passes (Request 2.6
    §15 "boundary equality").

    A structurally invalid candidate short-circuits immediately with only
    the STRUCTURALLY_INVALID reason -- every other CurationCandidateFacts
    field is best-effort/undefined on invalid data (Request 2.6 §6), so
    evaluating further predicates against it would not be meaningful.
    Otherwise every predicate is evaluated independently and every
    violation is collected -- a candidate can be rejected for multiple
    reasons at once (Request 2.6 §15).
    """

    def evaluate(
        self, facts: CurationCandidateFacts, policy: CurationPolicy
    ) -> CurationDecision:
        if not facts.valid:
            return CurationDecision(
                episode_id=facts.episode_id,
                aligned_artifact_checksum=facts.aligned_artifact_checksum,
                selected=False,
                reasons=[
                    CurationReason(
                        code=CurationRejectionCode.STRUCTURALLY_INVALID,
                        message=(
                            "aligned artifact failed structural validation "
                            f"(issue_count={facts.validation_issue_count})"
                        ),
                        actual=facts.validation_issue_count,
                    )
                ],
            )

        reasons: list[CurationReason] = []

        if policy.required_observation_channels:
            present = set(facts.observation_channels)
            missing = sorted(
                ch for ch in policy.required_observation_channels if ch not in present
            )
            for channel in missing:
                reasons.append(
                    CurationReason(
                        code=CurationRejectionCode.REQUIRED_OBSERVATION_CHANNEL_MISSING,
                        message=f"required observation channel {channel!r} is absent",
                        channel=channel,
                    )
                )

        if policy.required_action_channels:
            present = set(facts.action_channels)
            missing = sorted(
                ch for ch in policy.required_action_channels if ch not in present
            )
            for channel in missing:
                reasons.append(
                    CurationReason(
                        code=CurationRejectionCode.REQUIRED_ACTION_CHANNEL_MISSING,
                        message=f"required action channel {channel!r} is absent",
                        channel=channel,
                    )
                )

        if (
            policy.max_overall_missing_ratio is not None
            and facts.overall_missing_ratio > policy.max_overall_missing_ratio
        ):
            reasons.append(
                CurationReason(
                    code=CurationRejectionCode.MAX_OVERALL_MISSING_RATIO_EXCEEDED,
                    message=(
                        f"overall_missing_ratio={facts.overall_missing_ratio} "
                        f"exceeds max_overall_missing_ratio="
                        f"{policy.max_overall_missing_ratio}"
                    ),
                    actual=facts.overall_missing_ratio,
                    expected_max=policy.max_overall_missing_ratio,
                )
            )

        if (
            policy.max_channel_missing_ratio is not None
            and facts.max_channel_missing_ratio > policy.max_channel_missing_ratio
        ):
            reasons.append(
                CurationReason(
                    code=CurationRejectionCode.MAX_CHANNEL_MISSING_RATIO_EXCEEDED,
                    message=(
                        f"max_channel_missing_ratio="
                        f"{facts.max_channel_missing_ratio} exceeds "
                        f"max_channel_missing_ratio={policy.max_channel_missing_ratio}"
                    ),
                    actual=facts.max_channel_missing_ratio,
                    expected_max=policy.max_channel_missing_ratio,
                )
            )

        if (
            policy.max_interpolated_ratio is not None
            and facts.max_interpolated_ratio > policy.max_interpolated_ratio
        ):
            reasons.append(
                CurationReason(
                    code=CurationRejectionCode.MAX_INTERPOLATED_RATIO_EXCEEDED,
                    message=(
                        f"max_interpolated_ratio={facts.max_interpolated_ratio} "
                        f"exceeds max_interpolated_ratio="
                        f"{policy.max_interpolated_ratio}"
                    ),
                    actual=facts.max_interpolated_ratio,
                    expected_max=policy.max_interpolated_ratio,
                )
            )

        if (
            policy.max_abs_sync_delta_us is not None
            and facts.max_abs_sync_delta_us is not None
            and facts.max_abs_sync_delta_us > policy.max_abs_sync_delta_us
        ):
            reasons.append(
                CurationReason(
                    code=CurationRejectionCode.MAX_ABS_SYNC_DELTA_EXCEEDED,
                    message=(
                        f"max_abs_sync_delta_us={facts.max_abs_sync_delta_us} "
                        f"exceeds max_abs_sync_delta_us="
                        f"{policy.max_abs_sync_delta_us}"
                    ),
                    actual=facts.max_abs_sync_delta_us,
                    expected_max=policy.max_abs_sync_delta_us,
                )
            )

        if policy.allowed_tasks is not None and facts.task not in policy.allowed_tasks:
            reasons.append(
                CurationReason(
                    code=CurationRejectionCode.TASK_NOT_ALLOWED,
                    message=(
                        f"task={facts.task!r} is not in allowed_tasks="
                        f"{sorted(policy.allowed_tasks)}"
                    ),
                    actual=facts.task,
                    expected=sorted(policy.allowed_tasks),
                )
            )

        if (
            policy.allowed_outcomes is not None
            and facts.outcome not in policy.allowed_outcomes
        ):
            reasons.append(
                CurationReason(
                    code=CurationRejectionCode.OUTCOME_NOT_ALLOWED,
                    message=(
                        f"outcome={facts.outcome!r} is not in allowed_outcomes="
                        f"{sorted(o.value for o in policy.allowed_outcomes)}"
                    ),
                    actual=facts.outcome.value,
                    expected=sorted(o.value for o in policy.allowed_outcomes),
                )
            )

        return CurationDecision(
            episode_id=facts.episode_id,
            aligned_artifact_checksum=facts.aligned_artifact_checksum,
            selected=not reasons,
            reasons=reasons,
        )
