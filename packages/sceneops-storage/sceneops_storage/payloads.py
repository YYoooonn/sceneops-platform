"""Logical URI resolution and verified payload reads (ADR-016).

``PayloadUriResolver`` maps a Logical URI to the physical URI of the same
object key under the storage root configured for its namespace, and back. It
reads no database and interprets nothing about what the key names: a key is
appended to its root verbatim, so resolution is exactly the key layout the
producers wrote with ``join_uri``. Roots are strict: none may contain another,
so every physical URI under them has at most one Logical URI.

``VerifiedPayloadReader`` is the read path for pinned payload bytes. It
returns bytes only after their length and full-object sha256 equal the pin;
a missing object and every mismatch raise. Registration-time presence checks
(listing sizes) are a different, weaker guarantee and stay where they are.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.artifacts.logical_uri import (
    LogicalNamespace,
    LogicalUriError,
    PinnedPayload,
    format_logical_uri,
    parse_logical_uri,
    validate_object_key,
)
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.config import ArtifactSettings

from sceneops_storage.exceptions import ArtifactNotFoundError, ArtifactStoreError


class PayloadAccessError(ArtifactStoreError):
    """Pinned payload bytes cannot be provided."""


class UnresolvablePayloadUriError(PayloadAccessError):
    """A URI is not a Logical URI this resolver maps, or a physical URI lies
    under no configured root as a valid object key."""


class PayloadMissingError(PayloadAccessError):
    """No object is stored where a Logical URI resolves."""


class PayloadVerificationError(PayloadAccessError):
    """Stored bytes differ from their pin (size or sha256)."""


class PayloadUriResolver:
    def __init__(self, roots: Mapping[LogicalNamespace, str]) -> None:
        checked: dict[LogicalNamespace, str] = {}
        for namespace, root in roots.items():
            if not isinstance(namespace, LogicalNamespace):
                raise ValueError(f"unknown Logical URI namespace {namespace!r}")
            if not isinstance(root, str) or not root or root.endswith("/"):
                raise ValueError(
                    f"root of {namespace.value} must be a non-empty URI without a "
                    f"trailing '/', got {root!r}"
                )
            checked[namespace] = root
        for a, root_a in checked.items():
            for b, root_b in checked.items():
                if a != b and (root_a == root_b or root_b.startswith(f"{root_a}/")):
                    raise ValueError(
                        f"roots of {a.value} ({root_a}) and {b.value} ({root_b}) "
                        "overlap; a physical URI would have two Logical URIs"
                    )
        self._roots = MappingProxyType(checked)

    @classmethod
    def from_settings(cls, settings: ArtifactSettings) -> PayloadUriResolver:
        """The roots the producers write under: observation payloads and
        preserved sources."""
        return cls(
            {
                LogicalNamespace.OBSERVATION_PAYLOADS: (
                    settings.observation_payload_root_uri
                ),
                LogicalNamespace.SOURCES: settings.source_root_uri,
            }
        )

    @property
    def roots(self) -> Mapping[LogicalNamespace, str]:
        return self._roots

    def resolve(self, uri: str) -> str:
        """The physical URI of a Logical URI."""
        try:
            parsed = parse_logical_uri(uri)
        except LogicalUriError as exc:
            raise UnresolvablePayloadUriError(str(exc)) from exc
        root = self._roots.get(parsed.namespace)
        if root is None:
            raise UnresolvablePayloadUriError(
                f"{uri}: namespace {parsed.namespace.value} has no configured root"
            )
        return f"{root}/{parsed.key}"

    def logical_uri(self, physical_uri: str) -> str:
        """The Logical URI of a physical URI under a configured root. A URI
        that is not ``{root}/{valid key}`` for exactly one root is refused,
        never re-spelled."""
        for namespace, root in self._roots.items():
            if physical_uri.startswith(f"{root}/"):
                key = physical_uri[len(root) + 1 :]
                try:
                    validate_object_key(key)
                except LogicalUriError as exc:
                    raise UnresolvablePayloadUriError(
                        f"{physical_uri} is under the {namespace.value} root {root} "
                        f"but is not a valid object key: {exc}"
                    ) from exc
                return format_logical_uri(namespace, key)
        raise UnresolvablePayloadUriError(
            f"{physical_uri} is under no configured payload root "
            f"({', '.join(sorted(self._roots.values()))})"
        )


class VerifiedPayloadReader:
    def __init__(self, store: ArtifactStore, resolver: PayloadUriResolver) -> None:
        self._store = store
        self._resolver = resolver

    @property
    def resolver(self) -> PayloadUriResolver:
        return self._resolver

    async def read(self, ref: PinnedPayload) -> bytes:
        """The whole object ``ref`` names, only if it is exactly the pinned
        bytes."""
        physical = self._resolver.resolve(ref.uri)
        try:
            data = await self._store.read_bytes(physical)
        except (ArtifactNotFoundError, FileNotFoundError) as exc:
            raise PayloadMissingError(
                f"payload {ref.uri} is not stored at {physical}"
            ) from exc
        if len(data) != ref.size_bytes:
            raise PayloadVerificationError(
                f"payload {ref.uri} at {physical} has {len(data)} bytes; "
                f"the pin requires {ref.size_bytes}"
            )
        checksum = sha256_checksum(data)
        if checksum != ref.checksum:
            raise PayloadVerificationError(
                f"payload {ref.uri} at {physical} has {checksum}; the pin "
                f"requires {ref.checksum}"
            )
        return data


__all__ = [
    "PayloadAccessError",
    "PayloadMissingError",
    "PayloadUriResolver",
    "PayloadVerificationError",
    "UnresolvablePayloadUriError",
    "VerifiedPayloadReader",
]
