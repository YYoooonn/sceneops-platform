from __future__ import annotations

from enum import StrEnum


class SemanticField(StrEnum):
    """Every SceneOps concept an external-format export must account for
    (SceneOps V2 Request 3.1 §5). This is the fixed checklist a concrete
    adapter's ``semantic_capabilities()`` must classify -- a field it omits
    is treated as ``MappingKind.UNSUPPORTED`` (silence is never "lossless",
    see ExternalDatasetAdapter._resolve_semantic_losses)."""

    EPISODE_IDENTITY = "episode_identity"
    STEP_ORDERING = "step_ordering"
    TIMESTAMPS = "timestamps"
    OBSERVATION_ACTION_NAMESPACE = "observation_action_namespace"
    FEATURE_ORDERING = "feature_ordering"
    TASK_OUTCOME_METADATA = "task_outcome_metadata"
    # RESOLVED / INTERPOLATED / MISSING / ABSENT (AlignedSignalStatus +
    # FeatureAbsentError) -- see ExternalDatasetAdapter's module docstring
    # for why v1 dense projection (MissingFeaturePolicy.ERROR, Request
    # 2.7A §9) already forecloses MISSING/ABSENT from ever reaching an
    # ExternalStep, independent of any adapter's own capability.
    SIGNAL_STATUS = "signal_status"
    SOURCE_REVISION_TRACEABILITY = "source_revision_traceability"


class MappingKind(StrEnum):
    """How one SemanticField maps into a target format (SceneOps V2 Request
    3.1 §8)."""

    LOSSLESS = "lossless"
    LOSSY_EXPLICIT = "lossy_explicit"
    UNSUPPORTED = "unsupported"


class UnsupportedSemanticPolicy(StrEnum):
    """What ExternalDatasetAdapter.export() does when the adapter classifies
    a SemanticField as ``MappingKind.UNSUPPORTED`` (SceneOps V2 Request 3.1
    §5). Both members report the loss explicitly on ExternalExportReport --
    the only difference is whether export() also raises."""

    FAIL = "fail"
    RECORD = "record"
