from __future__ import annotations


class ExternalAdapterError(Exception):
    """Base class for external-dataset-adapter contract errors (SceneOps V2
    Request 3.1). Distinct from sceneops_analytics.learning_dataset's
    SceneOpsDatasetError (Request 2.7B) and sceneops_core.episodes.learning's
    NativeLearningDatasetError (Request 2.7A), which this package reuses
    unchanged for anything at or below the SceneOpsDataset boundary."""


class ExternalFeatureSchemaMismatchError(ExternalAdapterError):
    """Two EpisodeRefs selected for one export resolve
    ExternalExportConfig.projection to incompatible dense shapes -- mirrors
    SequenceSampler's SamplerSchemaMismatchError (Request 2.7C): an export
    likewise needs one uniform observation/action shape across every episode
    it writes, so this is checked once per export rather than deferred to a
    later per-episode surprise."""


class UnsupportedSemanticError(ExternalAdapterError):
    """The adapter classifies a SemanticField as MappingKind.UNSUPPORTED and
    ExternalExportConfig.unsupported_semantic_policy is FAIL (Request 3.1
    §5) -- the export refuses to silently drop a concept it cannot
    represent."""


class EpisodeCountMismatchError(ExternalAdapterError):
    """Validation invariant (Request 3.1 §8): ExternalExportReport.
    exported_episode_count does not equal len(source_episode_refs)."""


class StepCountMismatchError(ExternalAdapterError):
    """Validation invariant (Request 3.1 §8): ExternalExportReport.
    exported_step_count does not equal the sum of every exported
    ExternalEpisode's step count."""


class StepOrderingError(ExternalAdapterError):
    """Validation invariant (Request 3.1 §8): an ExternalEpisode's steps are
    not exactly step_index 0..n-1 in order, or timestamp_us does not
    strictly increase."""


class EpisodeRefTraceabilityError(ExternalAdapterError):
    """Validation invariant (Request 3.1 §8): an ExternalEpisode's
    episode_ref is not among the EpisodeRefs the source SceneOpsDataset
    actually exposes."""
