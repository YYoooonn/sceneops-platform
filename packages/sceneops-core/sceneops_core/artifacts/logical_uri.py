"""Logical URIs of immutable SceneOps-owned payload bytes (ADR-016).

A Logical URI names one stored object by a fixed namespace and its object key
relative to that namespace's storage root, never by bucket, endpoint, backend
or deployment::

    sceneops://observation-payloads/<encoded key>
    sceneops://sources/<encoded key>

The key is a ``/``-separated object key. Each segment is percent-encoded over
its UTF-8 bytes, with every byte outside the RFC 3986 unreserved set
(``A-Z a-z 0-9 - . _ ~``) written as ``%XX`` in uppercase hex. So ``+``, ``?``,
``#``, ``%``, spaces and non-ASCII characters are always escaped, and each key
has exactly one spelling: parsing accepts only that spelling and decodes it
exactly once. A key is never normalized (no case folding, no Unicode
normalization, no ``.`` / ``..`` resolution); one that has a second spelling of
the same object (an empty, ``.`` or ``..`` segment) or that a filesystem could
read differently (a backslash, a control character) is not a key at all.

Which storage root a namespace maps to is configuration
(``sceneops_storage.PayloadUriResolver``), not part of the URI, so the same
Logical URI names the same bytes under any root that holds the same keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol
from urllib.parse import quote

from sceneops_core.artifacts.media_types import validate_media_type
from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN

LOGICAL_URI_SCHEME: Final = "sceneops"
_PREFIX: Final = f"{LOGICAL_URI_SCHEME}://"
_ENCODED_SEGMENT_RE: Final = re.compile(r"(?:[A-Za-z0-9._~-]|%[0-9A-F]{2})+")
_ESCAPE_RE: Final = re.compile(r"%([0-9A-F]{2})")
_CHECKSUM_RE: Final = re.compile(SHA256_CHECKSUM_PATTERN)


class LogicalNamespace(StrEnum):
    """Version-stable namespace tokens. A token is never renamed or reused."""

    # Recording-derived payloads: {robot_run_id}/{artifact_id}.
    OBSERVATION_PAYLOADS = "observation-payloads"
    # Preserved source objects: contents/{content_id}/files/{path}, ...
    SOURCES = "sources"


class LogicalUriError(ValueError):
    """A value is not a canonical Logical URI or not a valid object key."""


def validate_object_key(key: str) -> str:
    """An object key a Logical URI can name, kept verbatim."""
    if not isinstance(key, str) or not key:
        raise LogicalUriError(f"object key must be a non-empty string, got {key!r}")
    try:
        key.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise LogicalUriError(f"object key must be valid UTF-8, got {key!r}") from exc
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in key):
        raise LogicalUriError(
            f"object key must not contain control characters: {key!r}"
        )
    if "\\" in key:
        raise LogicalUriError(f"object key must use '/' separators, got {key!r}")
    if any(segment in ("", ".", "..") for segment in key.split("/")):
        raise LogicalUriError(
            f"object key must not contain empty, '.' or '..' segments, got {key!r}"
        )
    return key


def _encode_segment(segment: str) -> str:
    # quote() leaves exactly the unreserved set unescaped and writes
    # uppercase hex over the UTF-8 bytes.
    return quote(segment, safe="", encoding="utf-8", errors="strict")


def _decode_segment(encoded: str, uri: str) -> str:
    if not _ENCODED_SEGMENT_RE.fullmatch(encoded):
        raise LogicalUriError(
            f"{uri!r}: segment {encoded!r} is not percent-encoded as "
            "unreserved characters and uppercase %XX escapes"
        )
    raw = _ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), encoded)
    try:
        decoded = raw.encode("latin-1").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LogicalUriError(f"{uri!r}: segment {encoded!r} is not UTF-8") from exc
    if "/" in decoded:
        raise LogicalUriError(f"{uri!r}: segment {encoded!r} encodes a '/'")
    if _encode_segment(decoded) != encoded:
        raise LogicalUriError(
            f"{uri!r}: segment {encoded!r} is not canonical; it is spelled "
            f"{_encode_segment(decoded)!r}"
        )
    return decoded


@dataclass(frozen=True)
class LogicalUri:
    namespace: LogicalNamespace
    key: str

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, LogicalNamespace):
            raise LogicalUriError(f"unknown namespace {self.namespace!r}")
        validate_object_key(self.key)

    def __str__(self) -> str:
        encoded = "/".join(_encode_segment(s) for s in self.key.split("/"))
        return f"{_PREFIX}{self.namespace.value}/{encoded}"


def format_logical_uri(namespace: LogicalNamespace, key: str) -> str:
    """The one Logical URI of ``key`` in ``namespace``."""
    return str(LogicalUri(namespace, key))


def parse_logical_uri(uri: str) -> LogicalUri:
    """The namespace and key of a canonical Logical URI; anything else,
    including another spelling of a valid one, is refused."""
    if not isinstance(uri, str) or not uri.startswith(_PREFIX):
        raise LogicalUriError(f"{uri!r} is not a {_PREFIX} URI")
    authority, slash, path = uri[len(_PREFIX) :].partition("/")
    try:
        namespace = LogicalNamespace(authority)
    except ValueError:
        raise LogicalUriError(
            f"{uri!r} names unknown namespace {authority!r}"
        ) from None
    if not slash or not path:
        raise LogicalUriError(f"{uri!r} names no object key")
    key = "/".join(_decode_segment(segment, uri) for segment in path.split("/"))
    parsed = LogicalUri(namespace, key)
    if str(parsed) != uri:
        raise LogicalUriError(f"{uri!r} is not canonical; it is spelled {parsed}")
    return parsed


class PinnedPayload(Protocol):
    """Payload bytes named by Logical URI and pinned by their full-object
    sha256 and size: what a verified read checks the bytes against."""

    @property
    def uri(self) -> str: ...

    @property
    def checksum(self) -> str: ...

    @property
    def size_bytes(self) -> int: ...


@dataclass(frozen=True)
class LogicalPayloadPin:
    """A ``PinnedPayload`` for one whole object, with its declared media type."""

    uri: str
    checksum: str
    size_bytes: int
    media_type: str

    def __post_init__(self) -> None:
        parse_logical_uri(self.uri)
        if not isinstance(self.checksum, str) or not _CHECKSUM_RE.fullmatch(
            self.checksum
        ):
            raise LogicalUriError(
                f"checksum must be 'sha256:<64 lowercase hex>', got {self.checksum!r}"
            )
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 0
        ):
            raise LogicalUriError(
                f"size_bytes must be a non-negative int, got {self.size_bytes!r}"
            )
        try:
            validate_media_type(self.media_type)
        except ValueError as exc:
            raise LogicalUriError(str(exc)) from exc


__all__ = [
    "LOGICAL_URI_SCHEME",
    "LogicalNamespace",
    "LogicalPayloadPin",
    "LogicalUri",
    "LogicalUriError",
    "PinnedPayload",
    "format_logical_uri",
    "parse_logical_uri",
    "validate_object_key",
]
