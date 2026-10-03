"""I-32 guard for the L1 RobotRun path: publication, the RobotRunManifest,
registration and the verified resolver never name an external source
format and never read acquisition-origin metadata. A recording from a real
robot, a replayed dataset or a batch-converted dataset takes one path.

The L1 conformance suite may report the origin record (inspection only)
but must not name a source format either.
"""

from __future__ import annotations

from pathlib import Path

import sceneops_core.robots
import sceneops_integrations.recording
import sceneops_worker.robots

_FORMATS = ("nuscenes", "lerobot")


def _sources(package) -> dict[str, str]:
    root = Path(package.__file__).parent
    return {
        f"{package.__name__}/{p.name}": p.read_text().lower() for p in root.glob("*.py")
    }


def test_robot_run_path_names_no_source_format() -> None:
    sources = {
        **_sources(sceneops_core.robots),
        **_sources(sceneops_integrations.recording),
        **_sources(sceneops_worker.robots),
    }
    assert {
        name for name, text in sources.items() if any(f in text for f in _FORMATS)
    } == set()


def test_only_the_conformance_report_mentions_acquisition_origin() -> None:
    sources = {
        **_sources(sceneops_core.robots),
        **_sources(sceneops_integrations.recording),
        **_sources(sceneops_worker.robots),
    }
    readers = {name for name, text in sources.items() if "acquisition_origin" in text}
    assert readers <= {
        "sceneops_integrations.recording/conformance.py",
        "sceneops_integrations.recording/__init__.py",  # re-exports the record name
    }
