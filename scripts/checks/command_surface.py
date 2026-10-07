#!/usr/bin/env python
"""Consistency of the supported command surface (`make check-commands`).

Checks, without running any workflow:

  * the E2E surface is exactly the supported journeys (plus the opt-in
    model-backend acceptance of one of them);
  * the `Validation` section of `make help` lists exactly the validation surface;
    every `make <target>` that `make help` advertises is a real target; the
    bootstrap / reset commands are advertised; the helper targets that compose the
    surface (baseline bootstraps and verifiers, suite internals) are callable but
    not advertised;
  * every `make test-infrastructure SUITE=<name>` resolves to a defined suite target;
  * no benchmark or prototype lives under scripts/, and no makefile fragment runs
    one;
  * no Makefile, makefile fragment or script under scripts/ references a
    deleted pipeline, job type, workflow or command;
  * no command, script or active document uses a retired test-state class name
    (the vocabulary is docs/development/test-matrix.md#test-state-classes).

Prints one line per violation and exits non-zero when there is any.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

E2E_JOURNEYS = {
    "e2e-streaming-equivalence",
    "e2e-scene-ml",
    "e2e-episode-learning",
    "e2e-cleanroom",
}
ACCEPTANCE = {"acceptance-grounding-dino"}

# The human-facing validation surface: the entries of the `Validation` section of `make
# help`, nothing more (docs/development/test-matrix.md).
VALIDATION_SURFACE = {
    "test",
    "test-integration",
    "test-infrastructure",
    "reference-contract-verify",
    "e2e-streaming-equivalence",
    "e2e-scene-ml",
    "e2e-episode-learning",
    "e2e-cleanroom",
}
# Environment / bootstrap commands, advertised outside the validation surface.
BOOTSTRAP = {
    "reference-data-bootstrap",
    "reference-contract-bootstrap",
    "local-reset",
}
MUST_BE_ADVERTISED = (
    VALIDATION_SURFACE | ACCEPTANCE | BOOTSTRAP | {"check-commands", "disk-report"}
)

# `make test-infrastructure SUITE=<name>`: each name has an `infra-suite-<name>` target.
INFRA_SUITES = {"pipelines", "recovery", "airflow", "kafka", "boundaries"}

# Callable building blocks that the surface composes (the contract bootstrap runs the
# baseline bootstraps, the cleanroom runs the corpus verifier, the kafka / boundaries
# suites run the isolated-environment tests). They must keep existing and are not
# advertised as workflows of their own.
HELPERS = {
    "canonical-bootstrap",
    "canonical-verify",
    "streaming-bootstrap",
    "streaming-verify",
    "streaming-compare",
    "reference-data-verify",
    "ros2-test",
    "smoke-streaming",
    "acquisition-test",
    "lerobot-test",
    "acquisition-image-check",
}

# Names of removed architecture and of commands folded into `test-infrastructure SUITE=`
# or deleted: none may appear in a command or a script.
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
    "e2e-batch-canonical",
    "smoke-api",
    "check-minio",
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
    "test-recovery",
    "test-infrastructure-airflow",
    "worker-imports",
]

# Measurement tooling lives in benchmarks/ and is never part of a command or of scripts/.
BENCHMARK_MARKERS = ("benchmark", "phase7")

# Test-state class names and flags that were renamed. A retired name beside the final one
# makes the vocabulary ambiguous (READ_ONLY_REFERENCE vs REFERENCE_READ_ONLY), so none may
# appear in a command, a script, the README or an active document. ADRs and historical
# records keep the words they were written with and are not scanned. The value lists the
# files that must name the retired term to refuse it.
RETIRED_VOCABULARY = {
    "READ_ONLY_REFERENCE": (),
    "MUTATING_ACQUISITION_TEST": (),
    "REQUIRE_CLEAN": ("makefiles/reference.mk",),
    "--require-clean": ("scripts/reference/tests/test_reference_contract.py",),
}
ACTIVE_DOCS = ("README.md", "docs/development", "docs/architecture", "docs/workflows")


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


def help_sections(help_text: str) -> dict[str, str]:
    """The sections of `make help`: each is introduced by a title framed in `====`."""
    parts = re.split(r"^=+\n(.*)\n=+\n", help_text, flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def section_entries(body: str) -> set[str]:
    """Targets a section introduces: lines that start with `  make <target>`."""
    return set(re.findall(r"^ {2}make ([a-z][a-z0-9-]*)", body, flags=re.M))


def scanned_files() -> list[Path]:
    files = [REPO_ROOT / "Makefile", *sorted((REPO_ROOT / "makefiles").glob("*.mk"))]
    for pattern in ("*.sh", "*.py"):
        files += [
            f
            for f in sorted((REPO_ROOT / "scripts").rglob(pattern))
            if f.name != Path(__file__).name
        ]
    return files


def vocabulary_files() -> list[Path]:
    files = scanned_files()
    for entry in ACTIVE_DOCS:
        path = REPO_ROOT / entry
        files += sorted(path.rglob("*.md")) if path.is_dir() else [path]
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

    validation = next(
        (
            body
            for title, body in help_sections(help_text).items()
            if title.startswith("Validation")
        ),
        None,
    )
    if validation is None:
        problems.append("make help has no `Validation` section")
    elif section_entries(validation) != VALIDATION_SURFACE:
        problems.append(
            f"the Validation section of make help lists {sorted(section_entries(validation))}, "
            f"expected exactly {sorted(VALIDATION_SURFACE)}"
        )

    for target in sorted(HELPERS & advertised):
        problems.append(f"helper `make {target}` is advertised by make help")
    for target in sorted(HELPERS - defined):
        problems.append(f"helper target `{target}` is not defined")

    suite_targets = {
        t.removeprefix("infra-suite-") for t in defined if t.startswith("infra-suite-")
    }
    for suite in sorted(INFRA_SUITES - suite_targets):
        problems.append(f"SUITE={suite} has no `infra-suite-{suite}` target")
    for suite in sorted(suite_targets - INFRA_SUITES):
        problems.append(f"`infra-suite-{suite}` is defined but is not a known SUITE")

    for path in sorted((REPO_ROOT / "scripts").rglob("*")):
        if path.is_file() and any(marker in path.name for marker in BENCHMARK_MARKERS):
            problems.append(
                f"{path.relative_to(REPO_ROOT)} is benchmark tooling: it belongs in benchmarks/"
            )

    for path in scanned_files():
        text = path.read_text()
        relative = path.relative_to(REPO_ROOT)
        for name in DELETED:
            if name in text:
                problems.append(f"{relative} references deleted `{name}`")
        if path.suffix == ".mk" and "benchmarks/" in text:
            problems.append(
                f"{relative} names benchmark tooling; no command runs a benchmark"
            )

    for path in vocabulary_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text()
        for term, allowed in RETIRED_VOCABULARY.items():
            if term in text and relative not in allowed:
                problems.append(f"{relative} uses the retired name `{term}`")

    for problem in problems:
        print(f"❌ {problem}")
    if not problems:
        print(
            f"✅ command surface consistent: {len(defined)} targets, "
            f"{len(advertised)} advertised, {len(VALIDATION_SURFACE)} validation commands, "
            f"{len(INFRA_SUITES)} infrastructure suites, {len(E2E_JOURNEYS)} E2E journeys"
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
