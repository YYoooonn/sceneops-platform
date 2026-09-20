from __future__ import annotations

import math
import re

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel

from .enums import AlignedSignalStatus, AlignedValueKind, AssociationPolicy
from .persistence import ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION, AlignedEpisodeArtifact
from .schemas import AlignedEpisode, AlignedSignal, LearningStep
from .semantics import ALIGNMENT_SEMANTICS_VERSION

# Distinct from ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION (envelope shape) and
# ALIGNMENT_SEMANTICS_VERSION (alignment algorithm behavior) -- this is the
# *validator's own rule set* version (SceneOps V2 Request 2.4 §31). Bump it
# when a structural check is added/changed/removed, independent of either
# of the other two versions.
ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION = "v1"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class AlignedEpisodeValidationIssue(SceneOpsBaseModel):
    type: str
    message: str
    blocking: bool = False
    field: str | None = None


class AlignedEpisodeValidationReport(SceneOpsBaseModel):
    validation_semantics_version: str = ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION
    episode_id: str

    valid: bool
    checked_fields: list[str] = Field(default_factory=list)
    issues: list[AlignedEpisodeValidationIssue] = Field(default_factory=list)


class AlignedEpisodeValidator:
    """Pure structural validator for AlignedEpisodeArtifact (SceneOps V2
    Request 2.4).

    Answers "can a downstream consumer interpret this aligned artifact
    according to the frozen temporal contract without encountering
    contradictory or malformed state" -- never "is this good enough for
    training" (that's a future quality/curation policy, not this class. A
    structurally valid artifact may have 90% missing observations, large
    but policy-valid time deltas, or a high interpolation ratio -- those
    are profile facts, see profiling.py, not validation failures).

    Deliberately does not recompute alignment or compare against source
    data (no DB/ArtifactStore/network/environment/worker dependency) --
    validates the artifact's own declared invariants only. Every issue
    this validator can currently raise is a genuine structural
    contradiction (blocking=True); the field exists on the issue model,
    mirroring EpisodeValidationIssue's shape, so a future non-blocking
    check can be added without a schema change.
    """

    def validate(
        self, artifact: AlignedEpisodeArtifact
    ) -> AlignedEpisodeValidationReport:
        issues: list[AlignedEpisodeValidationIssue] = []
        checked: list[str] = ["envelope"]

        self._check_envelope(artifact, issues)
        ae = artifact.aligned_episode

        checked.append("bounds")
        if not self._check_bounds(ae, issues):
            # Bounds are too broken to reason about a timeline at all --
            # same short-circuit shape as EpisodeManifestValidator's
            # missing-manifest case.
            return AlignedEpisodeValidationReport(
                episode_id=ae.episode_id,
                valid=False,
                checked_fields=checked,
                issues=issues,
            )

        checked.extend(["frequency", "timeline", "signals", "duplicate_diagnostic"])
        self._check_frequency(ae, issues)
        self._check_timeline(ae, issues)
        self._check_signals(ae, issues)
        self._check_duplicate_diagnostic(ae, issues)

        valid = not any(issue.blocking for issue in issues)
        return AlignedEpisodeValidationReport(
            episode_id=ae.episode_id,
            valid=valid,
            checked_fields=checked,
            issues=issues,
        )

    # ── envelope ─────────────────────────────────────────────────────────

    @staticmethod
    def _check_envelope(
        artifact: AlignedEpisodeArtifact, issues: list[AlignedEpisodeValidationIssue]
    ) -> None:
        if artifact.schema_version != ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="unsupported_schema_version",
                    message=(
                        f"AlignedEpisodeArtifact.schema_version="
                        f"{artifact.schema_version!r} is not supported "
                        f"(expected {ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION!r})"
                    ),
                    blocking=True,
                    field="schema_version",
                )
            )

        ae = artifact.aligned_episode
        sr = artifact.source_revision
        if sr.episode_id != ae.episode_id:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="episode_id_mismatch",
                    message=(
                        f"source_revision.episode_id={sr.episode_id!r} does not "
                        f"match aligned_episode.episode_id={ae.episode_id!r}"
                    ),
                    blocking=True,
                    field="source_revision.episode_id",
                )
            )

        if not sr.source_artifact_id:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="missing_source_artifact_id",
                    message="source_revision.source_artifact_id is empty",
                    blocking=True,
                    field="source_revision.source_artifact_id",
                )
            )

        if not sr.episode_manifest_uri:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="missing_episode_manifest_uri",
                    message="source_revision.episode_manifest_uri is empty",
                    blocking=True,
                    field="source_revision.episode_manifest_uri",
                )
            )

        if not _SHA256_HEX_RE.match(sr.source_manifest_sha256 or ""):
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="malformed_source_manifest_sha256",
                    message=(
                        f"source_revision.source_manifest_sha256="
                        f"{sr.source_manifest_sha256!r} is not a 64-character "
                        "lowercase hex sha256 digest"
                    ),
                    blocking=True,
                    field="source_revision.source_manifest_sha256",
                )
            )

        if ae.alignment_semantics_version != ALIGNMENT_SEMANTICS_VERSION:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="unsupported_alignment_semantics_version",
                    message=(
                        f"aligned_episode.alignment_semantics_version="
                        f"{ae.alignment_semantics_version!r} is not supported "
                        f"(expected {ALIGNMENT_SEMANTICS_VERSION!r})"
                    ),
                    blocking=True,
                    field="aligned_episode.alignment_semantics_version",
                )
            )

    # ── bounds ───────────────────────────────────────────────────────────

    @staticmethod
    def _check_bounds(
        ae: AlignedEpisode, issues: list[AlignedEpisodeValidationIssue]
    ) -> bool:
        if ae.source_start_timestamp_us > ae.source_end_timestamp_us:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="invalid_bounds",
                    message=(
                        f"source_start_timestamp_us ({ae.source_start_timestamp_us}) "
                        f"is after source_end_timestamp_us "
                        f"({ae.source_end_timestamp_us})"
                    ),
                    blocking=True,
                    field="source_start_timestamp_us",
                )
            )
            return False

        if ae.step_count < 1 or not ae.steps:
            # The frozen v1 fixed-frequency contract (Request 2.2 §8)
            # guarantees step_count >= 1 for any real align_episode() output
            # -- a zero-step artifact violates that contract outright.
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="empty_aligned_episode",
                    message="step_count/steps is empty -- violates the v1 "
                    "fixed-frequency timeline contract, which always "
                    "produces at least one step",
                    blocking=True,
                    field="step_count",
                )
            )
            return False

        return True

    # ── frequency ────────────────────────────────────────────────────────

    @staticmethod
    def _check_frequency(
        ae: AlignedEpisode, issues: list[AlignedEpisodeValidationIssue]
    ) -> None:
        if ae.dt_us < 1:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="invalid_dt_us",
                    message=f"dt_us={ae.dt_us} must be >= 1",
                    blocking=True,
                    field="dt_us",
                )
            )
        if ae.target_frequency_hz <= 0:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="invalid_target_frequency",
                    message=f"target_frequency_hz={ae.target_frequency_hz} must be > 0",
                    blocking=True,
                    field="target_frequency_hz",
                )
            )
        if ae.achieved_frequency_hz <= 0:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="invalid_achieved_frequency",
                    message=(
                        f"achieved_frequency_hz={ae.achieved_frequency_hz} must be > 0"
                    ),
                    blocking=True,
                    field="achieved_frequency_hz",
                )
            )
        if ae.dt_us >= 1:
            expected_achieved = 1_000_000 / ae.dt_us
            if not math.isclose(
                ae.achieved_frequency_hz, expected_achieved, rel_tol=1e-9
            ):
                issues.append(
                    AlignedEpisodeValidationIssue(
                        type="achieved_frequency_inconsistent_with_dt",
                        message=(
                            f"achieved_frequency_hz={ae.achieved_frequency_hz} is "
                            f"not consistent with dt_us={ae.dt_us} "
                            f"(expected {expected_achieved})"
                        ),
                        blocking=True,
                        field="achieved_frequency_hz",
                    )
                )

    # ── timeline ─────────────────────────────────────────────────────────

    @staticmethod
    def _check_timeline(
        ae: AlignedEpisode, issues: list[AlignedEpisodeValidationIssue]
    ) -> None:
        if len(ae.steps) != ae.step_count:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="step_count_mismatch",
                    message=(
                        f"step_count={ae.step_count} does not match "
                        f"len(steps)={len(ae.steps)}"
                    ),
                    blocking=True,
                    field="step_count",
                )
            )

        if ae.dt_us >= 1:
            expected_step_count = (
                ae.source_end_timestamp_us - ae.source_start_timestamp_us
            ) // ae.dt_us + 1
            if ae.step_count != expected_step_count:
                issues.append(
                    AlignedEpisodeValidationIssue(
                        type="step_count_inconsistent_with_bounds",
                        message=(
                            f"step_count={ae.step_count} does not match the frozen "
                            f"v1 endpoint contract's expected value "
                            f"{expected_step_count} for bounds "
                            f"[{ae.source_start_timestamp_us}, "
                            f"{ae.source_end_timestamp_us}] at dt_us={ae.dt_us}"
                        ),
                        blocking=True,
                        field="step_count",
                    )
                )

        if not ae.steps:
            return

        if ae.steps[0].timestamp_us != ae.source_start_timestamp_us:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="first_step_not_at_start",
                    message=(
                        f"steps[0].timestamp_us={ae.steps[0].timestamp_us} != "
                        f"source_start_timestamp_us={ae.source_start_timestamp_us}"
                    ),
                    blocking=True,
                    field="steps[0].timestamp_us",
                )
            )

        previous: int | None = None
        for i, step in enumerate(ae.steps):
            if step.timestamp_us < ae.source_start_timestamp_us or (
                step.timestamp_us > ae.source_end_timestamp_us
            ):
                issues.append(
                    AlignedEpisodeValidationIssue(
                        type="step_outside_bounds",
                        message=(
                            f"steps[{i}].timestamp_us={step.timestamp_us} is outside "
                            f"[{ae.source_start_timestamp_us}, "
                            f"{ae.source_end_timestamp_us}]"
                        ),
                        blocking=True,
                        field=f"steps[{i}].timestamp_us",
                    )
                )
            if previous is not None:
                if step.timestamp_us <= previous:
                    issues.append(
                        AlignedEpisodeValidationIssue(
                            type="non_monotonic_timestamps",
                            message=(
                                f"steps[{i}].timestamp_us={step.timestamp_us} is not "
                                f"strictly greater than steps[{i - 1}]."
                                f"timestamp_us={previous}"
                            ),
                            blocking=True,
                            field=f"steps[{i}].timestamp_us",
                        )
                    )
                elif ae.dt_us >= 1 and step.timestamp_us - previous != ae.dt_us:
                    issues.append(
                        AlignedEpisodeValidationIssue(
                            type="wrong_step_spacing",
                            message=(
                                f"steps[{i}].timestamp_us - steps[{i - 1}]."
                                f"timestamp_us = {step.timestamp_us - previous}, "
                                f"expected dt_us={ae.dt_us}"
                            ),
                            blocking=True,
                            field=f"steps[{i}].timestamp_us",
                        )
                    )
            previous = step.timestamp_us

        AlignedEpisodeValidator._check_channel_key_stability(ae, issues)

    @staticmethod
    def _check_channel_key_stability(
        ae: AlignedEpisode, issues: list[AlignedEpisodeValidationIssue]
    ) -> None:
        """align_episode() emits every known channel as a dict entry on
        every step (dense per-step maps, never sparse -- confirmed by
        reading engine.py: observation/action dicts are built by
        comprehension over the full canonical channel set at every step).
        A channel key set that varies across steps is not a valid v1
        output."""
        first_observations = set(ae.steps[0].observations.keys())
        first_actions = set(ae.steps[0].actions.keys())
        for i, step in enumerate(ae.steps[1:], start=1):
            if set(step.observations.keys()) != first_observations:
                issues.append(
                    AlignedEpisodeValidationIssue(
                        type="unstable_observation_channel_keys",
                        message=(
                            f"steps[{i}].observations key set differs from "
                            "steps[0] -- v1 always emits a dense per-step channel map"
                        ),
                        blocking=True,
                        field=f"steps[{i}].observations",
                    )
                )
            if set(step.actions.keys()) != first_actions:
                issues.append(
                    AlignedEpisodeValidationIssue(
                        type="unstable_action_channel_keys",
                        message=(
                            f"steps[{i}].actions key set differs from steps[0] -- "
                            "v1 always emits a dense per-step channel map"
                        ),
                        blocking=True,
                        field=f"steps[{i}].actions",
                    )
                )

    # ── signals ──────────────────────────────────────────────────────────

    @staticmethod
    def _check_signals(
        ae: AlignedEpisode, issues: list[AlignedEpisodeValidationIssue]
    ) -> None:
        for i, step in enumerate(ae.steps):
            for group in ("observations", "actions"):
                for channel, signal in getattr(step, group).items():
                    field_prefix = f"steps[{i}].{group}[{channel!r}]"
                    AlignedEpisodeValidator._check_one_signal(
                        step, signal, field_prefix, issues
                    )

    @staticmethod
    def _check_one_signal(
        step: LearningStep,
        signal: AlignedSignal,
        field_prefix: str,
        issues: list[AlignedEpisodeValidationIssue],
    ) -> None:
        def issue(type_: str, message: str) -> None:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type=type_, message=f"{field_prefix}: {message}", blocking=True
                )
            )

        if signal.status == AlignedSignalStatus.MISSING:
            if signal.value is not None:
                issue("missing_signal_has_value", "status=missing but value is set")
            if any(
                x is not None
                for x in (
                    signal.source_timestamp_us,
                    signal.time_delta_us,
                    signal.source_before_timestamp_us,
                    signal.source_after_timestamp_us,
                    signal.interpolation_ratio,
                )
            ):
                issue(
                    "missing_signal_has_provenance",
                    "status=missing but carries source/provenance fields",
                )
            return

        if signal.status == AlignedSignalStatus.RESOLVED:
            if signal.value is None:
                issue(
                    "resolved_signal_has_no_value", "status=resolved but value is None"
                )
            if signal.source_timestamp_us is None or signal.time_delta_us is None:
                issue(
                    "resolved_signal_missing_direct_provenance",
                    "status=resolved but source_timestamp_us/time_delta_us is None",
                )
            if any(
                x is not None
                for x in (
                    signal.source_before_timestamp_us,
                    signal.source_after_timestamp_us,
                    signal.interpolation_ratio,
                )
            ):
                issue(
                    "resolved_signal_has_interpolation_provenance",
                    "status=resolved but carries interpolation-only provenance",
                )
            if (
                signal.source_timestamp_us is not None
                and signal.time_delta_us is not None
            ):
                expected_delta = step.timestamp_us - signal.source_timestamp_us
                if signal.time_delta_us != expected_delta:
                    issue(
                        "time_delta_inconsistent",
                        f"time_delta_us={signal.time_delta_us} != "
                        f"timestamp_us - source_timestamp_us = {expected_delta}",
                    )
                if (
                    signal.policy == AssociationPolicy.EXACT
                    and signal.time_delta_us != 0
                ):
                    issue(
                        "exact_policy_nonzero_delta",
                        f"policy=exact but time_delta_us={signal.time_delta_us} != 0",
                    )
                if (
                    signal.policy == AssociationPolicy.LINEAR_INTERPOLATION
                    and signal.time_delta_us != 0
                ):
                    issue(
                        "linear_interpolation_resolved_nonzero_delta",
                        "policy=linear_interpolation can only be resolved on an "
                        f"exact hit (time_delta_us must be 0), got "
                        f"{signal.time_delta_us}",
                    )
                if (
                    signal.policy == AssociationPolicy.PREVIOUS
                    and signal.time_delta_us < 0
                ):
                    issue(
                        "previous_policy_negative_delta",
                        f"policy=previous but time_delta_us={signal.time_delta_us} < 0",
                    )
            return

        if signal.status == AlignedSignalStatus.INTERPOLATED:
            if signal.policy != AssociationPolicy.LINEAR_INTERPOLATION:
                issue(
                    "interpolated_status_wrong_policy",
                    f"status=interpolated but policy={signal.policy!r} "
                    "(only linear_interpolation ever interpolates)",
                )
            if signal.value is None:
                issue(
                    "interpolated_signal_has_no_value",
                    "status=interpolated but value is None",
                )
            elif signal.value.kind not in (
                AlignedValueKind.NUMERIC_SCALAR,
                AlignedValueKind.NUMERIC_VECTOR,
            ):
                issue(
                    "interpolated_unsupported_value_kind",
                    f"status=interpolated but value.kind={signal.value.kind!r} is "
                    "not interpolable (orientation/reference must never be "
                    "interpolated)",
                )
            if (
                signal.source_before_timestamp_us is None
                or signal.source_after_timestamp_us is None
            ):
                issue(
                    "interpolated_signal_missing_bracket",
                    "status=interpolated but source_before/after_timestamp_us is None",
                )
            elif not (
                signal.source_before_timestamp_us
                < step.timestamp_us
                < signal.source_after_timestamp_us
            ):
                issue(
                    "interpolation_bracket_ordering_invalid",
                    f"expected source_before ({signal.source_before_timestamp_us}) "
                    f"< timestamp ({step.timestamp_us}) < source_after "
                    f"({signal.source_after_timestamp_us})",
                )
            if (
                signal.source_timestamp_us is not None
                or signal.time_delta_us is not None
            ):
                issue(
                    "interpolated_signal_has_direct_provenance",
                    "status=interpolated but carries direct-association-only "
                    "provenance (source_timestamp_us/time_delta_us)",
                )
            if signal.interpolation_ratio is not None:
                if not (0 < signal.interpolation_ratio < 1):
                    issue(
                        "interpolation_ratio_out_of_range",
                        f"interpolation_ratio={signal.interpolation_ratio} is not "
                        "strictly between 0 and 1",
                    )
                elif (
                    signal.source_before_timestamp_us is not None
                    and signal.source_after_timestamp_us is not None
                ):
                    span = (
                        signal.source_after_timestamp_us
                        - signal.source_before_timestamp_us
                    )
                    expected_ratio = (
                        step.timestamp_us - signal.source_before_timestamp_us
                    ) / span
                    if not math.isclose(
                        signal.interpolation_ratio, expected_ratio, rel_tol=1e-9
                    ):
                        issue(
                            "interpolation_ratio_inconsistent",
                            f"interpolation_ratio={signal.interpolation_ratio} does "
                            f"not match recomputed ratio {expected_ratio}",
                        )
            return

    # ── duplicate diagnostic ────────────────────────────────────────────

    @staticmethod
    def _check_duplicate_diagnostic(
        ae: AlignedEpisode, issues: list[AlignedEpisodeValidationIssue]
    ) -> None:
        if ae.duplicate_discarded_count < 0:
            issues.append(
                AlignedEpisodeValidationIssue(
                    type="negative_duplicate_discarded_count",
                    message=(
                        f"duplicate_discarded_count={ae.duplicate_discarded_count} "
                        "must be >= 0"
                    ),
                    blocking=True,
                    field="duplicate_discarded_count",
                )
            )
