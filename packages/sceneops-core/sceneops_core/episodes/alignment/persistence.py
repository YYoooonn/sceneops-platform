from __future__ import annotations

import hashlib
import json

from sceneops_core.common.schemas import SceneOpsBaseModel

from .config import TemporalAlignmentConfig, alignment_config_hash
from .schemas import AlignedEpisode

# This envelope's own JSON structure version -- distinct from
# AlignedEpisode.alignment_semantics_version, which is the alignment
# *algorithm's* behavior version (SceneOps V2 Request 2.2 §3, Request 2.3
# §12). Bump this only when AlignedEpisodeArtifact's shape changes; bump
# alignment_semantics_version only when the alignment algorithm's behavior
# changes. The two are independent and must never be conflated.
ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION = "v1"


class EpisodeSourceRevision(SceneOpsBaseModel):
    """The exact canonical EpisodeManifest revision an AlignedEpisode was
    computed from (ADR-007 §18.5).

    - source_artifact_id: the EPISODE_MANIFEST ArtifactRecord, the revision
      the EpisodeRecord pointed to (or the caller pinned) when aligning.
    - source_manifest_sha256: the content identity of its bytes.
    - episode_manifest_uri: where those bytes were read; informational only,
      never identity.
    """

    episode_id: str
    episode_manifest_uri: str
    source_artifact_id: str
    source_manifest_sha256: str


class AlignedEpisodeArtifact(SceneOpsBaseModel):
    """Persisted envelope around one pure AlignedEpisode result (SceneOps V2
    Request 2.3 §11). Deliberately separate from AlignedEpisode itself --
    the pure engine (Request 2.2) never knows about artifacts, jobs, or
    source revisions, and this envelope never redefines alignment
    semantics, only wraps a result that was already produced.

    Deliberately excludes operational/runtime provenance (job_id,
    pipeline_run_id, execution_id, worker_id) -- that belongs on the
    ArtifactRecord/Job/ExecutionRecord that produced this artifact, not
    inside the artifact's own bytes (Request 2.3 §13).
    """

    schema_version: str = ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION
    source_revision: EpisodeSourceRevision
    aligned_episode: AlignedEpisode


def alignment_key(
    config: TemporalAlignmentConfig,
    alignment_semantics_version: str,
    source_clock: str,
) -> str:
    """Deterministic, source-independent identity of one alignment *recipe*:
    the full config, the algorithm's semantics version and the clock the
    alignment ran on (ADR-007 §33.6).

    Every input that changes an AlignedEpisode's bytes participates. The
    clock is part of the recipe because the same config aligned on another
    clock is a different result; without it two such results would collide
    on one key. Source content is deliberately excluded: the same recipe
    maps to the same key across episodes and source revisions, which is
    what the aligned-artifact URI's two path segments (source hash, then
    this key) represent independently (SceneOps V2 Request 2.3 §14)."""
    payload = json.dumps(
        {
            "alignment_config_hash": alignment_config_hash(config),
            "alignment_semantics_version": alignment_semantics_version,
            "source_clock": source_clock,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def aligned_episode_alignment_key(aligned: AlignedEpisode) -> str:
    """The recipe key an AlignedEpisode was produced by, recomputed from the
    result itself so readers never have to be told it."""
    return alignment_key(
        aligned.alignment_config,
        aligned.alignment_semantics_version,
        aligned.source_clock,
    )
