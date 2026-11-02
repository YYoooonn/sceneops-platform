"""The persisted contract version of the Scene analytics tables.

The tables themselves are built and read in ``sceneops_analytics.tables``. The
version lives here because the execution key depends on it and ``sceneops_core``
cannot import that package; nothing else about the tables belongs here.
"""

from __future__ import annotations

# The layout of scenes/observations/keyframes: their columns and Arrow types, the
# temporal context and source provenance they state, and their Parquet footer
# stamps. Independent of SCENE_MANIFEST_SCHEMA_V5: a change here changes no
# canonical manifest and no Scene revision.
# v2: every declared timestamp states its whole clock instance, the scenes table
# states its native source, and every table states its schema in its footer.
SCENE_ANALYTICS_SCHEMA_VERSION = "v2"

__all__ = ["SCENE_ANALYTICS_SCHEMA_VERSION"]
