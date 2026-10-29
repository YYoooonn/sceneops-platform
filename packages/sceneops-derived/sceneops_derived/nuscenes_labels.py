"""Native nuScenes labels: one registered nuScenes v1.0 SourceContent -> one
LabelSetManifest (ADR-015 §9, M3.0 OQ-6).

Pure and database-free. It reads the content only through the native reader
(``sceneops_sources.nuscenes``), which reads only verified content files, so
the label set is a function of the content bytes, these rules, the label set
id and the selected units. It never reads a SourceSnapshot, a DatasetVersion,
a Scene, a sample view or a sensor file, and writes nothing.

Rules:

    units          the content's scenes by scene.token (default: all), as the Scene
                   builder selects them; scene.name enters nothing
    anchor         one per sample of a selected scene: (content, content_id,
                   LIDAR_TOP, nuscenes.timestamp, sample.timestamp · 1 000) with
                   group_ref = sample.token. The sample must have exactly one
                   LIDAR_TOP key frame, at sample.timestamp exactly, whose file is
                   preserved in the content; otherwise the import fails. A sample
                   without annotations is covered with no label (annotated empty)
    labels         one per sample_annotation of a covered sample:
                   label_id = sample_annotation.token, instance_id =
                   instance.token, category = category.name, attributes = the
                   attribute names, sorted
    geometry       frame "global" (the frame of nuScenes annotations and ego poses,
                   the content Scene's world frame); center_m = translation,
                   size_wlh_m = size ([w, l, h]), rotation_wxyz = rotation, verbatim;
                   no velocity (nuScenes stores none)
    provenance     external, producer "nuscenes", producer_version = the table
                   version read from the bytes (v1.0-<split>)

Visibility, point counts and prev / next links are not carried: a label set
has no field for them, and the prev / next chain is the instance's sample
order.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from sceneops_core.labels import (
    Box3DLabel,
    LabelBox3D,
    LabelProvenance,
    LabelSetManifest,
    LabelSourceKind,
    ObservationAnchor,
)
from sceneops_core.provenance import PinnedContent, SourceTimeUnit, promote_to_ns
from sceneops_sources import ContentFileError, ContentFiles
from sceneops_sources.nuscenes import (
    NuScenesAnnotation,
    NuScenesAnnotations,
    NuScenesContent,
    NuScenesFormatError,
    NuScenesSample,
    NuScenesSampleData,
    NuScenesScene,
    read_nuscenes_annotations,
    read_nuscenes_content,
)

NUSCENES_LABEL_PRODUCER: Final = "nuscenes"
# nuScenes annotates a sample at its top-lidar key frame: sample.timestamp is
# that reading's timestamp.
ANCHOR_CHANNEL: Final = "LIDAR_TOP"
# The clock and world frame the nuScenes Scene builder declares for the same
# readings and poses (sceneops_scenes.nuscenes_builder), so an anchor names an
# observation of the content Scene and a box its world frame.
ANCHOR_CLOCK: Final = "nuscenes.timestamp"
BOX_FRAME: Final = "global"


class NuScenesLabelImportError(ValueError):
    """The content's annotations cannot be imported faithfully."""


def _ns(timestamp_us: int) -> int:
    return promote_to_ns(timestamp_us, SourceTimeUnit.MICROSECONDS)


def _select_scenes(
    content: NuScenesContent, unit_keys: Sequence[str] | None, content_id: str
) -> list[NuScenesScene]:
    by_key = {s.token: s for s in content.scenes}
    if unit_keys is None:
        selected = sorted(content.scenes, key=lambda s: s.token)
    else:
        unknown = sorted(set(unit_keys) - set(by_key))
        if unknown:
            raise NuScenesLabelImportError(
                f"unit_keys {unknown} name no scene of {content_id}"
            )
        selected = [by_key[k] for k in sorted(set(unit_keys))]
    if not selected:
        raise NuScenesLabelImportError("the content holds no scene")
    return selected


def _anchor_key_frames(content: NuScenesContent) -> dict[str, list[NuScenesSampleData]]:
    out: dict[str, list[NuScenesSampleData]] = {}
    for reading in content.sample_data.values():
        if (
            reading.is_key_frame
            and content.sensor_of(reading).channel == ANCHOR_CHANNEL
        ):
            out.setdefault(reading.sample_token, []).append(reading)
    return out


