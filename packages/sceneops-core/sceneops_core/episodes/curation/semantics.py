from __future__ import annotations

# Identifies the *behavior* of the curation evaluator itself -- which
# predicates exist and how each is checked -- not a package version, git
# SHA, or a particular CurationPolicy instance (SceneOps V2 Request 2.6
# §10, mirroring ALIGNMENT_SEMANTICS_VERSION/
# ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION/
# ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION). Bump it whenever a predicate's
# meaning changes (e.g. a rejection code is redefined) so that two
# EpisodeCurationManifests produced under different evaluator behavior never
# collide on curation_id even if the source export + policy happen to be
# byte-identical. CurationEvaluator is the only place that sets it.
CURATION_SEMANTICS_VERSION = "v1"

# The manifest envelope's own JSON shape version -- distinct from
# CURATION_SEMANTICS_VERSION above, same split as
# ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION vs ALIGNMENT_SEMANTICS_VERSION.
# Bump only when EpisodeCurationManifest's field set/shape changes.
#
# v1 -> v2 (SceneOps V2 Request 2.6A §3/§8): added explicit
# curation_semantics_version/validation_semantics_version/
# profile_semantics_version provenance fields -- a genuine shape change
# (new fields), not a change to what curation_id means or how policies are
# evaluated, so only this constant moves, not CURATION_SEMANTICS_VERSION.
EPISODE_CURATION_MANIFEST_SCHEMA_VERSION = "v2"
