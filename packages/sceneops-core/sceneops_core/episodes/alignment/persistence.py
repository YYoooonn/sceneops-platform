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
    """Identifies the exact EpisodeManifest revision an AlignedEpisode was
    computed from (SceneOps V2 Request 2.1B §15/§16, Request 2.3 §5-7).

    Three coordinates with three different roles -- do not collapse them:

    - episode_manifest_uri: physical source location. Deterministic per
      (dataset_id, dataset_version, episode_id); a later build_episodes
      run silently overwrites the bytes at this same URI.
    - source_artifact_id: producer lineage record (the ArtifactRecord this
      revision was read through). A new artifact_id is minted on every
      build_episodes execution even when the URI doesn't change -- useful
      for lineage/debugging, but NOT content identity by itself.
    - source_manifest_sha256: the actual content-revision identity. The
      strongest of the three, and the only one guaranteed to differ when,
      and only when, the underlying bytes actually differ.
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
    config: TemporalAlignmentConfig, alignment_semantics_version: str
) -> str:
    """Combines alignment config identity + semantics version into one
    deterministic, source-independent identity (SceneOps V2 Request 2.3
    §14). Deliberately excludes source content -- the same alignment
    "recipe" (config + semantics) maps to the same key across different
    episodes and different source revisions, which is exactly what the
    aligned-artifact URI's two path segments (source hash, then this key)
    need to represent independently (Request 2.3 §14)."""
    payload = json.dumps(
        {
            "alignment_config_hash": alignment_config_hash(config),
            "alignment_semantics_version": alignment_semantics_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
