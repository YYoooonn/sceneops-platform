from __future__ import annotations

from enum import StrEnum


class DatasetVersionStatus(StrEnum):
    """Generic, domain-agnostic DatasetVersion lifecycle.

    Processing progress is represented by Pipeline/Job/Run execution records
    and domain artifacts (e.g. SceneVersionSummary), not by a DatasetVersion
    status. A dataset version's readiness for a downstream operation (e.g.
    detection prediction/evaluation) is determined by explicit domain-scoped
    prerequisites -- see predict_detection.py/evaluate_detection.py's
    ``_require_scene_dataset_ready`` -- not by this field.
    """

    REGISTERED = "registered"
