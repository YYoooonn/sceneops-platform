from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


ArtifactId = str
ArtifactUri = str
DatasetId = str
DatasetVersion = str
JobId = str
JsonList = list[Any]
JsonValue = str | int | float | bool | None
Metadata = dict[str, Any]
ModelId = str
ModelVersion = str
PipelineRunId = str
RunId = str
JsonDict = dict[str, Any]


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.capitalize() for word in rest)


class SceneOpsBaseModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
    )

    def to_db_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_artifact_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")

    def to_api_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


class ErrorInfo(SceneOpsBaseModel):
    type: str
    message: str
    details: JsonDict = Field(default_factory=dict)
