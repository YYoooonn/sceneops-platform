"""Keyframe samples: a derived, workflow-facing view of canonical Scenes.

Detection works on synchronized samples. A canonical Scene does not contain
samples; it contains independently timed observations plus, where the
source defines one, a keyframe grouping over them. This module projects the
source-defined keyframes into samples and resolves each member observation's
calibration and source-associated ego pose. It selects nothing beyond what
the source grouped -- no nearest-frame association, interpolation or
resampling -- and its output is never written back as canonical data
(ADR-007 §13.6, §13.11).

Every Scene is read at the revision its index entry pins, verified by
checksum.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sceneops_core.datasets.schemas import DatasetManifest, DatasetSceneIndexEntry
from sceneops_core.provenance import ExternalUnitSource
from sceneops_core.scenes.schemas import (
    SceneAnnotation,
    SceneCalibration,
    SceneManifest,
    SceneModality,
    SceneObservation,
    ScenePose,
)

from sceneops_worker.scenes.artifacts import SceneArtifactStore


@dataclass(frozen=True)
class KeyframeObservation:
    observation: SceneObservation
    modality: SceneModality
    calibration: SceneCalibration | None
    ego_pose: ScenePose | None


@dataclass(frozen=True)
class KeyframeSample:
    scene_id: str
    sample_id: str
    group_id: str
    # The group's own reference time, in source_clock. Member observations
    # keep their own channel clocks.
    timestamp_ns: int
    source_clock: str
    # Keyed by verbatim source channel.
    observations: dict[str, KeyframeObservation] = field(default_factory=dict)
    annotations: list[SceneAnnotation] = field(default_factory=list)


def keyframe_sample_id(scene_id: str, group_id: str) -> str:
    return f"{scene_id}-{group_id}"


def keyframe_samples(*, scene_id: str, manifest: SceneManifest) -> list[KeyframeSample]:
    modality = {c.channel: c.modality for c in manifest.channels}
    calibrations = {c.calibration_id: c for c in manifest.calibrations}
    poses = {p.pose_id: p for p in manifest.poses}
    observations = {o.observation_id: o for o in manifest.observations}

    annotations_by_group: dict[str, list[SceneAnnotation]] = {}
    for annotation in manifest.annotations:
        if annotation.group_id is not None:
            annotations_by_group.setdefault(annotation.group_id, []).append(annotation)

    samples: list[KeyframeSample] = []
    for group in manifest.keyframes():
        members: dict[str, KeyframeObservation] = {}
        for observation_id in group.observation_ids:
            observation = observations[observation_id]
            members[observation.channel] = KeyframeObservation(
                observation=observation,
                modality=modality[observation.channel],
                calibration=calibrations.get(observation.calibration_id or ""),
                ego_pose=poses.get(observation.ego_pose_id or ""),
            )
        samples.append(
            KeyframeSample(
                scene_id=scene_id,
                sample_id=keyframe_sample_id(scene_id, group.group_id),
                group_id=group.group_id,
                timestamp_ns=group.timestamp_ns,
                source_clock=group.source_clock,
                observations=members,
                annotations=annotations_by_group.get(group.group_id, []),
            )
        )
    return samples


def annotation_source(manifest: SceneManifest) -> str | None:
    """Where a Scene's annotations come from, for display, audit and
    explicit user-configured selection: the external format for an external
    Scene, None otherwise. Processing never branches on it."""
    if not manifest.annotations:
        return None
    source = manifest.lineage.source
    return source.format if isinstance(source, ExternalUnitSource) else None


async def load_pinned_scene(
    scene_artifact_store: SceneArtifactStore, entry: DatasetSceneIndexEntry
) -> SceneManifest:
    return await scene_artifact_store.read_pinned_manifest(
        uri=entry.manifest_uri, checksum=entry.manifest_checksum
    )


async def iter_keyframe_samples(
    dataset_manifest: DatasetManifest,
    scene_artifact_store: SceneArtifactStore,
    *,
    max_samples: int | None = None,
) -> AsyncIterator[KeyframeSample]:
    yielded = 0
    for entry in dataset_manifest.scenes:
        manifest = await load_pinned_scene(scene_artifact_store, entry)
        for sample in keyframe_samples(scene_id=entry.scene_id, manifest=manifest):
            yield sample
            yielded += 1
            if max_samples is not None and yielded >= max_samples:
                return


__all__ = [
    "KeyframeObservation",
    "KeyframeSample",
    "annotation_source",
    "iter_keyframe_samples",
    "keyframe_sample_id",
    "keyframe_samples",
    "load_pinned_scene",
]
