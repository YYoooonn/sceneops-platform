#!/usr/bin/env python3
"""Capture backlog / replay-window benchmark: RobotRun arrival vs. Capture service.

Runs INSIDE the capture container (needs the ROS 2 interface definitions and a real
Kafka broker); `benchmark_capture_backlog.sh` is the entry point. It models what
a launcher does with the production processes:

  * a RobotRun arrives as one producer process (the bridge's publish path: RUN_START
    acknowledged, telemetry, flush, RUN_END), paced from the source timeline;
  * a dispatcher starts the real ``python -m sceneops_capture`` CLI, pinned to the
    acknowledged RUN_START position, whenever one of ``--slots`` is free (oldest run
    first, the finalized bag is removed afterwards);
  * a sampler logs, once a second, the VM (CPU, disk, network, memory, free disk), the
    topic's watermarks and the run states.

Everything is written to ``<results-dir>/<name>.json`` and summarised on the host with
``summarize`` (standard library only).

Subcommands
-----------
  drain      service rate: produce K runs first, then drain them with C Captures
  regime     arrivals at a fixed interval against C live-attached Captures
  calibrate  find the phase of the broker's 5-minute retention check
  outage     one producer under a broker stall (the caller pauses the broker)
  sweep      delete the ``bp.*`` topics this tool created
  summarize  drain | regime | headroom tables from the result files

Workloads: ``ref20`` (the whole 20 s reference run, 471 MB), ``ref<N>`` (its first N
seconds), ``small`` (synthetic odometry-sized records).

Safety: the run aborts below 1.6 GB free on the output volume or 300 MB of available
memory; topics are throwaway (``bp.<name>.<epoch>``) and deleted at the end. A measurement
is one host and one broker; it claims nothing beyond the workload and environment it ran on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import os
import queue as queue_mod
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

OUT_ROOT = Path("/out")
RECORDINGS = Path("/ref")
RESULTS = Path("/results")
CHANNELS_FILE = "/workspace/channels/surround-camera-lidar.json"
BOOTSTRAP = os.environ.get("SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
FRAMING = 415  # measured Kafka key + headers + batch share per record, bytes
MIN_FREE_DISK = 1.6e9
MIN_MEM_AVAILABLE = 300e6
GIB = 1 << 30
ISOLATED_MU_MBPS = (
    57.0  # one Capture reading a stored 244-471 MB run, measured on the dev host
)


# --------------------------------------------------------------------------- workloads


@dataclass(slots=True)
class Msg:
    topic: str
    mtype: str
    data: bytes
    t_ns: int  # source timeline relative to the first message


def load_reference(window_s: float | None) -> list[Msg]:
    from mcap.reader import make_reader

    path = next(RECORDINGS.glob("scene-0061-*.mcap"))
    msgs: list[Msg] = []
    t0 = None
    with open(path, "rb") as f:
        for schema, channel, m in make_reader(f).iter_messages(log_time_order=True):
            t0 = m.log_time if t0 is None else t0
            rel = m.log_time - t0
            if window_s is not None and rel > window_s * 1e9:
                break
            msgs.append(Msg(channel.topic, schema.name, m.data, rel))
    return msgs


def synthetic_small(records: int, payload_bytes: int, rec_per_s: float) -> list[Msg]:
    payload = bytes([0, 1, 0, 0]) + b"x" * max(0, payload_bytes - 4)
    step = int(1e9 / rec_per_s)
    return [
        Msg("/vehicle/odom", "nav_msgs/msg/Odometry", payload, i * step)
        for i in range(records)
    ]


def build_workload(args: argparse.Namespace) -> list[Msg]:
    if args.workload == "small":
        return synthetic_small(args.records, args.payload, args.small_rate)
    if args.workload == "ref20":
        return load_reference(None)
    if re.fullmatch(r"ref\d+", args.workload):
        return load_reference(float(args.workload[3:]))
    raise SystemExit(f"unknown workload {args.workload!r}")


def workload_bytes(msgs: list[Msg]) -> int:
    return sum(len(m.data) + FRAMING for m in msgs) + 2 * FRAMING


# --------------------------------------------------------------------------- kafka admin


def _admin(bootstrap: str):
    from confluent_kafka.admin import AdminClient

    return AdminClient({"bootstrap.servers": bootstrap})


def create_topic(
    name: str, partitions: int, config: dict[str, str] | None = None
) -> None:
    from confluent_kafka.admin import NewTopic

    admin = _admin(BOOTSTRAP)  # keep a reference: the futures die with the client
    futures = admin.create_topics(
        [
            NewTopic(
                name,
                num_partitions=partitions,
                replication_factor=1,
                config=config or {},
            )
        ]
    )
    futures[name].result(30)


def delete_topic(name: str, bootstrap: str | None = None) -> None:
    try:
        admin = _admin(bootstrap or BOOTSTRAP)
        admin.delete_topics([name], operation_timeout=30)[name].result(40)
    except Exception as exc:  # noqa: BLE001 - cleanup must not mask the result
        print(f"delete_topic {name}: {exc}", file=sys.stderr)


class Watermarks:
    def __init__(self, topic: str, partitions: int) -> None:
        from confluent_kafka import Consumer, TopicPartition

        # No subscribe and no commit: reading watermarks creates no group state.
        self._consumer = Consumer(
            {
                "bootstrap.servers": BOOTSTRAP,
                "group.id": "bp-bench-meta",
                "enable.auto.commit": False,
            }
        )
        self._tps = [TopicPartition(topic, p) for p in range(partitions)]

    def read(self) -> list[tuple[int, int]]:
        out = []
        for tp in self._tps:
            try:
                out.append(self._consumer.get_watermark_offsets(tp, timeout=2.0))
            except Exception:  # noqa: BLE001
                out.append((-1, -1))
        return out

    def close(self) -> None:
        self._consumer.close()


# --------------------------------------------------------------------------- stream process


def stream_proc(
    q, run_id: str, topic: str, msgs: list[Msg], rate: float, robot_id: str
) -> None:
    asyncio.run(_stream(q, run_id, topic, msgs, rate, robot_id))


async def _stream(
    q, run_id: str, topic: str, msgs: list[Msg], rate: float, robot_id: str
) -> None:
    from sceneops_streaming import (
        EnvelopeEncoding,
        KafkaTelemetryProducer,
        StreamingSettings,
        TelemetryEnvelope,
    )
    from sceneops_streaming.control import RunEventType, build_control_envelope

    producer = KafkaTelemetryProducer(
        settings=StreamingSettings(bootstrap_servers=BOOTSTRAP), topic=topic
    )
    try:
        start = build_control_envelope(
            event_type=RunEventType.RUN_START,
            robot_id=robot_id,
            robot_run_id=run_id,
            sequence_number=0,
        )
        pos = await producer.publish_and_wait(start, 30.0)
        q.put(("started", run_id, pos.partition, pos.offset, time.time()))
        t0 = time.monotonic()
        last_report = t0
        nbytes = 0
        for i, m in enumerate(msgs):
            if rate > 0:
                delay = t0 + m.t_ns / 1e9 / rate - time.monotonic()
                if delay > 0.0005:
                    await asyncio.sleep(delay)
            await producer.publish(
                TelemetryEnvelope(
                    robot_id=robot_id,
                    robot_run_id=run_id,
                    channel=m.topic,
                    message_type=m.mtype,
                    source_timestamp_ns=1_700_000_000_000_000_000 + m.t_ns,
                    sequence_number=i,
                    encoding=EnvelopeEncoding.ROS2_CDR,
                    payload=m.data,
                )
            )
            nbytes += len(m.data) + FRAMING
            if time.monotonic() - last_report >= 1.0:
                q.put(("progress", run_id, nbytes, i + 1, time.time()))
                last_report = time.monotonic()
        await producer.flush(600.0)
        await producer.publish(
            build_control_envelope(
                event_type=RunEventType.RUN_END,
                robot_id=robot_id,
                robot_run_id=run_id,
                sequence_number=1,
                telemetry_count=len(msgs),
            )
        )
        await producer.close(600.0)
        q.put(("ended", run_id, nbytes + 2 * FRAMING, len(msgs), time.time()))
    except Exception as exc:  # noqa: BLE001
        q.put(("stream_error", run_id, f"{type(exc).__name__}: {exc}", time.time()))
        raise


# --------------------------------------------------------------------------- lab


@dataclass
class Run:
    run_id: str
    partition: int = -1
    offset: int = -1
    t_start: float = 0.0  # RUN_START acknowledged
    t_end: float | None = None  # RUN_END acknowledged
    bytes: int = 0
    records: int = 0
    state: str = (
        "streaming"  # streaming | waiting | capturing | captured | expired | failed
    )
    t_capture_start: float | None = None
    t_done: float | None = None
    capture_cpu_s: float | None = None
    capture_rss_kb: int | None = None
    capture_rc: int | None = None
    capture_err: str | None = None
    cum_at_start: int = 0  # topic bytes produced (all runs) at RUN_START


class Lab:
    def __init__(
        self, name: str, topic: str, partitions: int, slots: int, results: Path
    ) -> None:
        self.name, self.topic, self.partitions, self.slots = (
            name,
            topic,
            partitions,
            slots,
        )
        self.results = results
        self.robot_id = "robot-bp"
        self.runs: dict[str, Run] = {}
        self.order: list[str] = []
        self.lock = threading.Lock()
        self.q = mp.get_context("fork").Queue()
        self.stop = threading.Event()
        self.dispatch_enabled = threading.Event()
        self.abort_reason: str | None = None
        self.samples: list[dict] = []
        self.procs: dict[str, mp.Process] = {}
        self.caps: dict[str, tuple[subprocess.Popen, Path]] = {}
        self.cum_bytes = 0
        self.captured_bytes = 0
        self.out = OUT_ROOT / name
        self.out.mkdir(parents=True, exist_ok=True)

    # producer side
    def launch_stream(self, run_id: str, msgs: list[Msg], rate: float) -> None:
        with self.lock:
            self.runs[run_id] = Run(run_id)
            self.order.append(run_id)
        proc = mp.get_context("fork").Process(
            target=stream_proc,
            args=(self.q, run_id, self.topic, msgs, rate, self.robot_id),
            daemon=True,
        )
        proc.start()
        self.procs[run_id] = proc

    def _pump(self) -> None:
        reported: dict[str, int] = {}
        while not self.stop.is_set() or not self.q.empty():
            try:
                ev = self.q.get(timeout=0.2)
            except queue_mod.Empty:
                continue
            kind, run_id = ev[0], ev[1]
            with self.lock:
                r = self.runs[run_id]
                if kind == "started":
                    r.partition, r.offset, r.t_start = ev[2], ev[3], ev[4]
                    r.cum_at_start = self.cum_bytes
                    r.state = "waiting"
                elif kind in ("progress", "ended"):
                    self.cum_bytes += ev[2] - reported.get(run_id, 0)
                    reported[run_id] = ev[2]
                    if kind == "ended":
                        r.t_end, r.bytes, r.records = ev[4], ev[2], ev[3]
                elif kind == "stream_error":
                    r.state, r.capture_err = "failed", "stream: " + ev[2]

    # capture side
    def _spawn_capture(self, r: Run) -> None:
        env = dict(os.environ)
        env["SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS"] = BOOTSTRAP
        env["SCENEOPS_STREAMING_KAFKA_TELEMETRY_TOPIC"] = self.topic
        log_path = self.out / f"{r.run_id}.log"
        proc = subprocess.Popen(
            [
                sys.executable,
                __file__,
                "capture-child",
                "--robot-id",
                self.robot_id,
                "--robot-run-id",
                r.run_id,
                "--output-root",
                str(self.out),
                "--channels-file",
                CHANNELS_FILE,
                "--idle-timeout-seconds",
                "120",
                "--start-position",
                f"{self.topic}:{r.partition}:{r.offset}",
            ],
            stdout=open(log_path, "wb"),
            stderr=subprocess.STDOUT,
            env=env,
        )
        r.state, r.t_capture_start = "capturing", time.time()
        self.caps[r.run_id] = (proc, log_path)

    def _reap(self) -> None:
        for run_id, (proc, log_path) in list(self.caps.items()):
            try:
                pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
            except ChildProcessError:
                pid, status, usage = proc.pid, 0, None
            if pid == 0:
                continue
            r = self.runs[run_id]
            r.t_done, r.capture_rc = time.time(), os.waitstatus_to_exitcode(status)
            if usage is not None:
                r.capture_cpu_s = usage.ru_utime + usage.ru_stime
            text = log_path.read_text(errors="replace")
            peak = re.search(r"capture_peak_rss_kb (\d+)", text)
            r.capture_rss_kb = int(peak.group(1)) if peak else None
            if r.capture_rc == 0 and "capture_summary" in text:
                r.state = "captured"
                self.captured_bytes += r.bytes
            else:
                lines = [ln for ln in text.strip().splitlines() if ln]
                r.capture_err = (lines[-1] if lines else "")[:300]
                r.state = "expired" if "ReplayExpiredError" in text else "failed"
            shutil.rmtree(self.out / run_id, ignore_errors=True)
            shutil.rmtree(self.out / ".partial" / run_id, ignore_errors=True)
            del self.caps[run_id]

    def _dispatch(self) -> None:
        while not self.stop.is_set():
            self._reap()
            if self.dispatch_enabled.is_set():
                with self.lock:
                    while len(self.caps) < self.slots:
                        nxt = next(
                            (i for i in self.order if self.runs[i].state == "waiting"),
                            None,
                        )
                        if nxt is None:
                            break
                        self._spawn_capture(self.runs[nxt])
            time.sleep(0.05)
        for proc, _ in self.caps.values():
            proc.kill()

    # sampler
    def _sample(self) -> None:
        import psutil

        marks = Watermarks(self.topic, self.partitions)
        prev = (_read_cpu(), _read_disk(), _read_net(), time.time())
        while not self.stop.is_set():
            time.sleep(1.0)
            now = time.time()
            cpu, disk, net = _read_cpu(), _read_disk(), _read_net()
            dt = now - prev[3]
            total = sum(cpu) - sum(prev[0]) or 1
            frac = [(a - b) / total for a, b in zip(cpu, prev[0])]
            free = os.statvfs(OUT_ROOT).f_bavail * os.statvfs(OUT_ROOT).f_frsize
            mem = psutil.virtual_memory().available
            with self.lock:
                states: dict[str, int] = {}
                for run_id in self.order:
                    states[self.runs[run_id].state] = (
                        states.get(self.runs[run_id].state, 0) + 1
                    )
                started = [
                    self.runs[i]
                    for i in self.order
                    if self.runs[i].state in ("waiting", "capturing", "streaming")
                    and self.runs[i].t_start > 0
                ]
                oldest = min(started, key=lambda r: r.t_start, default=None)
                produced, captured = self.cum_bytes, self.captured_bytes
            caps_rss = 0
            for proc, _ in list(self.caps.values()):
                try:
                    caps_rss += psutil.Process(proc.pid).memory_info().rss
                except Exception:  # noqa: BLE001
                    pass
            self.samples.append(
                {
                    "t": now,
                    "dt": dt,
                    "cpu_user": frac[0] + frac[1],
                    "cpu_sys": frac[2],
                    "cpu_iowait": frac[4],
                    "mem_avail": mem,
                    "free_disk": free,
                    "states": states,
                    "disk_w_bps": (disk[1] - prev[1][1]) * 512 / dt,
                    "disk_r_bps": (disk[0] - prev[1][0]) * 512 / dt,
                    "net_rx_bps": (net[0] - prev[2][0]) / dt,
                    "net_tx_bps": (net[1] - prev[2][1]) / dt,
                    "oldest_uncaptured_start": oldest.t_start if oldest else None,
                    "oldest_uncaptured_offset": oldest.offset if oldest else None,
                    "produced_cum": produced,
                    "captured_cum": captured,
                    "watermarks": marks.read(),
                    "capture_rss": caps_rss,
                    "n_caps": len(self.caps),
                }
            )
            prev = (cpu, disk, net, now)
            if free < MIN_FREE_DISK:
                self.abort_reason = f"free disk {free / 1e9:.2f} GB below the guard"
                self.stop.set()
            if mem < MIN_MEM_AVAILABLE:
                self.abort_reason = (
                    f"available memory {mem / 1e6:.0f} MB below the guard"
                )
                self.stop.set()
        marks.close()

    def start_threads(self) -> list[threading.Thread]:
        threads = [
            threading.Thread(target=f, daemon=True)
            for f in (self._pump, self._dispatch, self._sample)
        ]
        for t in threads:
            t.start()
        return threads

    def all_terminal(self) -> bool:
        with self.lock:
            return all(
                self.runs[i].state in ("captured", "expired", "failed")
                for i in self.order
            )

    def finish(self, threads: list[threading.Thread], meta: dict) -> None:
        self.stop.set()
        for t in threads:
            t.join(timeout=5)
        for proc in self.procs.values():
            if proc.is_alive():
                proc.terminate()
        self.results.mkdir(parents=True, exist_ok=True)
        (self.results / f"{self.name}.json").write_text(
            json.dumps(
                {
                    "meta": meta,
                    "abort": self.abort_reason,
                    "runs": [self.runs[i].__dict__ for i in self.order],
                    "samples": self.samples,
                }
            )
        )
        shutil.rmtree(self.out, ignore_errors=True)


def _read_cpu() -> list[int]:
    return [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]


def _read_disk() -> tuple[int, int]:
    for line in Path("/proc/diskstats").read_text().splitlines():
        f = line.split()
        if f[2] == "vda":
            return int(f[5]), int(f[9])
    return 0, 0


def _read_net() -> tuple[int, int]:
    for line in Path("/proc/net/dev").read_text().splitlines():
        if line.strip().startswith("eth0:"):
            f = line.split(":")[1].split()
            return int(f[0]), int(f[8])
    return 0, 0


# --------------------------------------------------------------------------- commands


def _meta(args: argparse.Namespace, **extra) -> dict:
    return {"args": {k: v for k, v in vars(args).items() if k != "fn"}, **extra}


def _topic_name(prefix: str) -> str:
    return f"bp.{prefix}.{int(time.time())}"


def cmd_drain(args: argparse.Namespace) -> None:
    msgs = build_workload(args)
    topic = _topic_name(args.name)
    create_topic(topic, args.partitions)
    lab = Lab(args.name, topic, args.partitions, args.slots, Path(args.results_dir))
    threads = lab.start_threads()
    t_prod0 = time.time()
    ids = [f"{args.name}-r{i:02d}" for i in range(args.runs)]
    for run_id in ids:
        lab.launch_stream(run_id, msgs, 0.0)
        if not args.concurrent_produce:
            lab.procs[run_id].join()
    for proc in lab.procs.values():
        proc.join()
    time.sleep(0.5)
    t_prod1 = time.time()
    time.sleep(args.settle)
    t_cap0 = time.time()
    lab.dispatch_enabled.set()
    deadline = time.time() + args.timeout
    while not lab.all_terminal() and time.time() < deadline and not lab.stop.is_set():
        time.sleep(0.2)
    t_cap1 = max((r.t_done or 0) for r in lab.runs.values()) or time.time()
    lab.finish(
        threads,
        _meta(
            args,
            topic=topic,
            per_run_bytes=workload_bytes(msgs),
            records_per_run=len(msgs),
            t_prod0=t_prod0,
            t_prod1=t_prod1,
            t_cap0=t_cap0,
            t_cap1=t_cap1,
        ),
    )
    delete_topic(topic)


def cmd_regime(args: argparse.Namespace) -> None:
    import random

    msgs = build_workload(args)
    topic = _topic_name(args.name)
    config = {
        key: str(value)
        for key, value in (
            ("retention.bytes", args.retention_bytes),
            ("retention.ms", args.retention_ms),
            ("segment.bytes", args.segment_bytes),
            ("segment.ms", args.segment_ms),
        )
        if value
    }
    create_topic(topic, args.partitions, config)
    lab = Lab(args.name, topic, args.partitions, args.slots, Path(args.results_dir))
    threads = lab.start_threads()
    lab.dispatch_enabled.set()
    while args.start_at and time.time() < args.start_at:
        time.sleep(0.05)
    t0 = time.time()
    rng = random.Random(args.seed)
    for i in range(args.count):
        jitter = (
            rng.uniform(-1, 1) * args.jitter * args.interval if args.jitter else 0.0
        )
        while time.time() < t0 + i * args.interval + jitter and not lab.stop.is_set():
            time.sleep(0.05)
        if lab.stop.is_set():
            break
        lab.launch_stream(f"{args.name}-r{i:03d}", msgs, args.stream_rate)
    deadline = time.time() + args.timeout
    while not lab.all_terminal() and time.time() < deadline and not lab.stop.is_set():
        time.sleep(0.5)
    lab.finish(
        threads,
        _meta(
            args,
            topic=topic,
            per_run_bytes=workload_bytes(msgs),
            records_per_run=len(msgs),
            t0=t0,
            topic_cfg=config,
        ),
    )
    delete_topic(topic)


def cmd_calibrate(args: argparse.Namespace) -> None:
    """The instant a record already past retention.ms is removed marks the broker's
    retention-check task; checks then repeat every log.retention.check.interval.ms."""
    from confluent_kafka import Producer

    topic = _topic_name("tick")
    create_topic(topic, 1, {"retention.ms": "1000", "segment.ms": "1000"})
    producer = Producer({"bootstrap.servers": BOOTSTRAP})
    producer.produce(topic, b"x", key=b"k")
    producer.flush(10)
    marks = Watermarks(topic, 1)
    produced = time.time()
    tick = None
    while time.time() - produced < 340 and tick is None:
        if marks.read()[0][0] >= 1:
            tick = time.time()
        time.sleep(0.5)
    marks.close()
    print(json.dumps({"produced": produced, "tick": tick}))
    delete_topic(topic)


def cmd_sweep(args: argparse.Namespace) -> None:
    bootstrap = args.bootstrap or BOOTSTRAP
    names = [
        t
        for t in _admin(bootstrap).list_topics(timeout=20).topics
        if t.startswith("bp.")
    ]
    for name in names:
        delete_topic(name, bootstrap)
    print("deleted", names)


def cmd_outage(args: argparse.Namespace) -> None:
    """One producer (the bridge's publish path) while the caller pauses the broker.
    Logs queue depth, memory and publish latency once a second."""
    import psutil
    from sceneops_streaming import (
        EnvelopeEncoding,
        KafkaTelemetryProducer,
        StreamingSettings,
        TelemetryEnvelope,
    )

    global BOOTSTRAP
    BOOTSTRAP = args.bootstrap
    msgs = (
        load_reference(None)
        if args.workload == "sensor"
        else synthetic_small(10000, args.payload, args.small_rate)
    )
    span = msgs[-1].t_ns + int(1e9 / 30)

    async def run() -> None:
        producer = KafkaTelemetryProducer(
            settings=StreamingSettings(bootstrap_servers=BOOTSTRAP), topic=args.topic
        )
        librdkafka = producer._producer  # queue depth is not exposed by the wrapper
        proc = psutil.Process()
        results = Path(args.results_dir)
        results.mkdir(parents=True, exist_ok=True)
        out = open(results / f"out-{args.name}.jsonl", "w", buffering=1)
        t0, wall0 = time.monotonic(), time.time()
        out.write(json.dumps({"phase": "start", "wall0": wall0}) + "\n")
        last, ok, nbytes, errors, latencies, first_error, i = t0, 0, 0, {}, [], None, 0
        while time.monotonic() - t0 < args.duration:
            cycle, k = divmod(i, len(msgs))
            m = msgs[k]
            if args.rate > 0:
                delay = (
                    t0 + (cycle * span + m.t_ns) / 1e9 / args.rate - time.monotonic()
                )
                if delay > 0.0005:
                    await asyncio.sleep(delay)
            envelope = TelemetryEnvelope(
                robot_id="r",
                robot_run_id="outage",
                channel=m.topic,
                message_type=m.mtype,
                source_timestamp_ns=1_700_000_000_000_000_000 + i,
                sequence_number=i,
                encoding=EnvelopeEncoding.ROS2_CDR,
                payload=m.data,
            )
            started = time.monotonic()
            try:
                await producer.publish(envelope)
                ok += 1
                nbytes += len(m.data) + FRAMING
            except Exception as exc:  # noqa: BLE001
                key = f"{type(exc).__name__}:{str(exc)[:60]}"
                errors[key] = errors.get(key, 0) + 1
                first_error = first_error or {
                    "t": time.time() - wall0,
                    "i": i,
                    "err": key,
                }
            latencies.append(time.monotonic() - started)
            i += 1
            if time.monotonic() - last >= 1.0:
                out.write(
                    json.dumps(
                        {
                            "t": round(time.time() - wall0, 2),
                            "offered": i,
                            "ok": ok,
                            "ok_bytes": nbytes,
                            "errs": sum(errors.values()),
                            "queued_msgs": len(librdkafka),
                            "rss_mb": round(proc.memory_info().rss / 1e6),
                            "lat_max_ms": round(max(latencies) * 1e3, 2),
                        }
                    )
                    + "\n"
                )
                latencies, last = [], time.monotonic()
        flush_started = time.monotonic()
        try:
            await producer.flush(args.flush_timeout)
            result = "ok"
        except Exception as exc:  # noqa: BLE001
            result = f"{type(exc).__name__}: {str(exc)[:120]}"
        final = {
            "phase": "flush",
            "result": result,
            "flush_s": round(time.monotonic() - flush_started, 2),
            "offered": i,
            "ok": ok,
            "errs": errors,
            "first_error": first_error,
            "queued_after": len(librdkafka),
        }
        out.write(json.dumps(final) + "\n")
        print(json.dumps(final))

    asyncio.run(run())


def cmd_capture_child(argv: list[str]) -> int:
    """`python -m sceneops_capture`, then report the process's own peak RSS (a child
    started by a large parent inherits the parent's high-water mark in rusage)."""
    from sceneops_capture.cli import main

    try:
        return main(argv)
    finally:
        hwm = re.search(r"VmHWM:\s+(\d+)", Path("/proc/self/status").read_text())
        print(f"capture_peak_rss_kb {hwm.group(1) if hwm else -1}", flush=True)


# --------------------------------------------------------------------------- summaries (stdlib only)


def _load(results_dir: str, name: str) -> dict:
    return json.loads((Path(results_dir) / f"{name}.json").read_text())


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def summarize_drain(args: argparse.Namespace) -> None:
    for name in args.names:
        d = _load(args.results_dir, name)
        m, runs = d["meta"], d["runs"]
        ok = [r for r in runs if r["state"] == "captured"]
        window = [s for s in d["samples"] if m["t_cap0"] <= s["t"] <= m["t_cap1"]]
        wall = m["t_cap1"] - m["t_cap0"]
        useful = sum(r["bytes"] for r in ok)
        rx = sum(
            s["net_rx_bps"] * s["dt"]
            for s in d["samples"]
            if m["t_cap0"] <= s["t"] <= m["t_cap1"] + 1
        )
        walls = [r["t_done"] - r["t_capture_start"] for r in ok]
        print(
            f"{name}: workload={m['args']['workload']} runs={len(ok)}/{len(runs)} slots={m['args']['slots']} "
            f"partitions={m['args']['partitions']} run={m['per_run_bytes'] / 1e6:.0f}MB/{m['records_per_run']}rec "
            f"abort={d['abort']}"
        )
        print(
            f"  drain wall={wall:.2f}s aggregate={useful / 1e6 / wall:.1f}MB/s {len(ok) / wall:.3f}runs/s "
            f"per-capture wall mean={_mean(walls):.2f}s ({_mean([r['bytes'] / w for r, w in zip(ok, walls)]) / 1e6:.1f}MB/s) "
            f"cpu={_mean([r['capture_cpu_s'] or 0 for r in ok]):.2f}s "
            f"rss={_mean([(r['capture_rss_kb'] or 0) / 1e3 for r in ok]):.0f}MB"
        )
        if window:
            print(
                f"  vm cpu={_mean([(s['cpu_user'] + s['cpu_sys']) * 100 for s in window]):.0f}% "
                f"iowait={_mean([s['cpu_iowait'] * 100 for s in window]):.0f}% "
                f"net_rx={_mean([s['net_rx_bps'] for s in window]) / 1e6:.0f}MB/s read-amplification={rx / useful:.2f}x "
                f"disk_w={_mean([s['disk_w_bps'] for s in window]) / 1e6:.0f}MB/s "
                f"free_min={min(s['free_disk'] for s in window) / 1e9:.2f}GB"
            )


def _fit_slope(xs: list[float], ys: list[float]) -> float:
    mx, my = _mean(xs), _mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / max(
        1e-9, sum((x - mx) ** 2 for x in xs)
    )


def summarize_regime(args: argparse.Namespace) -> None:
    print(
        "regime   C  offered  vs-isolated  peak_wait  peak_age_s  peak_backlog_MB  backlog_slope_MB/s  mean_wait_s  max_wait_s  drain_end_s"
    )
    for name in args.names:
        d = _load(args.results_dir, name)
        m, runs, samples = d["meta"], d["runs"], d["samples"]
        a, t0 = m["args"], m["t0"]
        offered = m["per_run_bytes"] / a["interval"] / 1e6
        arrivals_end = max(r["t_start"] for r in runs) - t0
        during = [s for s in samples if 0 <= s["t"] - t0 <= arrivals_end]
        slope = _fit_slope(
            [s["t"] - t0 for s in during],
            [(s["produced_cum"] - s["captured_cum"]) / 1e6 for s in during],
        )
        ages = [
            s["t"] - s["oldest_uncaptured_start"]
            for s in samples
            if s["oldest_uncaptured_start"]
        ]
        waits = [
            r["t_capture_start"] - r["t_start"] for r in runs if r["t_capture_start"]
        ]
        end = max(r["t_done"] for r in runs if r["t_done"]) - t0
        print(
            f"{name:8s} {a['slots']:2d} {offered:8.0f} {offered / (ISOLATED_MU_MBPS * a['slots']):11.2f} "
            f"{max(s['states'].get('waiting', 0) for s in samples):10d} {max(ages):11.1f} "
            f"{max((s['produced_cum'] - s['captured_cum']) / 1e6 for s in samples):16.0f} {slope:19.1f} "
            f"{_mean(waits):12.1f} {max(waits):11.1f} {end:12.1f}"
        )


def _headroom_cells(x: tuple | None) -> str:
    return f"{x[1]:9d} {x[2]:6.1f} {x[3] / 1e6:14.0f}" if x else " " * 31


def summarize_headroom(args: argparse.Namespace) -> None:
    """Replay headroom of the oldest uncaptured run, from the logged samples:
    time = retention.ms - age; size = (retention.bytes - segment.bytes - bytes appended since its
    RUN_START) / recent ingest rate. Defaults are the local broker's (24 h, 20 GiB, 1 GiB segments)."""
    print(
        "run       first loss@  headroom<=0@  | at loss: uncaptured  age_s  bytes_since_MB | run peak: uncaptured  age_s  bytes_since_MB  min_headroom_s"
    )
    for name in args.names:
        d = _load(args.results_dir, name)
        m, runs, samples = d["meta"], d["runs"], d["samples"]
        cfg, t0 = m.get("topic_cfg") or {}, m["t0"]
        retention_s = int(cfg.get("retention.ms", 86_400_000)) / 1000
        cap = int(cfg.get("retention.bytes", 20 * GIB)) - int(
            cfg.get("segment.bytes", GIB)
        )
        series, ingest, prev = [], 0.0, (samples[0]["t"], samples[0]["produced_cum"])
        for s in samples:
            if s["t"] > prev[0]:
                ingest = 0.7 * ingest + 0.3 * (s["produced_cum"] - prev[1]) / (
                    s["t"] - prev[0]
                )
                prev = (s["t"], s["produced_cum"])
            open_runs = [
                r
                for r in runs
                if r["t_start"]
                and r["t_start"] <= s["t"]
                and (r["t_done"] is None or r["t_done"] > s["t"])
            ]
            oldest = min(open_runs, key=lambda r: r["t_start"], default=None)
            age = s["t"] - oldest["t_start"] if oldest else 0.0
            since = s["produced_cum"] - oldest["cum_at_start"] if oldest else 0
            size_s = (cap - since) / ingest if ingest > 1e6 else float("inf")
            series.append(
                (
                    s["t"] - t0,
                    len(open_runs),
                    age,
                    since,
                    min(retention_s - age, size_s),
                    cap - since <= 0 or retention_s - age <= 0,
                )
            )
        losses = [
            r["t_done"] - t0 for r in runs if r["state"] == "expired" and r["t_done"]
        ]
        loss = min(losses) if losses else None
        zero = next((x for x in series if x[5]), None)
        at_loss = min(series, key=lambda x: abs(x[0] - loss)) if loss else None
        print(
            f"{name:9s} {('%.0f' % loss) if loss else '-':>11s} {('%.0f' % zero[0]) if zero else '-':>13s}  | {_headroom_cells(at_loss)} | "
            f"{max(x[1] for x in series):17d} {max(x[2] for x in series):6.1f} {max(x[3] for x in series) / 1e6:14.0f} "
            f"{min(x[4] for x in series if x[0] > 0):15.0f}"
        )


# --------------------------------------------------------------------------- main


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "capture-child":
        return cmd_capture_child(sys.argv[2:])

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--results-dir", default=str(RESULTS))
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--name", required=True)
        p.add_argument(
            "--workload", default="ref20", help="ref20 | ref<seconds> | small"
        )
        p.add_argument(
            "--records", type=int, default=20000, help="small: records per run"
        )
        p.add_argument("--payload", type=int, default=64, help="small: payload bytes")
        p.add_argument(
            "--small-rate", type=float, default=370.0, help="small: source records/s"
        )
        p.add_argument(
            "--slots", type=int, default=1, help="concurrent Capture processes"
        )
        p.add_argument("--partitions", type=int, default=1)
        p.add_argument("--timeout", type=float, default=600)

    d = sub.add_parser("drain")
    common(d)
    d.add_argument("--runs", type=int, required=True)
    d.add_argument(
        "--concurrent-produce",
        action="store_true",
        help="interleave the runs on the topic",
    )
    d.add_argument("--settle", type=float, default=2.0)
    d.set_defaults(fn=cmd_drain)

    r = sub.add_parser("regime")
    common(r)
    r.add_argument("--count", type=int, required=True, help="runs to start")
    r.add_argument(
        "--interval", type=float, required=True, help="seconds between run starts"
    )
    r.add_argument("--jitter", type=float, default=0.0, help="fraction of the interval")
    r.add_argument(
        "--stream-rate", type=float, default=1.0, help="x source real time; 0 = unpaced"
    )
    r.add_argument("--seed", type=int, default=7)
    r.add_argument(
        "--start-at",
        type=float,
        default=0.0,
        help="epoch second at which arrivals begin",
    )
    r.add_argument("--retention-bytes", type=int, default=0)
    r.add_argument("--retention-ms", type=int, default=0)
    r.add_argument("--segment-bytes", type=int, default=0)
    r.add_argument("--segment-ms", type=int, default=0)
    r.set_defaults(fn=cmd_regime)

    sub.add_parser("calibrate").set_defaults(fn=cmd_calibrate)

    sw = sub.add_parser("sweep")
    sw.add_argument("--bootstrap")
    sw.set_defaults(fn=cmd_sweep)

    o = sub.add_parser("outage")
    o.add_argument("--name", required=True)
    o.add_argument("--bootstrap", required=True)
    o.add_argument("--topic", required=True)
    o.add_argument("--workload", choices=["sensor", "small"], default="sensor")
    o.add_argument("--rate", type=float, default=1.0)
    o.add_argument("--duration", type=float, default=150)
    o.add_argument("--flush-timeout", type=float, default=120)
    o.add_argument("--payload", type=int, default=64)
    o.add_argument("--small-rate", type=float, default=370.0)
    o.set_defaults(fn=cmd_outage)

    s = sub.add_parser("summarize")
    s.add_argument("kind", choices=["drain", "regime", "headroom"])
    s.add_argument("names", nargs="+")
    s.set_defaults(
        fn=lambda a: {
            "drain": summarize_drain,
            "regime": summarize_regime,
            "headroom": summarize_headroom,
        }[a.kind](a)
    )

    args = ap.parse_args()
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
