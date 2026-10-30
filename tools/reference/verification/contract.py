"""The source-grounded reference contract and its derived expectations.

``reference_contract.json`` (hand-written, ``sceneops.reference_contract/2``)
names the corpus fixtures, the two ingestion modes with their RobotRun /
DatasetVersion identities, the build configurations and the expected shape.
``reference_expectations.json`` (generated, ``sceneops.reference_expectations/1``)
is what the documented rules derive from the locked recordings under those
configurations: unit keys, unit ids, windows, per-channel and per-stream
counts and digests. It is written only by ``reference_verify.py derive
--write`` from raw inputs, never from platform output, and every verification
re-derives it and fails on drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import rules
from .expectations import (
    EpisodeExpectation,
    SceneExpectation,
    expected_episodes,
    expected_scene,
)
from .findings import Findings
from .recording import RecordingFacts, read_recording_facts

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_SCHEMA = "sceneops.reference_contract/2"
EXPECTATIONS_SCHEMA = "sceneops.reference_expectations/1"
MODES = ("recording_import", "streaming_acquisition")


class ContractError(Exception):
    """The contract or one of its sources is unreadable or inconsistent."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read {path}: {exc}") from exc


@dataclass
class Sources:
    corpus_dir: Path
    contract: dict[str, Any]
    corpus: dict[str, Any]
    lock: dict[str, Any]
    scene_config: dict[str, Any]
    episode_config: dict[str, Any]

    @property
    def expectations_path(self) -> Path:
        return self.corpus_dir / self.contract["expectations"]

    def fixture_ids(self) -> list[str]:
        return [f["fixture_id"] for f in self.contract["fixtures"]]

    def run_id(self, mode: str, fixture_id: str) -> str:
        spec = self.contract["ingestion_modes"][mode]
        return spec["run_id_template"].format(
            baseline_id=spec["baseline_id"], fixture_id=fixture_id
        )

    def dataset_key(self, mode: str, selection: list[str] | None = None) -> str:
        """The DatasetVersion a mode's selection is composed into: the contract's
        version for every fixture, ``baseline-<fixture>`` for one fixture (the
        baseline bootstraps' name for a part of the contract, canonical-baseline.md)."""
        spec = self.contract["ingestion_modes"][mode]
        if selection is None or sorted(selection) == sorted(self.fixture_ids()):
            return f"{spec['dataset_id']}/{spec['dataset_version']}"
        if len(selection) == 1 and selection[0] in self.fixture_ids():
            return f"{spec['dataset_id']}/baseline-{selection[0]}"
        raise ContractError(
            f"a selection is every contract fixture or one of them, not {selection}"
        )

    def locked(self, fixture_id: str) -> dict[str, Any]:
        return self.lock["fixtures"][fixture_id]


def load_sources(corpus_dir: Path | None = None) -> Sources:
    corpus_dir = corpus_dir or REPO_ROOT / "config/reference/nuscenes-mini-v1"
    contract = read_json(corpus_dir / "reference_contract.json")
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise ContractError(f"unsupported contract schema {contract.get('schema')!r}")
    if sorted(contract.get("ingestion_modes", {})) != sorted(MODES):
        raise ContractError(f"ingestion_modes must be exactly {list(MODES)}")
    configs = contract["build_configs"]
    return Sources(
        corpus_dir=corpus_dir,
        contract=contract,
        corpus=read_json(corpus_dir / "corpus.json"),
        lock=read_json(corpus_dir / "corpus.lock.json"),
        scene_config=read_json(REPO_ROOT / configs["scene"]),
        episode_config=read_json(REPO_ROOT / configs["episode"]),
    )


def cached_recording(sources: Sources, fixture_id: str, cache_root: Path) -> Path:
    """The cached locked recording of a fixture: the file whose bytes the lock pins."""
    directory = cache_root / sources.contract["corpus"]["corpus_id"] / "recordings"
    candidates = sorted(directory.glob(f"{fixture_id}-*.mcap"))
    size = sources.locked(fixture_id)["recording"]["size_bytes"]
    sized = [c for c in candidates if c.stat().st_size == size]
    if not sized:
        raise ContractError(
            f"{fixture_id}: no cached recording of {size} bytes under {directory} "
            "(make reference-data-bootstrap REFERENCE_SCOPE=nuscenes-mini-full-10)"
        )
    return sized[0]


@dataclass
class Derived:
    """What the rules derive from one recording."""

    facts: RecordingFacts
    scene: SceneExpectation
    episodes: EpisodeExpectation

    def summary(self) -> dict[str, Any]:
        return {"scene": self.scene.summary(), "episode": self.episodes.summary()}


def derive(sources: Sources, facts: RecordingFacts) -> Derived:
    return Derived(
        facts=facts,
        scene=expected_scene(facts, sources.scene_config),
        episodes=expected_episodes(facts, sources.episode_config),
    )


def derive_fixture(sources: Sources, fixture_id: str, cache_root: Path) -> Derived:
    return derive(
        sources, read_recording_facts(cached_recording(sources, fixture_id, cache_root))
    )


def expected_unit_ids(
    sources: Sources, mode: str, fixture_id: str, derived: Derived
) -> dict[str, list[str]]:
    run_id = sources.run_id(mode, fixture_id)
    return {
        "scene": [
            rules.unit_id(
                domain="scene",
                source_kind="recording",
                source_id=run_id,
                unit_key=derived.scene.unit_key,
            )
        ],
        "episode": [
            rules.unit_id(
                domain="episode",
                source_kind="recording",
                source_id=run_id,
                unit_key=u.unit_key,
            )
            for u in derived.episodes.units
        ],
    }


def expectations_document(
    sources: Sources, derived: dict[str, Derived]
) -> dict[str, Any]:
    """The committed form of the derived expectations."""
    configs = sources.contract["build_configs"]
    return {
        "schema": EXPECTATIONS_SCHEMA,
        "corpus_id": sources.contract["corpus"]["corpus_id"],
        "rules": "docs/development/reference-verification.md",
        "derived_from": {
            "corpus_lock": rules.checksum(
                (sources.corpus_dir / "corpus.lock.json").read_bytes()
            ),
            "scene_build_config": rules.checksum(
                (REPO_ROOT / configs["scene"]).read_bytes()
            ),
            "episode_build_config": rules.checksum(
                (REPO_ROOT / configs["episode"]).read_bytes()
            ),
        },
        "fixtures": {
            fid: {
                "recording": {
                    "sha256": d.facts.checksum,
                    "size_bytes": d.facts.size_bytes,
                    "message_count": d.facts.message_count,
                },
                **d.summary(),
                "units": {
                    mode: expected_unit_ids(sources, mode, fid, d) for mode in MODES
                },
            }
            for fid, d in sorted(derived.items())
        },
    }


def check_expectations(
    f: Findings,
    committed: dict[str, Any],
    derived_doc: dict[str, Any],
    fixtures: list[str] | None = None,
) -> None:
    """The committed expectations are exactly what the rules derive now."""
    g = f.scoped(layer="expectations")
    g.equal(
        committed.get("schema"),
        EXPECTATIONS_SCHEMA,
        "expectations_drift",
        "expectations schema",
    )
    g.equal(
        committed.get("derived_from"),
        derived_doc["derived_from"],
        "expectations_drift",
        "derived_from (lock, build configs)",
    )
    for fid in fixtures or sorted(derived_doc["fixtures"]):
        g.scoped(fixture_id=fid).equal(
            committed.get("fixtures", {}).get(fid),
            derived_doc["fixtures"].get(fid),
            "expectations_drift",
            "derived expectations",
        )
