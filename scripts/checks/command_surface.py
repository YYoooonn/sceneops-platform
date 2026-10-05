#!/usr/bin/env python
"""Consistency of the supported command surface (`make check-commands`).

Checks, without running any workflow:

  * the E2E surface is exactly the five journeys (plus the opt-in model-backend
    acceptance of one of them);
  * every `make <target>` that `make help` advertises is a real target, and
    every journey / baseline / test target is advertised;
  * no Makefile, makefile fragment or script under scripts/ references a
    deleted pipeline, job type or workflow.

Prints one line per violation and exits non-zero when there is any.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

E2E_JOURNEYS = {
    "e2e-batch-canonical",
    "e2e-streaming-equivalence",
    "e2e-scene-ml",
    "e2e-episode-learning",
    "e2e-cleanroom",
}
ACCEPTANCE = {"acceptance-grounding-dino"}
MUST_BE_ADVERTISED = (
    E2E_JOURNEYS
    | ACCEPTANCE
    | {
        "canonical-bootstrap",
        "canonical-verify",
        "reference-data-bootstrap",
        "reference-data-verify",
        "test",
        "test-integration",
        "test-infrastructure",
        "test-infrastructure-airflow",
    }
)

# Names of removed architecture: none may appear in a command or a script.
DELETED = [
    "e2e-recording-scene",
    "e2e-recording-episode",
    "e2e-perception",
    "e2e-episode-alignment",
    "e2e-robot-learning",
    "e2e-robot-run-learning",
    "e2e-episode-building",
    "e2e-episode-curation",
    "e2e-scene-analytics-export",
    "e2e-batch-acquisition",
    "e2e-interop",
    "e2e-lerobot-container",
    "e2e-bootstrap",
    "smoke-lerobot-container",
    "verify-reliability",
    "verify-airflow-backend",
    "scenario_curation",
    "detection_evaluation",
    "aligned_episode_building",
    "raw_log_episode_building",
    "raw_log_scene_building",
    "dataset_scene_ingestion",
    "unavailable_until",
    "UNAVAILABLE until",
]


def _make(*args: str, check: bool = True) -> str:
    return subprocess.run(
        ["make", "--no-print-directory", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=check,
    ).stdout


def defined_targets() -> set[str]:
    # `make -q` exits 1 for an out-of-date target; only the database matters.
    database = _make("-pqRr", "help", check=False)
    return set(re.findall(r"^([a-zA-Z0-9][a-zA-Z0-9_.-]*):(?!=)", database, flags=re.M))


def advertised_targets(help_text: str) -> set[str]:
    return set(re.findall(r"make ([a-z][a-z0-9-]*)", help_text))


def scanned_files() -> list[Path]:
    files = [REPO_ROOT / "Makefile", *sorted((REPO_ROOT / "makefiles").glob("*.mk"))]
    for pattern in ("*.sh", "*.py"):
        files += [
            f
            for f in sorted((REPO_ROOT / "scripts").rglob(pattern))
            if f.name != Path(__file__).name and "dev" not in f.relative_to(REPO_ROOT).parts
        ]
    return files


def main() -> int:
    problems: list[str] = []
    defined = defined_targets()
    help_text = _make("help")

    e2e_targets = {t for t in defined if t.startswith("e2e-")}
    if e2e_targets != E2E_JOURNEYS:
        problems.append(
            f"E2E surface is {sorted(e2e_targets)}, expected exactly {sorted(E2E_JOURNEYS)}"
        )

    advertised = advertised_targets(help_text)
    for target in sorted(advertised - defined):
        problems.append(f"make help advertises `make {target}`, which is not a target")
    for target in sorted(MUST_BE_ADVERTISED - advertised):
        problems.append(f"`make {target}` is not advertised by make help")
    for target in sorted(MUST_BE_ADVERTISED - defined):
        problems.append(f"required target `{target}` is not defined")

    for path in scanned_files():
        text = path.read_text()
        for name in DELETED:
            if name in text:
                problems.append(f"{path.relative_to(REPO_ROOT)} references deleted `{name}`")

    for problem in problems:
        print(f"❌ {problem}")
    if not problems:
        print(
            f"✅ command surface consistent: {len(defined)} targets, "
            f"{len(advertised)} advertised, {len(E2E_JOURNEYS)} E2E journeys"
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
