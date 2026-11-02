"""The temporal lineage verifier (tools/reference/temporal_lineage.py) over a synthetic
platform state built by the real builders, engine and writers.

The expected instance of every comparison below comes from the pinned canonical
manifest, read back from the store and parsed, never from the value under test, and
the raw Parquet columns are read directly, so a passing test is not a round trip
comparing a value with itself. A forged fault is self-consistent: checksums and pins
agree with the forged bytes, the readers accept them, and only the manifest refutes them.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest

TOOLS = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS.parents[1]
sys.path.insert(0, str(TOOLS))

from temporal_lineage import (  # noqa: E402
    TemporalLineageVerifier,
    VerifiedAligned,
    report_of,
    sha256,
)
from temporal_lineage_controls import (  # noqa: E402
    ALIGNED_CONTROLS,
    LEARNING_CONTROLS,
    SCENE_CONTROLS,
    AlignedTarget,
    ControlNotApplicable,
    LearningTarget,
    Overlay,
    SceneTarget,
    _forge_aligned,
    _observation_window_substituted,
    _Snapshot,
    run_aligned_controls,
    run_learning_controls,
    run_scene_controls,
)
from temporal_lineage_fixture import (  # noqa: E402
    DATASET,
    EPISODES,
    VERSION,
    World,
    build_world,
    native_episode,
    recording_episode,
)
from verification.findings import Findings  # noqa: E402

from sceneops_analytics import (  # noqa: E402
    SceneOpsDataset,
    query_parquet,
    query_scene_analytics,
    read_scene_table_bytes,
    scene_table_schema_stamp,
)
from sceneops_analytics.parquet_contract import TABLE_SCHEMA_METADATA_KEY  # noqa: E402
from sceneops_core.common.canonical_json import canonical_json_bytes  # noqa: E402
from sceneops_core.common.derived_ids import derived_artifact_id  # noqa: E402
from sceneops_core.episodes.alignment import load_aligned_episode_artifact  # noqa: E402
from sceneops_core.episodes.learning import EpisodeRef  # noqa: E402
from sceneops_core.episodes.learning_export import LearningDataExportManifest  # noqa: E402
from sceneops_core.episodes.schemas import load_canonical_episode_manifest  # noqa: E402
from sceneops_core.provenance import manifest_artifact_id_for  # noqa: E402
from sceneops_core.scenes.schemas import load_canonical_scene_manifest  # noqa: E402
from sceneops_core.temporal import (  # noqa: E402
    ClockInstance,
    ClockOrigin,
    IncomparableClocksError,
    require_comparable,
    restore_clock_instance,
)


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    return run(build_world(tmp_path_factory.mktemp("temporal-world")))


def _read(world: World, uri: str) -> bytes:
    return run(world.store.read_bytes(uri))


def _pinned_episode(world: World, name: str):
    """The EpisodeManifest as a verifier reads it, from the pinned bytes."""
    artifact = world.aligned_entries[name][1]
    record = world.records[artifact.source_revision.source_artifact_id]
    return load_canonical_episode_manifest(_read(world, record["uri"]))


def _pinned_scene(world: World, name: str):
    member = next(
        m
        for m in world.versions[("scene", DATASET, VERSION)]
        if m["scene_id"] == world.scene_id(name)
    )
    record = world.records[member["manifest_artifact_id"]]
    return load_canonical_scene_manifest(_read(world, record["uri"]))


def _verify_aligned(source, artifact_id: str, checksum: str | None = None) -> Findings:
    findings = Findings()
    run(
        TemporalLineageVerifier(source).verify_aligned(
            findings, aligned_artifact_id=artifact_id, checksum=checksum
        )
    )
    return findings


def _verify_export(source, artifact_id: str, checksum: str | None = None) -> Findings:
    findings = Findings()
    run(
        TemporalLineageVerifier(source).verify_learning_export(
            findings, manifest_artifact_id=artifact_id, checksum=checksum
        )
    )
    return findings


def _verify_scenes(source, table_uris) -> Findings:
    findings = Findings()
    run(
        TemporalLineageVerifier(source).verify_scene_analytics(
            findings, dataset_id=DATASET, dataset_version=VERSION, table_uris=table_uris
        )
    )
    return findings


def _comparable(a: ClockInstance, b: ClockInstance) -> bool:
    try:
        require_comparable(a, b)
    except IncomparableClocksError:
        return False
    return True


def _forged_aligned(world: World, name: str, mutate) -> tuple[Overlay, str, str]:
    return run(
        _forge_aligned(
            world, AlignedTarget(world.aligned[name]), lambda document: mutate(document)
        )
    )


# ── correct contexts pass (matrix 6, 7, 8) ───────────────────────────────────


@pytest.mark.parametrize("name", EPISODES)
def test_a_correct_aligned_episode_is_what_its_pinned_manifest_issues(world, name):
    findings = _verify_aligned(world, world.aligned[name])
    assert findings.ok, findings.violations
    assert findings.checked["aligned_clock_instance"] == 1

    artifact = load_aligned_episode_artifact(
        _read(world, world.records[world.aligned[name]]["uri"])
    )
    manifest = _pinned_episode(world, name)
    clock = artifact.aligned_episode.source_clock
    assert artifact.aligned_episode.clock_instance == manifest.clock_instance(clock)


@pytest.mark.parametrize("export", ["base_export", "incremental_export"])
def test_a_correct_learning_export_is_what_each_pinned_manifest_issues(world, export):
    artifact_id = getattr(world, export)
    findings = _verify_export(world, artifact_id)
    assert findings.ok, findings.violations

    manifest = LearningDataExportManifest.model_validate_json(
        _read(world, world.records[artifact_id]["uri"])
    )
    frame = pl.read_parquet(
        io.BytesIO(_read(world, manifest.table_uris["learning_episodes"]))
    )
    names = {world.episode_id(n): n for n in EPISODES}
    assert frame.height == len(manifest.inputs) >= 4
    for row in frame.iter_rows(named=True):
        pinned = _pinned_episode(world, names[row["episode_id"]])
        assert restore_clock_instance(
            row["source_clock"], row["clock_origin"], row["clock_scope"]
        ) == pinned.clock_instance(row["source_clock"])


def test_correct_scene_analytics_are_what_each_pinned_manifest_issues(world):
    findings = _verify_scenes(world, world.scene_tables)
    assert findings.ok, findings.violations

    names = {world.scene_id(n): n for n in world.scenes}
    scenes = pl.read_parquet(io.BytesIO(_read(world, world.scene_tables["scenes"])))
    assert scenes.height == len(world.scenes)
    for row in scenes.iter_rows(named=True):
        manifest = _pinned_scene(world, names[row["scene_id"]])
        assert restore_clock_instance(
            row["window_clock"], row["window_clock_origin"], row["window_clock_scope"]
        ) == manifest.clock_instance(manifest.declared_window().source_clock)


# ── a wrong context fails, and for that reason (matrix 9 - 14) ───────────────


def _with_scope_of(world: World, other: str):
    scope = list(world.aligned_entries[other][1].aligned_episode.clock_scope)

    def mutate(document: dict) -> None:
        document["alignedEpisode"]["clockScope"] = scope

    return mutate


@pytest.mark.parametrize(
    ("name", "other"),
    [
        pytest.param("rec-a-0", "rec-b-0", id="another-robot-run"),
        pytest.param("native-0", "native-1", id="another-lerobot-episode"),
    ],
)
def test_a_structurally_valid_scope_of_another_unit_fails_on_the_comparison(
    world, name, other
):
    overlay, artifact_id, checksum = _forged_aligned(
        world, name, _with_scope_of(world, other)
    )
    # Everything a reader checks holds: the bytes are the pinned ones and they parse
    # into a valid instance. Nothing short of the manifest can refute it.
    data = run(overlay.store.read_bytes(overlay.records[artifact_id]["uri"]))
    assert sha256(data) == checksum
    forged = load_aligned_episode_artifact(data).aligned_episode
    assert forged.clock_instance == restore_clock_instance(
        forged.source_clock, forged.clock_origin, forged.clock_scope
    )

    findings = _verify_aligned(overlay, artifact_id, checksum)
    assert findings.codes() == {"aligned_clock_instance"}
    assert findings.checked["aligned_checksum"] == 2


def test_a_failure_names_what_was_stated_and_what_was_issued(world):
    overlay, artifact_id, checksum = _forged_aligned(
        world, "rec-a-0", _with_scope_of(world, "rec-b-0")
    )
    (violation,) = _verify_aligned(overlay, artifact_id, checksum).violations
    assert "run-b" in violation["message"] and "run-a" in violation["message"]
    assert violation["code"] == "aligned_clock_instance"
    assert violation["aligned_artifact_id"] == artifact_id


def test_a_correct_scope_on_the_wrong_origin_fails(world):
    def mutate(document: dict) -> None:
        document["alignedEpisode"]["clockOrigin"] = "unit"

    overlay, artifact_id, checksum = _forged_aligned(world, "rec-a-0", mutate)
    assert _verify_aligned(overlay, artifact_id, checksum).codes() == {
        "aligned_clock_instance"
    }


def test_a_valid_timestamp_on_an_undeclared_clock_id_fails(world):
    def mutate(document: dict) -> None:
        document["alignedEpisode"]["sourceClock"] = "sensor.header_stamp"

    overlay, artifact_id, checksum = _forged_aligned(world, "rec-a-0", mutate)
    assert _verify_aligned(overlay, artifact_id, checksum).codes() == {
        "aligned_clock_declared"
    }


def test_an_artifact_naming_another_episode_fails(world):
    other = world.episode_id("rec-b-0")

    def mutate(document: dict) -> None:
        document["alignedEpisode"]["episodeId"] = other
        document["sourceRevision"]["episodeId"] = other

    overlay, artifact_id, checksum = _forged_aligned(world, "rec-a-0", mutate)
    assert "episode_identity" in _verify_aligned(overlay, artifact_id, checksum).codes()


def test_an_export_pinning_the_artifact_of_another_episode_fails(world):
    record = world.records[world.base_export]
    document = json.loads(_read(world, record["uri"]))
    first, second = document["inputs"][0], document["inputs"][1]
    first["alignedArtifactId"] = second["alignedArtifactId"]
    data = json.dumps(document).encode()
    uri = record["uri"] + ".forged"
    overlay = Overlay(
        world,
        objects={uri: data},
        records={world.base_export: {**record, "uri": uri, "checksum": sha256(data)}},
    )
    codes = _verify_export(overlay, world.base_export, sha256(data)).codes()
    assert "aligned_checksum" in codes or "episode_identity" in codes


def test_a_row_agreeing_with_a_wrong_aligned_episode_still_fails_on_the_manifest(world):
    """Each layer is held to the manifest too: an AlignedEpisode and its learning row
    can agree with each other and both be another unit's."""
    artifact = world.aligned_entries["rec-a-0"][1]
    manifest = _pinned_episode(world, "rec-a-0")
    clock = artifact.aligned_episode.source_clock
    wrong = world.aligned_entries["rec-b-0"][1].aligned_episode.clock_instance
    assert wrong.clock == clock and wrong != manifest.clock_instance(clock)
    forged = artifact.model_copy(
        update={
            "aligned_episode": artifact.aligned_episode.model_copy(
                update={"clock_scope": wrong.scope}
            )
        }
    )

    class Stated:
        clock_instance = wrong
        episode_ref = EpisodeRef(
            episode_id=artifact.aligned_episode.episode_id,
            aligned_artifact_checksum="sha256:" + "0" * 64,
        )

    findings = Findings()
    TemporalLineageVerifier(world)._check_learning_row(
        findings,
        None,
        Stated,
        VerifiedAligned(forged, manifest, manifest.clock_instance(clock)),
    )
    assert findings.codes() == {"learning_clock_instance"}
    assert findings.checked["learning_clock_instance"] == 2
    assert len(findings.violations) == 1


