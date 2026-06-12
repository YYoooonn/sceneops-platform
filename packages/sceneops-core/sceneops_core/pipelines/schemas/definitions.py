from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.jobs.schemas import JobType

from .enums import PipelineType


class PipelineTaskOutputKind(StrEnum):
    REF = "ref"
    SUMMARY = "summary"
    METRIC = "metric"
    ARTIFACT = "artifact"


class PipelineTaskOutputSpec(SceneOpsBaseModel):
    """Declares one output value a task produces and where it is stored."""

    name: str
    kind: PipelineTaskOutputKind
    source: str
    target: str | None = None
    required: bool = False
    default: Any | None = None


class PipelineTaskQualityRuleType(StrEnum):
    BLOCK_IF_TRUE = "block_if_true"
    BLOCK_IF_EQUALS = "block_if_equals"
    BLOCK_IF_IN = "block_if_in"


class PipelineTaskQualityRule(SceneOpsBaseModel):
    """Declares one quality contract a task result must satisfy."""

    rule_type: PipelineTaskQualityRuleType
    source: str
    value: Any | None = None
    message: str | None = None
    code: str = "quality_gate_blocked"


class PipelineTaskDefinition(SceneOpsBaseModel):
    pipeline_task_id: str
    name: str
    order: int
    job_type: JobType

    depends_on_pipeline_task_ids: list[str] = Field(default_factory=list)
    default_params: JsonDict = Field(default_factory=dict)

    optional: bool = False

    param_keys: list[str] = Field(default_factory=list)
    outputs: list[PipelineTaskOutputSpec] = Field(default_factory=list)
    quality_rules: list[PipelineTaskQualityRule] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class PipelineDefinition(SceneOpsBaseModel):
    type: PipelineType
    name: str
    description: str | None = None

    tasks: list[PipelineTaskDefinition]

    # Pipeline availability contract.
    # supported=False / implemented=False pipelines are hidden from normal API
    # listing and rejected at create/run time with a clear error message.
    supported: bool = True
    experimental: bool = False
    implemented: bool = True

    metadata: JsonDict = Field(default_factory=dict)
