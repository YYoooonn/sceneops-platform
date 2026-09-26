from .dataset import SceneOpsDataset
from .errors import (
    CurationManifestMismatchError,
    DatasetManifestMismatchError,
    EpisodeNotFoundError,
    LearningDataIntegrityError,
    LearningTableMissingError,
    SamplerSchemaMismatchError,
    SceneOpsDatasetError,
    ShardIndexMismatchError,
    StepOutOfRangeError,
)
from .numpy_adapter import (
    DEFAULT_DTYPE,
    NumPySequenceSample,
    materialize_sequences,
    to_numpy,
)
from .sampler import SequenceSampler
from .schemas import EpisodeMetadata

# NOTE: torch_adapter is deliberately NOT imported here -- it is the only
# module in this package that imports torch (an optional dependency, see
# pyproject.toml's [project.optional-dependencies] "torch" extra). Import it
# explicitly: `from sceneops_analytics.learning_dataset.torch_adapter import
# SceneOpsTorchDataset`.

__all__ = [
    "DEFAULT_DTYPE",
    "CurationManifestMismatchError",
    "DatasetManifestMismatchError",
    "EpisodeMetadata",
    "EpisodeNotFoundError",
    "LearningDataIntegrityError",
    "LearningTableMissingError",
    "NumPySequenceSample",
    "SamplerSchemaMismatchError",
    "SceneOpsDataset",
    "SceneOpsDatasetError",
    "SequenceSampler",
    "ShardIndexMismatchError",
    "StepOutOfRangeError",
    "materialize_sequences",
    "to_numpy",
]
