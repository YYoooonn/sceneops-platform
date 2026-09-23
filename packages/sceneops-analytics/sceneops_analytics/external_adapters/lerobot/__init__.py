"""SceneOpsDataset -> LeRobot dataset (SceneOps V2 Request 3.3): the first
concrete ExternalDatasetAdapter/ExternalDatasetWriter pair (Request 3.1/
3.1A) built over LeRobot's official ``lerobot.datasets.lerobot_dataset.
LeRobotDataset`` API.

Requires the ``lerobot`` extra (``pip install sceneops-analytics[lerobot]``).
This is the only subpackage in sceneops_analytics that imports lerobot --
nothing else needs it, and it is never imported by sceneops_analytics/
__init__.py or external_adapters/__init__.py, so plain ``import
sceneops_analytics`` never requires lerobot to be installed. Import this
subpackage explicitly::

    from sceneops_analytics.external_adapters.lerobot import LeRobotDatasetAdapter
"""

from __future__ import annotations

from .adapter import FORMAT_NAME, LeRobotDatasetAdapter
from .errors import (
    EmptyEpisodeUnsupportedError,
    EmptyLeRobotExportError,
    InconsistentFrequencyError,
    LeRobotAdapterError,
    LeRobotTaskRequiredError,
    NonIntegerFrequencyError,
    NonUniformTimelineError,
    UndeterminableFrequencyError,
)
from .writer import LeRobotDatasetWriter

__all__ = [
    "FORMAT_NAME",
    "EmptyEpisodeUnsupportedError",
    "EmptyLeRobotExportError",
    "InconsistentFrequencyError",
    "LeRobotAdapterError",
    "LeRobotDatasetAdapter",
    "LeRobotDatasetWriter",
    "LeRobotTaskRequiredError",
    "NonIntegerFrequencyError",
    "NonUniformTimelineError",
    "UndeterminableFrequencyError",
]
