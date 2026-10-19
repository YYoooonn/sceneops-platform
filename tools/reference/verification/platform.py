"""The contract's platform state, verified against the derived expectations.

Per ingestion mode and fixture:

```text
RobotRun     registered under the contract robot; recording artifact id
             art-robotrun-<run>; the stored recording is the record's bytes;
             Recording Import pins the locked recording, Streaming Acquisition a
             capture whose re-derived content equals the locked one's
units        exactly the expected unit ids of the run (f(domain, recording, run,
             unit_key)), one revision each
revision     pin self-consistent; manifest bytes read back, canonical, checksum =
             the revision's; lineage binding, window, fingerprint, build config;
             content = what the recording and the build configuration determine
payloads     every reference resolves to its record and stored object; one artifact
             per recorded message and extraction, shared by Scene and Episode
DatasetVersion  exactly the mode's expected units, registered pins, composition
             checksum recomputed from the pins, summaries from the expectations
```
"""

from __future__ import annotations

from typing import Any

from . import checks
from .contract import MODES, Derived, Sources, expected_unit_ids
from .findings import Findings
from .recording import RecordingFacts


def verify_platform(
    f: Findings,
    sources: Sources,
    obs: dict[str, Any],
    derived: dict[str, Derived],
    *,
    captured: dict[str, RecordingFacts] | None = None,
    derive_captured: Any = None,
    deep: bool = False,
    fixtures: list[str] | None = None,
    modes: tuple[str, ...] = MODES,
) -> None:
    """``derived``: per fixture, the expectations of the locked recording.
    ``captured``: per streaming run id, the facts of its stored recording;
    ``derive_captured(facts)`` derives a ``Derived`` from them. ``modes``: the
    ingestion modes verified; the others are neither expected nor checked."""
    contract = sources.contract
    units = contract["units"]
    selected = fixtures or sources.fixture_ids()
    for mode in modes:
        spec = contract["ingestion_modes"][mode]
        expected_members: dict[str, str] = {}
        for fid in sources.fixture_ids():
            d = derived[fid]
            ids = expected_unit_ids(sources, mode, fid, d)
            expected_members.update({u: "scene" for u in ids["scene"]})
            expected_members.update({u: "episode" for u in ids["episode"]})
            if fid not in selected:
                continue
            g = f.scoped(layer="platform", mode=mode, fixture_id=fid)
            run_id = sources.run_id(mode, fid)
            run = checks.check_robot_run(g, obs, run_id, spec["robot_id"])
            if run is None:
                continue
            recording = run.get("recording_artifact") or {}
            locked = sources.locked(fid)["recording"]
            if spec["recording"] == "locked":
                g.equal(
                    recording.get("checksum"),
                    locked["sha256"],
                    "robot_run_recording",
                    "Recording Import pins the locked recording",
                )
                recording_checksum = locked["sha256"]
            else:
                recording_checksum = recording.get("checksum")
                stored = (captured or {}).get(run_id)
                if g.check(
                    stored is not None,
                    "streaming_content",
                    f"the captured recording of {run_id} was not read back",
                ):
                    g.equal(
                        stored.checksum,
                        recording_checksum,
                        "robot_run_recording",
                        "captured recording bytes",
                    )
                    g.equal(
                        stored.topic_counts,
                        dict(sorted(locked["topic_counts"].items())),
                        "streaming_content",
                        "captured per-topic counts vs the lock",
                    )
                    g.equal(
                        derive_captured(stored).summary(),
                        d.summary(),
                        "streaming_content",
                        "canonical content derived from the capture vs from the locked recording",
                    )

            refs: list[dict[str, Any]] = []
            ids = expected_unit_ids(sources, mode, fid, d)
            for domain, wanted, count in (
                ("scene", ids["scene"], units["scenes_per_robot_run"]),
                ("episode", ids["episode"], units["episodes_per_robot_run"]),
            ):
                g.equal(
                    len(wanted),
                    count,
                    "unit_shape",
                    f"{domain}s per RobotRun derived from the recording",
                )
                g.equal(
                    sorted(checks.run_units(obs, domain, run_id)),
                    sorted(wanted),
                    "unit_set",
                    f"{domain} units of {run_id}",
                )
            for unit_id in ids["scene"]:
                for revision in _revisions(
                    g,
                    obs,
                    "scene",
                    unit_id,
                    run_id,
                    d.scene.unit_key,
                    units["revisions_per_unit"],
                ):
                    manifest = checks.load_manifest(g, obs, "scene", revision)
                    if manifest is None:
                        continue
                    checks.check_lineage(
                        g, manifest, robot_run_id=run_id, recording_checksum=recording_checksum, unit_key=d.scene.unit_key,
                        window=d.scene.window, revision=revision, build_config_file=sources.scene_config, producer=d.scene.producer,
                    )  # fmt: skip
                    refs += checks.check_scene_manifest(g, manifest, d.scene, revision)
            for unit_id, unit in zip(ids["episode"], d.episodes.units):
                for revision in _revisions(
                    g,
                    obs,
                    "episode",
                    unit_id,
                    run_id,
                    unit.unit_key,
                    units["revisions_per_unit"],
                ):
                    manifest = checks.load_manifest(g, obs, "episode", revision)
                    if manifest is None:
                        continue
                    checks.check_lineage(
                        g, manifest, robot_run_id=run_id, recording_checksum=recording_checksum, unit_key=unit.unit_key,
                        window=unit.window, revision=revision, build_config_file=sources.episode_config, producer=None,
                    )  # fmt: skip
                    refs += checks.check_episode_manifest(g, manifest, unit, revision)
            checks.check_payloads(g, obs, run_id, refs, deep=deep)

        checks.check_dataset_version(
            f.scoped(layer="platform", mode=mode, fixture_id=None),
            obs,
            sources.dataset_key(mode),
            expected_members,
            checks.summary_for(
                [derived[x].scene for x in sources.fixture_ids()],
                [derived[x].episodes for x in sources.fixture_ids()],
            ),
        )


def _revisions(
    g: Findings,
    obs: dict[str, Any],
    domain: str,
    unit_id: str,
    run_id: str,
    unit_key: str,
    count: int,
) -> list[dict[str, Any]]:
    revisions = checks.unit_revisions(g, obs, domain, unit_id, run_id, unit_key)
    if revisions:
        g.equal(
            len(revisions), count, "revision_count", f"{domain} {unit_id} revisions"
        )
    return revisions
