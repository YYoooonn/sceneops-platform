"""ProfileEpisodeJobHandler: descriptive profiles of registered Episode
revisions, per role and per stream, each stream in its own clock."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sceneops_core.episodes.schemas import EpisodeProfileRunRecord
from sceneops_core.jobs.schemas import JobType, ProfileEpisodeJobParams
from sceneops_worker.episodes.resolver import EpisodeNotRegisteredError
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.profile_episode import ProfileEpisodeJobHandler

sys.path.insert(0, str(Path(__file__).parents[1] / "episodes"))
from episode_context import S, job, make_context, register, sample_manifest  # noqa: E402


def _request(episode_ids, context) -> JobHandlerRequest:
    params = ProfileEpisodeJobParams(
        dataset_id="d1", dataset_version="v1", episode_ids=episode_ids
    )
    return JobHandlerRequest(
        job=job(JobType.PROFILE_EPISODE, params.model_dump()),
        params=params,
        context=context,
    )


async def test_profile_counts_roles_and_pins_the_revision() -> None:
    registered = [register(sample_manifest("a")), register(sample_manifest("b"))]
    context = make_context(registered)
    ids = [r.episode_id for r, _, _ in registered]

    result = await ProfileEpisodeJobHandler().run(_request(ids, context))

    assert result.checked_episode_count == 2
    assert (result.observation_count, result.state_count, result.action_count) == (
        2,
        4,
        2,
    )
    assert result.action_topics == ["/control"]
    per_episode = [
        call.args[0]
        for call in context.runs.episode_runs.upsert.await_args_list
        if isinstance(call.args[0], EpisodeProfileRunRecord) and call.args[0].episode_id
    ]
    assert len(per_episode) == 2
    assert all(run.window_duration_ns == 3 * S for run in per_episode)
    streams = {s["topic"]: s for s in per_episode[0].summary["streams"]}
    assert streams["/odom"]["count"] == 2
    assert streams["/odom"]["source_clock"] == "sensor.header_stamp"


async def test_unregistered_episode_fails_the_job() -> None:
    with pytest.raises(EpisodeNotRegisteredError):
        await ProfileEpisodeJobHandler().run(
            _request(["episode-missing"], make_context([]))
        )
