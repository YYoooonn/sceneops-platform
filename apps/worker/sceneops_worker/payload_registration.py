"""The registration gate for payload references (ADR-017).

A ``PayloadRef`` names its bytes by Logical URI alone; the schema decides
nothing about whose bytes they are. Before a revision may be registered, the
registrar proves, against the unit's own registered source, that every
reference names bytes that source owns, as registered and as stored. There is
one verifier per source, and the registrar, which already branches on the
binding, calls the one of its unit's source with every reference of the input
in one batch:

    every reference   it is a valid ``PayloadRef`` (re-validated: canonical
                      URI, checksum, size, media type, selector); its
                      namespace is the one the source's bytes live in; it
                      resolves under the configured root
                      (``PayloadUriResolver``); a URI referenced more than once
                      carries the same checksum, size and media type each time
    recording         ``verify_recording_payload_refs``: the key is the
                      payload key ``{robot_run_id}/{artifact_id}``
                      (``observation_payload_key``) of the unit's own RobotRun;
                      the ArtifactRecord is looked up by that artifact id,
                      never by URI (physical URIs are not unique in the
                      database), and must be an ``OBSERVATION_PAYLOAD`` owned
                      by that RobotRun, stored exactly where the URI resolves,
                      with exactly the pinned checksum, size and media type
    content           ``verify_content_payload_refs``: the key lies under the
                      files prefix of the unit's own content, derived from its
                      inventory ArtifactRecord (D4); the path is a file of the
                      content's inventory with exactly the pinned sha256 and
                      size, and the URI resolves to exactly ``content_file_uri``
                      of that path. A file of another content is refused even
                      when its bytes are identical. The media type and a
                      selector are the builder's assertion, covered by the
                      manifest checksum: the inventory records no media type
    every object      present in the store with the pinned size, by one
                      listing of the RobotRun's payload prefix or the
                      content's files prefix

A stored object is never evidence of ownership on its own: a recording
payload needs its registration record, and a content file its inventory
entry. Lookups and listings are per input, not per reference, and repeated
references are checked once.

This checks presence and size, not bytes: payloads are hashed when published
and content files when the content is registered, and both are write-once.
``VerifiedPayloadReader`` re-verifies the full-object sha256 on every read;
neither check replaces the other. The verifiers read only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from sceneops_core.artifacts.logical_uri import (
    LogicalNamespace,
    format_logical_uri,
    parse_logical_uri,
)
from sceneops_core.artifacts.schemas import (
    ArtifactKind,
    ArtifactOwnerType,
    ArtifactRecord,
    PayloadRef,
)
from sceneops_core.common.identifiers import validate_local_id
from sceneops_core.robots.schemas import RobotRunRecord
from sceneops_core.sources import validate_source_path
from sceneops_recording.observations.store import observation_payload_key
from sceneops_sources import (
    SourceLayoutError,
    content_file_uri,
    content_files_prefix,
    content_id_of_inventory_uri,
)
from sceneops_storage import PayloadUriResolver, UnresolvablePayloadUriError

from sceneops_worker.core.context import WorkerContext
from sceneops_worker.payloads import payload_uri_resolver
from sceneops_worker.sources.content import RegisteredContent


class PayloadRegistrationError(RuntimeError):
    """A Logical URI payload reference is not backed by the unit's own
    registered source, or its object is not stored as pinned."""


@dataclass(frozen=True)
class _Pin:
    """One distinct referenced object, as every reference to it pins it."""

    uri: str
    key: str
    physical_uri: str
    checksum: str
    size_bytes: int
    media_type: str


def _raise_if(problems: Iterable[str], what: str) -> None:
    problems = sorted(set(problems))
    if problems:
        raise PayloadRegistrationError(f"{len(problems)} {what}: {problems[:5]}")


def _invalid(ref: object, namespace: LogicalNamespace) -> str | None:
    if not isinstance(ref, PayloadRef):
        return f"a {type(ref).__name__} is not a Logical URI payload reference"
    try:
        # A PayloadRef built without validation must not pass unchecked.
        PayloadRef.model_validate(ref.model_dump())
    except ValidationError as exc:
        return f"{ref.uri!r} is not a valid PayloadRef: {exc.errors()[0]['msg']}"
    actual = parse_logical_uri(ref.uri).namespace
    if actual != namespace:
        return (
            f"{ref.uri} is in {actual.value}; this unit's payloads lie in "
            f"{namespace.value}"
        )
    return None


def _pins(
    refs: Iterable[PayloadRef],
    *,
    namespace: LogicalNamespace,
    resolver: PayloadUriResolver,
) -> list[_Pin]:
    distinct = set(refs)
    _raise_if(
        (p for ref in distinct if (p := _invalid(ref, namespace)) is not None),
        "payload reference(s) are not valid here",
    )
    pinned: dict[str, set[tuple[str, int, str]]] = {}
    for ref in distinct:
        pinned.setdefault(ref.uri, set()).add(
            (ref.checksum, ref.size_bytes, ref.media_type)
        )
    _raise_if(
        (
            f"{uri} is referenced with {len(values)} different checksum, size or "
            "media type pins"
            for uri, values in pinned.items()
            if len(values) > 1
        ),
        "payload URI(s) are pinned inconsistently",
    )
    pins: list[_Pin] = []
    problems: list[str] = []
    for uri, values in sorted(pinned.items()):
        ((checksum, size_bytes, media_type),) = values
        try:
            physical_uri = resolver.resolve(uri)
        except UnresolvablePayloadUriError as exc:
            problems.append(str(exc))
            continue
        pins.append(
            _Pin(
                uri=uri,
                key=parse_logical_uri(uri).key,
                physical_uri=physical_uri,
                checksum=checksum,
                size_bytes=size_bytes,
                media_type=media_type,
            )
        )
    _raise_if(problems, "payload URI(s) do not resolve")
    return pins


def _record_mismatch(
    pin: _Pin, artifact_id: str, record: ArtifactRecord | None
) -> str | None:
    """Why ``record`` does not back the payload ``pin`` names, or None."""
    if record is None:
        return f"payload artifact {artifact_id} is not registered"
    if record.kind != ArtifactKind.OBSERVATION_PAYLOAD:
        return f"payload artifact {artifact_id} is a {record.kind!r}"
    for field in ("checksum", "size_bytes", "media_type"):
        if getattr(record, field) != getattr(pin, field):
            return (
                f"payload artifact {artifact_id} {field} "
                f"{getattr(record, field)!r} != referenced {getattr(pin, field)!r}"
            )
    return None


async def _verify_stored(
    context: WorkerContext, prefix: str, pins: Iterable[_Pin]
) -> None:
    stored = {
        obj.uri: obj.size_bytes
        for obj in await context.artifact_store.list_objects(prefix)
    }
    problems: list[str] = []
    for pin in pins:
        size = stored.get(pin.physical_uri)
        if size is None:
            problems.append(f"{pin.uri} has no object at {pin.physical_uri}")
        elif size != pin.size_bytes:
            problems.append(
                f"{pin.uri} object at {pin.physical_uri} has {size} bytes; the "
                f"reference pins {pin.size_bytes}"
            )
    _raise_if(problems, "payload object(s) are not stored as pinned")


async def verify_recording_payload_refs(
    context: WorkerContext, run: RobotRunRecord, refs: Iterable[PayloadRef]
) -> Mapping[str, str]:
    """Every reference names a registered observation payload of ``run``
    (the unit's own registered RobotRun) as its OBSERVATION_PAYLOAD record
    and the store hold it. Returns each verified Logical URI's physical URI;
    raises ``PayloadRegistrationError`` otherwise."""
    resolver = payload_uri_resolver(context)
    pins = _pins(
        refs, namespace=LogicalNamespace.OBSERVATION_PAYLOADS, resolver=resolver
    )
    if not pins:
        return {}

    problems: list[str] = []
    artifact_ids: dict[str, str] = {}
    for pin in pins:
        run_id, _, artifact_id = pin.key.partition("/")
        if run_id != run.run_id:
            problems.append(
                f"{pin.uri} names a payload of RobotRun {run_id!r}, not of the "
                f"unit's RobotRun {run.run_id!r}"
            )
            continue
        try:
            validate_local_id(artifact_id, field="payload artifact_id")
        except ValueError:
            problems.append(f"{pin.uri} is not a payload key {{robot_run_id}}/{{id}}")
            continue
        if pin.key != observation_payload_key(
            robot_run_id=run.run_id, artifact_id=artifact_id
        ):
            problems.append(f"{pin.uri} is not a payload key {{robot_run_id}}/{{id}}")
            continue
        if pin.size_bytes < 1:
            # The recording builders never extract an empty payload.
            problems.append(f"{pin.uri} pins an empty payload")
            continue
        artifact_ids[pin.uri] = artifact_id
    _raise_if(problems, "recording payload reference(s) name no payload of the run")

    records = await context.artifact_record_store.get_many(
        sorted(set(artifact_ids.values()))
    )
    for pin in pins:
        artifact_id = artifact_ids[pin.uri]
        record = records.get(artifact_id)
        problem = _record_mismatch(pin, artifact_id, record)
        if problem is not None:
            problems.append(f"{pin.uri}: {problem}")
        elif (
            record.owner_type != ArtifactOwnerType.ROBOT_RUN
            or record.owner_id != run.run_id
        ):
            problems.append(
                f"{pin.uri}: payload artifact {artifact_id} is owned by "
                f"{record.owner_type!r} {record.owner_id!r}, not by RobotRun "
                f"{run.run_id!r}"
            )
        elif record.uri != pin.physical_uri:
            problems.append(
                f"{pin.uri}: payload artifact {artifact_id} is stored at "
                f"{record.uri}, not at {pin.physical_uri}"
            )
    _raise_if(problems, "recording payload reference(s) do not match their records")

    await _verify_stored(
        context,
        resolver.resolve(
            format_logical_uri(LogicalNamespace.OBSERVATION_PAYLOADS, run.run_id)
        ),
        pins,
    )
    return {pin.uri: pin.physical_uri for pin in pins}


async def verify_content_payload_refs(
    context: WorkerContext, content: RegisteredContent, refs: Iterable[PayloadRef]
) -> Mapping[str, str]:
    """Every reference names a file of ``content`` (the unit's own registered
    SourceContent) as its inventory pins it and the store holds it. Returns
    each verified Logical URI's physical URI; raises
    ``PayloadRegistrationError`` otherwise."""
    resolver = payload_uri_resolver(context)
    pins = _pins(refs, namespace=LogicalNamespace.SOURCES, resolver=resolver)
    if not pins:
        return {}

    content_id = content.record.content_id
    try:
        stored_as = content_id_of_inventory_uri(content.inventory_uri)
        files_prefix = content_files_prefix(content.inventory_uri)
        files_key = parse_logical_uri(resolver.logical_uri(files_prefix)).key
    except (SourceLayoutError, UnresolvablePayloadUriError) as exc:
        raise PayloadRegistrationError(
            f"files of {content_id} are not stored under the sources root: {exc}"
        ) from exc
    if stored_as != content_id:
        raise PayloadRegistrationError(
            f"inventory of {content_id} is stored at {content.inventory_uri}"
        )

    entries = {entry.path: entry for entry in content.inventory.files}
    problems: list[str] = []
    for pin in pins:
        if not pin.key.startswith(f"{files_key}/"):
            problems.append(
                f"{pin.uri} does not name a file of the unit's content {content_id} "
                f"(its files lie under {files_key}/)"
            )
            continue
        path = pin.key[len(files_key) + 1 :]
        try:
            validate_source_path(path)
        except ValueError as exc:
            problems.append(f"{pin.uri} does not name a content file: {exc}")
            continue
        entry = entries.get(path)
        if entry is None:
            problems.append(f"{pin.uri}: {path!r} is not a file of {content_id}")
        elif (entry.sha256, entry.size_bytes) != (pin.checksum, pin.size_bytes):
            problems.append(
                f"{pin.uri}: {path!r} is referenced as {pin.size_bytes} bytes "
                f"{pin.checksum}; the inventory pins {entry.size_bytes} bytes "
                f"{entry.sha256}"
            )
        elif content_file_uri(content.inventory_uri, path) != pin.physical_uri:
            problems.append(
                f"{pin.uri} resolves to {pin.physical_uri}, but {content_id} "
                f"stores {path!r} at {content_file_uri(content.inventory_uri, path)}"
            )
    _raise_if(problems, "content payload reference(s) do not match the inventory")

    await _verify_stored(context, files_prefix, pins)
    return {pin.uri: pin.physical_uri for pin in pins}


__all__ = [
    "PayloadRegistrationError",
    "verify_content_payload_refs",
    "verify_recording_payload_refs",
]
