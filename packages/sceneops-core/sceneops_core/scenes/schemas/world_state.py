"""World State: a reserved, currently-unimplemented hook for richer 3D scene
reconstruction (scene graph / physics bodies / static+dynamic assets) beyond
today's flat SceneManifest.

Status as of Stabilization Request 5's audit: no job handler reads or
writes any of this — build_scenes.py never branches on the
`build_world_state` param that threads through pipeline/job schemas
(defaults to False everywhere), no persisted SceneRecord has
world_state_manifest_uri set, and SceneManifest.world_state is never
populated. Retained rather than removed because it's a coherent, complete
schema (not confused/abandoned code) with zero compatibility cost
(everything defaults off, nothing depends on it) — a deliberate future hook,
not dead code left over from a removed feature. If it's still unimplemented
by the time a broader architecture pass happens, that's the point to decide
whether to build it or finally drop it.
"""

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
