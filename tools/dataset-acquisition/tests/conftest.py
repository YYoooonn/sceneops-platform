from __future__ import annotations

from pathlib import Path

import pytest
from synthetic_nuscenes import write_dataroot


@pytest.fixture()
def dataroot(tmp_path: Path) -> Path:
    return write_dataroot(tmp_path / "nuscenes")
