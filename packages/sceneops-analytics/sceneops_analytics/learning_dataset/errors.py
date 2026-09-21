from __future__ import annotations


class SceneOpsDatasetError(Exception):
    """Base class for SceneOpsDataset access-layer errors (SceneOps V2
    Request 2.7B). Distinct from sceneops_core.episodes.learning's pure
    projection errors (Request 2.7A), which this package reuses unchanged
    for anything below the row-reconstruction boundary."""


class LearningTableMissingError(SceneOpsDatasetError):
    """A required table name ('learning_episodes'/'learning_steps'/
    'learning_signals') is absent from LearningDataExportManifest.table_uris
    -- e.g. the export was built with a restricted ``tables`` config
    (Request 2.5's ``LearningDataExportConfig.tables``)."""


class DatasetManifestMismatchError(SceneOpsDatasetError):
    """curation_manifest.dataset_id/dataset_version does not match
    learning_manifest.dataset_id/dataset_version -- a structural mismatch
    independent of the checksum-based export identity check
    (CurationManifestMismatchError)."""


class CurationManifestMismatchError(SceneOpsDatasetError):
    """The opened EpisodeCurationManifest does not pin the exact
    LearningDataExportManifest being opened -- either
    source_learning_export.checksum != learning_manifest_checksum (the
    curation run selected over different bytes than this export), or a
    selected aligned_artifact_checksum is not among learning_manifest.inputs
    at all (the curation manifest references a revision this export never
    declared)."""


class LearningDataIntegrityError(SceneOpsDatasetError):
    """The learning-data snapshot's declared inputs (LearningDataExportManifest.inputs)
    and its actual learning_episodes.parquet rows disagree -- e.g. a
    declared/selected (episode_id, aligned_artifact_checksum) has no
    corresponding row in the table, or vice versa."""


class EpisodeNotFoundError(SceneOpsDatasetError):
    """The given EpisodeRef is not exposed by this SceneOpsDataset (not in
    the snapshot, or excluded by the opened curation selection)."""


class StepOutOfRangeError(SceneOpsDatasetError):
    """step_index is outside [0, step_count) for the given EpisodeRef, or
    (get_window) start_step/horizon does not fit -- see
    sceneops_core.episodes.learning.SequenceBoundaryError for the
    window-specific case, which this module re-raises unchanged."""
