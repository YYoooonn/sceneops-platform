"""The real ROS 2 bridge and Capture against a Kafka broker that stops answering.

Per scenario: a throwaway broker container joins the compose network; the real bridge
``main()`` (librdkafka's queue cut to 2,000 messages so no scenario needs a gigabyte)
bridges synthetic ``/vehicle/odom`` messages at a fixed rate while the broker is frozen
and thawed with ``docker pause`` on a schedule; then a pinned Capture reads the run
back. Printed per scenario (JSON): the bridge's exit code and summary, what the broker
holds (telemetry count, holes, control events, the RUN_END count) and why Capture
accepted or refused the run.

``--old-producer`` restores the earlier ``_produce_sync`` (produce, then poll(0)) and
``--old-exit`` the earlier exit decision (``failed_count`` only), to measure the
difference on the same workload.

    .venv/bin/python tools/benchmarks/streaming/experiment_producer_outage.py SCENARIO

Needs Docker, the ``streaming-bridge`` and ``capture`` images (``make
streaming-ros-build``) and the broker image. It never touches the compose broker.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient

from sceneops_streaming import (
    is_control_envelope,
    parse_run_end_telemetry_count,
    parse_run_event,
)
from sceneops_streaming.wire import decode_envelope

REPO = Path(__file__).resolve().parents[3]
ROS_SCRIPTS = Path(__file__).resolve().parent / "producer_outage_ros"
COMPOSE = ["docker", "compose", "--env-file", ".env.local", "--profile", "streaming"]
NETWORK = "sceneops-network"
BROKER_IMAGE = "apache/kafka:3.9.2"
TOPIC = "sceneops.robot.telemetry.v1"
QUEUE_MESSAGES = 2000

# name -> (messages, rate msg/s, outages [(freeze_at_s, thaw_at_s | None)], queue)
# Times are seconds after the publisher's first message.
SCENARIOS: dict[str, dict] = {
    "healthy": dict(count=6000, rate=1000, outages=[]),
    # 1 s outage: the queue does not fill.
    "short": dict(count=6000, rate=1000, outages=[(1.5, 2.5)]),
    # 3 s outage: the queue fills and the wait outlasts the outage.
    "absorbed": dict(count=6000, rate=1000, outages=[(1.5, 4.5)]),
    # 7.5 s outage: records are refused while the queue is full.
    "medium": dict(count=8000, rate=1000, outages=[(1.5, 9.0)]),
    # The broker never returns.
    "never": dict(count=8000, rate=1000, outages=[(1.5, None)]),
    # SIGTERM at 15 s into an outage that never ends.
    "never-term": dict(count=8000, rate=1000, outages=[(1.5, None)], term_at=15),
    # The queue never fills; the broker is back only after the first shutdown flush
    # timed out, during close()'s second flush.
    "tail-stall": dict(count=8000, rate=1000, outages=[(1.5, 25.0)], queue=100_000),
}


def sh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, cwd=REPO)


class Broker:
    """A disposable broker container: reachable from the compose network as
    ``<name>:9092`` and from the host on ``localhost:<port>``."""

    def __init__(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.name = f"fault-broker-{uuid.uuid4().hex[:6]}"

    def start(self) -> None:
        env = {
            "KAFKA_NODE_ID": "1",
            "KAFKA_PROCESS_ROLES": "broker,controller",
            "KAFKA_LISTENERS": "PLAINTEXT://:9092,PLAINTEXT_HOST://:9094,CONTROLLER://:9093",
            "KAFKA_ADVERTISED_LISTENERS": f"PLAINTEXT://{self.name}:9092,PLAINTEXT_HOST://localhost:{self.port}",
            "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT",
            "KAFKA_CONTROLLER_LISTENER_NAMES": "CONTROLLER",
            "KAFKA_INTER_BROKER_LISTENER_NAME": "PLAINTEXT",
            "KAFKA_CONTROLLER_QUORUM_VOTERS": "1@localhost:9093",
            "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
            "KAFKA_AUTO_CREATE_TOPICS_ENABLE": "true",
            "KAFKA_NUM_PARTITIONS": "1",
            "KAFKA_LOG_MESSAGE_TIMESTAMP_TYPE": "LogAppendTime",
        }
        command = [
            "docker", "run", "-d", "--rm", "--name", self.name,
            "--network", NETWORK, "-p", f"127.0.0.1:{self.port}:9094",
        ]  # fmt: skip
        for key, value in env.items():
            command += ["-e", f"{key}={value}"]
        started = subprocess.run(
            command + [BROKER_IMAGE], capture_output=True, text=True
        )
        if started.returncode != 0:
            raise RuntimeError(started.stderr)
        admin = AdminClient({"bootstrap.servers": f"localhost:{self.port}"})
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                admin.list_topics(timeout=2)
                return
            except Exception:
                time.sleep(1)
        raise RuntimeError("broker did not become ready")

    def freeze(self) -> None:
        sh("docker", "pause", self.name)

    def thaw(self) -> None:
        sh("docker", "unpause", self.name)

    def stop(self) -> None:
        sh("docker", "rm", "-f", self.name)


def run_container(
    name: str,
    service: str,
    args: list[str],
    broker: Broker,
    *,
    env: tuple[str, ...] = (),
    entrypoint: str | None = None,
    detach: bool = True,
) -> None:
    command = COMPOSE + ["run", "--no-deps", "-T", "--name", name]
    if detach:
        command.append("-d")
    command += ["-e", f"SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS={broker.name}:9092"]
    for item in env:
        command += ["-e", item]
    command += ["-v", f"{ROS_SCRIPTS}:/scratch:ro"]
    if entrypoint:
        command += ["--entrypoint", entrypoint]
    result = subprocess.run(
        command + [service] + args, capture_output=True, text=True, cwd=REPO
    )
    if result.returncode != 0 and detach:
        raise RuntimeError(result.stderr[-500:])


def logs(name: str) -> str:
    result = sh("docker", "logs", name)
    return result.stdout + result.stderr


def wait_exit(name: str, timeout: float) -> int | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, code = sh(
            "docker", "inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", name
        ).stdout.split()
        if status == "exited":
            return int(code)
        time.sleep(0.5)
    return None


def broker_view(broker: Broker, run: str) -> dict:
    consumer = Consumer(
        {
            "bootstrap.servers": f"localhost:{broker.port}",
            "group.id": uuid.uuid4().hex,
            "enable.auto.commit": False,
            "enable.partition.eof": True,
        }
    )
    consumer.assign([TopicPartition(TOPIC, 0, 0)])
    sequences: list[int] = []
    events: list[str] = []
    run_end_count = None
    while True:
        message = consumer.poll(10)
        if message is None:
            break
        if message.error():
            if message.error().code() == -191:  # _PARTITION_EOF
                break
            continue
        envelope = decode_envelope(headers=message.headers(), value=message.value())
        if envelope.robot_run_id != run:
            continue
        if is_control_envelope(envelope):
            event = parse_run_event(envelope)
            events.append(event.value)
            if event.value == "RUN_END":
                run_end_count = parse_run_end_telemetry_count(envelope)
        else:
            sequences.append(envelope.sequence_number)
    consumer.close()
    missing = set(range(max(sequences) + 1)) - set(sequences) if sequences else set()
    return {
        "telemetry_on_broker": len(sequences),
        "highest_sequence": max(sequences) if sequences else None,
        "holes": len(missing),
        "ordered": sequences == sorted(sequences),
        "control_events": events,
        "run_end_count": run_end_count,
    }


def run_scenario(
    name: str,
    *,
    count: int,
    rate: float,
    outages: list[tuple[float, float | None]],
    term_at: float | None = None,
    queue: int = QUEUE_MESSAGES,
    old_producer: bool = False,
    old_exit: bool = False,
) -> dict:
    tag = uuid.uuid4().hex[:6]
    run = f"run-{name}-{tag}"
    bridge, publisher, capture = f"br-{tag}", f"pb-{tag}", f"cp-{tag}"
    result: dict = {
        "scenario": name,
        "messages": count,
        "rate_per_second": rate,
        "outages": outages,
        "queue_messages": queue,
        "old_producer": old_producer,
        "old_exit": old_exit,
    }
    broker = Broker()
    broker.start()
    try:
        run_container(
            bridge,
            "streaming-bridge",
            [
                "python3",
                "/scratch/bridge_wrapper.py",
                "--robot-id",
                "r",
                "--robot-run-id",
                run,
                "--exit-after-idle-seconds",
                "5",
                "--publish-timeout-seconds",
                "5",
            ],  # fmt: skip
            broker,
            env=(
                f"QUEUE_MESSAGES={queue}",
                f"OLD_PRODUCE={int(old_producer)}",
                f"OLD_EXIT={int(old_exit)}",
            ),
            entrypoint="/ros_entrypoint.sh",
        )
        deadline = time.time() + 90
        start_position = None
        while time.time() < deadline and start_position is None:
            match = re.search(r"run_started (\{.*\})", logs(bridge))
            if match:
                start_position = json.loads(match.group(1))["start_position"]
            else:
                time.sleep(0.5)
        if start_position is None:
            raise RuntimeError(
                "RUN_START was not acknowledged:\n" + logs(bridge)[-800:]
            )
        run_container(
            publisher,
            "streaming-bridge",
            [
                "python3",
                "/scratch/odom_pub.py",
                "--rate",
                str(rate),
                "--count",
                str(count),
            ],
            broker,
            entrypoint="/ros_entrypoint.sh",
        )
        deadline = time.time() + 60
        while "subscribers" not in logs(publisher) and time.time() < deadline:
            time.sleep(0.2)

        origin = time.monotonic()

        def at(seconds: float, action) -> None:
            delay = origin + seconds - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            action()

        schedule = []
        for freeze_at, thaw_at in outages:
            schedule.append((freeze_at, broker.freeze))
            if thaw_at is not None:
                schedule.append((thaw_at, broker.thaw))
        if term_at is not None:
            schedule.append(
                (term_at, lambda: sh("docker", "kill", "-s", "TERM", bridge))
            )
        threads = [threading.Thread(target=at, args=item) for item in schedule]
        for thread in threads:
            thread.start()
        result["bridge_exit_code"] = wait_exit(bridge, 400)
        result["bridge_seconds"] = round(time.monotonic() - origin, 1)
        for thread in threads:
            thread.join()

        bridge_log = logs(bridge)
        summary = re.search(r"bridge_summary (\{.*\})", bridge_log)
        result["bridge_summary"] = json.loads(summary.group(1)) if summary else None
        result["run_end_withheld"] = "RUN_END withheld" in bridge_log
        result["bridge_errors"] = sorted(
            {
                re.sub(r"\d+", "N", line.split("]: ", 1)[-1])[:110]
                for line in bridge_log.splitlines()
                if "ERROR" in line or "[ERROR]" in line
            }
        )[:6]
        broker.thaw()  # Capture reads what the broker holds
        result["broker"] = broker_view(broker, run)
        run_container(
            capture,
            "capture",
            [
                "--robot-id",
                "r",
                "--robot-run-id",
                run,
                "--output-root",
                "/tmp/capture",
                "--idle-timeout-seconds",
                "15",
                "--start-position",
                start_position,
            ],  # fmt: skip
            broker,
            detach=False,
        )
        result["capture_exit_code"] = int(
            sh("docker", "inspect", "-f", "{{.State.ExitCode}}", capture).stdout.strip()
        )
        result["capture_last_line"] = logs(capture).strip().splitlines()[-1][:200]
    finally:
        for container in (bridge, publisher, capture):
            sh("docker", "rm", "-f", container)
        broker.stop()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--old-producer", action="store_true")
    parser.add_argument("--old-exit", action="store_true")
    args = parser.parse_args()
    outcome = run_scenario(
        args.scenario,
        **SCENARIOS[args.scenario],
        old_producer=args.old_producer,
        old_exit=args.old_exit,
    )
    print(json.dumps(outcome, indent=1))


if __name__ == "__main__":
    main()