def _other_revision_of(world: World, name: str):
    """A second registered revision of the unit: same binding, so the same issued
    instance, and other bytes because another build made it."""
    assert name == "rec-a-0"
    manifest = recording_episode("run-a", "segment-0000")
    payload = json.loads(manifest.to_canonical_bytes())
    payload["streams"][0]["schema_name"] = "other/msg/Schema"
    data = canonical_json_bytes(payload)
    other = load_canonical_episode_manifest(data)
    checksum = other.checksum()
    artifact_id = manifest_artifact_id_for(
        unit_id=world.episode_id(name), manifest_checksum=checksum
    )
    uri = str(world.root / "canonical" / "episode" / f"other-{checksum[7:19]}.json")
    return artifact_id, checksum, uri, data


def _claiming_other_revision(world: World) -> tuple[Overlay, str, str, str]:
    artifact_id, checksum, uri, data = _other_revision_of(world, "rec-a-0")

    def mutate(document: dict) -> None:
        document["sourceRevision"]["sourceArtifactId"] = artifact_id
        document["sourceRevision"]["sourceManifestSha256"] = checksum.removeprefix(
            "sha256:"
        )
        document["sourceRevision"]["episodeManifestUri"] = uri

    overlay, forged_id, forged_checksum = _forged_aligned(world, "rec-a-0", mutate)
    overlay.objects[uri] = data
    overlay.records[artifact_id] = {
        "artifact_id": artifact_id,
        "kind": "episode_manifest",
        "uri": uri,
        "checksum": checksum,
    }
    return overlay, forged_id, forged_checksum, artifact_id


