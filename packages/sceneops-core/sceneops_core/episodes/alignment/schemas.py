from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.episodes.schemas.enums import EpisodeOutcome
from sceneops_core.sensors import SensorModality

from .config import TemporalAlignmentConfig
from .enums import AlignedSignalStatus, AlignedValueKind, AssociationPolicy


class AlignedValue(SceneOpsBaseModel):
    """A resolved/interpolated signal payload, tagged by structural kind
    (SceneOps V2 Request 2.2 §27). Flat-with-nullable-fields, matching the
    existing EpisodeObservationFrame convention rather than introducing a
    discriminated-union type not otherwise used in this codebase. Only the
    fields matching ``kind`` are populated; the rest stay None."""

    kind: AlignedValueKind

    scalar: float | None = None
    vector: list[float] | None = None

    reference_channel: str | None = None
    reference_modality: SensorModality | None = None
    reference_uri: str | None = None
    reference_metadata: JsonDict = Field(default_factory=dict)


class AlignedSignal(SceneOpsBaseModel):
    """One channel's resolved value at one aligned timestep (SceneOps V2
    Request 2.2 §26/§28/§29). ``status`` is always explicit — never inferred
    from ``value is None``."""

    channel: str
    policy: AssociationPolicy
    status: AlignedSignalStatus

    value: AlignedValue | None = None

    # Direct-association provenance (exact / nearest / previous).
    # time_delta_us = aligned_timestamp_us - source_timestamp_us:
    #   positive -> source is earlier, zero -> exact, negative -> source is
    #   later. Never negative for `previous` (SceneOps V2 Request 2.2 §28).
    source_timestamp_us: int | None = None
    time_delta_us: int | None = None

    # Interpolation provenance (SceneOps V2 Request 2.2 §29).
    source_before_timestamp_us: int | None = None
    source_after_timestamp_us: int | None = None
    interpolation_ratio: float | None = None


class LearningStep(SceneOpsBaseModel):
    """One fixed-frequency timestep (SceneOps V2 Request 2.2 §34). Only
    per-timestep data lives here; task/outcome/episode metadata stay on
    AlignedEpisode unless they ever become genuinely per-timestep signals."""

    timestamp_us: int
    observations: dict[str, AlignedSignal] = Field(default_factory=dict)
    actions: dict[str, AlignedSignal] = Field(default_factory=dict)


class AlignedEpisode(SceneOpsBaseModel):
    """Canonical aligned learning representation of one Episode (SceneOps V2
    Request 2.2 §33). Deliberately excludes persistence/runtime fields
    (ArtifactRecord.artifact_id, artifact URI, job_id, pipeline_run_id,
    source manifest content hash) — those are Request 2.3's responsibility,
    not the pure engine's."""

    episode_id: str

    source_start_timestamp_us: int
    source_end_timestamp_us: int
    source_clock: str

    alignment_semantics_version: str
    alignment_config: TemporalAlignmentConfig

    target_frequency_hz: float
    achieved_frequency_hz: float
    dt_us: int

    step_count: int
    duplicate_discarded_count: int

    task: str | None = None
    outcome: EpisodeOutcome = EpisodeOutcome.UNKNOWN
    metadata: JsonDict = Field(default_factory=dict)

    steps: list[LearningStep] = Field(default_factory=list)
