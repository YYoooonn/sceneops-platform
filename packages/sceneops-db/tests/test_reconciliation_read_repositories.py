"""The read-only batch queries the acquisition reconciler uses (ADR-008 §3.2,
Phase 12.3): ``PostgresRobotRunRepository.get_many`` and
``PostgresJobRepository.list_for_execution_keys``. Rows live in the test's own
uncommitted transaction (``db_session``), so nothing persists."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.jobs.schemas import JobManifest, JobStatus, JobType
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.jobs import PostgresJobRepository
from sceneops_db.postgres.robots import PostgresRobotRunRepository

_T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


async def test_robot_runs_get_many_returns_only_existing_keyed_by_run_id(
    db_session, unique_id, seed_robot_run
):
    first, second = unique_id("run"), unique_id("run")
    for run_id in (first, second):
        await seed_robot_run(
            db_session, run_id=run_id, recording_checksum="sha256:" + "1" * 64
        )
    repository = PostgresRobotRunRepository(db_session)

    found = await repository.get_many([second, "no-such-run", first, first])

    assert sorted(found) == sorted([first, second])
    assert found[first].run_id == first
    assert found[first].registered_at is not None
    assert await repository.get_many([]) == {}
    assert await repository.get_many(["no-such-run"]) == {}


async def test_robot_runs_get_many_chunks_large_id_lists(
    db_session, unique_id, seed_robot_run
):
    run_id = unique_id("run")
    await seed_robot_run(
        db_session, run_id=run_id, recording_checksum="sha256:" + "1" * 64
    )

    ids = [f"absent-{index}" for index in range(2500)] + [run_id]

    found = await PostgresRobotRunRepository(db_session).get_many(ids)

    assert list(found) == [run_id]


def _job(job_id: str, key: str, status: JobStatus, hours: int, **kw) -> JobManifest:
    return JobManifest(
        job_id=job_id,
        type=kw.pop("type", JobType.REGISTER_ROBOT_RUN),
        status=status,
        execution_key=key,
        created_at=_T0 + timedelta(hours=hours),
        queued_at=_T0 + timedelta(hours=hours),
        **kw,
    )


async def test_list_for_execution_keys_returns_every_status_oldest_first(
    db_session, unique_id
):
    key_a, key_b, key_other = (unique_id("key") for _ in range(3))
    repository = PostgresJobRepository(db_session)
    # Created out of order and in different statuses: no status filter and
    # (created_at, job_id) ordering, unlike find_by_execution_key.
    await repository.create(_job("job-" + key_a + "-3", key_a, JobStatus.RUNNING, 3))
    await repository.create(_job("job-" + key_a + "-1", key_a, JobStatus.FAILED, 1))
    await repository.create(_job("job-" + key_a + "-2", key_a, JobStatus.SUCCEEDED, 2))
    await repository.create(_job("job-" + key_b + "-1", key_b, JobStatus.PENDING, 4))
    await repository.create(
        _job("job-" + key_other + "-1", key_other, JobStatus.PENDING, 1)
    )

    jobs = await repository.list_for_execution_keys(
        [key_b, key_a, key_a], type=JobType.REGISTER_ROBOT_RUN
    )

    assert [job.job_id for job in jobs] == [
        f"job-{key_a}-1",
        f"job-{key_a}-2",
        f"job-{key_a}-3",
        f"job-{key_b}-1",
    ]
    assert [job.status for job in jobs] == [
        JobStatus.FAILED,
        JobStatus.SUCCEEDED,
        JobStatus.RUNNING,
        JobStatus.PENDING,
    ]
    assert (
        await repository.list_for_execution_keys([], type=JobType.REGISTER_ROBOT_RUN)
        == []
    )


async def test_list_for_execution_keys_orders_equal_timestamps_by_job_id(
    db_session, unique_id
):
    key = unique_id("key")
    repository = PostgresJobRepository(db_session)
    for suffix in ("c", "a", "b"):
        await repository.create(_job(f"job-{key}-{suffix}", key, JobStatus.FAILED, 1))

    jobs = await repository.list_for_execution_keys(
        [key], type=JobType.REGISTER_ROBOT_RUN
    )

    assert [job.job_id for job in jobs] == [f"job-{key}-{s}" for s in "abc"]


async def test_list_for_execution_keys_filters_by_job_type(db_session, unique_id):
    key = unique_id("key")
    repository = PostgresJobRepository(db_session)
    await repository.create(_job(f"job-{key}-reg", key, JobStatus.FAILED, 1))
    await repository.create(
        _job(
            f"job-{key}-other",
            key,
            JobStatus.FAILED,
            2,
            type=JobType.INGEST_ROBOT_STATES,
        )
    )

    jobs = await repository.list_for_execution_keys(
        [key], type=JobType.REGISTER_ROBOT_RUN
    )

    assert [job.job_id for job in jobs] == [f"job-{key}-reg"]


async def test_list_for_execution_keys_chunks_large_key_lists(db_session, unique_id):
    key = unique_id("key")
    repository = PostgresJobRepository(db_session)
    await repository.create(_job(f"job-{key}", key, JobStatus.FAILED, 1))

    jobs = await repository.list_for_execution_keys(
        [f"absent-{index}" for index in range(2500)] + [key],
        type=JobType.REGISTER_ROBOT_RUN,
    )

    assert [job.job_id for job in jobs] == [f"job-{key}"]


# ── artifact lifecycle reads (ADR-008 §6.1) ───────────────────────────────────


async def test_list_run_ids_for_root_returns_only_runs_registered_under_the_root(
    db_session, unique_id, seed_robot_run
):
    # seed_robot_run registers under s3://sceneops-test/robot_runs/<run_id>/.
    first, second = sorted(unique_id("run") for _ in range(2))
    for run_id in (second, first):
        await seed_robot_run(
            db_session, run_id=run_id, recording_checksum="sha256:" + "1" * 64
        )
    repository = PostgresRobotRunRepository(db_session)

    here = await repository.list_run_ids_for_root("s3://sceneops-test/robot_runs")

    assert {first, second} <= set(here)
    assert here == sorted(here)
    assert (
        await repository.list_run_ids_for_root("s3://sceneops-test/robot_runs/") == here
    )
    # Another root, and a root that merely shares a text prefix, own none of them.
    assert not {first, second} & set(
        await repository.list_run_ids_for_root("s3://sceneops-test/other_root")
    )
    assert not {first, second} & set(
        await repository.list_run_ids_for_root("s3://sceneops-test/robot")
    )


async def test_list_by_uri_prefix_is_a_directory_prefix_not_a_substring(
    db_session, unique_id
):
    token = unique_id("root")
    root = f"s3://bucket/{token}"
    repository = PostgresArtifactRefRepository(db_session)
    for suffix, uri in {
        "a": f"{root}/run-1/recording.mcap",
        "b": f"{root}/run-1/robot_run_manifest.json",
        "sibling": f"{root}-other/run-1/recording.mcap",  # shares the text, not the directory
        "elsewhere": f"s3://bucket/{token}x/recording.mcap",
    }.items():
        await repository.create(
            artifact_id=f"{token}-{suffix}",
            ref=ArtifactRef(kind=ArtifactKind.ROBOT_RUN_RECORDING, uri=uri),
        )

    found = await repository.list_by_uri_prefix(root)
    found_trailing_slash = await repository.list_by_uri_prefix(root + "/")

    assert [r.artifact_id for r in found] == [f"{token}-a", f"{token}-b"]
    assert found_trailing_slash == found
    assert await repository.list_by_uri_prefix(f"{root}/no-such-run") == []


async def test_list_by_uri_prefix_treats_like_wildcards_literally(
    db_session, unique_id
):
    token = unique_id("wild")
    repository = PostgresArtifactRefRepository(db_session)
    for suffix, uri in {
        "literal": f"s3://bucket/{token}_a%b/recording.mcap",
        "lookalike": f"s3://bucket/{token}XaZZb/recording.mcap",
    }.items():
        await repository.create(
            artifact_id=f"{token}-{suffix}",
            ref=ArtifactRef(kind=ArtifactKind.ROBOT_RUN_RECORDING, uri=uri),
        )

    found = await repository.list_by_uri_prefix(f"s3://bucket/{token}_a%b")

    assert [r.artifact_id for r in found] == [f"{token}-literal"]
