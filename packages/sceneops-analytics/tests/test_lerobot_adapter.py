"""Tests for sceneops_analytics.external_adapters.lerobot (SceneOps V2
Request 3.3): the first concrete ExternalDatasetAdapter/ExternalDatasetWriter
pair (Request 3.1/3.1A), built over LeRobot's real
``lerobot.datasets.lerobot_dataset.LeRobotDataset`` API.

This module is the only test module in the package that imports lerobot --
skipped entirely (not erroring) if the ``lerobot`` extra isn't installed, via
``pytest.importorskip`` below, mirroring test_torch_adapter.py's pattern.
Every other test module in this package must pass regardless of whether this
one runs (see test_package_boundaries.py's sibling assertions in spirit --
this module additionally exercises that ``import sceneops_analytics`` works
without lerobot installed).
"""

from __future__ import annotations

import pytest

lerobot = pytest.importorskip("lerobot")

from sceneops_analytics.external_adapters import (  # noqa: E402
    ExternalExportConfig,
    MappingKind,
    SemanticField,
    UnsupportedSemanticPolicy,
)
from sceneops_analytics.external_adapters.lerobot import (  # noqa: E402
    EmptyEpisodeUnsupportedError,
    InconsistentFrequencyError,
    LeRobotDatasetAdapter,
    LeRobotTaskRequiredError,
    NonUniformTimelineError,
    UndeterminableFrequencyError,
)
from sceneops_analytics.testing.interop_dataset import (  # noqa: E402
    EPISODE_A_REV1_REF,
    EPISODE_A_REV2_REF,
    EPISODE_B_REF,
    INTEROP_FEATURE_PROJECTION,
    build_interop_test_dataset,
)
from sceneops_core.datasets.schemas.external import ExternalDatasetRef  # noqa: E402


def _config(**overrides) -> ExternalExportConfig:
    # unsupported_semantic_policy=RECORD: SIGNAL_STATUS is unconditionally
    # UNSUPPORTED for every adapter (Phase 2.7A dense projection already
    # collapses RESOLVED/INTERPOLATED before this layer ever sees a step --
    # see adapter.py's module docstring), so ExternalExportConfig's own
    # default of FAIL would make every real export raise
    # UnsupportedSemanticError regardless of target format. Tests that
    # specifically want FAIL's behavior override this explicitly.
    defaults = dict(
        projection=INTEROP_FEATURE_PROJECTION,
        unsupported_semantic_policy=UnsupportedSemanticPolicy.RECORD,
    )
    defaults.update(overrides)
    return ExternalExportConfig(**defaults)


