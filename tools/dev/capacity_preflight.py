#!/usr/bin/env python3
"""Capacity preflight of a reference workload (`make capacity-preflight`; the
canonical and streaming bootstraps run it before their first write).

The Docker VM's filesystem holds PostgreSQL, MinIO, the Kafka log and the capture
volume together. Filling it stops the shared PostgreSQL server for every database
on it, the reference one included, and its crash recovery cannot finish until there
is room again (docs/development/local-development.md#disk-hygiene). A workload is
therefore refused before it writes anything when the free space cannot hold its
estimated peak with a margin.

    python3 tools/dev/capacity_preflight.py --mode recording_import|streaming_acquisition \\
        (--fixture <id> ... | --scope <scope>) [--corpus nuscenes-mini-v1]

Estimate, per fixture, from its locked recording size R (corpus.lock.json):

    MinIO      R (the published or captured recording) + DERIVED_RATIO x R (the
               payloads the reference build configurations extract, the manifests
               and run records)
    Kafka      KAFKA_LOG_RATIO x R per streamed fixture; the broker keeps it for its
               24 h replay window, so it accumulates over the whole run
    capture    the largest R of the streamed fixtures: one capture at a time, removed
               once its recording is provably registered

    required = (MinIO + Kafka + capture) x (1 + MARGIN) + POSTGRES_RESERVE + FLOOR

POSTGRES_RESERVE is the server's max_wal_size (1 GiB); FLOOR is what must still be
free at the estimated peak. The ratios are measurements of the reference workload
(nuscenes-mini-v1 under config/baselines/), not bounds for other build
configurations. Rows and manifests are small beside them and covered by the margin.

The Docker VM is measured inside the running PostgreSQL server container
(POSTGRES_CONTAINER, default sceneops-postgres-1): the filesystem whose exhaustion
the preflight exists to prevent. The host volume must keep FLOOR free.

stdout: one JSON report (sceneops.capacity_preflight/1). Exit status: 0 when the
workload fits, 1 when it does not, 2 when the workload or the free space could not
be determined (the preflight never passes without a measurement).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_SCHEMA = "sceneops.capacity_preflight/1"
MODES = ("recording_import", "streaming_acquisition")
GIB = 1024**3

# Bytes of the objects derived from a recording (payloads, manifests, run records) over
# the recording's: the highest per-fixture ratio measured, scene-0061 streamed,
# (298,096,503 + 1,521,405 + 7,518) / 355,793,127 = 0.842, rounded up
# (docs/history/streaming-platform-verification.md); the 10-fixture Recording Import
# mean is 0.780 (recording-platform-verification.md).
DERIVED_RATIO = 0.85
# Kafka log bytes over the finalized MCAP of a streamed reference fixture
# (docs/history/streaming-retention-study.md, "Data rates").
KAFKA_LOG_RATIO = 1.33
MARGIN = 0.25
POSTGRES_RESERVE_BYTES = 1 * GIB
FLOOR_BYTES = 2 * GIB


class PreflightError(Exception):
    """The workload or the free space cannot be determined."""


@dataclass(frozen=True)
class Estimate:
    minio_bytes: int
    kafka_bytes: int
    capture_peak_bytes: int

    @property
    def growth_bytes(self) -> int:
        return self.minio_bytes + self.kafka_bytes + self.capture_peak_bytes

    @property
    def required_free_bytes(self) -> int:
        return (
            math.ceil(self.growth_bytes * (1 + MARGIN))
            + POSTGRES_RESERVE_BYTES
            + FLOOR_BYTES
        )

    def as_dict(self) -> dict[str, int | float]:
        return {
            "minio_bytes": self.minio_bytes,
            "kafka_bytes": self.kafka_bytes,
            "capture_peak_bytes": self.capture_peak_bytes,
            "growth_bytes": self.growth_bytes,
            "margin": MARGIN,
            "postgres_reserve_bytes": POSTGRES_RESERVE_BYTES,
            "floor_bytes": FLOOR_BYTES,
            "required_free_bytes": self.required_free_bytes,
        }


def estimate(mode: str, recording_sizes: list[int]) -> Estimate:
    """What a workload of ``mode`` over recordings of these sizes adds at its peak."""
    if mode not in MODES:
        raise PreflightError(f"unknown mode {mode!r}; one of {list(MODES)}")
    total = sum(recording_sizes)
    streaming = mode == "streaming_acquisition"
    return Estimate(
        minio_bytes=math.ceil(total * (1 + DERIVED_RATIO)),
        kafka_bytes=math.ceil(total * KAFKA_LOG_RATIO) if streaming else 0,
        capture_peak_bytes=max(recording_sizes, default=0) if streaming else 0,
    )


def decide(
    required: Estimate, *, docker_vm_free_bytes: int, host_free_bytes: int
) -> tuple[bool, str]:
    if docker_vm_free_bytes < required.required_free_bytes:
        return False, (
            f"the Docker VM has {docker_vm_free_bytes / GIB:.1f} GiB free; the workload "
            f"needs {required.required_free_bytes / GIB:.1f} GiB "
            f"({required.growth_bytes / GIB:.1f} GiB estimated growth + {MARGIN:.0%}, "
            f"{POSTGRES_RESERVE_BYTES / GIB:.0f} GiB PostgreSQL WAL, "
            f"{FLOOR_BYTES / GIB:.0f} GiB floor)"
        )
    if host_free_bytes < FLOOR_BYTES:
        return False, (
            f"the host volume has {host_free_bytes / GIB:.1f} GiB free, below the "
            f"{FLOOR_BYTES / GIB:.0f} GiB floor"
        )
    return True, "fits"


# ── workload ─────────────────────────────────────────────────────────────────


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError(f"cannot read {path}: {exc}") from exc


def resolve_fixtures(
    corpus: dict, lock: dict, *, fixtures: list[str], scope: str | None
) -> dict[str, int]:
    """fixture id -> locked recording size of the selection."""
    if scope is not None:
        scopes = corpus.get("scopes", {})
        if scope not in scopes:
            raise PreflightError(f"unknown scope {scope!r}; one of {sorted(scopes)}")
        members = scopes[scope]
        fixtures = (
            [f["fixture_id"] for f in corpus["fixtures"]]
            if members == ["*"]
            else members
        )
    locked = lock.get("fixtures", {})
    unknown = [f for f in fixtures if f not in locked]
    if unknown:
        raise PreflightError(f"not locked fixtures: {unknown}")
    return {f: int(locked[f]["recording"]["size_bytes"]) for f in fixtures}


# ── measurement ──────────────────────────────────────────────────────────────


def docker_vm_free_bytes(container: str) -> int:
    """Free bytes of the filesystem the PostgreSQL server writes to."""
    result = subprocess.run(
        ["docker", "exec", container, "df", "-Pk", "/var/lib/postgresql/data"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PreflightError(
            f"cannot measure the Docker VM through {container}: "
            f"{(result.stderr or result.stdout).strip()[:300]} (is `make local-up` running?)"
        )
    return parse_df_available(result.stdout)


def parse_df_available(output: str) -> int:
    """Available bytes from POSIX `df -Pk` output (the data line's fourth field)."""
    lines = [line for line in output.splitlines() if line.strip()]
    try:
        return int(lines[1].split()[3]) * 1024
    except (IndexError, ValueError) as exc:
        raise PreflightError(f"unexpected df output: {output[:300]!r}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mode", required=True, choices=MODES)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--fixture", action="append", default=[])
    selection.add_argument("--scope")
    parser.add_argument("--corpus", default="nuscenes-mini-v1")
    args = parser.parse_args(argv)

    container = os.environ.get("POSTGRES_CONTAINER", "sceneops-postgres-1")
    corpus_dir = REPO_ROOT / "config/reference" / args.corpus
    try:
        sizes = resolve_fixtures(
            _read_json(corpus_dir / "corpus.json"),
            _read_json(corpus_dir / "corpus.lock.json"),
            fixtures=args.fixture,
            scope=args.scope,
        )
        required = estimate(args.mode, list(sizes.values()))
        vm_free = docker_vm_free_bytes(container)
    except PreflightError as exc:
        print(f"capacity_preflight: {exc}", file=sys.stderr)
        return 2
    host_free = shutil.disk_usage(REPO_ROOT).free
    ok, reason = decide(
        required, docker_vm_free_bytes=vm_free, host_free_bytes=host_free
    )
    print(
        json.dumps(
            {
                "schema": REPORT_SCHEMA,
                "ok": ok,
                "reason": reason,
                "workload": {
                    "mode": args.mode,
                    "corpus": args.corpus,
                    "fixtures": sorted(sizes),
                    "recording_bytes": sum(sizes.values()),
                },
                "estimate": required.as_dict(),
                "measured": {
                    "docker_vm_free_bytes": vm_free,
                    "host_free_bytes": host_free,
                    "probe_container": container,
                },
            },
            sort_keys=True,
        )
    )
    if not ok:
        print(f"capacity_preflight: refused: {reason}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
