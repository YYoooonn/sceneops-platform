from __future__ import annotations


class NativeLearningDatasetError(Exception):
    """Base class for pure native-learning-dataset contract errors
    (sceneops-core, no I/O) -- SceneOps V2 Request 2.7A."""


class FeatureAbsentError(NativeLearningDatasetError):
    """A projected channel is not declared by the AlignedEpisode at all --
    distinct from FeatureMissingError, which means the channel exists but a
    specific step could not resolve a value for it (Request 2.7A §3/§9)."""


class FeatureMissingError(NativeLearningDatasetError):
    """A projected channel's signal has status=missing -- either at one
    step during per-step projection, or at every step, when raised while
    resolving a FeatureSchema. MissingFeaturePolicy.ERROR is the only v1
    behavior: no zero-fill/forward-fill/NaN-fill/drop (Request 2.7A §9)."""


class UnsupportedFeatureKindError(NativeLearningDatasetError):
    """A projected channel resolves to a non-numeric AlignedValueKind
    (orientation/reference) -- v1 dense projection only supports
    numeric_scalar/numeric_vector (Request 2.7A §5)."""


class FeatureShapeMismatchError(NativeLearningDatasetError):
    """A projected channel's numeric kind/dimension is not stable across
    steps -- scalar<->vector kind changes and vector-length changes both
    fail rather than silently pad/truncate (Request 2.7A §8)."""


class DuplicateFeatureChannelError(NativeLearningDatasetError):
    """The same channel appears more than once within one FeatureProjection
    namespace (observation or action). Channel order is semantically
    meaningful, so a duplicate is ambiguous, not a set (Request 2.7A §4)."""


class SequenceBoundaryError(NativeLearningDatasetError):
    """A SequenceRef's [start_step, start_step + horizon) window does not
    fit inside the Episode's actual step_count (Request 2.7A §14)."""
