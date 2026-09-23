from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.episodes.learning import (
    EpisodeRef,
    FeatureProjection,
    FeatureSchema,
    MissingFeaturePolicy,
)
from sceneops_core.episodes.schemas.enums import EpisodeOutcome

from .enums import MappingKind, SemanticField, UnsupportedSemanticPolicy


class ExternalExportConfig(SceneOpsBaseModel):
    """The caller's explicit request for one ExternalDatasetAdapter.export()
    run (SceneOps V2 Request 3.1 §2/§6). ``projection`` reuses Phase 2.7A's
    FeatureProjection unchanged -- snapshot selection (which EpisodeRefs),
    feature projection (which channels/order), and external serialization
    (this package's adapters) stay three separate concerns."""

    projection: FeatureProjection

    # None means "every EpisodeRef the source SceneOpsDataset exposes" --
    # mirrors LearningDataExportManifest.inputs/EpisodeCurationManifest's
    # convention of never inferring "latest"/"all" implicitly at the wrong
    # layer, while still letting a caller narrow an export explicitly.
    episode_refs: list[EpisodeRef] | None = None

    missing_policy: MissingFeaturePolicy = MissingFeaturePolicy.ERROR
    unsupported_semantic_policy: UnsupportedSemanticPolicy = (
        UnsupportedSemanticPolicy.FAIL
    )


class ExternalStep(SceneOpsBaseModel):
    """Framework-neutral external-format view of one LearningStep (SceneOps
    V2 Request 3.1 §2), structurally mirroring
    sceneops_core.episodes.learning.StepSample minus ``episode_ref`` --
    that identity lives once on the enclosing ExternalEpisode instead of
    being repeated per step."""

    step_index: int
    timestamp_us: int

    observation: list[float] = Field(default_factory=list)
    action: list[float] = Field(default_factory=list)


class ExternalEpisode(SceneOpsBaseModel):
    """Framework-neutral external-format view of one Episode revision
    (SceneOps V2 Request 3.1 §2/§4). ``episode_ref`` is the exact
    traceability coordinate (episode_id + aligned_artifact_checksum, never
    an ArtifactRecord id) back to the SceneOps revision this projection came
    from."""

    episode_ref: EpisodeRef

    task: str | None = None
    outcome: EpisodeOutcome = EpisodeOutcome.UNKNOWN

    steps: list[ExternalStep] = Field(default_factory=list)


class SemanticLoss(SceneOpsBaseModel):
    """One explicit record of a SceneOps semantic an export could not carry
    into the target format losslessly (SceneOps V2 Request 3.1 §5/§8).
    ``mapping`` is always LOSSY_EXPLICIT or UNSUPPORTED here -- fields the
    adapter maps losslessly are never reported (see
    ExternalDatasetAdapter._resolve_semantic_losses)."""

    field: SemanticField
    mapping: MappingKind
    detail: str


class ExternalExportReport(SceneOpsBaseModel):
    """Everything one ExternalDatasetAdapter.export() run reports (SceneOps
    V2 Request 3.1 §7) -- an in-memory contract only; no job/artifact/DB
    persistence exists for this yet."""

    format_name: str
    format_version: str

    source_dataset_id: str
    source_dataset_version: str
    source_export_id: str

    # Exactly the EpisodeRefs this export selected (config.episode_refs, or
    # every EpisodeRef the source SceneOpsDataset exposed if None) -- in
    # export order, which is always dataset.episodes() order or the
    # caller's explicit order.
    source_episode_refs: list[EpisodeRef] = Field(default_factory=list)

    exported_episode_count: int = 0
    exported_step_count: int = 0

    # None only when exported_episode_count == 0 -- there is then no
    # FeatureSchema to resolve config.projection against.
    feature_schema: FeatureSchema | None = None

    semantic_losses: list[SemanticLoss] = Field(default_factory=list)
