"""The runtime source boundary check (tools/checks/runtime_source_boundary.sh) needs
Docker, so `make check-runtime-boundary` runs it; this pins the one property its
output parsing depends on."""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "runtime_source_boundary.sh"


def test_compose_runs_quietly_because_probe_output_is_compared_line_by_line():
    # A probe passes only when its merged stdout + stderr is exactly "done"; Compose
    # writes "Container … Creating / Created" to stderr on `run` unless quiet.
    text = SCRIPT.read_text()
    compose = re.search(r"^COMPOSE=\((.*?)\)", text, re.M | re.S).group(1)
    assert "--progress quiet" in compose
    assert '"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh "$svc"' in text
