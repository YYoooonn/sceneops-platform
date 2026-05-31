from __future__ import annotations

from typing import Any

from pydantic import Field
from sceneops_core.schemas.base import SceneOpsBaseModel


JsonDict = dict[str, Any]


class ErrorInfo(SceneOpsBaseModel):
    type: str
    message: str
    details: JsonDict = Field(default_factory=dict)
