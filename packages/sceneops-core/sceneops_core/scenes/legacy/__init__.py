"""Pre-canonical Scene producer contracts.

Everything here belongs to the nuScenes scene integration and the raw-log
scene builder, which still emit sample-centric manifests with
source-root-relative payloads. Their output is never registered as a
canonical Scene; see ``manifests`` for the boundary.
"""

from .manifests import (
    LegacySceneAnnotationManifest,
    LegacySceneManifest,
    LegacySceneSampleManifest,
    LegacySceneSensorFrameManifest,
)
from .requests import BuildScenesRequest
from .sampling import (
    EgoPoseResolveStrategy,
    FrameAssociationStrategy,
    SampleGroupingConfig,
    SampleGroupingStrategy,
)
from .segmentation import (
    MissingSequencePolicy,
    SceneSegmentationConfig,
    SceneSegmentationStrategy,
)
from .segments import SceneSegment, SceneSegmentIndex

__all__ = [
    "BuildScenesRequest",
    "EgoPoseResolveStrategy",
    "FrameAssociationStrategy",
    "LegacySceneAnnotationManifest",
    "LegacySceneManifest",
    "LegacySceneSampleManifest",
    "LegacySceneSensorFrameManifest",
    "MissingSequencePolicy",
    "SampleGroupingConfig",
    "SampleGroupingStrategy",
    "SceneSegment",
    "SceneSegmentIndex",
    "SceneSegmentationConfig",
    "SceneSegmentationStrategy",
]
