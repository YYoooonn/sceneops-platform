from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .policy import CurationDecision, CurationPolicy
from .semantics import EPISODE_CURATION_MANIFEST_SCHEMA_VERSION


class SourceLearningExportRef(SceneOpsBaseModel):
    """Pinned LearningDataExportManifest revision this curation run selected
    over (SceneOps V2 Request 2.6 §3/§9). ``artifact_id`` is producer
    lineage; ``checksum`` (the manifest's own persisted-bytes checksum) is
    the content-revision identity that participates in curation_id --
    mirroring EpisodeSourceRevision's lineage-id-vs-content-hash split."""

    artifact_id: str
    checksum: str


class CurationCandidateSummary(SceneOpsBaseModel):
    total: int = 0
    selected: int = 0
    rejected: int = 0


class EpisodeCurationManifest(SceneOpsBaseModel):
    """Persisted result of one curation run (SceneOps V2 Request 2.6 §9):
    exactly which pinned AlignedEpisode revisions were selected, which were
    rejected, why, under which policy, and from which source snapshot.

    Never mutates Episode/AlignedEpisode/DatasetVersion state and never
    rewrites the source columnar Parquet -- this manifest is a selection
    layer over an existing, immutable LearningDataExportManifest snapshot
    (Request 2.6 §14).
    """

    schema_version: str = EPISODE_CURATION_MANIFEST_SCHEMA_VERSION
    curation_id: str

    dataset_id: str
    dataset_version: str

    source_learning_export: SourceLearningExportRef

    policy: CurationPolicy
    policy_hash: str

    # SceneOps V2 Request 2.6A §2/§3: explicit provenance of every analysis
    # semantics version that participates in curation_id (see
    # identity.episode_curation_id) -- persisted here so a manifest reader
    # never has to infer "which validator/profiler build produced these
    # decisions" from currently-installed package constants. Deliberately
    # separate from CurationPolicy.curation_semantics_version (unchanged,
    # nested, and not itself a dependency-provenance field) -- these three
    # are the run's actual recorded provenance, not policy configuration.
    curation_semantics_version: str
    validation_semantics_version: str
    profile_semantics_version: str

    candidates: CurationCandidateSummary = Field(
        default_factory=CurationCandidateSummary
    )

    # Always sorted by (episode_id, aligned_artifact_checksum) -- order must
    # never depend on manifest.inputs iteration order (Request 2.6 §15
    # determinism).
    decisions: list[CurationDecision] = Field(default_factory=list)

    # Sorted -- convenience projection of decisions[].selected == True,
    # exactly what a downstream LeRobot export (Request 2.7, not
    # implemented here) consumes without re-walking decisions.
    selected_aligned_artifact_checksums: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)