async def test_lerobot_feature_and_schema_generation(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")

    report = await adapter.export(bootstrap.dataset, _config())

    assert report.feature_schema is not None
    assert report.feature_schema.observation_dim == 7  # 3 + 3 + 1, see interop fixture
    assert report.feature_schema.action_dim == 4  # 3 + 1

    ds = lerobot.datasets.lerobot_dataset.LeRobotDataset(
        repo_id="interop", root=tmp_path / "lerobot-out"
    )
    assert ds.meta.features["observation.state"]["shape"] == (7,)
    assert ds.meta.features["action"]["shape"] == (4,)
    assert ds.meta.fps == 10


async def test_observation_action_ordering_and_task_mapping(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")

    await adapter.export(bootstrap.dataset, _config())

    ds = lerobot.datasets.lerobot_dataset.LeRobotDataset(
        repo_id="interop", root=tmp_path / "lerobot-out"
    )
    expected = bootstrap.expected_by_ref[EPISODE_A_REV1_REF]
    # Episode order in export() is dataset.episodes() order, which is
    # EPISODE_A_REV1, EPISODE_A_REV2, EPISODE_B (sorted by (episode_id,
    # checksum) -- see InteropDatasetBootstrap's docstring), so
    # episode_index 0 is EPISODE_A_REV1.
    item = ds[0]
    assert item["observation.state"].tolist() == pytest.approx(
        expected.steps[0].observation
    )
    assert item["action"].tolist() == pytest.approx(expected.steps[0].action)
    assert item["task"] == expected.task == "pick"


async def test_three_episode_revisions_exported_and_distinguishable(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")

    report = await adapter.export(bootstrap.dataset, _config())

    assert report.exported_episode_count == 3
    assert report.source_episode_refs == [
        EPISODE_A_REV1_REF,
        EPISODE_A_REV2_REF,
        EPISODE_B_REF,
    ]

    ds = lerobot.datasets.lerobot_dataset.LeRobotDataset(
        repo_id="interop", root=tmp_path / "lerobot-out"
    )
    assert ds.meta.total_episodes == 3

    # Same logical episode_id ("ep-a"), two different checksums -- both
    # revisions made it into the export as distinct LeRobot episodes with
    # their own (different) content, proving episode_id alone was never used
    # as the write key.
    rev1_expected = bootstrap.expected_by_ref[EPISODE_A_REV1_REF]
    rev2_expected = bootstrap.expected_by_ref[EPISODE_A_REV2_REF]
    assert rev1_expected.steps[0].observation != rev2_expected.steps[0].observation

    ep0_frame = ds[0]
    # episode_index 1 is EPISODE_A_REV2 (see export-order comment above);
    # its first frame is right after EPISODE_A_REV1's step_count frames.
    rev1_step_count = bootstrap.expected_by_ref[EPISODE_A_REV1_REF].step_count
    ep1_frame = ds[rev1_step_count]
    assert (
        ep0_frame["observation.state"].tolist()
        != ep1_frame["observation.state"].tolist()
    )


async def test_writer_lifecycle_initialize_write_finalize(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")

    writer = adapter.open_writer(_config())
    await writer.initialize()
    assert writer._dataset is None  # real LeRobotDataset.create() is deferred

    report = await adapter.export(bootstrap.dataset, _config())
    assert report.exported_episode_count == 3

    # finalize() must have run exactly once -- verified indirectly: the
    # dataset is loadable (LeRobotDataset.finalize() is what flushes parquet
    # footers, see writer.py's module docstring) and reports every episode.
    ds = lerobot.datasets.lerobot_dataset.LeRobotDataset(
        repo_id="interop", root=tmp_path / "lerobot-out"
    )
    assert ds.meta.total_episodes == 3
    assert len(ds) == sum(spec.step_count for spec in bootstrap.expected_episodes)


async def test_compatible_fixed_frequency_timeline_exports_cleanly(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")

    report = await adapter.export(bootstrap.dataset, _config())

    assert report.exported_episode_count == 3
    ds = lerobot.datasets.lerobot_dataset.LeRobotDataset(
        repo_id="interop", root=tmp_path / "lerobot-out"
    )
    assert ds.meta.fps == 10  # interop fixture's TARGET_FREQUENCY_HZ


async def test_semantic_loss_reporting_matches_capability_classification(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)
    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")

    report = await adapter.export(
        bootstrap.dataset,
        _config(unsupported_semantic_policy=UnsupportedSemanticPolicy.RECORD),
    )

    losses = {loss.field: loss.mapping for loss in report.semantic_losses}
    assert losses[SemanticField.EPISODE_IDENTITY] is MappingKind.LOSSY_EXPLICIT
    assert losses[SemanticField.TIMESTAMPS] is MappingKind.LOSSY_EXPLICIT
    assert losses[SemanticField.TASK_OUTCOME_METADATA] is MappingKind.LOSSY_EXPLICIT
    assert losses[SemanticField.SIGNAL_STATUS] is MappingKind.UNSUPPORTED
    assert (
        losses[SemanticField.SOURCE_REVISION_TRACEABILITY] is MappingKind.LOSSY_EXPLICIT
    )
    # LOSSLESS fields are never reported as losses (Request 3.1's
    # _resolve_semantic_losses skips them).
    assert SemanticField.STEP_ORDERING not in losses
    assert SemanticField.OBSERVATION_ACTION_NAMESPACE not in losses
    assert SemanticField.FEATURE_ORDERING not in losses


async def test_deterministic_export(tmp_path):
    bootstrap = await build_interop_test_dataset(tmp_path)

    report1 = await LeRobotDatasetAdapter(
        repo_id="interop-1", root=tmp_path / "out-1"
    ).export(bootstrap.dataset, _config())
    report2 = await LeRobotDatasetAdapter(
        repo_id="interop-2", root=tmp_path / "out-2"
    ).export(bootstrap.dataset, _config())

    assert report1.exported_episode_count == report2.exported_episode_count
    assert report1.exported_step_count == report2.exported_step_count
    assert report1.source_episode_refs == report2.source_episode_refs
    assert report1.feature_schema == report2.feature_schema
    assert report1.semantic_losses == report2.semantic_losses

    ds1 = lerobot.datasets.lerobot_dataset.LeRobotDataset(
        repo_id="interop-1", root=tmp_path / "out-1"
    )
    ds2 = lerobot.datasets.lerobot_dataset.LeRobotDataset(
        repo_id="interop-2", root=tmp_path / "out-2"
    )
    for i in range(len(ds1)):
        assert (
            ds1[i]["observation.state"].tolist() == ds2[i]["observation.state"].tolist()
        )
        assert ds1[i]["action"].tolist() == ds2[i]["action"].tolist()


async def test_dataset_ref_points_at_export_target(tmp_path):
    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")

    ref = adapter.dataset_ref

    assert isinstance(ref, ExternalDatasetRef)
    assert ref.format == "lerobot"
    assert ref.uri == str(tmp_path / "lerobot-out")
    assert ref.external_name == "interop"


async def test_empty_episode_rejected(tmp_path):
    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")
    writer = adapter.open_writer(_config())
    await writer.initialize()

    from sceneops_analytics.external_adapters import ExternalEpisode

    with pytest.raises(EmptyEpisodeUnsupportedError):
        await writer.write_episode(
            ExternalEpisode(episode_ref=EPISODE_A_REV1_REF, task="pick", steps=[])
        )


async def test_null_task_rejected(tmp_path):
    from sceneops_analytics.external_adapters import ExternalEpisode, ExternalStep

    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")
    writer = adapter.open_writer(_config())
    await writer.initialize()

    episode = ExternalEpisode(
        episode_ref=EPISODE_A_REV1_REF,
        task=None,
        steps=[
            ExternalStep(step_index=0, timestamp_us=0, observation=[0.0], action=[0.0]),
            ExternalStep(
                step_index=1, timestamp_us=100_000, observation=[1.0], action=[1.0]
            ),
        ],
    )
    with pytest.raises(LeRobotTaskRequiredError):
        await writer.write_episode(episode)


async def test_incompatible_nonuniform_timeline_rejected(tmp_path):
    from sceneops_analytics.external_adapters import ExternalEpisode, ExternalStep

    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")
    writer = adapter.open_writer(_config())
    await writer.initialize()

    episode = ExternalEpisode(
        episode_ref=EPISODE_A_REV1_REF,
        task="pick",
        steps=[
            ExternalStep(step_index=0, timestamp_us=0, observation=[0.0], action=[0.0]),
            ExternalStep(
                step_index=1, timestamp_us=100_000, observation=[1.0], action=[1.0]
            ),
            ExternalStep(
                step_index=2, timestamp_us=250_000, observation=[2.0], action=[2.0]
            ),
        ],
    )
    with pytest.raises(NonUniformTimelineError):
        await writer.write_episode(episode)


async def test_inconsistent_frequency_across_episodes_rejected(tmp_path):
    from sceneops_analytics.external_adapters import ExternalEpisode, ExternalStep

    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")
    writer = adapter.open_writer(_config())
    await writer.initialize()

    # observation/action are 2-dim here, not 1-dim: LeRobot 0.4.4 has a real
    # upstream bug for any feature whose total flattened shape is exactly
    # (1,) -- lerobot.datasets.utils.get_hf_features_from_features maps
    # shape==(1,) to a scalar `datasets.Value` (not a length-1
    # `datasets.Sequence`), but add_frame()/save_episode() still round-trips
    # it as a length-1 numpy array, which datasets.Features.encode_example
    # then fails to coerce ("only 0-dimensional arrays can be converted to
    # Python scalars") -- reproduced independent of this adapter with a bare
    # LeRobotDataset.create(features={"x": {"shape": (1,), ...}}). Real
    # SceneOps data never hits this (the interop fixture's dims are 7/4),
    # so this test -- which only cares about fps-mismatch rejection, not
    # dimensionality -- simply avoids the unrelated upstream edge case.
    ten_hz = ExternalEpisode(
        episode_ref=EPISODE_A_REV1_REF,
        task="pick",
        steps=[
            ExternalStep(
                step_index=0, timestamp_us=0, observation=[0.0, 0.1], action=[0.0, 0.1]
            ),
            ExternalStep(
                step_index=1,
                timestamp_us=100_000,
                observation=[1.0, 1.1],
                action=[1.0, 1.1],
            ),
        ],
    )
    twenty_hz = ExternalEpisode(
        episode_ref=EPISODE_B_REF,
        task="place",
        steps=[
            ExternalStep(
                step_index=0, timestamp_us=0, observation=[0.0, 0.1], action=[0.0, 0.1]
            ),
            ExternalStep(
                step_index=1,
                timestamp_us=50_000,
                observation=[1.0, 1.1],
                action=[1.0, 1.1],
            ),
        ],
    )
    await writer.write_episode(ten_hz)
    with pytest.raises(InconsistentFrequencyError):
        await writer.write_episode(twenty_hz)


async def test_undeterminable_frequency_from_single_step_first_episode_rejected(
    tmp_path,
):
    from sceneops_analytics.external_adapters import ExternalEpisode, ExternalStep

    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")
    writer = adapter.open_writer(_config())
    await writer.initialize()

    one_step = ExternalEpisode(
        episode_ref=EPISODE_A_REV1_REF,
        task="pick",
        steps=[
            ExternalStep(step_index=0, timestamp_us=0, observation=[0.0], action=[0.0])
        ],
    )
    with pytest.raises(UndeterminableFrequencyError):
        await writer.write_episode(one_step)


async def test_finalize_exactly_once(tmp_path, monkeypatch):
    bootstrap = await build_interop_test_dataset(tmp_path)
    adapter = LeRobotDatasetAdapter(repo_id="interop", root=tmp_path / "lerobot-out")

    finalize_calls = 0
    real_finalize = lerobot.datasets.lerobot_dataset.LeRobotDataset.finalize

    def _counting_finalize(self):
        nonlocal finalize_calls
        finalize_calls += 1
        return real_finalize(self)

    monkeypatch.setattr(
        lerobot.datasets.lerobot_dataset.LeRobotDataset, "finalize", _counting_finalize
    )

    await adapter.export(bootstrap.dataset, _config())

    assert finalize_calls == 1
