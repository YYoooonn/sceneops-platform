from __future__ import annotations

# Identifies the *behavior* of the alignment algorithm itself — not a
# package version, git SHA, deployment version, or user configuration
# (SceneOps V2 Request 2.2 §3). Every AlignedEpisode records this; bump it
# whenever a change to tie-breaking, duplicate resolution, timeline
# endpoints, or interpolation semantics would change output for existing
# inputs. align_episode() is the only place that sets it — callers cannot
# override it.
ALIGNMENT_SEMANTICS_VERSION = "v1"