def test_an_equal_instance_from_another_revision_is_refuted_by_the_alignment_record(
    world,
):
    """The issued instance is the same, so the comparison alone cannot tell: the record
    registered when the alignment ran names the revision it read."""
    overlay, forged_id, forged_checksum, _ = _claiming_other_revision(world)
    original = world.records[world.aligned["rec-a-0"]]["metadata"]
    overlay.records[forged_id] = {**overlay.records[forged_id], "metadata": original}
    findings = _verify_aligned(overlay, forged_id, forged_checksum)
    assert findings.codes() == {"aligned_record"}
    assert findings.checked["aligned_clock_instance"] == 1


def test_an_equal_instance_from_a_revision_the_version_does_not_pin_is_refused(world):
    """A learning export is held to the revision its DatasetVersion pins."""
    other_id, _, _, _ = _other_revision_of(world, "rec-a-0")
    episode_id = world.episode_id("rec-a-0")

    def pin_other(rows: list[dict]) -> list[dict]:
        return [
            {**r, "manifest_artifact_id": other_id}
            if r["episode_id"] == episode_id
            else r
            for r in rows
        ]

    overlay = Overlay(world, member_changes={"episode": pin_other})
    assert _verify_export(overlay, world.base_export).codes() == {"learning_membership"}


def test_a_source_revision_whose_bytes_changed_fails(world):
    artifact = world.aligned_entries["rec-a-0"][1]
    record = world.records[artifact.source_revision.source_artifact_id]
    overlay = Overlay(
        world, objects={record["uri"]: _read(world, record["uri"]) + b" "}
    )
    assert (
        "source_revision_checksum"
        in _verify_aligned(overlay, world.aligned["rec-a-0"]).codes()
    )


