"""Content checksums and checksum-qualified artifact names (ADR-007 §19).

Every checksum SceneOps persists has the form ``sha256:<64 lowercase hex>``.
Canonical Scene and Episode manifests are stored under checksum-qualified
names, so several immutable revisions of one unit coexist without any key
ever being overwritten.
"""

from __future__ import annotations

import hashlib
import re
from typing import Final

SHA256_CHECKSUM_PATTERN: Final = r"^sha256:[0-9a-f]{64}$"
_SHA256_CHECKSUM_RE = re.compile(SHA256_CHECKSUM_PATTERN)


def sha256_checksum(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def checksum_hex(checksum: str) -> str:
    """The hex digest of a ``sha256:<hex>`` checksum."""
    if not _SHA256_CHECKSUM_RE.fullmatch(checksum):
        raise ValueError(
            f"checksum must be 'sha256:<64 lowercase hex>', got {checksum!r}"
        )
    return checksum.removeprefix("sha256:")


def checksum_qualified_manifest_name(checksum: str) -> str:
    """``manifest-<hex>.json``: the write-once object name of one canonical
    manifest revision."""
    return f"manifest-{checksum_hex(checksum)}.json"


__all__ = [
    "SHA256_CHECKSUM_PATTERN",
    "checksum_hex",
    "checksum_qualified_manifest_name",
    "sha256_checksum",
]
