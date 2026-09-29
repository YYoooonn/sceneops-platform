#!/usr/bin/env python
"""Print config/baselines/canonical-v0.0.yaml as JSON on stdout.

The bash orchestrators (canonical_bootstrap.sh/canonical_verify.sh) never
parse YAML themselves -- they shell out to this script once and `jq` the
result, so the YAML file stays the single authoritative source for scene
ordering/dataset identities/expected counts (see that file's own header).

Usage:
    uv run python scripts/canonical/read_baseline_spec.py
    uv run python scripts/canonical/read_baseline_spec.py --spec path/to/other.yaml

Env overrides:
    CANONICAL_SOURCE_ROOT_URI   overrides source.root_uri (host-side callers
                                 use this the same way scripts/e2e/lib.sh's
                                 E2E_BOOTSTRAP_SOURCE_ROOT_URI overrides the
                                 in-container nuScenes path -- the pipelines'
                                 own in-container default only resolves
                                 inside api/worker, where ./data:/data is
                                 bind-mounted).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

DEFAULT_SPEC_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "baselines" / "canonical-v0.0.yaml"
)


def load_spec(spec_path: Path) -> dict:
    with spec_path.open("r") as f:
        spec = yaml.safe_load(f)

    override = os.environ.get("CANONICAL_SOURCE_ROOT_URI")
    if override:
        spec["source"]["root_uri"] = override

    return spec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    args = parser.parse_args()

    spec = load_spec(args.spec)
    json.dump(spec, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