def _scenes_forged(world: World, table: str, mutate) -> Findings:
    snapshot = _Snapshot(world, SceneTarget(DATASET, VERSION, world.scene_tables))

    async def forge() -> None:
        rows = await snapshot.rows(table)
        mutate(rows)
        await snapshot.write(table, rows)

    run(forge())
    overlay = Overlay(world, objects=snapshot.objects, records=snapshot.records)
    return _verify_scenes(overlay, snapshot.uris)


def test_a_scene_table_stating_a_declared_clock_of_the_wrong_timestamp_fails(world):
    """Not an unknown clock: one the manifest does declare, for something else."""
    scene_id = world.scene_id("scene-a")

    def mutate(rows: list[dict]) -> None:
        for row in rows:
            if row["scene_id"] == scene_id:
                row["window_clock"] = "camera.header_stamp"

    assert _scenes_forged(world, "scenes", mutate).codes() == {"scene_window_instance"}


def test_a_scene_row_pinning_another_revision_fails(world):
    scene_id = world.scene_id("scene-b")

    def mutate(rows: list[dict]) -> None:
        for row in rows:
            if row["scene_id"] == scene_id:
                row["manifest_artifact_id"] = "scene-manifest-" + "0" * 32

    assert _scenes_forged(world, "scenes", mutate).codes() == {"scene_revision"}


def test_a_member_pinning_bytes_that_are_not_its_manifest_fails(world):
    members = world.versions[("scene", DATASET, VERSION)]
    overlay = Overlay(
        world,
        member_changes={
            "scene": lambda rows: [
                {**rows[0], "manifest_checksum": rows[1]["manifest_checksum"]},
                *rows[1:],
            ]
        },
    )
    assert len(members) > 1
    assert (
        "scene_revision_checksum" in _verify_scenes(overlay, world.scene_tables).codes()
    )


# ── multi-unit storage (matrix 15 - 19) ──────────────────────────────────────


def test_one_shard_holds_episodes_with_separate_contexts(world):
    manifest = LearningDataExportManifest.model_validate_json(
        _read(world, world.records[world.base_export]["uri"])
    )
    (shard,) = manifest.shard_index.learning_steps
    assert len(shard.episodes) == 4

    async def read() -> dict[EpisodeRef, ClockInstance]:
        dataset = await SceneOpsDataset.open(
            learning_manifest=manifest,
            learning_manifest_checksum=world.records[world.base_export]["checksum"],
            artifact_store=world.store,
        )
        return {
            m.episode_ref: dataset.get_episode(m.episode_ref).clock_instance
            for m in shard.episodes
        }

    instances = run(read())
    names = {world.episode_id(n): n for n in EPISODES}
    # Equal clock ids and times in one physical file, each row group its own unit's.
    for ref, instance in instances.items():
        pinned = _pinned_episode(world, names[ref.episode_id])
        assert instance == pinned.clock_instance(instance.clock)
    assert len(set(instances.values())) == 3


def test_a_swapped_row_group_in_a_shared_shard_is_refused(world):
    results = run(run_learning_controls(world, LearningTarget(world.base_export)))
    swapped = next(r for r in results if r["control"] == "learning_row_groups_swapped")
    assert swapped["observed_codes"] == ["learning_rows_owner"]


def _reused_shards(world: World) -> list[str]:
    base = LearningDataExportManifest.model_validate_json(
        _read(world, world.records[world.base_export]["uri"])
    )
    incremental = LearningDataExportManifest.model_validate_json(
        _read(world, world.records[world.incremental_export]["uri"])
    )
    assert incremental.base_export_id == base.export_id
    base_uris = {s.uri for s in base.shard_index.learning_steps}
    return [s.uri for s in incremental.shard_index.learning_steps if s.uri in base_uris]