def _anchor(
    sample: NuScenesSample,
    key_frames: list[NuScenesSampleData],
    *,
    content_id: str,
    preserved: set[str],
) -> ObservationAnchor:
    if len(key_frames) != 1:
        raise NuScenesLabelImportError(
            f"sample {sample.token} has {len(key_frames)} {ANCHOR_CHANNEL} key "
            "frames; its annotations are anchored on exactly one"
        )
    (key_frame,) = key_frames
    if key_frame.timestamp != sample.timestamp:
        raise NuScenesLabelImportError(
            f"sample {sample.token} is at {sample.timestamp} µs, its "
            f"{ANCHOR_CHANNEL} key frame {key_frame.token} at {key_frame.timestamp} "
            "µs; the anchor is the key frame at the sample's own time"
        )
    if key_frame.filename not in preserved:
        raise NuScenesLabelImportError(
            f"sample {sample.token}: its {ANCHOR_CHANNEL} key frame "
            f"{key_frame.filename!r} is not a file of {content_id}, so no Scene "
            "of the content can hold the anchored observation"
        )
    return ObservationAnchor(
        source_kind="content",
        source_id=content_id,
        channel=ANCHOR_CHANNEL,
        source_clock=ANCHOR_CLOCK,
        timestamp_ns=_ns(sample.timestamp),
        group_ref=sample.token,
    )


def _labels(
    annotations: NuScenesAnnotations,
    sample_annotations: list[NuScenesAnnotation],
    anchor: ObservationAnchor,
) -> list[Box3DLabel]:
    out = []
    for annotation in sample_annotations:
        try:
            out.append(
                Box3DLabel(
                    label_id=annotation.token,
                    anchor=anchor,
                    category=annotations.category_of(annotation).name,
                    instance_id=annotation.instance_token,
                    box=LabelBox3D(
                        frame_id=BOX_FRAME,
                        center_m=annotation.translation,
                        size_wlh_m=annotation.size,
                        rotation_wxyz=annotation.rotation,
                    ),
                    attributes=sorted(
                        annotations.attributes[a].name
                        for a in annotation.attribute_tokens
                    ),
                )
            )
        except ValueError as exc:
            raise NuScenesLabelImportError(
                f"sample_annotation {annotation.token} is not representable: {exc}"
            ) from exc
    return out


def nuscenes_label_set(
    *,
    pinned: PinnedContent,
    files: ContentFiles,
    label_set_id: str,
    unit_keys: Sequence[str] | None = None,
) -> LabelSetManifest:
    """The label set of the annotations of ``unit_keys`` (default: every
    scene of the content)."""
    if files.inventory != pinned.inventory:
        raise NuScenesLabelImportError(
            "the content files are not the pinned content's inventory"
        )
    try:
        content = read_nuscenes_content(files)
        annotations = read_nuscenes_annotations(files)
    except (NuScenesFormatError, ContentFileError) as exc:
        raise NuScenesLabelImportError(str(exc)) from exc
    unknown_samples = sorted(
        {a.sample_token for a in annotations.annotations.values()}
        - set(content.samples)
    )
    if unknown_samples:
        raise NuScenesLabelImportError(
            f"sample_annotation references unknown samples {unknown_samples[:10]}"
        )
    content_id = pinned.content_id
    preserved = {entry.path for entry in pinned.inventory.files}
    by_sample = annotations.by_sample()
    key_frames = _anchor_key_frames(content)
    coverage: list[ObservationAnchor] = []
    labels: list[Box3DLabel] = []
    for scene in _select_scenes(content, unit_keys, content_id):
        try:
            samples = content.scene_samples(scene)
        except NuScenesFormatError as exc:
            raise NuScenesLabelImportError(str(exc)) from exc
        for sample in samples:
            anchor = _anchor(
                sample,
                key_frames.get(sample.token, []),
                content_id=content_id,
                preserved=preserved,
            )
            coverage.append(anchor)
            labels.extend(_labels(annotations, by_sample.get(sample.token, []), anchor))
    try:
        return LabelSetManifest.normalized(
            label_set_id=label_set_id,
            provenance=LabelProvenance(
                kind=LabelSourceKind.EXTERNAL,
                producer=NUSCENES_LABEL_PRODUCER,
                producer_version=content.version,
            ),
            coverage=coverage,
            labels=labels,
        )
    except ValueError as exc:
        raise NuScenesLabelImportError(str(exc)) from exc


__all__ = [
    "ANCHOR_CHANNEL",
    "ANCHOR_CLOCK",
    "BOX_FRAME",
    "NUSCENES_LABEL_PRODUCER",
    "NuScenesLabelImportError",
    "nuscenes_label_set",
]
