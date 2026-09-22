"""External dataset adapter contracts (SceneOps V2 Request 3.1, write
lifecycle refined in Request 3.1A): the framework-neutral vocabulary for
projecting a SceneOpsDataset (Request 2.7B) into external robot-learning
dataset formats.

::

    SceneOpsDataset
            -> ExternalDatasetAdapter.export(dataset, ExternalExportConfig)
            -> ExternalDatasetWriter.initialize()
            -> ExternalEpisode / ExternalStep
            -> ExternalDatasetWriter.write_episode() x N -> finalize()
            -> ExternalExportReport

SceneOps remains canonical; everything in this package is a projection over
an already-open SceneOpsDataset, never a redefinition of it or of Request
2.7A's pure contracts (EpisodeRef, FeatureProjection, FeatureSchema,
StepSample). This package defines the shared contract only -- no concrete
LeRobot/RLDS adapter exists yet, and none of these types are jobs,
ArtifactRecords, or persisted anywhere.
"""

from .adapter import ExternalDatasetAdapter
from .enums import MappingKind, SemanticField, UnsupportedSemanticPolicy
from .errors import (
    EpisodeCountMismatchError,
    EpisodeRefTraceabilityError,
    ExternalAdapterError,
    ExternalFeatureSchemaMismatchError,
    StepCountMismatchError,
    StepOrderingError,
    UnsupportedSemanticError,
)
from .schemas import (
    ExternalEpisode,
    ExternalExportConfig,
    ExternalExportReport,
    ExternalStep,
    SemanticLoss,
)
from .validation import (
    validate_episode_count,
    validate_episode_ref_traceability,
    validate_step_count,
    validate_step_ordering,
)
from .writer import ExternalDatasetWriter

__all__ = [
    "EpisodeCountMismatchError",
    "EpisodeRefTraceabilityError",
    "ExternalAdapterError",
    "ExternalDatasetAdapter",
    "ExternalDatasetWriter",
    "ExternalEpisode",
    "ExternalExportConfig",
    "ExternalExportReport",
    "ExternalFeatureSchemaMismatchError",
    "ExternalStep",
    "MappingKind",
    "SemanticField",
    "SemanticLoss",
    "StepCountMismatchError",
    "StepOrderingError",
    "UnsupportedSemanticError",
    "UnsupportedSemanticPolicy",
    "validate_episode_count",
    "validate_episode_ref_traceability",
    "validate_step_count",
    "validate_step_ordering",
]
