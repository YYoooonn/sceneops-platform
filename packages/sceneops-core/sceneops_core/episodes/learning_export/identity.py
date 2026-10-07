from __future__ import annotations

import hashlib
import json

from .schemas import LEARNING_DATA_SCHEMA_VERSION, LearningDataExportConfig


def learning_data_export_id(
    *,
    aligned_checksums: list[str],
    export_config: LearningDataExportConfig,
    schema_version: str = LEARNING_DATA_SCHEMA_VERSION,
    base_export_id: str | None = None,
) -> str:
    """Deterministic export identity (SceneOps V2 Request 2.5 §6/§12):
    sorted input aligned checksums + columnar schema version + export
    config.

    Sorting is load-bearing -- an export over {A, B} and one over {B, A}
    must produce the identical export_id, matching the same
    order-independence requirement Request 2.2's execution-key params
    normalization already established for AlignEpisode inputs. Deliberately
    excludes aligned_artifact_id/episode_id (lineage, not content identity)
    -- only the content checksums participate, for the same reason
    source_artifact_id/aligned_artifact_id are excluded from ALIGN_EPISODE/
    VALIDATE_ALIGNED_EPISODE's execution-key identity (Request 2.3 §18,
    Request 2.4 §16).

    An incremental export is a different physical object from a full export
    over the same episodes -- it reuses the base export's shards and rows,
    which keep the base's ``export_id`` -- so the base is part of its
    identity. Two exports that must not share bytes never share an id.
    """
    document = {
        "aligned_checksums": sorted(aligned_checksums),
        "schema_version": schema_version,
        "export_config": export_config.model_dump(mode="json", exclude_none=True),
    }
    if base_export_id is not None:
        document["base_export_id"] = base_export_id
    payload = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
