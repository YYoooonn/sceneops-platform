"""Materializes a canonical RobotRun recording (an ArtifactStore-backed
MCAP, see Phase 6.4/registration.py) to an execution-scoped local temp
file for ``RosbagAdapter``, which stays storage-agnostic -- it never
learns about ``s3://``, MinIO, ``ArtifactStore``, or HTTP, only ever a
local filesystem path (``open()``).

    RobotRun.mcap_uri (ArtifactStore-backed)
      -> materialize_recording() -- read + local temp copy + checksum verify
      -> RosbagAdapter(local_path) -- existing, unmodified

The canonical ArtifactStore object is read-only from this module's
perspective -- never written to or deleted. The local copy is the
opposite: execution-scoped, temporary, and disposable -- deleted
immediately once the caller's ``async with`` block exits, on both the
success and exception paths.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from sceneops_core.artifacts.contracts import ArtifactStore


class MaterializationChecksumError(ValueError):
    """The materialized local copy's bytes don't match the expected
    (ArtifactRecord) checksum -- refuses to hand a possibly-wrong file to
    RosbagAdapter or any other downstream reader."""


def is_local_uri(uri: str) -> bool:
    """True for a URI RosbagAdapter/``open()`` can already read directly
    (no scheme, or the ``file://`` scheme) -- false for anything
    ArtifactStore-backed (``s3://``, ...), which needs
    ``materialize_recording()`` first. Mirrors
    ``LocalArtifactStore._to_path``'s own scheme check
    (``sceneops_storage.backends.local``) rather than inventing a second
    URI classification rule."""
    return urlparse(uri).scheme in ("", "file")


@asynccontextmanager
async def materialize_recording(
    *,
    artifact_store: ArtifactStore,
    uri: str,
    expected_checksum: str | None = None,
) -> AsyncIterator[Path]:
    """Read ``uri`` from ``artifact_store`` into a fresh, execution-scoped
    local temp file and yield its path.

    Execution-scoped, not a shared cache: ``tempfile.TemporaryDirectory()``
    creates a fresh, uniquely-named directory per call (via
    ``tempfile.mkdtemp``'s own randomness) -- two concurrent
    materializations of the SAME ``uri`` (e.g. two independent
    BuildEpisodes job executions against the same RobotRun) get two
    independent local copies, never a shared path one job's cleanup could
    remove out from under the other.

    Cleanup is guaranteed on both the success and exception path: the
    ``with tempfile.TemporaryDirectory()`` block wraps the ``yield``, so
    its directory (and the file inside it) is removed whether the
    caller's own body completes normally or raises -- equivalent to a
    ``try/finally`` around the yield.

    ``expected_checksum`` (the platform's ``"sha256:<hex>"`` convention,
    e.g. from the RobotRun's registered ``ArtifactRecord.checksum``,
    Phase 6.4) is optional -- verified against the materialized copy when
    given, skipped otherwise. Never a second checksum/identity system:
    this only ever compares against a checksum the caller already
    resolved from the one canonical ArtifactRecord.
    """
    data = await artifact_store.read_bytes(uri)

    with tempfile.TemporaryDirectory(prefix="sceneops-materialize-") as tmp_dir:
        file_name = Path(urlparse(uri).path).name or "recording.mcap"
        local_path = Path(tmp_dir) / file_name
        local_path.write_bytes(data)

        if expected_checksum is not None:
            actual_checksum = (
                f"sha256:{hashlib.sha256(local_path.read_bytes()).hexdigest()}"
            )
            if actual_checksum != expected_checksum:
                raise MaterializationChecksumError(
                    f"materialized bytes for {uri} do not match the expected "
                    f"checksum (expected={expected_checksum}, "
                    f"actual={actual_checksum})"
                )

        yield local_path
