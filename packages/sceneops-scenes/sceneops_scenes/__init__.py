"""Scene domain logic that needs no worker runtime: building Scenes from a registered
recording, validating and profiling a Scene manifest, and the Scene artifact layout.

Registration, resolution and the Jobs that run them are in ``apps/worker``."""

from .artifacts import SceneArtifactStore

__all__ = ["SceneArtifactStore"]
