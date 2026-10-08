"""Resolution of pinned SceneSampleViews into detection inputs (ADR-007 §33.5).

``load_detection_samples`` is the only way a detection run turns derived
inputs into samples: every view and every Scene revision it names is read
through its pinned checksum, every payload through its verified
ArtifactRecord, and nothing is looked up by location or "latest".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sceneops_core.inference.schemas import PredictionInputRef
from sceneops_core.labels import Box3DLabel, LabelSetManifest
from sceneops_core.sample_views import SceneSampleResolver, SceneSampleViewManifest

from sceneops_worker.core.context import WorkerContext
from sceneops_worker.derived.resolution import ResolvedView, resolve_label_set
from sceneops_inference.detection.base import DetectionSampleInput
from sceneops_inference.detection.uris import normalize_image_uri
from sceneops_worker.recordings.payload_refs import ArtifactPayloadLocator


class SampleViewResolutionError(ValueError):
    """A pinned sample view cannot be resolved for the requested run."""


@dataclass
class DetectionSampleSelection:
    samples: list[DetectionSampleInput] = field(default_factory=list)
    inputs: list[PredictionInputRef] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


async def load_detection_samples(
    context: WorkerContext,
    *,
    views: list[tuple[ResolvedView, list[str] | None]],
    camera_channel: str,
    lidar_channel: str | None,
    max_samples: int | None,
    with_reference_labels: bool,
) -> DetectionSampleSelection:
    """Samples of the pinned views, in (scene, sample) order.

    ``views`` pairs each resolved view with the sample ids selected from it
    (None = every sample). ``max_samples`` caps the total, taken from the
    front of that order. A sample that lacks the camera observation is
    skipped with a reason; a view that associates the camera channel with
    none of its samples is an error, never an empty result.
    """
    selection = DetectionSampleSelection()
    locator = ArtifactPayloadLocator(context.artifact_record_store)
    label_cache: dict[str, LabelSetManifest] = {}

    async def labels_of(
        view: SceneSampleViewManifest, sample_id: str
    ) -> list[Box3DLabel]:
        sample = view.sample(sample_id)
        assert sample is not None
        found: list[Box3DLabel] = []
        by_ref = {ref.label_set_id: ref for ref in view.label_sets}
        for entry in sample.labels:
            if not entry.label_ids:
                continue
            ref = by_ref[entry.label_set_id]
            if ref.manifest_checksum not in label_cache:
                label_cache[ref.manifest_checksum] = await resolve_label_set(
                    context, ref
                )
            wanted = set(entry.label_ids)
            found.extend(
                label
                for label in label_cache[ref.manifest_checksum].labels
                if label.label_id in wanted
            )
        return found

    remaining = max_samples
    for resolved, selected in sorted(views, key=lambda pair: pair[0].ref.scene_id):
        view = resolved.view
        wanted_ids = None if selected is None else set(selected)
        if wanted_ids is not None:
            unknown = wanted_ids - {s.sample_id for s in view.samples}
            if unknown:
                raise SampleViewResolutionError(
                    f"view {resolved.ref.manifest_artifact_id} has no samples "
                    f"{sorted(unknown)[:5]}"
                )
        candidates = [
            s for s in view.samples if wanted_ids is None or s.sample_id in wanted_ids
        ]
        if remaining is not None:
            candidates = candidates[: max(remaining, 0)]
        if not candidates:
            continue
        resolved_samples = {
            r.sample_id: r
            for r in SceneSampleResolver(view, resolved.scene).resolve_all()
        }
        with_camera = [
            s
            for s in candidates
            if resolved_samples[s.sample_id].member(camera_channel)
        ]
        if not with_camera:
            raise SampleViewResolutionError(
                f"view {resolved.ref.manifest_artifact_id} associates no "
                f"{camera_channel!r} observation with any selected sample"
            )
        for sample in candidates:
            if sample not in with_camera:
                selection.skipped.append(
                    {
                        "scene_id": view.scene.scene_id,
                        "sample_id": sample.sample_id,
                        "reason": f"no_{camera_channel}_observation",
                    }
                )

        rs = [resolved_samples[s.sample_id] for s in with_camera]
        refs = [r.member(camera_channel).observation.payload for r in rs]  # type: ignore[union-attr]
        if lidar_channel is not None:
            refs += [
                m.observation.payload
                for r in rs
                if (m := r.member(lidar_channel)) is not None
            ]
        uris = await locator.uris(refs)

        for sample, r in zip(with_camera, rs):
            camera = r.member(camera_channel)
            assert camera is not None
            lidar = r.member(lidar_channel) if lidar_channel is not None else None
            selection.samples.append(
                DetectionSampleInput(
                    scene_id=view.scene.scene_id,
                    sample_id=sample.sample_id,
                    camera_channel=camera_channel,
                    image_uri=normalize_image_uri(
                        uris[camera.observation.payload.artifact_id]
                    ),
                    camera=camera,
                    lidar=lidar,
                    lidar_uri=(
                        uris[lidar.observation.payload.artifact_id]
                        if lidar is not None
                        else None
                    ),
                    pose=r.pose,
                    reference_labels=(
                        await labels_of(view, sample.sample_id)
                        if with_reference_labels
                        else []
                    ),
                )
            )
        selection.inputs.append(
            PredictionInputRef(
                sample_view=resolved.ref,
                sample_ids=sorted(s.sample_id for s in with_camera),
            )
        )
        if remaining is not None:
            remaining -= len(with_camera)
            if remaining <= 0:
                break
    if not selection.samples:
        raise SampleViewResolutionError("the pinned inputs yield no detection samples")
    return selection


__all__ = [
    "DetectionSampleSelection",
    "SampleViewResolutionError",
    "load_detection_samples",
]