def test_incremental_verification_covers_reused_shards_not_only_the_delta(world):
    assert _reused_shards(world)
    findings = Findings()
    verifier = TemporalLineageVerifier(world)
    run(
        verifier.verify_learning_export(
            findings, manifest_artifact_id=world.incremental_export
        )
    )
    assert findings.ok, findings.violations
    # Two tables' reused shards and every Episode, the base's included.
    assert verifier.stats["reused_shards_verified"] == 2
    assert verifier.stats["learning_episodes"] == len(EPISODES)
    assert verifier.stats["shards_verified"] == 4
    for name in EPISODES:
        assert world.episode_id(name) in verifier.verified.aligned


@pytest.mark.parametrize(
    ("control", "code"),
    [
        ("learning_step_timestamps_shifted", "learning_steps"),
        ("learning_shard_bytes_changed", "learning_shard_checksum"),
    ],
)
def test_a_fault_in_a_reused_shard_of_an_incremental_export_is_detected(
    world, control, code
):
    """The controls touch the first shard, which here came from the base."""
    assert LearningDataExportManifest.model_validate_json(
        _read(world, world.records[world.incremental_export]["uri"])
    ).shard_index.learning_steps[0].uri in _reused_shards(world)
    results = run(
        run_learning_controls(world, LearningTarget(world.incremental_export))
    )
    assert next(r for r in results if r["control"] == control)["observed_codes"] == [
        code
    ]


def test_scenes_of_one_version_keep_their_own_clock_declarations(world):
    frame = pl.read_parquet(io.BytesIO(_read(world, world.scene_tables["scenes"])))
    windows = {
        row["scene_id"]: restore_clock_instance(
            row["window_clock"], row["window_clock_origin"], row["window_clock_scope"]
        )
        for row in frame.iter_rows(named=True)
    }
    assert windows[world.scene_id("scene-native")].origin == ClockOrigin.UNIX_EPOCH
    assert not _comparable(
        windows[world.scene_id("scene-a")], windows[world.scene_id("scene-b")]
    )
    assert _comparable(
        windows[world.scene_id("scene-a")], windows[world.scene_id("scene-a-1")]
    )


def test_channels_of_one_scene_keep_their_own_clock_instances(world):
    frame = pl.read_parquet(
        io.BytesIO(_read(world, world.scene_tables["observations"]))
    )
    manifest = _pinned_scene(world, "scene-a")
    rows = frame.filter(pl.col("scene_id") == world.scene_id("scene-a"))
    clocks = {}
    for row in rows.iter_rows(named=True):
        clocks[row["channel"]] = restore_clock_instance(
            row["source_clock"], row["clock_origin"], row["clock_scope"]
        )
    assert len(set(clocks.values())) == 2
    declared = {c.channel: c.source_clock for c in manifest.channels}
    for channel, instance in clocks.items():
        assert instance == manifest.clock_instance(declared[channel])


def test_window_observation_and_keyframe_contexts_each_hold_to_their_own_declaration(
    world,
):
    """Three clocks in one Scene: every table's row is held to the declaration of what
    it times, so one context substituted for another is caught."""
    manifest = _pinned_scene(world, "scene-a")
    window_clock = manifest.declared_window().source_clock
    group_clocks = {k.source_clock for k in manifest.keyframes()}
    channel_clocks = {c.source_clock for c in manifest.channels}
    assert window_clock not in group_clocks | channel_clocks
    assert group_clocks <= channel_clocks and channel_clocks - group_clocks

    window = manifest.clock_instance(window_clock)

    def on_window(rows: list[dict]) -> None:
        for row in rows:
            if row["scene_id"] == world.scene_id("scene-a"):
                row["source_clock"] = window.clock
                row["clock_origin"] = window.origin.value
                row["clock_scope"] = list(window.scope)

    assert _scenes_forged(world, "keyframes", on_window).codes() == {
        "keyframe_instance"
    }
    assert _scenes_forged(world, "observations", on_window).codes() == {
        "observation_instance"
    }


def test_a_consistent_scope_change_of_one_clock_passes_the_readers_and_not_the_verifier(
    world,
):
    """Whole-table agreement cannot refute it: every row of (Scene, clock) says the
    same, which is all a structural reader can ask."""
    scene_id = world.scene_id("scene-a")

    def mutate(rows: list[dict]) -> None:
        for row in rows:
            if row["scene_id"] == scene_id:
                row["clock_scope"] = ["recording", "run-b"]

    snapshot = _Snapshot(world, SceneTarget(DATASET, VERSION, world.scene_tables))

    async def forge() -> None:
        rows = await snapshot.rows("observations")
        mutate(rows)
        await snapshot.write("observations", rows)

    run(forge())
    uri = snapshot.uris["observations"]
    assert read_scene_table_bytes(snapshot.objects[uri], "observations", uri=uri).height
    overlay = Overlay(world, objects=snapshot.objects, records=snapshot.records)
    assert _verify_scenes(overlay, snapshot.uris).codes() == {"observation_instance"}


