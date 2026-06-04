from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class SceneNodeType(StrEnum):
    SKY = "sky"
    BACKGROUND = "background"
    RIGID = "rigid"
    DEFORMABLE = "deformable"
    SMPL_HUMAN = "smpl_human"
    EGO = "ego"
    UNKNOWN = "unknown"


class PhysicsBodyType(StrEnum):
    NONE = "none"
    FIXED = "fixed"
    KINEMATIC = "kinematic"
    DYNAMIC = "dynamic"


class SceneNodeManifest(SceneOpsBaseModel):
    node_id: str
    node_type: SceneNodeType = SceneNodeType.UNKNOWN

    parent_id: str | None = None

    asset_refs: list[str] = Field(default_factory=list)

    initial_transform: JsonDict = Field(default_factory=dict)
    trajectory_uri: str | None = None

    visual_uri: str | None = None
    collider_uri: str | None = None

    physics_body_type: PhysicsBodyType = PhysicsBodyType.NONE

    metadata: JsonDict = Field(default_factory=dict)


class SceneGraphManifest(SceneOpsBaseModel):
    scene_id: str

    root_node_id: str | None = None
    nodes: list[SceneNodeManifest] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class WorldStateManifest(SceneOpsBaseModel):
    scene_id: str

    scene_graph: SceneGraphManifest

    static_asset_uris: list[str] = Field(default_factory=list)
    dynamic_asset_uris: list[str] = Field(default_factory=list)

    coordinate_system: str | None = None
    unit: str = "meter"

    metadata: JsonDict = Field(default_factory=dict)
