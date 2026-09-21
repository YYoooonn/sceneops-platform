from __future__ import annotations

from pydantic import ConfigDict, Field, model_validator

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.episodes.alignment.enums import AlignedValueKind, ChannelNamespace

from .errors import DuplicateFeatureChannelError


class EpisodeRef(SceneOpsBaseModel):
    """Immutable semantic coordinate for one aligned Episode revision
    (SceneOps V2 Request 2.7A §3). Never episode_id alone -- one episode_id
    may have multiple aligned revisions, and aligned_artifact_checksum (not
    the lineage-only artifact_id) is the actual revision identity, the same
    checksum-is-identity convention AlignedArtifactRevision/
    SourceLearningExportRef already use elsewhere in this package. Frozen
    so it is hashable and usable as a dict/set key -- two EpisodeRefs with
    the same episode_id but different checksums are distinct."""

    model_config = ConfigDict(frozen=True)

    episode_id: str
    aligned_artifact_checksum: str


class FeatureProjection(SceneOpsBaseModel):
    """A training consumer's explicit, order-significant request for
    observation/action channels (SceneOps V2 Request 2.7A §4). Order is
    never normalized -- it directly determines dense output ordering.
    Observation and action are separate namespaces: the same channel
    string may appear in both without colliding."""

    observation_channels: list[str] = Field(default_factory=list)
    action_channels: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_no_duplicate_channels(self) -> "FeatureProjection":
        for namespace, channels in (
            (ChannelNamespace.OBSERVATION, self.observation_channels),
            (ChannelNamespace.ACTION, self.action_channels),
        ):
            seen: set[str] = set()
            for channel in channels:
                if channel in seen:
                    raise DuplicateFeatureChannelError(
                        f"channel {channel!r} appears more than once in "
                        f"FeatureProjection.{namespace.value}_channels"
                    )
                seen.add(channel)
        return self


class FeatureSchemaEntry(SceneOpsBaseModel):
    """One projected channel's resolved dense-array placement (SceneOps V2
    Request 2.7A §7) -- lets downstream code map a dense dimension index
    back to the SceneOps channel it came from without guessing."""

    channel: str
    kind: AlignedValueKind
    dimension: int
    offset: int


class FeatureSchema(SceneOpsBaseModel):
    """Resolved dense shape for one FeatureProjection against one Episode's
    steps (SceneOps V2 Request 2.7A §7/§8). Entries follow
    FeatureProjection's declared order exactly; offsets are cumulative and
    non-overlapping within each namespace."""

    observations: list[FeatureSchemaEntry] = Field(default_factory=list)
    actions: list[FeatureSchemaEntry] = Field(default_factory=list)

    observation_dim: int = 0
    action_dim: int = 0


class StepSample(SceneOpsBaseModel):
    """Framework-neutral dense representation of one LearningStep (SceneOps
    V2 Request 2.7A §11). Deliberately excludes alignment provenance
    (policy, status, time_delta_us, ...) -- that stays on the underlying
    AlignedSignal/LearningStep; this is a training-consumption view, not a
    provenance view."""

    episode_ref: EpisodeRef
    step_index: int
    timestamp_us: int

    observation: list[float] = Field(default_factory=list)
    action: list[float] = Field(default_factory=list)


class SequenceRef(SceneOpsBaseModel):
    """Semantic reference to one contiguous, single-Episode window (SceneOps
    V2 Request 2.7A §12). Always belongs to exactly one EpisodeRef and never
    crosses an Episode boundary -- there is no continuation semantics
    across EpisodeRefs in v1."""

    model_config = ConfigDict(frozen=True)

    episode_ref: EpisodeRef
    start_step: int = Field(ge=0)
    horizon: int = Field(gt=0)


class SequenceSample(SceneOpsBaseModel):
    """Dense fixed-horizon representation of one SequenceRef (SceneOps V2
    Request 2.7A §13). len(timestamps_us) == len(observation) ==
    len(action) == horizon always -- v1 never pads across an Episode
    boundary and never concatenates across Episodes."""

    episode_ref: EpisodeRef
    start_step: int
    horizon: int

    timestamps_us: list[int] = Field(default_factory=list)
    observation: list[list[float]] = Field(default_factory=list)
    action: list[list[float]] = Field(default_factory=list)
