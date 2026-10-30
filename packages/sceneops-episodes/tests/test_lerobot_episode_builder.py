"""The LeRobot Episode builder (ADR-015 §6.2, §6.5–§6.7; M3.0 D2, D3, D8, D9,
D13).

Synthetic v3.0 trees exercise each rule and each refusal; expectations are the
values the tree was written with. The pinned pusht copy is held to the numbers
of the M3 plan (S3 acceptance), measured on the raw data in M3.0; those tests
skip without `make reference-source-fetch`.
"""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path

import pytest

from sceneops_core.artifacts.logical_uri import LogicalNamespace, format_logical_uri
from sceneops_core.episodes.lerobot_build import LeRobotEpisodeBuildConfig
from sceneops_core.episodes.recording_build import EpisodeStreamRole
from sceneops_core.episodes.schemas import load_canonical_episode_manifest
from sceneops_core.provenance import PinnedContent
from sceneops_core.sources import SnapshotSelection
from sceneops_episodes.lerobot_builder import (
    LeRobotEpisodeBuildError,
    plan_lerobot_episodes,
)
from sceneops_sources import LocalContentFiles, build_inventory, select_files
from sceneops_sources.lerobot_synthetic import (
    INCLUDE,
    VIDEO_PATH,
    float32,
    write_lerobot_tree,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PUSHT = REPO_ROOT / "data/raw/lerobot/pusht"

CONFIG = LeRobotEpisodeBuildConfig(
    features=[
        {"feature": "observation.image", "role": "observation"},
        {"feature": "observation.state", "role": "state"},
        {"feature": "action", "role": "action"},
        {"feature": "next.reward", "role": "transition"},
        {"feature": "next.done", "role": "transition"},
        {"feature": "next.success", "role": "transition"},
    ]
)


def _plan(root: Path, *, include=INCLUDE, config=CONFIG, unit_keys=None):
    inventory = build_inventory(
        root, select_files(root, SnapshotSelection(include=tuple(include)))
    )
    return plan_lerobot_episodes(
        pinned=PinnedContent(inventory=inventory),
        files=LocalContentFiles(root, inventory),
        config=config,
        unit_keys=unit_keys,
    )


def _values(manifest, topic):
    return [o.values.get("value") for o in manifest.occurrences() if o.topic == topic]


# --- rules ---------------------------------------------------------------------------


@pytest.mark.parametrize("names_form", ["dict", "list"])
@pytest.mark.parametrize("fixed", [True, False])
def test_one_episode_per_native_episode_on_its_own_frame_grid(
    tmp_path, names_form, fixed
):
    write_lerobot_tree(
        tmp_path, lengths=(3, 2), fps=30, names_form=names_form, fixed_size_lists=fixed
    )
    plan = _plan(tmp_path)

    assert [e.unit_key for e in plan.episodes] == ["0", "1"]
    second = plan.episodes[1].manifest
    source = second.lineage.source
    assert source.source_kind == "content"
    assert (
        source.source_clock,
        source.start_timestamp_ns,
        source.end_timestamp_ns,
    ) == (
        "lerobot.frame_time",
        0,
        33_333_334,  # round_half_even(10⁹ / 30) + 1
    )
    assert second.frame_timeline.model_dump() == {
        "clock": "lerobot.frame_time",
        "fps": 30,
        "frame_count": 2,
    }
    assert [d.model_dump(mode="json") for d in second.clock_domains] == [
        {"clock": "lerobot.frame_time", "origin": "unit", "resolution_ns": 1}
    ]
    assert sorted({o.timestamp_ns for o in second.occurrences()}) == [0, 33_333_333]
    # Values bit-equal to the stored float32, global index as native_ref.
    assert _values(second, "observation.state") == [
        [float32(1.0), float32(-0.0)],
        [float32(1.25), float32(-1.0)],
    ]
    assert _values(second, "action") == [[0.0, 1.0], [1.5, 1.0]]
    assert [o.native_ref for o in second.states] == ["3", "4"]
    state = second.stream("observation.state")
    assert state.fields[0].component_names == ["motor_0", "motor_1"]
    assert state.schema_name == "float32"
    # No occurrence carries the recorded timestamp or frame index.
    assert all(
        set(o.values) <= {"value", "task", "task_index"} for o in second.occurrences()
    )


def test_videos_are_frame_selectors_into_the_preserved_file(tmp_path):
    write_lerobot_tree(tmp_path, lengths=(3, 2))
    plan = _plan(tmp_path)
    video = (tmp_path / VIDEO_PATH).read_bytes()
    content_id = plan.episodes[1].manifest.lineage.source.content_id
    refs = [o.payload for o in plan.episodes[1].manifest.observations]
    # The preserved file in place, by its sources Logical URI: nothing copied.
    assert {(r.uri, r.media_type, r.size_bytes, r.checksum) for r in refs} == {
        (
            format_logical_uri(
                LogicalNamespace.SOURCES, f"contents/{content_id}/files/{VIDEO_PATH}"
            ),
            "video/mp4",
            len(video),
            "sha256:" + hashlib.sha256(video).hexdigest(),
        )
    }
    # Episode 1 starts at from_timestamp = 3 / 10 in the shared video; the
    # selector is the frame's time in the video's own time base.
    start = Fraction(0.3)
    assert [r.selector.timestamp_ns for r in refs] == [
        round((start + Fraction(k, 10)) * 10**9) for k in range(2)
    ]
    assert {r.selector.kind for r in refs} == {"video_frame"}
    assert plan.payloads == {}


def test_task_events_mark_every_change_of_task(tmp_path):
    write_lerobot_tree(
        tmp_path,
        lengths=(4,),
        tasks=("reach", "grasp"),
        task_index=lambda e, k: 0 if k < 2 else 1,
    )
    events = _plan(tmp_path).episodes[0].manifest.events
    assert [(o.timestamp_ns, o.values) for o in events] == [
        (0, {"task": "reach", "task_index": 0}),
        (200_000_000, {"task": "grasp", "task_index": 1}),
    ]


def test_transitions_keep_one_verbatim_value_per_frame_and_derive_nothing(tmp_path):
    write_lerobot_tree(tmp_path, lengths=(3,))
    manifest = _plan(tmp_path).episodes[0].manifest
    assert _values(manifest, "next.done") == [False, False, True]
    assert _values(manifest, "next.reward") == [0.0, 0.125, 0.25]
    assert _values(manifest, "next.success") == [False, False, False]
    assert [o.topic for o in manifest.events] == ["task_index"]


def test_a_subset_builds_the_same_revisions(tmp_path):
    write_lerobot_tree(tmp_path, lengths=(3, 2, 4))
    full = {e.unit_key: e.manifest.checksum() for e in _plan(tmp_path).episodes}
    subset = _plan(tmp_path, unit_keys=["2", "0"])
    assert [e.unit_key for e in subset.episodes] == ["0", "2"]
    assert {e.unit_key: e.manifest.checksum() for e in subset.episodes} == {
        k: full[k] for k in ("0", "2")
    }
    assert subset.producer == _plan(tmp_path).producer


def test_build_is_deterministic_and_canonical(tmp_path):
    write_lerobot_tree(tmp_path)
    first = _plan(tmp_path).episodes[0].manifest.to_canonical_bytes()
    assert _plan(tmp_path).episodes[0].manifest.to_canonical_bytes() == first
    assert load_canonical_episode_manifest(first).to_canonical_bytes() == first
    document = json.loads(first)
    for forbidden in ("snapshot", "upstream", "license", "mit"):
        assert forbidden not in first.decode()
    assert (
        document["lineage"]["producer"]["producer_id"]
        == "sceneops.lerobot_episode_builder"
    )


# --- refusals ------------------------------------------------------------------------


def _refused(root: Path, match: str, **kwargs) -> None:
    with pytest.raises(LeRobotEpisodeBuildError, match=match):
        _plan(root, **kwargs)


def test_a_timestamp_off_the_frame_grid_is_refused(tmp_path):
    write_lerobot_tree(
        tmp_path, timestamp=lambda e, k: float32(k / 10 + (1e-6 if k == 1 else 0))
    )
    _refused(tmp_path, "is not float32")


def test_frames_that_are_not_dense_from_zero_are_refused(tmp_path):
    write_lerobot_tree(tmp_path, frame_index=lambda e, k: k + 1)
    _refused(tmp_path, "dense from 0")


@pytest.mark.parametrize("fps", [10.0, 0, "10"])
def test_a_rate_that_is_not_a_positive_integer_is_refused(tmp_path, fps):
    write_lerobot_tree(tmp_path, fps=fps, timestamp=lambda e, k: 0.0)
    _refused(tmp_path, "not a positive integer")


def test_an_unsupported_codebase_version_is_refused(tmp_path):
    write_lerobot_tree(tmp_path, codebase_version="v2.1")
    _refused(tmp_path, "not supported")


def test_an_unknown_unit_key_is_refused(tmp_path):
    write_lerobot_tree(tmp_path)
    _refused(tmp_path, r"\['7'\] name no episode", unit_keys=["0", "7"])


def test_a_unit_whose_files_are_not_preserved_is_refused(tmp_path):
    write_lerobot_tree(tmp_path)
    _refused(tmp_path, "are not files of", include=("README.md", "data/**", "meta/**"))


def test_video_evidence_that_disagrees_is_refused(tmp_path):
    write_lerobot_tree(
        tmp_path, video_path="videos/observation.image/chunk-000/file-000.mkv"
    )
    info = json.loads((tmp_path / "meta/info.json").read_text())
    info["video_path"] = info["video_path"].replace(".mp4", ".mkv")
    (tmp_path / "meta/info.json").write_text(json.dumps(info))
    _refused(tmp_path, "evidence for video/mp4")


def test_names_in_more_than_one_group_are_refused(tmp_path):
    write_lerobot_tree(tmp_path)
    info = json.loads((tmp_path / "meta/info.json").read_text())
    info["features"]["observation.state"]["names"] = {"a": ["x"], "b": ["y"]}
    (tmp_path / "meta/info.json").write_text(json.dumps(info))
    _refused(tmp_path, "only one group of names")


@pytest.mark.parametrize(
    ("selection", "match"),
    [
        ({"feature": "observation.velocity", "role": "state"}, "not a feature"),
        (
            {"feature": "observation.image", "role": "state"},
            "can only be an observation",
        ),
    ],
)
def test_features_the_dataset_cannot_provide_are_refused(tmp_path, selection, match):
    write_lerobot_tree(tmp_path)
    _refused(tmp_path, match, config=LeRobotEpisodeBuildConfig(features=[selection]))


# --- the pinned pusht copy (M3 S3 acceptance) -----------------------------------------


@pytest.fixture(scope="module")
def pusht_plan():
    if not PUSHT.is_dir():
        pytest.skip(f"{PUSHT} absent: run `make reference-source-fetch`")
    return _plan(PUSHT)


def test_pusht_units_counts_and_transitions(pusht_plan):
    episodes = pusht_plan.episodes
    assert [e.unit_key for e in episodes] == [str(i) for i in range(206)]
    for role in (
        EpisodeStreamRole.OBSERVATION,
        EpisodeStreamRole.STATE,
        EpisodeStreamRole.ACTION,
    ):
        assert pusht_plan.count(role) == 25_650
    assert pusht_plan.count(EpisodeStreamRole.TRANSITION) == 3 * 25_650
    transitions = [o for e in episodes for o in e.manifest.transitions]
    assert (
        sum(1 for o in transitions if o.topic == "next.done" and o.values["value"])
        == 412
    )
    assert (
        sum(
            1 for o in transitions if o.topic == "next.reward" and o.values["value"] > 0
        )
        == 20_725
    )
    assert not any(o.values["value"] for o in transitions if o.topic == "next.success")
    for episode in episodes:
        frames = episode.manifest.frame_timeline.frame_count
        done = [
            o.values["value"]
            for o in episode.manifest.transitions
            if o.topic == "next.done"
        ]
        assert done == [False] * (frames - 2) + [True, True]
        assert [
            (o.timestamp_ns, o.values["task"]) for o in episode.manifest.events
        ] == [(0, "Push the T-shaped block onto the T-shaped target.")]


def test_pusht_episode_zero_and_one(pusht_plan):
    first = pusht_plan.episodes[0].manifest
    assert first.frame_timeline.model_dump() == {
        "clock": "lerobot.frame_time",
        "fps": 10,
        "frame_count": 161,
    }
    source = first.lineage.source
    assert (source.start_timestamp_ns, source.end_timestamp_ns) == (0, 16_000_000_001)
    assert [o.timestamp_ns for o in first.states] == [
        k * 100_000_000 for k in range(161)
    ]
    for topic in ("observation.state", "action"):
        assert first.stream(topic).fields[0].component_names == ["motor_0", "motor_1"]
    second_video = pusht_plan.episodes[1].manifest.observations[0].payload
    assert second_video.logical_uri().key.endswith(
        "/files/videos/observation.image/chunk-000/file-000.mp4"
    )
    assert second_video.checksum.startswith("sha256:f58d1185")
    assert second_video.media_type == "video/mp4"
    assert second_video.selector.timestamp_ns == 16_100_000_000


def test_the_builder_emits_episode_manifest_v4_with_video_frame_selectors(tmp_path):
    """The emitted schema version moves only with the builder's semantics
    version (ADR-017)."""
    from sceneops_core.artifacts.schemas import PayloadRef
    from sceneops_core.episodes.schemas import EPISODE_MANIFEST_SCHEMA_V4
    from sceneops_episodes.lerobot_builder import LEROBOT_EPISODE_SEMANTICS_VERSION

    write_lerobot_tree(tmp_path)
    manifest = _plan(tmp_path).episodes[0].manifest
    assert manifest.schema_version == EPISODE_MANIFEST_SCHEMA_V4
    assert (
        manifest.lineage.producer.semantics_version
        == (LEROBOT_EPISODE_SEMANTICS_VERSION)
        == 2
    )
    payloads = [o.payload for o in manifest.occurrences() if o.payload is not None]
    assert {type(p) for p in payloads} == {PayloadRef}
    assert all(p.selector is not None for p in payloads)
