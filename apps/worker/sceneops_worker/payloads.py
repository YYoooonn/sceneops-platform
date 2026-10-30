"""Verified payload access for workflows that read payload bytes (ADR-015
§6.5, MS-10; ADR-016, ADR-017).

A canonical ``PayloadRef`` is already a pin: Logical URI, full-object sha256,
size and media type. Registration proved that the URI names bytes of the
unit's own registered source, so a consumer reads it as it is, the same way
for every source, through ``payload_reader``, which verifies the bytes against
the pin; nothing is looked up by artifact or content id.

A reference with a selector names part of its object (one frame of a video).
A consumer that can only decode whole objects refuses it with
``require_whole_payloads`` rather than reading the whole object as if it were
that part.
"""

from __future__ import annotations

from collections.abc import Iterable

from sceneops_core.artifacts.schemas import PayloadRef
from sceneops_storage import PayloadUriResolver, VerifiedPayloadReader

from sceneops_worker.core.context import WorkerContext


class UnsupportedPayloadSelectorError(RuntimeError):
    """A reference selects part of its object (a video frame), which the
    consumer cannot decode."""


def payload_uri_resolver(context: WorkerContext) -> PayloadUriResolver:
    return PayloadUriResolver.from_settings(context.settings.artifact)


def payload_reader(context: WorkerContext) -> VerifiedPayloadReader:
    return VerifiedPayloadReader(context.artifact_store, payload_uri_resolver(context))


def require_whole_payloads(refs: Iterable[PayloadRef], *, consumer: str) -> None:
    """Refuse every reference that selects part of its object."""
    selected = sorted({(r.uri, r.selector.kind) for r in refs if r.selector})
    if selected:
        raise UnsupportedPayloadSelectorError(
            f"{len(selected)} payload reference(s) select part of their object, "
            f"which {consumer} cannot decode; refusing rather than reading the "
            f"whole object: {selected[:5]}"
        )


__all__ = [
    "UnsupportedPayloadSelectorError",
    "payload_reader",
    "payload_uri_resolver",
    "require_whole_payloads",
]
