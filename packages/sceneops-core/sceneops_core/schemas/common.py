from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


JsonDict = dict[str, Any]


class ErrorInfo(BaseModel):
    type: str
    message: str
    details: JsonDict = Field(default_factory=dict)
