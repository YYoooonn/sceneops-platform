"""Native nuScenes labels on real PostgreSQL and real MinIO (ADR-015 §9, M3.0
OQ-6): IMPORT_LABELS {snapshot_id} from a registered SourceSnapshot, its
idempotency across retries and declarations, its independence from Scenes
and DatasetVersions, and sample views of the content Scene resolving the
same labels per keyframe group whichever channel anchors them.

Each test preserves its own synthetic nuScenes v1.0 tree (its LICENSE carries
the test's id, so its content is its own) under its own declaration.
Expectations come from the values the tree was written with. Requires the
disposable database and bucket of `make test-integration`; skips (and so
fails there) otherwise.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from sceneops_core.jobs.schemas import (
    BuildSceneSampleViewsJobParams,
    ImportLabelsJobParams,
    JobManifest,
    JobStatus,
    JobType,
)
from sceneops_core.labels import LabelSetManifest, load_canonical_label_set
from sceneops_core.provenance import PinnedContent
from sceneops_core.sample_views import (
    MemberPolicy,
    SampleAnchorPolicy,
    SampleViewPolicy,
    load_canonical_sample_view,
)
from sceneops_core.sources import SnapshotSelection
from sceneops_db.session import get_async_sessionmaker
from sceneops_derived.nuscenes_labels import nuscenes_label_set
from sceneops_sources import LocalContentFiles, build_inventory, select_files
from sceneops_sources.nuscenes_synthetic import (
    INCLUDE,
    INSTANCES,
    token,
    write_nuscenes_tree,
)
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.derived import (
    BuildSceneSampleViewsJobHandler,
    ImportLabelsJobHandler,
)
from sceneops_worker.jobs.derived.import_labels import NativeLabelImportError
from sceneops_derived.nuscenes_labels import NuScenesLabelImportError
from tests.scenes.test_content_scene_integration import _Env

SCENES = sorted([token("scene", 0), token("scene", 1)])
SAMPLES = 3
# Every sample but each scene's last annotates each instance.
LABELS_PER_SCENE = (SAMPLES - 1) * len(INSTANCES)


class _LabelEnv(_Env):
    async def import_labels(self, **params):
        self.jobs += 1
        job_id = f"job-{self.run_id}-{self.jobs}"
        validated = ImportLabelsJobParams.model_validate(params)
        async with get_async_sessionmaker()() as session:
            return await ImportLabelsJobHandler().run(
                JobHandlerRequest(
                    job=JobManifest(
                        job_id=job_id,
                        type=JobType.IMPORT_LABELS,
                        status=JobStatus.RUNNING,
                        params=validated.model_dump(mode="json"),
                    ),
                    params=validated,
                    context=self.context(session),
                )
            )

    async def views(self, dataset_version: str, policy: SampleViewPolicy, label_sets):
        self.jobs += 1
        params = BuildSceneSampleViewsJobParams(
            dataset_id=f"ds-{self.run_id}",
            dataset_version=dataset_version,
            policy=policy,
            label_sets=list(label_sets),
        )
        async with get_async_sessionmaker()() as session:
            return await BuildSceneSampleViewsJobHandler().run(
                JobHandlerRequest(
                    job=JobManifest(
                        job_id=f"job-{self.run_id}-{self.jobs}",
                        type=JobType.BUILD_SCENE_SAMPLE_VIEWS,
                        status=JobStatus.RUNNING,
                        params=params.model_dump(mode="json"),
                    ),
                    params=params,
                    context=self.context(session),
                )
            )

    async def record(self, artifact_id: str):
        async with get_async_sessionmaker()() as session:
            return await self.context(session).artifact_record_store.get(artifact_id)

    async def read(self, uri: str) -> bytes:
        return await self.store.read_bytes(uri)

    async def label_rows(self, label_set_id: str) -> int:
        async with get_async_sessionmaker()() as session:
            return (
                await session.execute(
                    text("SELECT count(*) FROM artifacts WHERE owner_id = :o"),
                    {"o": label_set_id},
                )
            ).scalar_one()

    async def objects(self) -> int:
        return len(await self.store.list_objects(self.settings.artifact_root_uri))

    def expected(self, label_set_id: str, unit_keys=None) -> LabelSetManifest:
        """The label set the local tree yields, through the pure importer."""
        inventory = build_inventory(
            self.root, select_files(self.root, SnapshotSelection(include=INCLUDE))
        )
        return nuscenes_label_set(
            pinned=PinnedContent(inventory=inventory),
            files=LocalContentFiles(self.root, inventory),
            label_set_id=label_set_id,
            unit_keys=unit_keys,
        )


@pytest.fixture()
async def env(
    _fresh_database_connection, _minio_reachable, worker_settings, unique_id, tmp_path
):
    dependencies_module._artifact_store = None
    run_id = unique_id("labels")
    root = write_nuscenes_tree(tmp_path / "tree", scenes=2, samples=SAMPLES, sweeps=1)
    (root / "LICENSE").write_text(f"{run_id}\n")
    yield _LabelEnv(worker_settings, run_id, root)
    dependencies_module._artifact_store = None


async def test_native_import_registers_one_revision_and_converges(env):
    snapshot_a, content_id = await env.snapshot("a")
    label_set_id = f"gt-{env.run_id}"

    first = await env.import_labels(snapshot_id=snapshot_a, label_set_id=label_set_id)
    assert (first.snapshot_id, first.content_id) == (snapshot_a, content_id)
    assert (first.content_ids, first.robot_run_ids) == ([content_id], [])
    assert (first.covered_anchor_count, first.label_count) == (
        2 * SAMPLES,
        2 * LABELS_PER_SCENE,
    )
    assert first.created is True
    record = await env.record(first.label_set.manifest_artifact_id)
    assert (record.kind, record.owner_type, record.owner_id) == (
        "label_set_manifest",
        "label_set",
        label_set_id,
    )
    assert record.metadata["source_content_id"] == content_id
    assert "snapshot" not in str(record.metadata)
    stored = await env.read(record.uri)
    # The stored revision is the pure function of the content bytes.
    assert stored == env.expected(label_set_id).to_canonical_bytes()
    assert load_canonical_label_set(stored).checksum() == record.checksum
    settled = (await env.label_rows(label_set_id), await env.objects())
    assert settled[0] == 1

    # A retry writes no row or object and returns the same revision.
    again = await env.import_labels(snapshot_id=snapshot_a, label_set_id=label_set_id)
    assert (again.label_set, again.created) == (first.label_set, False)
    assert (await env.label_rows(label_set_id), await env.objects()) == settled

    # Another declaration of the same bytes: the same revision, recorded as
    # read through B, while the artifact keeps the Job that first registered it.
    snapshot_b, same_content = await env.snapshot("b")
    objects_with_b = await env.objects()
    assert same_content == content_id
    through_b = await env.import_labels(
        snapshot_id=snapshot_b, label_set_id=label_set_id
    )
    assert (through_b.label_set, through_b.created) == (first.label_set, False)
    assert through_b.snapshot_id == snapshot_b
    assert (await env.record(first.label_set.manifest_artifact_id)).job_id == (
        f"job-{env.run_id}-1"
    )
    assert await env.label_rows(label_set_id) == 1
    assert await env.objects() == objects_with_b

    # A subset is its own revision of the same label set, never an overwrite.
    subset = await env.import_labels(
        snapshot_id=snapshot_a, label_set_id=label_set_id, unit_keys=[SCENES[0]]
    )
    assert subset.label_set.manifest_checksum != first.label_set.manifest_checksum
    assert (subset.unit_keys, subset.label_count) == ([SCENES[0]], LABELS_PER_SCENE)
    assert await env.label_rows(label_set_id) == 2
    assert await env.read(first.manifest_uri) == stored


async def test_native_import_refusals_write_nothing(env):
    snapshot_id, _ = await env.snapshot()
    before = await env.objects()
    with pytest.raises(NativeLabelImportError, match="not registered"):
        await env.import_labels(
            snapshot_id="snapshot-" + "0" * 32, label_set_id="gt-missing"
        )
    with pytest.raises(NuScenesLabelImportError, match="name no scene"):
        await env.import_labels(
            snapshot_id=snapshot_id, label_set_id="gt-bad", unit_keys=["f" * 32]
        )
    assert await env.objects() == before
    assert await env.label_rows("gt-missing") == await env.label_rows("gt-bad") == 0


@pytest.mark.parametrize("anchor_channel", ["CAM_FRONT", "LIDAR_TOP"])
async def test_views_resolve_the_same_labels_per_keyframe_group(env, anchor_channel):
    snapshot_id, content_id = await env.snapshot()
    built = await env.build(snapshot_id)
    registration = await env.register(built.manifest_artifact_ids)
    pins = [r.pin.model_dump(mode="json") for r in registration.revisions]
    composed = await env.compose(dataset_version="labelled", pins=pins)

    label_set_id = f"gt-{env.run_id}"
    imported = await env.import_labels(
        snapshot_id=snapshot_id, label_set_id=label_set_id
    )
    # Labels are not members: importing them changes no DatasetVersion.
    recomposed = await env.compose(dataset_version="labelled-again", pins=pins)
    assert (
        recomposed.version.composition_checksum == composed.version.composition_checksum
    )

    others = sorted({"CAM_FRONT", "LIDAR_TOP"} - {anchor_channel})
    result = await env.views(
        "labelled",
        SampleViewPolicy(
            anchor=SampleAnchorPolicy(channel=anchor_channel),
            members=[
                MemberPolicy(channel=c, tolerance_ns=50_000_000, required=False)
                for c in others
            ],
        ),
        [imported.label_set],
    )
    labels = load_canonical_label_set(await env.read(imported.manifest_uri))
    expected = {
        a.group_ref: sorted(
            label.label_id for label in labels.labels if label.anchor == a
        )
        for a in labels.coverage
    }
    found: dict[str, list[str]] = {}
    for view_ref in result.views:
        record = await env.record(view_ref.manifest_artifact_id)
        view = load_canonical_sample_view(await env.read(record.uri))
        _, scene = await env.manifest(view.scene.manifest_artifact_id)
        group_of = {o: g.native_ref for g in scene.groups for o in g.observation_ids}
        (stats,) = view.label_stats
        assert stats.unattached_label_count == 0
        for sample in view.samples:
            (entry,) = sample.labels
            if entry.covered:
                (group,) = {
                    group_of[o]
                    for o in sample.member_observation_ids()
                    if o in group_of
                }
                assert group not in found
                found[group] = entry.label_ids
    assert found == expected
    assert sum(map(len, found.values())) == imported.label_count == 2 * LABELS_PER_SCENE
    assert content_id in {a.source_id for a in labels.coverage}
