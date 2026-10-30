"""A tiny hand-built recording in the reference corpus' shape, for tests.

Messages are CDR-encoded and framed as MCAP records here, from the format
specifications, so the verifier's readers are tested against bytes they did
not write.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import zstandard

T0 = 1_532_402_927_604_844_000


class Cdr:
    def __init__(self) -> None:
        self.buf = bytearray(b"\x00\x01\x00\x00")

    def _align(self, n: int) -> None:
        while (len(self.buf) - 4) % n:
            self.buf.append(0)

    def _p(self, fmt: str, size: int, value: Any) -> Cdr:
        self._align(size)
        self.buf += struct.pack("<" + fmt, value)
        return self

    def i32(self, v: int) -> Cdr:
        return self._p("i", 4, v)

    def u32(self, v: int) -> Cdr:
        return self._p("I", 4, v)

    def u8(self, v: int) -> Cdr:
        return self._p("B", 1, v)

    def f32(self, v: float) -> Cdr:
        return self._p("f", 4, v)

    def f64(self, v: float) -> Cdr:
        return self._p("d", 8, v)

    def string(self, s: str) -> Cdr:
        raw = s.encode() + b"\x00"
        self.u32(len(raw))
        self.buf += raw
        return self

    def octets(self, b: bytes) -> Cdr:
        self.u32(len(b))
        self.buf += b
        return self

    def header(self, stamp: int, frame: str) -> Cdr:
        return self.i32(stamp // 1_000_000_000).u32(stamp % 1_000_000_000).string(frame)

    def bytes(self) -> bytes:
        return bytes(self.buf)


def image(stamp: int, data: bytes) -> bytes:
    return Cdr().header(stamp, "cam_front").string("jpeg").octets(data).bytes()


def cloud(stamp: int, data: bytes) -> bytes:
    c = Cdr().header(stamp, "lidar_top").u32(1).u32(len(data) // 20).u32(1)
    c.string("x").u32(0).u8(7).u32(1)
    c.u8(0).u32(20).u32(len(data)).octets(data).u8(0)
    return c.bytes()


def tf(transforms: list[tuple[int, str, str, list[float], list[float]]]) -> bytes:
    c = Cdr().u32(len(transforms))
    for stamp, parent, child, t, q in transforms:
        c.header(stamp, parent).string(child)
        for v in t:
            c.f64(v)
        w, x, y, z = q
        for v in (x, y, z, w):
            c.f64(v)
    return c.bytes()


K = [1266.4, 0.0, 816.2, 0.0, 1266.4, 491.5, 0.0, 0.0, 1.0]


def camera_info(stamp: int) -> bytes:
    c = Cdr().header(stamp, "cam_front").u32(900).u32(1600).string("plumb_bob").u32(5)
    for _ in range(5):
        c.f64(0.0)
    for v in (
        K + [1, 0, 0, 0, 1, 0, 0, 0, 1] + K[:3] + [0.0] + K[3:6] + [0.0] + K[6:] + [0.0]
    ):
        c.f64(float(v))
    c.u32(0).u32(0).u32(0).u32(0).u32(0).u32(0).u8(0)
    return c.bytes()


def odom(stamp: int, x: float, y: float, vx: float) -> bytes:
    c = Cdr().header(stamp, "odom").string("")
    for v in (x, y, 0.0, 0.0, 0.0, 0.0, 1.0):
        c.f64(v)
    for _ in range(36):
        c.f64(0.0)
    for v in (vx, 0.0, 0.0, 0.0, 0.0, 0.0):
        c.f64(v)
    for _ in range(36):
        c.f64(0.0)
    return c.bytes()


def battery(stamp: int, pct: float) -> bytes:
    c = Cdr().header(stamp, "")
    for v in (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, pct):
        c.f32(v)
    return c.bytes()


def string(payload: dict[str, Any]) -> bytes:
    return Cdr().string(json.dumps(payload)).bytes()


SCHEMAS = {
    "/tf_static": "tf2_msgs/msg/TFMessage",
    "/tf": "tf2_msgs/msg/TFMessage",
    "/camera/front/image/compressed": "sensor_msgs/msg/CompressedImage",
    "/camera/front/camera_info": "sensor_msgs/msg/CameraInfo",
    "/lidar/top/points": "sensor_msgs/msg/PointCloud2",
    "/vehicle/odom": "nav_msgs/msg/Odometry",
    "/vehicle/status": "sensor_msgs/msg/BatteryState",
    "/vehicle/control": "std_msgs/msg/String",
    "/mission/status": "std_msgs/msg/String",
}


def messages(
    seed: str = "a", second_task: bool = False
) -> list[tuple[str, int, bytes]]:
    """(topic, log_time, data) in write order."""
    ms = 1_000_000
    static = [(T0, "base_link", "cam_front", [1.7, 0.01, 1.5], [0.5, -0.5, 0.5, -0.5]),
              (T0, "base_link", "lidar_top", [0.94, 0.0, 1.84], [0.7, 0.0, 0.0, -0.7])]  # fmt: skip
    out = [("/mission/status", T0, string({"mission_id": f"mission-{seed}", "operation_state": "running", "source_timestamp_ns": T0})),
           ("/tf_static", T0, tf(static))]  # fmt: skip
    for i in range(3):
        t = T0 + 10 * ms + i * 50 * ms
        out += [("/camera/front/image/compressed", t, image(t, f"\xff\xd8\xff{seed}{i}".encode("latin-1"))),
                ("/camera/front/camera_info", t, camera_info(t))]  # fmt: skip
    for i in range(2):
        t = T0 + 20 * ms + i * 50 * ms
        out.append(
            ("/lidar/top/points", t, cloud(t, bytes([i]) * 40 + seed.encode() * 20))
        )
    for i in range(4):
        t = T0 + i * 30 * ms
        out.append(
            (
                "/tf",
                t,
                tf(
                    [
                        (
                            t,
                            "map",
                            "base_link",
                            [410.0 + i, 1179.0, 0.0],
                            [0.57, 0.0, 0.0, -0.82],
                        )
                    ]
                ),
            )
        )
    for i in range(2):
        t = T0 + 5 * ms + i * 70 * ms
        out.append(("/vehicle/odom", t, odom(t, 411.0 + i, 1180.0, 8.5 + i)))
    t = T0 + 40 * ms
    out.append(("/vehicle/status", t, battery(t, 0.91)))
    out.append(
        (
            "/vehicle/control",
            t,
            string(
                {"steering": 3.0, "throttle": 0, "brake": 0, "source_timestamp_ns": t}
            ),
        )
    )
    end = T0 + 200 * ms
    out.append(
        (
            "/mission/status",
            end,
            string(
                {
                    "mission_id": f"mission-{seed}",
                    "operation_state": "completed",
                    "source_timestamp_ns": end,
                }
            ),
        )
    )
    if second_task:
        for state, t in (("running", end + ms), ("completed", end + 2 * ms)):
            out.append(
                (
                    "/mission/status",
                    t,
                    string(
                        {
                            "mission_id": f"mission-{seed}",
                            "operation_state": state,
                            "source_timestamp_ns": t,
                        }
                    ),
                )
            )
    return sorted(out, key=lambda m: m[1])


def _record(op: int, body: bytes) -> bytes:
    return bytes([op]) + struct.pack("<Q", len(body)) + body


def _s(text: str) -> bytes:
    raw = text.encode()
    return struct.pack("<I", len(raw)) + raw


def mcap(msgs: list[tuple[str, int, bytes]]) -> bytes:
    topics = sorted({t for t, _, _ in msgs})
    ids = {t: i + 1 for i, t in enumerate(topics)}
    inner = b""
    for t in topics:
        inner += _record(
            0x03,
            struct.pack("<H", ids[t])
            + _s(SCHEMAS[t])
            + _s("ros2msg")
            + struct.pack("<I", 0),
        )
        inner += _record(
            0x04,
            struct.pack("<HH", ids[t], ids[t])
            + _s(t)
            + _s("cdr")
            + struct.pack("<I", 0),
        )
    seq: dict[str, int] = {}
    for t, log_time, data in msgs:
        seq[t] = seq.get(t, 0) + 1
        inner += _record(
            0x05, struct.pack("<HIQQ", ids[t], seq[t], log_time, log_time) + data
        )
    compressed = zstandard.ZstdCompressor().compress(inner)
    chunk = (
        struct.pack("<QQQI", msgs[0][1], msgs[-1][1], len(inner), 0)
        + _s("zstd")
        + struct.pack("<Q", len(compressed))
        + compressed
    )
    body = (
        _record(0x01, _s("") + _s(""))
        + _record(0x06, chunk)
        + _record(0x0F, struct.pack("<I", 0))
        + _record(0x02, b"\x00" * 20)
    )
    return b"\x89MCAP0\r\n" + body + b"\x89MCAP0\r\n"


def write_corpus(
    root: Path, fixtures: dict[str, bytes], contract: dict[str, Any]
) -> tuple[Path, Path]:
    """A corpus directory (contract, corpus, lock) and a recording cache for the fixtures."""
    corpus_dir = root / "config"
    cache = root / "cache"
    (cache / "tiny" / "recordings").mkdir(parents=True)
    corpus_dir.mkdir()
    lock: dict[str, Any] = {"corpus_id": "tiny", "fixtures": {}}
    for fid, data in fixtures.items():
        (cache / "tiny" / "recordings" / f"{fid}-000000000000.mcap").write_bytes(data)
        counts: dict[str, int] = {}
        for t, _, _ in [m for m in _decode_topics(data)]:
            counts[t] = counts.get(t, 0) + 1
        lock["fixtures"][fid] = {
            "recording": {
                "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "message_count": sum(counts.values()),
                "topic_counts": dict(sorted(counts.items())),
            }
        }
    (corpus_dir / "corpus.lock.json").write_text(json.dumps(lock))
    (corpus_dir / "corpus.json").write_text(
        json.dumps(
            {
                "corpus_id": "tiny",
                "fixtures": [{"fixture_id": f, "source_unit": f} for f in fixtures],
                "scopes": {"all": ["*"]},
            }
        )
    )
    (corpus_dir / "reference_contract.json").write_text(json.dumps(contract))
    return corpus_dir, cache


def _decode_topics(data: bytes):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from verification.mcap_records import iter_messages_from_bytes

    return [(m.topic, m.log_time, m.data) for m in iter_messages_from_bytes(data)]


def contract(fixtures: list[str]) -> dict[str, Any]:
    def mode(name: str, recording: str) -> dict[str, Any]:
        return {
            "baseline_id": name,
            "robot_id": f"robot-{name}",
            "dataset_id": f"sceneops-{name}",
            "dataset_version": "baseline",
            "run_id_template": "run-{baseline_id}-{fixture_id}",
            "recording": recording,
        }

    return {
        "schema": "sceneops.reference_contract/2",
        "contract_version": 2,
        "corpus": {"corpus_id": "tiny", "scope": "all"},
        "fixtures": [{"fixture_id": f} for f in fixtures],
        "build_configs": {
            "scene": "config/baselines/scene_build_config.json",
            "episode": "config/baselines/episode_build_config.json",
        },
        "expectations": "reference_expectations.json",
        "ingestion_modes": {
            "recording_import": mode("ref-tiny", "locked"),
            "streaming_acquisition": mode("stream-ref-tiny", "captured"),
        },
        "units": {
            "scenes_per_robot_run": 1,
            "episodes_per_robot_run": 1,
            "revisions_per_unit": 1,
        },
        "expected_totals": {
            "fixtures": len(fixtures),
            "robot_runs": 2 * len(fixtures),
            "scenes": 2 * len(fixtures),
            "episodes": 2 * len(fixtures),
        },
    }
