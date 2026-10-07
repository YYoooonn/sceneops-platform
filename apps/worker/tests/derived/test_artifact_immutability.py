"""Derived artifacts are immutable, content-pinned and convergent (evaluation,
ScenarioSets and their reports), through the real job handlers and stores.

* a record's checksum is the checksum of the bytes at its URI, and the URI
  names that content;
* re-executing a Job that reproduces the same bytes converges on the same
  objects and the same records instead of appending duplicates;
* a different result under the same run id never replaces what a record pins.
"""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.evaluations.schemas import load_canonical_evaluation_manifest
from sceneops_worker.runs.artifacts import RunArtifactConflictError
from tests.derived.flow_support import evaluate, mine, predict
from tests.derived.test_detection_vertical import _world


def _records(harness, *kinds: ArtifactKind):
    return [
        r for r in harness.repo.records.values() if r.kind in {k.value for k in kinds}
    ]


async def _prediction(tmp_path):
    harness, _scenes, label_set, views = await _world(tmp_path)
    prediction = await predict(harness, sample_views=views, max_samples=None)
    return harness, label_set, prediction


async def test_evaluation_records_pin_the_bytes_at_their_content_named_uris(tmp_path):
    harness, label_set, prediction = await _prediction(tmp_path)
    evaluation = await evaluate(
        harness, inference_run_id=prediction.inference_run_id, label_set=label_set
    )

    records = _records(harness, ArtifactKind.EVALUATION_MANIFEST, ArtifactKind.METRICS)
    assert {r.uri for r in records} == {
        evaluation.evaluation_manifest_uri,
        evaluation.metrics_uri,
    }
    for record in records:
        data = await harness.artifact_store.read_bytes(record.uri)
        assert record.checksum == sha256_checksum(data)
        assert record.size_bytes == len(data)
        assert record.checksum.removeprefix("sha256:") in record.uri
        assert record.run_id == evaluation.evaluation_run_id

    # The manifest is canonical, names no location or time of its own, and
    # pins every per-sample result by checksum.
    manifest_bytes = await harness.artifact_store.read_bytes(
        evaluation.evaluation_manifest_uri
    )
    manifest = load_canonical_evaluation_manifest(manifest_bytes)
    assert manifest.sample_count == len(manifest.sample_shards) > 0
    for shard in manifest.sample_shards:
        assert shard.checksum == sha256_checksum(
            await harness.artifact_store.read_bytes(shard.uri)
        )


async def test_re_executing_an_evaluation_job_converges(tmp_path):
    harness, label_set, prediction = await _prediction(tmp_path)

    async def run():
        return await evaluate(
            harness,
            inference_run_id=prediction.inference_run_id,
            label_set=label_set,
            job_id="job-eval",
        )

    first = await run()
    ids = {
        r.artifact_id
        for r in _records(
            harness, ArtifactKind.EVALUATION_MANIFEST, ArtifactKind.METRICS
        )
    }
    manifest_bytes = await harness.artifact_store.read_bytes(
        first.evaluation_manifest_uri
    )

    again = await run()

    assert again.evaluation_manifest_uri == first.evaluation_manifest_uri
    assert again.metrics_uri == first.metrics_uri
    assert {
        r.artifact_id
        for r in _records(
            harness, ArtifactKind.EVALUATION_MANIFEST, ArtifactKind.METRICS
        )
    } == ids
    assert (
        await harness.artifact_store.read_bytes(first.evaluation_manifest_uri)
        == manifest_bytes
    )


async def test_a_different_result_under_a_reused_run_id_never_overwrites(tmp_path):
    harness, label_set, prediction = await _prediction(tmp_path)
    first = await evaluate(
        harness,
        inference_run_id=prediction.inference_run_id,
        label_set=label_set,
        evaluation_run_id="eval-fixed",
    )
    manifest_bytes = await harness.artifact_store.read_bytes(
        first.evaluation_manifest_uri
    )
    metrics_bytes = await harness.artifact_store.read_bytes(first.metrics_uri)

    with pytest.raises(RunArtifactConflictError, match="write-once"):
        await evaluate(
            harness,
            inference_run_id=prediction.inference_run_id,
            label_set=label_set,
            evaluation_run_id="eval-fixed",
            categories=["human.pedestrian"],
        )

    assert (
        await harness.artifact_store.read_bytes(first.evaluation_manifest_uri)
        == manifest_bytes
    )
    assert await harness.artifact_store.read_bytes(first.metrics_uri) == metrics_bytes


async def test_distinct_evaluation_jobs_have_distinct_runs_and_artifacts(tmp_path):
    harness, label_set, prediction = await _prediction(tmp_path)
    one = await evaluate(
        harness, inference_run_id=prediction.inference_run_id, label_set=label_set
    )
    two = await evaluate(
        harness, inference_run_id=prediction.inference_run_id, label_set=label_set
    )

    assert one.evaluation_run_id != two.evaluation_run_id
    assert one.evaluation_manifest_uri != two.evaluation_manifest_uri
    assert len(_records(harness, ArtifactKind.EVALUATION_MANIFEST)) == 2


async def test_re_executing_a_mining_job_converges_on_one_scenario_set(tmp_path):
    harness, _scenes, _ls, views = await _world(tmp_path)

    first = await mine(harness, views, label_set_id="gt", job_id="job-mine")
    sets = _records(harness, ArtifactKind.SCENARIO_SET_MANIFEST)
    reports = _records(harness, ArtifactKind.SCENARIO_MINING_REPORT)

    again = await mine(harness, views, label_set_id="gt", job_id="job-mine")

    assert again.scenario_set_id == first.scenario_set_id
    assert again.scenario_set_checksum == first.scenario_set_checksum
    assert again.report_uri == first.report_uri
    assert _records(harness, ArtifactKind.SCENARIO_SET_MANIFEST) == sets
    assert _records(harness, ArtifactKind.SCENARIO_MINING_REPORT) == reports
    for record in reports:
        data = await harness.artifact_store.read_bytes(record.uri)
        assert record.checksum == sha256_checksum(data)


async def test_distinct_mining_jobs_make_distinct_scenario_sets(tmp_path):
    harness, _scenes, _ls, views = await _world(tmp_path)
    one = await mine(harness, views, label_set_id="gt")
    two = await mine(harness, views, label_set_id="gt")
    assert one.scenario_set_id != two.scenario_set_id
