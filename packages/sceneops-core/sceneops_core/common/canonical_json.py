"""The one canonical JSON serializer for SceneOps manifests (ADR-007 §8.3).

Canonical form:

    encoding    UTF-8, no BOM
    format      compact separators (",", ":"), no insignificant whitespace,
                no trailing newline
    key order   lexicographic by Unicode code point, at every nesting level
    strings     non-ASCII emitted as UTF-8, never \\u-escaped
    floats      shortest round-trip decimal representation; NaN/Infinity rejected
    null        emitted as ``null``

Callers pass plain JSON values (dict / list / str / int / float / bool /
None). Domain-specific normalization (timestamp formatting, array ordering)
belongs to the manifest model, never to this function, so every manifest
type shares exactly one byte-level encoding.
"""

from __future__ import annotations

import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
