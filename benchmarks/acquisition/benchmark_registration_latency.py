#!/usr/bin/env python
"""Measure REGISTER_ROBOT_RUN latency on the live local stack (ADR-008 §5.3, B4).

The reconciler's stall threshold must exceed the worst registration time the
platform can legitimately take, so it is derived from measurement rather than
chosen. This tool measures, through the real production path
(``POST /robot-runs:register`` -> Job -> Celery -> worker ``register_robot_run``
-> PostgreSQL / MinIO):

* worker execution time (``finished_at - started_at``) per recording size;
* queue / dispatch latency (``started_at - queued_at``), idle and under load;
* whether ``heartbeat_at`` moves while a registration runs;
* the worker container's peak memory while it registers a large recording.

Recordings are published under throwaway ``bench-reg-*`` run ids (fresh run ids
so registration cannot take the "already registered" shortcut, R5): the repo's
fixture MCAPs, the real nuScenes scene-0061 recording of the canonical baseline,
and synthetic multiples of it. Everything the tool creates is removed at the end
(``--keep`` to inspect). Profiling tool, not a test: no pass/fail assertions.

Requires ``make local-up`` + ``make reference-contract-bootstrap`` (the source recording)
and a running ``worker-jobs``.

Usage::

    uv run python benchmarks/acquisition/benchmark_registration_latency.py \\
        --out /tmp/registration_latency.json
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import statistics
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from mcap.reader import make_reader
from mcap.writer import Writer

from sceneops_core.config import ArtifactSettings
from sceneops_core.robots.manifest import CaptureSource, CaptureSourceKind
from sceneops_integrations.recording.publisher import publish_recording_bytes
from sceneops_storage import create_artifact_store

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "apps" / "worker" / "tests" / "fixtures" / "rosbag"
SOURCE_RUN_ID = "run-canonical-scene-0061"
WORKER_CONTAINER = "sceneops-worker-jobs-1"
TERMINAL = {"succeeded", "failed", "cancelled", "blocked"}


def _settings() -> ArtifactSettings:
    return ArtifactSettings(
        backend="minio",
        root_uri=os.environ.get("BENCH_ROOT_URI", "s3://sceneops/artifacts"),
        endpoint_url=os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9000"),
        region="ap-northeast-2",
        access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
        secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
    )


def _repeat_recording(source: bytes, times: int) -> bytes:
    """``times`` copies of the recording's messages laid end to end in time:
    the same schemas, channels and per-message payloads, a proportionally
    larger file and message count."""
    reader = make_reader(io.BytesIO(source))
    summary = reader.get_summary()
    assert summary is not None and summary.statistics is not None
    span = (
        summary.statistics.message_end_time - summary.statistics.message_start_time + 1
    )
    out = io.BytesIO()
    writer = Writer(out)
    writer.start(profile="ros2", library="benchmark_registration_latency")
    schema_ids: dict[int, int] = {}
    channel_ids: dict[int, int] = {}
    for schema in summary.schemas.values():
        schema_ids[schema.id] = writer.register_schema(
            name=schema.name, encoding=schema.encoding, data=schema.data
        )
    for channel in summary.channels.values():
        channel_ids[channel.id] = writer.register_channel(
            topic=channel.topic,
            message_encoding=channel.message_encoding,
            schema_id=schema_ids.get(channel.schema_id, 0),
            metadata=dict(channel.metadata),
        )
    for copy in range(times):
        for _, _, message in make_reader(io.BytesIO(source)).iter_messages(
            log_time_order=True
        ):
            writer.add_message(
                channel_id=channel_ids[message.channel_id],
                log_time=message.log_time + copy * span,
                publish_time=message.publish_time + copy * span,
                data=message.data,
                sequence=message.sequence,
            )
    writer.finish()
    return out.getvalue()


class _MemorySampler:
    """Peak resident memory of the worker container (docker stats polling)."""

    def __init__(self, container: str) -> None:
        self._container = container
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.peak_mib = 0.0
        self.samples = 0

    @staticmethod
    def _to_mib(text: str) -> float:
        value = text.strip().split("/")[0].strip()
        for unit, scale in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024)):
            if value.endswith(unit):
                return float(value[: -len(unit)]) * scale
        return 0.0

    def _run(self) -> None:
        while not self._stop.is_set():
            proc = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.MemUsage}}",
                    self._container,
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                self.peak_mib = max(self.peak_mib, self._to_mib(proc.stdout))
                self.samples += 1

    def __enter__(self) -> "_MemorySampler":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


class _Bench:
    def __init__(self, api: httpx.Client, store, root_uri: str) -> None:
        self.api = api
        self.store = store
        self.root_uri = root_uri

    async def publish(self, label: str, data: bytes) -> str:
        suffix = uuid.uuid4().hex[:8]
        publication = await publish_recording_bytes(
            artifact_store=self.store,
            root_uri=self.root_uri,
            recording_bytes=data,
            run_id=f"bench-reg-{label}-{suffix}",
            robot_id=f"bench-reg-robot-{label}",
            robot_platform="benchmark",
            capture_source=CaptureSource(
                kind=CaptureSourceKind.KAFKA, topics=["sceneops.robot.telemetry.v1"]
            ),
            source_clock="mcap_log_time",
        )
        return publication.manifest_uri

    def submit(self, manifest_uri: str) -> str:
        response = self.api.post(
            "/robot-runs:register", json={"manifest_uri": manifest_uri}
        )
        response.raise_for_status()
        return response.json()["job"]["jobId"]

    def wait(self, job_id: str, *, poll: float = 0.05, timeout: float = 900.0) -> dict:
        deadline = time.monotonic() + timeout
        heartbeats: set[str | None] = set()
        while True:
            job = self.api.get(f"/jobs/{job_id}").json()["job"]
            if job["status"] == "running":
                heartbeats.add(job.get("heartbeatAt"))
            if job["status"] in TERMINAL:
                job["_heartbeats_while_running"] = len(heartbeats)
                return job
            if time.monotonic() > deadline:
                raise TimeoutError(job_id)
            time.sleep(poll)

    @staticmethod
    def timings(job: dict, *, submitted: float, done: float) -> dict:
        return {
            "status": job["status"],
            "created_to_queued_s": _seconds(job["createdAt"], job["queuedAt"]),
            "dispatch_queued_to_started_s": _seconds(job["queuedAt"], job["startedAt"]),
            "execution_started_to_finished_s": _seconds(
                job["startedAt"], job["finishedAt"]
            ),
            "heartbeat_minus_started_s": _seconds(job["startedAt"], job["heartbeatAt"]),
            "heartbeat_minus_finished_s": _seconds(
                job["heartbeatAt"], job["finishedAt"]
            ),
            "client_submit_to_terminal_s": round(done - submitted, 3),
            "result_created": (job.get("result") or {}).get("created"),
        }

    async def single(self, label: str, data: bytes, repeats: int) -> dict:
        runs = []
        peaks = []
        for _ in range(repeats):
            manifest_uri = await self.publish(label, data)
            with _MemorySampler(WORKER_CONTAINER) as sampler:
                submitted = time.monotonic()
                job_id = self.submit(manifest_uri)
                job = self.wait(job_id)
                done = time.monotonic()
            timing = self.timings(job, submitted=submitted, done=done)
            timing["heartbeats_seen_while_running"] = job["_heartbeats_while_running"]
            runs.append(timing)
            peaks.append(sampler.peak_mib)
        return {
            "label": label,
            "size_bytes": len(data),
            "runs": runs,
            "worker_peak_mem_mib_during_run": max(peaks),
            "execution_s_median": statistics.median(
                r["execution_started_to_finished_s"] for r in runs
            ),
            "execution_s_max": max(r["execution_started_to_finished_s"] for r in runs),
        }

    async def concurrent(self, label: str, data: bytes, count: int) -> dict:
        manifests = [await self.publish(label, data) for _ in range(count)]
        with _MemorySampler(WORKER_CONTAINER) as sampler:
            submitted = time.monotonic()
            job_ids = [self.submit(uri) for uri in manifests]
            jobs = []
            for job_id in job_ids:
                job = self.wait(job_id)
                jobs.append((job, time.monotonic()))
        runs = [self.timings(j, submitted=submitted, done=d) for j, d in jobs]
        return {
            "label": label,
            "size_bytes": len(data),
            "concurrent_jobs": count,
            "runs": runs,
            "worker_peak_mem_mib_during_run": sampler.peak_mib,
            "execution_s_max": max(r["execution_started_to_finished_s"] for r in runs),
            "dispatch_s_max": max(r["dispatch_queued_to_started_s"] for r in runs),
            "client_submit_to_terminal_s_max": max(
                r["client_submit_to_terminal_s"] for r in runs
            ),
        }


def _cleanup() -> dict:
    sql = """
    with j as (select job_id from jobs where params->>'manifest_uri' like '%/bench-reg-%'),
    e as (delete from execution_records where resource_id in (select job_id from j) returning 1),
    jd as (delete from jobs where job_id in (select job_id from j) returning 1),
    r as (delete from robot_runs where run_id like 'bench-reg-%' returning 1),
    a as (delete from artifacts where owner_id like 'bench-reg-%' returning 1),
    ro as (delete from robots where robot_id like 'bench-reg-robot-%' returning 1)
    select (select count(*) from jd), (select count(*) from r),
           (select count(*) from a), (select count(*) from ro), (select count(*) from e);
    """
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "sceneops-postgres-1",
            "psql",
            "-U",
            "sceneops",
            "-d",
            "sceneops",
            "-At",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
    )
    return {"psql_rc": proc.returncode, "deleted": proc.stdout.strip() or proc.stderr}


async def _delete_objects(store, root_uri: str) -> int:
    prefixes = {
        obj.uri.rsplit("/", 1)[0]
        for obj in await store.list_objects(root_uri)
        if "/bench-reg-" in obj.uri
    }
    for prefix in sorted(prefixes):
        await store.delete_prefix(prefix)
    return len(prefixes)


async def _main(args: argparse.Namespace) -> dict:
    settings = _settings()
    store = create_artifact_store(settings)
    root_uri = settings.robot_run_root_uri
    source = await store.read_bytes(f"{root_uri}/{SOURCE_RUN_ID}/recording.mcap")

    api = httpx.Client(
        base_url=f"{os.environ.get('API_BASE_URL', 'http://localhost:8000')}"
        f"{os.environ.get('API_PREFIX', '/api/v1')}",
        timeout=60.0,
    )
    bench = _Bench(api, store, root_uri)
    report: dict = {
        "recorded_at": datetime.now().astimezone().isoformat(),
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT
        ).stdout.strip(),
        "worker_concurrency": 4,
        "cases": [],
    }

    def record(case: dict) -> None:
        report["cases"].append(case)
        if args.out is not None:
            args.out.write_text(json.dumps(report, indent=2, sort_keys=True))

    try:
        for name in ("std_msgs_string", "nav_msgs_odometry", "can_replay_scene_0061"):
            data = (FIXTURES / f"{name}.mcap").read_bytes()
            record(await bench.single(name, data, args.repeats_small))
        record(await bench.single("nuscenes_scene_0061", source, args.repeats_large))
        for times in args.multiples:
            data = _repeat_recording(source, times)
            record(await bench.single(f"nuscenes_scene_0061_x{times}", data, 1))
            del data
        record(await bench.concurrent("nuscenes_scene_0061_concurrent", source, 5))
    finally:
        if not args.keep:
            report["cleanup_objects_deleted"] = await _delete_objects(store, root_uri)
            report["cleanup_rows"] = _cleanup()
        api.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats-small", type=int, default=5)
    parser.add_argument("--repeats-large", type=int, default=3)
    parser.add_argument(
        "--multiples", type=int, nargs="*", default=[2], help="synthetic x-times sizes"
    )
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    report = asyncio.run(_main(args))
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