def test_recording_and_content_scenes_state_their_own_source(world):
    frame = pl.read_parquet(io.BytesIO(_read(world, world.scene_tables["scenes"])))
    by_id = {r["scene_id"]: r for r in frame.iter_rows(named=True)}
    recording = by_id[world.scene_id("scene-a")]
    content = by_id[world.scene_id("scene-native")]
    assert (recording["robot_run_id"], recording["content_id"]) == ("run-a", None)
    assert recording["source_kind"] == "recording"
    assert content["robot_run_id"] is None and content["content_id"]
    assert content["source_kind"] == "content"


def test_a_row_pairing_a_scene_with_the_other_source_kind_is_refused(world):
    results = run(
        run_scene_controls(world, SceneTarget(DATASET, VERSION, world.scene_tables))
    )
    paired = next(r for r in results if r["control"] == "scene_other_source")
    assert paired["detected"]


# ── comparability through the derived artifacts (matrix 1 - 5) ───────────────


@pytest.fixture(scope="module")
def verified(world):
    verifier = TemporalLineageVerifier(world)
    findings = Findings()
    run(
        verifier.verify_learning_export(
            findings, manifest_artifact_id=world.incremental_export
        )
    )
    run(
        verifier.verify_scene_analytics(
            findings,
            dataset_id=DATASET,
            dataset_version=VERSION,
            table_uris=world.scene_tables,
        )
    )
    assert findings.ok, findings.violations
    return verifier.verified


def _learning(world, verified, name) -> ClockInstance:
    return next(
        instance
        for ref, instance in verified.learning.items()
        if ref.episode_id == world.episode_id(name)
    )


def test_independent_robot_runs_on_one_clock_with_equal_times_are_incomparable(
    world, verified
):
    a, b = world.episodes["rec-a-0"], world.episodes["rec-b-0"]
    assert [s.timestamp_ns for s in a.states] == [s.timestamp_ns for s in b.states]
    first, second = (
        _learning(world, verified, "rec-a-0"),
        _learning(world, verified, "rec-b-0"),
    )
    assert first.clock == second.clock and not _comparable(first, second)


def test_segments_of_one_robot_run_share_a_source_origin_clock(world, verified):
    assert _comparable(
        _learning(world, verified, "rec-a-0"), _learning(world, verified, "rec-a-1")
    )


def test_a_scene_and_an_episode_of_one_robot_run_share_their_declared_clock(
    world, verified
):
    window = verified.scene_windows[world.scene_id("scene-a")]
    episode = _learning(world, verified, "rec-a-0")
    assert window.clock == episode.clock
    assert _comparable(window, episode)
    assert _comparable(window, verified.aligned[world.episode_id("rec-a-0")])
    assert not _comparable(window, _learning(world, verified, "rec-b-0"))
    # A clock only one of them declares is not forced into an equality.
    camera = verified.scene_clocks[world.scene_id("scene-a")]["camera.header_stamp"]
    assert not _comparable(camera, episode)


def test_lerobot_episodes_with_identical_frame_times_are_incomparable(world, verified):
    first, second = world.episodes["native-0"], world.episodes["native-1"]
    assert [s.timestamp_ns for s in first.states] == [
        s.timestamp_ns for s in second.states
    ]
    a, b = (
        _learning(world, verified, "native-0"),
        _learning(world, verified, "native-1"),
    )
    assert a.clock == b.clock and a.origin == ClockOrigin.UNIT and not _comparable(a, b)


def test_a_unix_epoch_instance_has_global_scope(world, verified):
    instance = _learning(world, verified, "epoch-0")
    assert instance == ClockInstance(
        clock="x.epoch", origin=ClockOrigin.UNIX_EPOCH, scope=()
    )
    # Another content's unit on the same absolute clock is the same instance.
    other = native_episode(
        "7", content="sha256:" + "d" * 64, origin="unix_epoch", clock="x.epoch"
    )
    assert _comparable(instance, other.clock_instance("x.epoch"))


# ── negative controls and reproducibility (matrix 25 - 28) ───────────────────


def test_every_aligned_control_is_detected(world):
    target = world.aligned_entries["rec-b-0"][1]
    results = run(
        run_aligned_controls(
            world,
            AlignedTarget(
                world.aligned["rec-a-0"],
                other_scope=list(target.aligned_episode.clock_scope),
                other_episode_id=target.source_revision.episode_id,
                other_source_artifact_id=target.source_revision.source_artifact_id,
                other_source_sha256=target.source_revision.source_manifest_sha256,
                other_source_uri=target.source_revision.episode_manifest_uri,
            ),
        )
    )
    assert {r["control"] for r in results} == {c.name for c in ALIGNED_CONTROLS} | {
        "aligned_bytes_changed"
    }
    assert [r["control"] for r in results if not r["detected"]] == []


