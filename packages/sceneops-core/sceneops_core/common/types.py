from __future__ import annotations

from typing import Any, TypeAlias

JsonDict: TypeAlias = dict[str, Any]
JsonList: TypeAlias = list[Any]
JsonValue: TypeAlias = str | int | float | bool | None | JsonDict | JsonList

Metadata: TypeAlias = dict[str, Any]

DatasetId: TypeAlias = str
DatasetVersion: TypeAlias = str

ModelId: TypeAlias = str
ModelVersion: TypeAlias = str

JobId: TypeAlias = str
PipelineRunId: TypeAlias = str
RunId: TypeAlias = str

ArtifactId: TypeAlias = str
ArtifactUri: TypeAlias = str
