"""LeRobotDatasetAdapter: the concrete ExternalDatasetAdapter (Request 3.1)
targeting LeRobot's official dataset API (SceneOps V2 Request 3.3).

::

    SceneOpsDataset
            -> LeRobotDatasetAdapter.export(dataset, ExternalExportConfig)
            -> LeRobotDatasetWriter (see writer.py)
            -> a real lerobot.datasets.lerobot_dataset.LeRobotDataset on disk
            -> ExternalDatasetRef  (via .dataset_ref, not part of the report)

Requires the ``lerobot`` extra (``pip install sceneops-analytics[lerobot]``).
This subpackage is the only place in sceneops_analytics that imports
lerobot -- nothing else needs it, and this subpackage is never imported by
sceneops_analytics/__init__.py or external_adapters/__init__.py, so
``import sceneops_analytics`` (and ``import sceneops_analytics.
external_adapters``) never requires lerobot to be installed. Import this
subpackage explicitly: ``from sceneops_analytics.external_adapters.lerobot
import LeRobotDatasetAdapter``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from lerobot.datasets.lerobot_dataset import (
    CODEBASE_VERSION as _LEROBOT_CODEBASE_VERSION,
)

from sceneops_analytics.external_adapters.adapter import ExternalDatasetAdapter
from sceneops_analytics.external_adapters.enums import MappingKind, SemanticField
from sceneops_analytics.external_adapters.schemas import ExternalExportConfig
from sceneops_core.datasets.schemas.external import ExternalDatasetRef

from .writer import LeRobotDatasetWriter

FORMAT_NAME = "lerobot"

# The LeRobot on-disk dataset codebase/schema version this adapter targets
# (e.g. "3.0") -- distinct from the `lerobot` pip package version (e.g.
# "0.4.4"). ExternalDatasetRef.format_version's own docstring example
# ("lerobot"/"2.1") is this dataset-schema version, not the package
# version, so this adapter reports the same thing: whatever
# lerobot.datasets.lerobot_dataset.CODEBASE_VERSION the installed package
# actually writes (that constant is spelled "v3.0" -- the leading "v" is
# stripped here so format_version reads like the "2.1" convention
# ExternalDatasetRef's own docstring uses, and so error messages that
# already prepend their own "v", e.g. ExternalDatasetAdapter._resolve_
# semantic_losses's "{format_name} v{format_version}", don't double up),
# read from the installed package rather than hardcoded, so a future
# lerobot upgrade can't silently make this string wrong.


class LeRobotDatasetAdapter(ExternalDatasetAdapter):
    """SceneOpsDataset -> LeRobot dataset (SceneOps V2 Request 3.3).

    Construct one instance per export target (``repo_id``/``root`` are
    this export's target identity, mirroring how ``lerobot.datasets.
    lerobot_dataset.LeRobotDataset.create`` itself takes them) and call
    ``await adapter.export(dataset, ExternalExportConfig(...))`` -- exactly
    the frozen ExternalDatasetAdapter usage pattern (Request 3.1), with no
    LeRobot-specific fields added to ExternalExportConfig itself.
    """

    def __init__(self, *, repo_id: str, root: str | Path) -> None:
        self._repo_id = repo_id
        self._root = Path(root)

    @property
    def format_name(self) -> str:
        return FORMAT_NAME

    @property
    def format_version(self) -> str:
        return _LEROBOT_CODEBASE_VERSION.removeprefix("v")

    def semantic_capabilities(self) -> Mapping[SemanticField, MappingKind]:
        """SceneOps V2 Request 3.3 §7's classification. Every SemanticField
        is listed explicitly (see errors.py/writer.py module docstrings for
        the per-field reasoning) -- none rely on the "omitted == UNSUPPORTED"
        default (ExternalDatasetAdapter._resolve_semantic_losses)."""
        return {
            # LeRobot's episode_index is a bare, sequential integer with no
            # native slot for SceneOps' (episode_id, aligned_artifact_
            # checksum) identity. Full traceability still exists -- export
            # order (== add_frame/save_episode call order == LeRobot's own
            # episode_index assignment order) is exactly ExternalExportReport
            # .source_episode_refs' order (Request 3.1 §7) -- but recovering
            # it requires keeping that report alongside the LeRobot dataset,
            # so this is reported as loss rather than claimed lossless.
            SemanticField.EPISODE_IDENTITY: MappingKind.LOSSY_EXPLICIT,
            # LeRobotDatasetWriter.add_frame() is called once per step in
            # export order with no reordering or dropping (an empty Episode
            # is rejected outright -- errors.EmptyEpisodeUnsupportedError --
            # rather than silently reordered/coerced).
            SemanticField.STEP_ORDERING: MappingKind.LOSSLESS,
            # LeRobot's per-frame "timestamp" is structurally a *relative*
            # fixed-frequency value (frame_index / fps) -- see writer.py's
            # module docstring. Given this writer's validated uniform-grid
            # requirement, intra-episode spacing survives exactly, but the
            # absolute source-clock anchor (EpisodeMetadata.
            # source_start_timestamp_us/source_clock -- which channel of
            # SceneOps timestamp is measured relative to) is not represented
            # by any LeRobot field, so this is not claimed lossless.
            SemanticField.TIMESTAMPS: MappingKind.LOSSY_EXPLICIT,
            # observation/action stay two separate LeRobot features
            # ("observation.state" vs "action") -- no shared namespace, no
            # collision possible.
            SemanticField.OBSERVATION_ACTION_NAMESPACE: MappingKind.LOSSLESS,
            # FeatureProjection's declared channel order determines dense
            # vector order (sceneops_core.episodes.learning.projection),
            # which LeRobotDatasetWriter writes into observation.state/
            # action in that same order, unchanged.
            SemanticField.FEATURE_ORDERING: MappingKind.LOSSLESS,
            # LeRobot's per-frame "task" string carries EpisodeMetadata.task
            # unchanged (LeRobotTaskRequiredError if it's None -- never
            # substituted). EpisodeOutcome has no LeRobot-native field at
            # all -- there is nothing in LeRobot's schema an episode outcome
            # could be written into.
            SemanticField.TASK_OUTCOME_METADATA: MappingKind.LOSSY_EXPLICIT,
            # RESOLVED vs INTERPOLATED is already collapsed before this
            # adapter ever sees a step (dense projection, Request 2.7A §9) --
            # see ExternalDatasetAdapter's own module docstring. Not a
            # LeRobot-specific limitation.
            SemanticField.SIGNAL_STATUS: MappingKind.UNSUPPORTED,
            # Same reasoning as EPISODE_IDENTITY, at the dataset level:
            # LeRobot's info.json has no field for SceneOps'
            # dataset_id/dataset_version/export_id. ExternalExportReport.
            # source_dataset_id/source_dataset_version/source_export_id
            # (Request 3.1 §7) is the explicit record of this, once again
            # kept alongside the LeRobot dataset rather than written inside
            # it.
            SemanticField.SOURCE_REVISION_TRACEABILITY: MappingKind.LOSSY_EXPLICIT,
        }

    def open_writer(self, config: ExternalExportConfig) -> LeRobotDatasetWriter:
        return LeRobotDatasetWriter(repo_id=self._repo_id, root=self._root)

    @property
    def dataset_ref(self) -> ExternalDatasetRef:
        """This export's target as an ExternalDatasetRef (SceneOps V2
        Request 3.3 §8) -- never a SceneOps DatasetVersion. Computable from
        this adapter's own construction args alone (``repo_id``/``root``
        fix the target before export() ever runs), so this is valid to read
        any time, though it only refers to a complete, valid LeRobot dataset
        on disk once ``export()`` has returned successfully."""
        return ExternalDatasetRef(
            format=self.format_name,
            format_version=self.format_version,
            uri=str(self._root),
            external_name=self._repo_id,
        )


__all__ = ["LeRobotDatasetAdapter"]