@pytest.mark.parametrize("export", ["base_export", "incremental_export"])
def test_every_learning_control_is_detected(world, export):
    results = run(run_learning_controls(world, LearningTarget(getattr(world, export))))
    assert {r["control"] for r in results} == {c.name for c in LEARNING_CONTROLS}
    assert [r["control"] for r in results if not r["detected"]] == []


def test_every_scene_control_is_detected(world):
    results = run(
        run_scene_controls(world, SceneTarget(DATASET, VERSION, world.scene_tables))
    )
    assert {r["control"] for r in results} == {c.name for c in SCENE_CONTROLS}
    # Every control has a fault to inject here, the window substitution included.
    assert [r["control"] for r in results if not r["detected"]] == []
    assert not [r for r in results if "not_applicable" in r]


def test_a_control_with_no_fault_to_inject_is_not_reported_as_detected(world, tmp_path):
    """A state whose observations are all on the window's clock: substituting the
    window's instance changes no byte, so it neither fails nor counts as detected."""
    source = Path(_read_uri_path(world, "observations"))
    frame = pl.read_parquet(source).filter(pl.col("source_clock") == "x.clock")
    assert frame.height
    scenes = pl.read_parquet(Path(_read_uri_path(world, "scenes"))).filter(
        pl.col("window_clock") == "x.clock"
    )
    assert scenes.height == 1
    observations = tmp_path / "observations.parquet"
    frame.write_parquet(observations)
    only_scene = tmp_path / "scenes.parquet"
    scenes.write_parquet(only_scene)
    target = SceneTarget(
        DATASET,
        VERSION,
        {"scenes": str(only_scene), "observations": str(observations)},
    )

    class Files:
        async def read_bytes(self, uri: str) -> bytes:
            return Path(uri).read_bytes()

    class Source:
        store = Files()

    snapshot = _Snapshot(Source(), target)
    with pytest.raises(ControlNotApplicable):
        run(_observation_window_substituted(snapshot))


def test_an_empty_table_has_no_fault_to_inject_and_no_control_crashes(world, tmp_path):
    """A recording Scene has no source-defined groups, so a real keyframes table can be
    empty: its controls are not applicable, and the others still run."""
    frame = pl.read_parquet(world.scene_tables["keyframes"]).clear()
    path = tmp_path / "keyframes.parquet"
    frame.write_parquet(
        path,
        metadata={TABLE_SCHEMA_METADATA_KEY: scene_table_schema_stamp("keyframes")},
    )
    data = path.read_bytes()
    uri = str(path)
    artifact_id = derived_artifact_id(
        prefix="analyticstable",
        logical_id=f"{DATASET}:{VERSION}:keyframes",
        checksum=sha256(data),
    )
    overlay = Overlay(
        world,
        objects={uri: data},
        records={
            artifact_id: {
                "artifact_id": artifact_id,
                "kind": "analytics_table",
                "uri": uri,
                "checksum": sha256(data),
            }
        },
    )
    target = SceneTarget(DATASET, VERSION, {**world.scene_tables, "keyframes": uri})
    results = {r["control"]: r for r in run(run_scene_controls(overlay, target))}
    for name in ("keyframe_scope_of_another_unit", "keyframe_dropped"):
        assert "not_applicable" in results[name] and not results[name]["detected"]
    assert results["observation_scope_of_another_unit"]["detected"]


def _read_uri_path(world: World, table: str) -> str:
    return world.scene_tables[table]


def _full_report(world: World) -> dict:
    verifier = TemporalLineageVerifier(world)
    findings = Findings()
    run(
        verifier.verify_learning_export(
            findings, manifest_artifact_id=world.base_export
        )
    )
    run(
        verifier.verify_learning_export(
            findings, manifest_artifact_id=world.incremental_export
        )
    )
    run(
        verifier.verify_scene_analytics(
            findings,
            dataset_id=DATASET,
            dataset_version=VERSION,
            table_uris=world.scene_tables,
        )
    )
    return report_of(verifier, findings)


def _tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_identical_inputs_give_identical_reports_and_verification_writes_nothing(world):
    before = _tree(world.root)
    first = _full_report(world)
    second = _full_report(world)
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["ok"]
    assert _tree(world.root) == before


def test_a_forgery_is_only_ever_in_the_overlay(world):
    before = _tree(world.root)
    _forged_aligned(world, "rec-a-0", _with_scope_of(world, "rec-b-0"))
    run(run_scene_controls(world, SceneTarget(DATASET, VERSION, world.scene_tables)))
    assert _tree(world.root) == before


