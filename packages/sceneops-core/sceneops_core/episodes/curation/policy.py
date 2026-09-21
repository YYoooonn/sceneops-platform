from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.episodes.schemas.enums import EpisodeOutcome

from .semantics import CURATION_SEMANTICS_VERSION


class CurationPolicy(SceneOpsBaseModel):
    """Typed, deterministic v1 selection policy over AlignedEpisode revisions
    (SceneOps V2 Request 2.6 §4). Every field is optional and independent --
    an omitted field means "no restriction on this dimension", never "reject
    everything" or "any implicit default threshold". Two CurationPolicy
    instances that differ only in the ordering of a list field are the same
    policy (see curation.identity.curation_policy_hash): list fields here are
    always treated as unordered sets.

    Deliberately excludes anything not already produced by Request 2.4/2.5 --
    no invented quality score, no ML-based signal, no metadata this platform
    doesn't already compute (Request 2.6 §17).
    """

    curation_semantics_version: str = CURATION_SEMANTICS_VERSION

    # Structural coverage -- checked against the set of channel names the
    # AlignedEpisode actually declares (ABSENT, in the frozen-context sense:
    # a channel this candidate never had, distinct from a channel that was
    # merely MISSING at some steps -- that distinction is
    # max_channel_missing_ratio's job, below).
    required_observation_channels: list[str] | None = None
    required_action_channels: list[str] | None = None

    # Reuses AlignedEpisodeProfile's own aggregate fields verbatim -- see
    # Request 2.6 §5, never redefined here.
    max_overall_missing_ratio: float | None = None
    max_channel_missing_ratio: float | None = None
    max_abs_sync_delta_us: int | None = None

    # max(ChannelCoverageProfile.interpolated_ratio) across every channel --
    # the same "max across channels" aggregation
    # AlignedEpisodeProfile.max_channel_missing_ratio already uses, just
    # applied to the (unchanged) per-channel interpolated_ratio field instead
    # of missing_ratio. Not a new metric definition, only a new aggregation
    # of an existing one.
    max_interpolated_ratio: float | None = None

    allowed_tasks: list[str] | None = None
    allowed_outcomes: list[EpisodeOutcome] | None = None


class CurationRejectionCode(StrEnum):
    STRUCTURALLY_INVALID = "structurally_invalid"
    REQUIRED_OBSERVATION_CHANNEL_MISSING = "required_observation_channel_missing"
    REQUIRED_ACTION_CHANNEL_MISSING = "required_action_channel_missing"
    MAX_OVERALL_MISSING_RATIO_EXCEEDED = "max_overall_missing_ratio_exceeded"
    MAX_CHANNEL_MISSING_RATIO_EXCEEDED = "max_channel_missing_ratio_exceeded"
    MAX_INTERPOLATED_RATIO_EXCEEDED = "max_interpolated_ratio_exceeded"
    MAX_ABS_SYNC_DELTA_EXCEEDED = "max_abs_sync_delta_exceeded"
    TASK_NOT_ALLOWED = "task_not_allowed"
    OUTCOME_NOT_ALLOWED = "outcome_not_allowed"


class CurationReason(SceneOpsBaseModel):
    """One machine-readable explanation for a rejection (SceneOps V2 Request
    2.6 §7). ``actual``/``expected_max`` are populated for threshold-style
    rejections; ``channel``/``expected`` for set-membership-style rejections.
    Every field beyond ``code``/``message`` is optional so one shape covers
    every rejection kind without a discriminated union."""

    code: CurationRejectionCode
    message: str

    channel: str | None = None
    actual: float | int | str | None = None
    expected_max: float | int | None = None
    expected: list[str] | None = None


class CurationDecision(SceneOpsBaseModel):
    """Exactly one entry per candidate aligned revision (SceneOps V2 Request
    2.6 §7/§13) -- never collapsed to episode_id alone. ``selected`` is
    always the boolean complement of "reasons is non-empty"; both are kept
    as explicit fields (rather than deriving one from the other at read
    time) so a persisted manifest is self-describing without re-running the
    evaluator."""

    episode_id: str
    aligned_artifact_checksum: str

    selected: bool
    reasons: list[CurationReason] = Field(default_factory=list)


class CurationCandidateFacts(SceneOpsBaseModel):
    """Everything CurationEvaluator needs about one candidate, gathered by
    the worker handler from a resolved+checksum-verified AlignedEpisodeArtifact
    and its freshly recomputed AlignedEpisodeValidationReport/
    AlignedEpisodeProfile (SceneOps V2 Request 2.6 §5/§6/§8). Pure input --
    no DB/ArtifactStore/network reference anywhere on this model.

    When ``valid`` is False every field below it is best-effort/undefined
    (structurally broken data may not support a meaningful profile) --
    CurationEvaluator never reads them in that case; see
    CurationEvaluator.evaluate's short-circuit.
    """

    episode_id: str
    aligned_artifact_checksum: str

    valid: bool
    validation_issue_count: int = 0

    task: str | None = None
    outcome: EpisodeOutcome = EpisodeOutcome.UNKNOWN

    observation_channels: list[str] = Field(default_factory=list)
    action_channels: list[str] = Field(default_factory=list)

    overall_missing_ratio: float = 0.0
    max_channel_missing_ratio: float = 0.0
    max_interpolated_ratio: float = 0.0
    max_abs_sync_delta_us: int | None = None
