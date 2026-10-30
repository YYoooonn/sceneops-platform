"""Pure SceneSampleView builder (ADR-007 §33.3). No I/O, DB or storage.

Deterministic: identical Scene bytes, policy and label sets always produce
identical output.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence

from sceneops_core.common.derived_ids import sample_view_artifact_id
from sceneops_core.labels.resolution import (
    AmbiguousAnchorError,
    ResolvedAnchor,
    SceneAnchorResolver,
)
from sceneops_core.labels.schemas import Box3DLabel, LabelSetManifest, LabelSetRef
from sceneops_core.scenes.schemas.manifests import (
    SceneManifest,
    SceneObservation,
    ScenePose,
)

from .schemas import (
    AssociationMode,
    DroppedAnchor,
    LabelAttachmentStats,
    SampleLabels,
    SampleMember,
    SamplePose,
    SampleViewError,
    SampleViewPolicy,
    SceneRevisionRef,
    SceneSample,
    SceneSampleViewManifest,
)


class SampleViewPolicyError(SampleViewError):
    """The policy cannot be applied to this Scene (unknown channel, an
    association that would cross clocks, ...)."""


class SceneLacksAnchorChannelError(SampleViewPolicyError):
    """The Scene does not declare the policy's anchor channel, so it has no
    samples under this policy. A workflow over many Scenes skips such a
    Scene explicitly; any other policy error is a configuration fault."""


def _associate(
    timestamps: list[int],
    anchor_ns: int,
    mode: AssociationMode,
    tolerance_ns: int,
) -> int | None:
    """Index into ``timestamps`` (ascending, ties in canonical order) of the
    associated entry, or None when nothing lies within the tolerance."""
    if not timestamps:
        return None
    if mode == AssociationMode.PREVIOUS:
        index = bisect_right(timestamps, anchor_ns) - 1
        if index < 0 or anchor_ns - timestamps[index] > tolerance_ns:
            return None
        # Several entries may share the timestamp: the first in canonical order.
        return bisect_left(timestamps, timestamps[index])
    right = bisect_left(timestamps, anchor_ns)
    candidates: list[int] = []
    if right < len(timestamps):
        candidates.append(right)
    if right > 0:
        candidates.append(bisect_left(timestamps, timestamps[right - 1]))
    best = min(
        candidates, key=lambda i: (abs(timestamps[i] - anchor_ns), timestamps[i], i)
    )
    if abs(timestamps[best] - anchor_ns) > tolerance_ns:
        return None
    return best


def build_scene_sample_view(
    *,
    scene: SceneRevisionRef,
    manifest: SceneManifest,
    policy: SampleViewPolicy,
    label_sets: Sequence[tuple[LabelSetRef, LabelSetManifest]] = (),
) -> SceneSampleViewManifest:
    channels = {c.channel: c for c in manifest.channels}
    anchor_channel = channels.get(policy.anchor.channel)
    if anchor_channel is None:
        raise SceneLacksAnchorChannelError(
            f"scene {scene.scene_id} does not declare anchor channel "
            f"{policy.anchor.channel!r}"
        )
    anchor_clock = anchor_channel.source_clock
    for member_policy in policy.members:
        member_channel = channels.get(member_policy.channel)
        if member_channel is not None and member_channel.source_clock != anchor_clock:
            raise SampleViewPolicyError(
                f"channel {member_policy.channel!r} is on clock "
                f"{member_channel.source_clock!r}, not the anchor clock "
                f"{anchor_clock!r}; association never converts between clocks"
            )

    by_channel: dict[str, list[SceneObservation]] = defaultdict(list)
    for observation in manifest.observations:
        by_channel[observation.channel].append(observation)
    for observations in by_channel.values():
        observations.sort(key=lambda o: (o.timestamp_ns, o.observation_id))
    times = {
        channel: [o.timestamp_ns for o in observations]
        for channel, observations in by_channel.items()
    }

    poses: list[ScenePose] = []
    pose_times: list[int] = []
    if policy.pose is not None:
        frame_pair = [
            p
            for p in manifest.poses
            if p.transform.parent_frame_id == policy.pose.parent_frame_id
            and p.transform.child_frame_id == policy.pose.child_frame_id
        ]
        poses = sorted(
            (p for p in frame_pair if p.source_clock == anchor_clock),
            key=lambda p: (p.timestamp_ns, p.pose_id),
        )
        if frame_pair and not poses:
            raise SampleViewPolicyError(
                f"pose {policy.pose.parent_frame_id!r} <- "
                f"{policy.pose.child_frame_id!r} exists only on clocks other than "
                f"the anchor clock {anchor_clock!r}; association never converts "
                "between clocks"
            )
        pose_times = [p.timestamp_ns for p in poses]

    samples: list[SceneSample] = []
    dropped: list[DroppedAnchor] = []
    anchors = by_channel.get(policy.anchor.channel, [])
    for rank, anchor in enumerate(anchors):
        if rank % policy.anchor.stride != 0:
            continue
        reasons: list[str] = []
        members: list[SampleMember] = []
        for member_policy in policy.members:
            index = _associate(
                times.get(member_policy.channel, []),
                anchor.timestamp_ns,
                member_policy.association,
                member_policy.tolerance_ns,
            )
            if index is None:
                if member_policy.required:
                    reasons.append(f"missing_member:{member_policy.channel}")
                continue
            chosen = by_channel[member_policy.channel][index]
            members.append(
                SampleMember(
                    channel=chosen.channel,
                    observation_id=chosen.observation_id,
                    timestamp_ns=chosen.timestamp_ns,
                    time_delta_ns=chosen.timestamp_ns - anchor.timestamp_ns,
                )
            )
        pose: SamplePose | None = None
        if policy.pose is not None:
            index = _associate(
                pose_times,
                anchor.timestamp_ns,
                policy.pose.association,
                policy.pose.tolerance_ns,
            )
            if index is None:
                if policy.pose.required:
                    reasons.append("missing_pose")
            else:
                pose = SamplePose(
                    pose_id=poses[index].pose_id,
                    time_delta_ns=poses[index].timestamp_ns - anchor.timestamp_ns,
                )
        if reasons:
            dropped.append(
                DroppedAnchor(
                    observation_id=anchor.observation_id, reasons=sorted(reasons)
                )
            )
            continue
        samples.append(
            SceneSample(
                sample_id=f"smp-{rank:06d}",
                anchor=SampleMember(
                    channel=anchor.channel,
                    observation_id=anchor.observation_id,
                    timestamp_ns=anchor.timestamp_ns,
                    time_delta_ns=0,
                ),
                members=members,
                pose=pose,
            )
        )

    refs, label_stats, samples = _attach_labels(
        manifest=manifest, samples=samples, label_sets=label_sets
    )
    return SceneSampleViewManifest(
        scene=scene,
        policy=policy,
        anchor_clock=anchor_clock,
        label_sets=refs,
        label_stats=label_stats,
        samples=samples,
        dropped=dropped,
    )


def _label_index(
    resolver: SceneAnchorResolver, label_set: LabelSetManifest
) -> tuple[dict[ResolvedAnchor, list[Box3DLabel]], set[ResolvedAnchor]]:
    """Labels and covered anchors of ``label_set`` that resolve into this
    Scene, by where they resolve. Anchors of another source, unit or channel
    selection belong to other Scenes and are ignored here; an anchor that
    contradicts the Scene fails the view (``AnchorResolutionError``)."""
    covered = {
        resolved
        for anchor in label_set.coverage
        if (resolved := resolver.resolve(anchor)) is not None
    }
    labels: dict[ResolvedAnchor, list[Box3DLabel]] = defaultdict(list)
    for label in label_set.labels:
        resolved = resolver.resolve(label.anchor)
        if resolved is not None:
            labels[resolved].append(label)
    return labels, covered


def _attach_labels(
    *,
    manifest: SceneManifest,
    samples: list[SceneSample],
    label_sets: Sequence[tuple[LabelSetRef, LabelSetManifest]],
) -> tuple[list[LabelSetRef], list[LabelAttachmentStats], list[SceneSample]]:
    """Labels attach through the observations a sample holds. A label
    resolved to an observation is presented by a sample holding that
    observation; a label resolved to a source-defined group (``group_ref``)
    by a sample holding any member of that group, so views anchored on
    different channels present a group's labels alike. An observation or a
    group can be held by several samples (a slow channel nearest to two
    anchors), so each is *owned* by exactly one sample: the one whose anchor
    instant is nearest to a held member, the earlier on a tie. A label is
    therefore never counted twice."""
    ordered = sorted(label_sets, key=lambda pair: pair[0].label_set_id)
    ids = [ref.label_set_id for ref, _ in ordered]
    if len(ids) != len(set(ids)):
        raise SampleViewPolicyError("a label set may be pinned only once")

    observation_ns = {o.observation_id: o.timestamp_ns for o in manifest.observations}
    group_members = {g.group_id: g.observation_ids for g in manifest.groups}
    holders: dict[str, list[SceneSample]] = defaultdict(list)
    for sample in samples:
        for observation_id in sample.member_observation_ids():
            holders[observation_id].append(sample)

    def owner(resolved: ResolvedAnchor) -> SceneSample | None:
        members = (
            [resolved.observation_id]
            if resolved.group_id is None
            else group_members[resolved.group_id]
        )
        held = [
            (sample, observation_ns[observation_id])
            for observation_id in members
            for sample in holders.get(observation_id, ())
        ]
        if not held:
            return None
        return min(
            held,
            key=lambda pair: (
                abs(pair[0].anchor.timestamp_ns - pair[1]),
                pair[0].anchor.timestamp_ns,
                pair[0].sample_id,
            ),
        )[0]

    resolver = SceneAnchorResolver(manifest)
    stats: list[LabelAttachmentStats] = []
    per_sample: dict[str, list[SampleLabels]] = {s.sample_id: [] for s in samples}
    for ref, label_set in ordered:
        labels, covered = _label_index(resolver, label_set)
        covered_samples: set[str] = set()
        for resolved in covered:
            owning = owner(resolved)
            if owning is not None:
                covered_samples.add(owning.sample_id)
        owned: dict[str, set[str]] = defaultdict(set)
        unattached = 0
        for resolved, observed_labels in labels.items():
            owning = owner(resolved)
            if owning is None:
                unattached += len(observed_labels)
            else:
                owned[owning.sample_id].update(
                    item.label_id for item in observed_labels
                )
        for sample in samples:
            per_sample[sample.sample_id].append(
                SampleLabels(
                    label_set_id=ref.label_set_id,
                    covered=sample.sample_id in covered_samples,
                    label_ids=sorted(owned.get(sample.sample_id, ())),
                )
            )
        stats.append(
            LabelAttachmentStats(
                label_set_id=ref.label_set_id,
                covered_observation_count=len(covered),
                attached_label_count=sum(len(v) for v in owned.values()),
                unattached_label_count=unattached,
            )
        )
    attached_samples = [
        sample.model_copy(update={"labels": per_sample[sample.sample_id]})
        for sample in samples
    ]
    return [ref for ref, _ in ordered], stats, attached_samples


__all__ = [
    "AmbiguousAnchorError",
    "SampleViewPolicyError",
    "SceneLacksAnchorChannelError",
    "build_scene_sample_view",
    "sample_view_artifact_id",
]
