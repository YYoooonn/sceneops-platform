"""Shared golden-fixture round-trip verification for the LeRobot interop
E2E (SceneOps V2 Request 3.4 §4/§5/§6/§7/§11), extracted so
``scripts/e2e/e2e_lerobot_export.py`` (host/isolated-venv export) and
``scripts/e2e/e2e_lerobot_container_verify.py`` (container export, read
back host-side, SceneOps V2 Request 4.3) check identical claims against
identical expectations from one place, never two independently-maintained
copies. Nothing here changed behavior when this module was extracted --
every check, message, and expectation is verbatim what
``e2e_lerobot_export.py`` already froze.

Imports ``lerobot`` (``OfficialLeRobotDataset``) -- runs ONLY inside
``tools/lerobot-integration``'s isolated venv (SceneOps V2 Request 3.3A),
same as both scripts above.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset as OfficialLeRobotDataset

from sceneops_analytics.external_adapters import MappingKind, SemanticField
from sceneops_analytics.testing.interop_dataset import (
    EPISODE_A_REV1_REF,
    EPISODE_A_REV2_REF,
    EPISODE_B_REF,
    compute_expected_interop_episodes,
)

# The frozen Request 3.3 semantic-loss classification this E2E must observe
# in the real report -- literal SemanticField/MappingKind values (the
# existing enums/contracts), never a re-derivation of *why* each field maps
# that way (that logic lives, and stays, in LeRobotDatasetAdapter.
# semantic_capabilities()). See adapter.py's own capability comments for
# the reasoning behind each entry.
EXPECTED_LOSSY_OR_UNSUPPORTED: dict[SemanticField, MappingKind] = {
    SemanticField.EPISODE_IDENTITY: MappingKind.LOSSY_EXPLICIT,
    SemanticField.TIMESTAMPS: MappingKind.LOSSY_EXPLICIT,
    SemanticField.TASK_OUTCOME_METADATA: MappingKind.LOSSY_EXPLICIT,
    SemanticField.SIGNAL_STATUS: MappingKind.UNSUPPORTED,
    SemanticField.SOURCE_REVISION_TRACEABILITY: MappingKind.LOSSY_EXPLICIT,
}
# LOSSLESS fields are never reported on ExternalExportReport.semantic_losses
# at all (ExternalDatasetAdapter._resolve_semantic_losses, Request 3.1) --
# their absence from the report IS the lossless claim.
EXPECTED_LOSSLESS_FIELDS: set[SemanticField] = {
    SemanticField.STEP_ORDERING,
    SemanticField.OBSERVATION_ACTION_NAMESPACE,
    SemanticField.FEATURE_ORDERING,
}

EXPECTED_EPISODE_ORDER = [EPISODE_A_REV1_REF, EPISODE_A_REV2_REF, EPISODE_B_REF]


class RoundtripAssertionError(AssertionError):
    """A round-trip claim did not hold (SceneOps V2 Request 3.4 §7) --
    always caught at the top level and reported as a clear, single-line
    failure with a non-zero exit code, never a silent pass or a bare
    traceback."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RoundtripAssertionError(message)


def verify_export_report(report) -> None:
    """SceneOps V2 Request 3.4 §4/§5/§11: everything checkable from an
    ``ExternalExportReport`` alone, before ever touching the LeRobot output
    on disk. ``report`` may be the real object an in-process ``adapter.
    export()`` returned, or one reconstructed via ``ExternalExportReport.
    model_validate()`` from a container's ``IntegrationResult.
    result_metadata`` -- this function only reads attributes, so either
    works identically."""
    check(
        report.exported_episode_count == 3,
        f"exported_episode_count={report.exported_episode_count}, expected 3",
    )
    check(
        report.exported_step_count == 22,
        f"exported_step_count={report.exported_step_count}, expected 22 (8+8+6)",
    )
    check(
        report.source_episode_refs == EXPECTED_EPISODE_ORDER,
        f"source_episode_refs={report.source_episode_refs!r}, expected "
        f"{EXPECTED_EPISODE_ORDER!r} -- the two 'ep-a' revisions must both "
        "appear, in canonical (episode_id, checksum)-sorted order",
    )
    check(report.feature_schema is not None, "feature_schema is None")
    check(
        report.feature_schema.observation_dim == 7,
        f"observation_dim={report.feature_schema.observation_dim}, expected 7",
    )
    check(
        report.feature_schema.action_dim == 4,
        f"action_dim={report.feature_schema.action_dim}, expected 4",
    )

    losses_by_field = {loss.field: loss.mapping for loss in report.semantic_losses}
    for field, expected_mapping in EXPECTED_LOSSY_OR_UNSUPPORTED.items():
        check(
            field in losses_by_field,
            f"semantic_losses is missing expected entry for {field.value!r}",
        )
        check(
            losses_by_field[field] == expected_mapping,
            f"semantic_losses[{field.value!r}]={losses_by_field[field]!r}, "
            f"expected {expected_mapping!r}",
        )
    for field in EXPECTED_LOSSLESS_FIELDS:
        check(
            field not in losses_by_field,
            f"semantic_losses unexpectedly reports {field.value!r} "
            "(LOSSLESS fields must never appear)",
        )


