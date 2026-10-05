"""Joins a SceneSampleView with the Scene revision it pins.

The view stores references only; consumers (inference, evaluation,
curation) read the pinned Scene manifest and resolve a sample into the
observations, calibrations and pose it names. Pure and storage-free: the
caller supplies the already checksum-verified Scene manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sceneops_core.scenes.schemas.manifests import (
    SceneCalibration,
    SceneManifest,
    SceneObservation,
    ScenePose,
)
from sceneops_core.scenes.schemas.enums import SceneModality

from .schemas import SceneSample, SceneSampleViewManifest


class SampleViewResolutionError(ValueError):
    """The view names something its pinned Scene revision does not contain."""


@dataclass(frozen=True)
class ResolvedMember:
    channel: str
    modality: SceneModality
    observation: SceneObservation
    calibration: SceneCalibration | None
    time_delta_ns: int


@dataclass(frozen=True)
class ResolvedSample:
    scene_id: str
    sample_id: str
    anchor: ResolvedMember
    members: dict[str, ResolvedMember] = field(default_factory=dict)
    pose: ScenePose | None = None

    def member(self, channel: str) -> ResolvedMember | None:
        if channel == self.anchor.channel:
            return self.anchor
        return self.members.get(channel)


class SceneSampleResolver:
    """Resolves the samples of one view against its pinned Scene manifest."""

    def __init__(self, view: SceneSampleViewManifest, manifest: SceneManifest) -> None:
        self._view = view
        self._modality = {c.channel: c.modality for c in manifest.channels}
        self._calibrations = {c.calibration_id: c for c in manifest.calibrations}
        self._poses = {p.pose_id: p for p in manifest.poses}
        self._observations = {o.observation_id: o for o in manifest.observations}

    def _member(self, member) -> ResolvedMember:  # type: ignore[no-untyped-def]
        observation = self._observations.get(member.observation_id)
        if observation is None or observation.timestamp_ns != member.timestamp_ns:
            raise SampleViewResolutionError(
                f"observation {member.observation_id!r} of the view is not in "
                "the pinned scene revision"
            )
        return ResolvedMember(
            channel=member.channel,
            modality=self._modality[observation.channel],
            observation=observation,
            calibration=self._calibrations.get(observation.calibration_id or ""),
            time_delta_ns=member.time_delta_ns,
        )

    def resolve(self, sample: SceneSample) -> ResolvedSample:
        pose = None
        if sample.pose is not None:
            pose = self._poses.get(sample.pose.pose_id)
            if pose is None:
                raise SampleViewResolutionError(
                    f"pose {sample.pose.pose_id!r} of the view is not in the "
                    "pinned scene revision"
                )
        return ResolvedSample(
            scene_id=self._view.scene.scene_id,
            sample_id=sample.sample_id,
            anchor=self._member(sample.anchor),
            members={m.channel: self._member(m) for m in sample.members},
            pose=pose,
        )

    def resolve_all(self) -> list[ResolvedSample]:
        return [self.resolve(s) for s in self._view.samples]


__all__ = [
    "ResolvedMember",
    "ResolvedSample",
    "SampleViewResolutionError",
    "SceneSampleResolver",
]