def test_the_read_side_of_the_verifier_has_no_way_to_write(world):
    for method in ("write_bytes", "write_json", "delete_prefix"):
        assert not hasattr(world.store, method)
        assert not hasattr(Overlay(world).store, method)


def test_each_pinned_revision_is_resolved_once_whatever_the_row_count(world):
    verifier = TemporalLineageVerifier(world)
    findings = Findings()

    def scenes() -> None:
        run(
            verifier.verify_scene_analytics(
                findings,
                dataset_id=DATASET,
                dataset_version=VERSION,
                table_uris=world.scene_tables,
            )
        )

    scenes()
    assert verifier.stats["manifest_resolutions"] == len(world.scenes)
    assert verifier.stats["observations_rows"] > len(world.scenes)
    # Verifying again within the run reads no manifest again.
    scenes()
    assert verifier.stats["manifest_resolutions"] == len(world.scenes)
    assert verifier.stats["manifest_cache_hits"] == len(world.scenes)
    assert findings.ok, findings.violations

    verifier = TemporalLineageVerifier(world)
    for _ in range(2):
        run(
            verifier.verify_learning_export(
                findings, manifest_artifact_id=world.base_export
            )
        )
    assert verifier.stats["manifest_resolutions"] == 4
    assert verifier.stats["aligned_artifacts"] == 4
    assert verifier.stats["alignments_recomputed"] == 4


# ── the reader boundary (matrix 24) ──────────────────────────────────────────


@pytest.fixture()
def local_tables(world, tmp_path) -> dict[str, str]:
    paths = {}
    for table, uri in world.scene_tables.items():
        path = tmp_path / f"{table}.parquet"
        path.write_bytes(_read(world, uri))
        paths[table] = str(path)
    return paths


def test_only_the_validated_door_reads_scene_tables(local_tables):
    assert pq.read_metadata(local_tables["scenes"]).num_rows == 4
    with pytest.raises(ValueError, match="query_scene_analytics"):
        query_parquet(
            {"scenes": local_tables["scenes"]}, "SELECT count(*) AS n FROM scenes"
        )
    frame = query_scene_analytics(local_tables, "SELECT count(*) AS n FROM scenes")
    assert frame["n"].item() == 4


def test_a_join_on_the_whole_identity_keeps_comparable_rows_and_drops_the_rest(
    world, local_tables
):
    """Equal clock ids and times across a Scene of run-a, one more segment of it and
    run-b: a join on (clock, timestamp) calls all of them simultaneous."""
    sql = """
        SELECT a.scene_id AS left_scene, b.scene_id AS right_scene
        FROM observations a JOIN observations b
          ON a.source_clock = b.source_clock AND a.timestamp_ns = b.timestamp_ns
         AND a.scene_id < b.scene_id
        {whole}
    """
    naive = query_scene_analytics(local_tables, sql.format(whole=""))
    whole = query_scene_analytics(
        local_tables,
        sql.format(
            whole="AND a.clock_origin = b.clock_origin AND a.clock_scope = b.clock_scope"
        ),
    )
    pair = {world.scene_id("scene-a"), world.scene_id("scene-a-1")}
    assert {frozenset(r) for r in naive.iter_rows()} > {frozenset(pair)}
    assert {frozenset(r) for r in whole.iter_rows()} == {frozenset(pair)}
    assert whole.height < naive.height


def test_no_production_module_uses_the_contractless_door():
    """``query_parquet`` declares no table contract, and no active consumer needs it."""
    callers = []
    for top in ("apps", "packages", "integrations"):
        for path in (REPO_ROOT / top).rglob("*.py"):
            parts = path.relative_to(REPO_ROOT).parts
            if "tests" in parts or any(p.startswith(".") for p in parts):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and (
                    getattr(node.func, "id", None) == "query_parquet"
                    or getattr(node.func, "attr", None) == "query_parquet"
                ):
                    callers.append(str(path))
    assert callers == []


# ── the verifier obeys what it enforces ──────────────────────────────────────


def _checker():
    spec = importlib.util.spec_from_file_location(
        "source_dispatch_for_lineage", REPO_ROOT / "tools/checks/source_dispatch.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_verifier_reads_no_scope_element_and_derives_no_scope():
    """Its comparison is independent of what a scope names. tools/ is outside the
    production scan, so the rule is applied to it here."""
    checker = _checker()
    path = TOOLS / "temporal_lineage.py"
    assert [
        s
        for s in checker.sites(path, "tools/reference/temporal_lineage.py")
        if s.rule == checker.SCOPE_OPACITY
    ] == []
    tree = ast.parse(path.read_text())
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "origin_scope" not in attributes
    # It issues nothing: every expected instance is the manifest's.
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "ClockInstance"
    ]
    assert calls == []