def verify_official_readback(root: Path, *, repo_id: str) -> dict:
    """SceneOps V2 Request 3.4 §6: read back exclusively through LeRobot's
    own official ``LeRobotDataset`` API -- never by hand-parsing its
    Parquet/metadata files. Filesystem inspection (below, in the returned
    summary dict) is diagnostic only, never load-bearing for an
    assertion."""
    ds = OfficialLeRobotDataset(repo_id=repo_id, root=root)

    check(
        ds.meta.total_episodes == 3,
        f"total_episodes={ds.meta.total_episodes}, expected 3",
    )
    check(len(ds) == 22, f"len(dataset)={len(ds)}, expected 22")
    check(
        ds.meta.fps == 10, f"fps={ds.meta.fps}, expected 10 (interop fixture's 10.0 Hz)"
    )
    check(
        tuple(ds.meta.features["observation.state"]["shape"]) == (7,),
        f"observation.state shape={ds.meta.features['observation.state']['shape']}, expected (7,)",
    )
    check(
        tuple(ds.meta.features["action"]["shape"]) == (4,),
        f"action shape={ds.meta.features['action']['shape']}, expected (4,)",
    )

    expected_by_ref = compute_expected_interop_episodes()
    expected_in_order = [expected_by_ref[ref] for ref in EXPECTED_EPISODE_ORDER]

    frame_idx = 0
    previous_episode_observation_first_frame: list[float] | None = None
    for episode_index, expected_episode in enumerate(expected_in_order):
        episode_start = frame_idx
        for step_index, expected_step in enumerate(expected_episode.steps):
            item = ds[frame_idx]
            check(
                item["episode_index"].item() == episode_index,
                f"frame {frame_idx}: episode_index={item['episode_index'].item()}, "
                f"expected {episode_index}",
            )
            check(
                item["frame_index"].item() == step_index,
                f"frame {frame_idx}: frame_index={item['frame_index'].item()}, "
                f"expected {step_index} (within-episode frame ordering)",
            )

            actual_obs = item["observation.state"].numpy().astype(np.float64).tolist()
            actual_action = item["action"].numpy().astype(np.float64).tolist()
            expected_obs_f32 = (
                np.asarray(expected_step.observation, dtype=np.float32)
                .astype(np.float64)
                .tolist()
            )
            expected_action_f32 = (
                np.asarray(expected_step.action, dtype=np.float32)
                .astype(np.float64)
                .tolist()
            )
            check(
                np.allclose(actual_obs, expected_obs_f32, rtol=1e-5, atol=1e-5),
                f"frame {frame_idx}: observation.state={actual_obs} != expected "
                f"{expected_obs_f32} (float32-rounded golden value)",
            )
            check(
                np.allclose(actual_action, expected_action_f32, rtol=1e-5, atol=1e-5),
                f"frame {frame_idx}: action={actual_action} != expected "
                f"{expected_action_f32} (float32-rounded golden value)",
            )

            check(
                item["task"] == expected_episode.task,
                f"frame {frame_idx}: task={item['task']!r}, expected {expected_episode.task!r}",
            )

            # LeRobot's per-frame "timestamp" is a *relative*, episode-local
            # value derived from frame_index/fps (see writer.py's module
            # docstring) -- it is compared here only against that same
            # relative expectation (step_index / fps), never against
            # SceneOps' absolute timestamp_us, which would misrepresent it
            # as lossless (SceneOps V2 Request 3.4 §4's explicit warning).
            actual_timestamp = item["timestamp"].item()
            expected_timestamp = step_index / ds.meta.fps
            check(
                abs(actual_timestamp - expected_timestamp) < 1e-4,
                f"frame {frame_idx}: timestamp={actual_timestamp}, expected "
                f"~{expected_timestamp} (step_index/fps)",
            )

            if step_index == 0:
                if episode_index == 1:
                    # The second "ep-a" revision must be real, distinct
                    # content from the first -- not a coincidentally-equal
                    # duplicate (SceneOps V2 Request 3.4 §4 episode-revision
                    # check).
                    check(
                        previous_episode_observation_first_frame != actual_obs,
                        "EPISODE_A_REV1 and EPISODE_A_REV2 have identical "
                        "first-frame observation.state -- revisions are not "
                        "actually distinguishable in the exported dataset",
                    )
                previous_episode_observation_first_frame = actual_obs

            frame_idx += 1

        check(
            frame_idx - episode_start == expected_episode.step_count,
            f"episode_index={episode_index}: wrote "
            f"{frame_idx - episode_start} frames, expected "
            f"{expected_episode.step_count}",
        )

    return {
        "total_episodes": ds.meta.total_episodes,
        "total_frames": len(ds),
        "fps": ds.meta.fps,
        "observation_state_shape": list(ds.meta.features["observation.state"]["shape"]),
        "action_shape": list(ds.meta.features["action"]["shape"]),
        # Diagnostic only, not load-bearing for any assertion above.
        "on_disk_files": sorted(
            str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
        ),
    }
