"""A minimal MCAP record reader, written from the MCAP specification.

The verifier reads recordings without the ``mcap`` package that the SceneOps
recording reader uses, so a defect shared by the production reader and this
check cannot hide itself. Only what verification needs is implemented: the
data section is scanned record by record in file order (chunks are
decompressed one at a time), and every message is yielded with its channel's
topic and schema. Summary records, indexes and CRCs are not trusted: counts
and order come from the messages themselves.
"""

from __future__ import annotations

import mmap
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"\x89MCAP0\r\n"

OP_SCHEMA = 0x03
OP_CHANNEL = 0x04
OP_MESSAGE = 0x05
OP_CHUNK = 0x06
OP_FOOTER = 0x02
OP_DATA_END = 0x0F


class McapFormatError(ValueError):
    """The bytes are not a well-formed MCAP file."""


@dataclass(frozen=True)
class Schema:
    schema_id: int
    name: str
    encoding: str
    data: bytes


@dataclass(frozen=True)
class Channel:
    channel_id: int
    schema_id: int
    topic: str
    message_encoding: str


@dataclass(frozen=True)
class Message:
    topic: str
    schema_name: str
    message_encoding: str
    sequence: int
    log_time: int
    publish_time: int
    data: bytes
    # Position among all messages of the file, in file order.
    file_index: int


class _Cursor:
    def __init__(self, data: bytes | memoryview) -> None:
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if end > len(self.data):
            raise McapFormatError("record truncated")
        out = bytes(self.data[self.pos : end])
        self.pos = end
        return out

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def string(self) -> str:
        return self.take(self.u32()).decode("utf-8")

    def rest(self) -> bytes:
        out = bytes(self.data[self.pos :])
        self.pos = len(self.data)
        return out


def _decompress(compression: str, payload: bytes, size: int) -> bytes:
    if compression == "":
        return payload
    if compression == "zstd":
        import zstandard

        return zstandard.ZstdDecompressor().decompress(payload, max_output_size=size)
    if compression == "lz4":
        import lz4.frame

        return lz4.frame.decompress(payload)
    raise McapFormatError(f"unsupported chunk compression {compression!r}")


def _records(
    data: bytes | memoryview, start: int, stop: int
) -> Iterator[tuple[int, memoryview]]:
    view = memoryview(data)
    pos = start
    while pos < stop:
        if pos + 9 > stop:
            raise McapFormatError("record header truncated")
        opcode = view[pos]
        (length,) = struct.unpack_from("<Q", view, pos + 1)
        body_start = pos + 9
        body_end = body_start + length
        if body_end > stop:
            raise McapFormatError(f"record 0x{opcode:02x} truncated")
        yield opcode, view[body_start:body_end]
        pos = body_end


class _Scanner:
    def __init__(self) -> None:
        self.schemas: dict[int, Schema] = {}
        self.channels: dict[int, Channel] = {}
        self.count = 0
        self.done = False

    def visit(self, opcode: int, body: memoryview) -> Iterator[Message]:
        cursor = _Cursor(body)
        if opcode == OP_SCHEMA:
            schema_id = cursor.u16()
            name = cursor.string()
            encoding = cursor.string()
            schema = Schema(schema_id, name, encoding, cursor.take(cursor.u32()))
            if self.schemas.setdefault(schema_id, schema) != schema:
                raise McapFormatError(f"schema {schema_id} is redefined")
        elif opcode == OP_CHANNEL:
            channel_id = cursor.u16()
            schema_id = cursor.u16()
            topic = cursor.string()
            encoding = cursor.string()
            channel = Channel(channel_id, schema_id, topic, encoding)
            if self.channels.setdefault(channel_id, channel) != channel:
                raise McapFormatError(f"channel {channel_id} is redefined")
        elif opcode == OP_MESSAGE:
            channel_id = cursor.u16()
            sequence = cursor.u32()
            log_time = cursor.u64()
            publish_time = cursor.u64()
            channel = self.channels.get(channel_id)
            if channel is None:
                raise McapFormatError(f"message on undeclared channel {channel_id}")
            schema = self.schemas.get(channel.schema_id)
            yield Message(
                topic=channel.topic,
                schema_name=schema.name if schema else "",
                message_encoding=channel.message_encoding,
                sequence=sequence,
                log_time=log_time,
                publish_time=publish_time,
                data=cursor.rest(),
                file_index=self.count,
            )
            self.count += 1
        elif opcode == OP_CHUNK:
            cursor.u64()  # message_start_time
            cursor.u64()  # message_end_time
            uncompressed_size = cursor.u64()
            cursor.u32()  # uncompressed_crc
            compression = cursor.string()
            records = cursor.take(cursor.u64())
            inner = _decompress(compression, records, uncompressed_size)
            if len(inner) != uncompressed_size:
                raise McapFormatError("chunk size differs from its declared size")
            for inner_opcode, inner_body in _records(inner, 0, len(inner)):
                yield from self.visit(inner_opcode, inner_body)
        elif opcode in (OP_DATA_END, OP_FOOTER):
            self.done = True


def iter_messages_from_bytes(data: bytes | memoryview) -> Iterator[Message]:
    """Every message of the data section, chunks included, in file order."""
    if bytes(data[: len(MAGIC)]) != MAGIC or bytes(data[-len(MAGIC) :]) != MAGIC:
        raise McapFormatError("missing MCAP magic at the start or the end")
    scanner = _Scanner()
    for opcode, body in _records(data, len(MAGIC), len(data) - len(MAGIC)):
        yield from scanner.visit(opcode, body)
        if scanner.done:
            return


def iter_messages(path: Path) -> Iterator[Message]:
    """Every message of an MCAP file in file order, read through a memory map."""
    with (
        path.open("rb") as handle,
        mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as view,
    ):
        yield from iter_messages_from_bytes(view)
