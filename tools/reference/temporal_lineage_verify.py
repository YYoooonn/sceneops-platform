#!/usr/bin/env python3
"""Cross-artifact temporal ownership of derived artifacts (ADR-021).

    selftest   a synthetic platform state built by the real builders and writers must
               pass, and every negative control must be detected; proves the checks,
               not the platform; no stack
    verify     the running platform's artifacts (FastAPI + object store, read-only)
               against the canonical manifests they pin, then every negative control
               in an overlay of what was read; nothing is written. Any of
                 --aligned-artifact ID        one AlignedEpisode Artifact
                 --learning-export ID         a learning export manifest artifact, and
                                              through it every aligned artifact it pins
                 --analytics-job JOB_ID       the Scene tables an
                                              EXPORT_ANALYTICS_SNAPSHOT Job wrote

Rules: docs/development/reference-verification.md ("Derived temporal ownership"). Prints
one JSON report (sceneops.temporal_lineage_report/1) on stdout and exits non-zero on any
violation or undetected control.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from temporal_lineage import (  # noqa: E402
    REPORT_SCHEMA,
    TemporalLineageVerifier,
    report_of,
)
from temporal_lineage_controls import (  # noqa: E402
    AlignedTarget,
    LearningTarget,
    SceneTarget,
    run_aligned_controls,
    run_learning_controls,
    run_scene_controls,
)
from verification.findings import Findings  # noqa: E402
from verification.observation import HttpApi, S3Store, _paged, snake  # noqa: E402

from sceneops_storage import ArtifactNotFoundError  # noqa: E402


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class _S3ReadStore:
    """The object store through the reference verifiers' read-only client."""

    def __init__(self, s3: S3Store) -> None:
        self._s3 = s3

    async def read_bytes(self, uri: str) -> bytes:
        data = self._s3.get(uri)
        if data is None:
            raise ArtifactNotFoundError(uri)
        return data

    async def read_range(self, uri: str, offset: int, length: int) -> bytes:
        bucket, key = self._s3._split(uri)
        response = self._s3.client.get_object(
            Bucket=bucket, Key=key, Range=f"bytes={offset}-{offset + length - 1}"
        )
        return response["Body"].read()


class PlatformSource:
    """A LineageSource over the running platform: GET requests and object reads only."""

    def __init__(self, api: HttpApi, s3: S3Store) -> None:
        self.api = api
        self.store = _S3ReadStore(s3)
        self._records: dict[str, dict[str, Any] | None] = {}
        self._members: dict[tuple[str, str, str], list[dict[str, Any]] | None] = {}

    def artifact_record(self, artifact_id: str) -> dict[str, Any] | None:
        if artifact_id not in self._records:
            found = self.api(f"/artifacts/{urllib.parse.quote(artifact_id)}")
            self._records[artifact_id] = snake(found["artifact"]) if found else None
        return self._records[artifact_id]

    def members(
        self, domain: str, dataset_id: str, dataset_version: str
    ) -> list[dict[str, Any]] | None:
        key = (domain, dataset_id, dataset_version)
        if key not in self._members:
            base = (
                f"/datasets/{urllib.parse.quote(dataset_id)}"
                f"/versions/{urllib.parse.quote(dataset_version)}"
            )
            if not self.api(base):
                self._members[key] = None
            else:
                plural = f"{domain}s"
                self._members[key] = [
                    snake(row) for row in _paged(self.api, f"{base}/{plural}", plural)
                ]
        return self._members[key]


def _emit(report: dict[str, Any], controls: list[dict[str, Any]], **extra: Any) -> int:
    undetected = [
        c["control"]
        for c in controls
        if not c["detected"] and "not_applicable" not in c
    ]
    report = {
        **report,
        "ok": report["ok"] and not undetected,
        "negative_controls": controls,
        "undetected_controls": undetected,
        **extra,
    }
    assert report["schema"] == REPORT_SCHEMA
    print(json.dumps(report, indent=2, sort_keys=True))
    for violation in report["violations"][:50]:
        _log(f"❌ [{violation['code']}] {violation['message']}")
    for name in undetected:
        _log(f"❌ negative control {name} was not detected")
    detected = sum(1 for c in controls if c["detected"])
    applicable = sum(1 for c in controls if "not_applicable" not in c)
    _log(
        f"{'✅' if report['ok'] else '❌'} {report['check_count']} checks, "
        f"{len(report['violations'])} violation(s), negative controls "
        f"{detected}/{applicable} detected "
        f"({len(controls) - applicable} not applicable), {report['stats']}"
    )
    return 0 if report["ok"] else 1


def _aligned_target(
    aligned_artifact_id: str, verified: Any, pool: list
) -> AlignedTarget:
    """A target with another unit of the same run, for the controls that need one."""
    target = verified.artifact
    for _, candidate in pool:
        other = candidate.artifact.source_revision
        if other.episode_id != target.source_revision.episode_id:
            scope = list(candidate.artifact.aligned_episode.clock_scope)
            if scope and scope != list(target.aligned_episode.clock_scope):
                return AlignedTarget(
                    aligned_artifact_id,
                    other_scope=scope,
                    other_episode_id=other.episode_id,
                    other_source_artifact_id=other.source_artifact_id,
                    other_source_sha256=other.source_manifest_sha256,
                    other_source_uri=other.episode_manifest_uri,
                )
    return AlignedTarget(aligned_artifact_id)


async def _verify(args: argparse.Namespace) -> int:
    api = HttpApi(
        os.environ.get("API_BASE_URL", "http://localhost:8000"),
        os.environ.get("API_PREFIX", "/api/v1"),
    )
    source = PlatformSource(api, S3Store.from_env())
    verifier = TemporalLineageVerifier(source)
    findings = Findings()
    scene_targets: list[SceneTarget] = []

    for artifact_id in args.aligned_artifact or []:
        await verifier.verify_aligned(findings, aligned_artifact_id=artifact_id)
    for manifest_id in args.learning_export or []:
        await verifier.verify_learning_export(
            findings, manifest_artifact_id=manifest_id
        )
    for job_id in args.analytics_job or []:
        job = (api(f"/jobs/{urllib.parse.quote(job_id)}") or {}).get("job")
        if not findings.check(
            bool(job) and job.get("status") == "succeeded",
            "scene_table_record",
            f"{job_id} is no succeeded export: {job!r}",
        ):
            continue
        result = job["result"]
        target = SceneTarget(
            result["dataset_id"], result["dataset_version"], result["table_uris"]
        )
        scene_targets.append(target)
        await verifier.verify_scene_analytics(
            findings,
            dataset_id=target.dataset_id,
            dataset_version=target.dataset_version,
            table_uris=target.table_uris,
        )

    report = report_of(verifier, findings)
    if not findings.ok:
        _log("negative controls are run only on a state that passes")
        return _emit(report, [])
    controls: list[dict[str, Any]] = []
    pool = verifier.verified_aligned()
    # Named artifacts, or else one of each origin the run read.
    named = set(args.aligned_artifact or [])
    seen_origins: set[str] = set()
    for artifact_id, verified in pool:
        origin = verified.artifact.aligned_episode.clock_origin.value
        if artifact_id in named or (not named and origin not in seen_origins):
            seen_origins.add(origin)
            controls += await run_aligned_controls(
                source, _aligned_target(artifact_id, verified, pool)
            )
    for manifest_id in args.learning_export or []:
        controls += await run_learning_controls(source, LearningTarget(manifest_id))
    for target in scene_targets:
        controls += await run_scene_controls(source, target)
    return _emit(report, controls)


async def _selftest() -> int:
    from temporal_lineage_fixture import DATASET, VERSION, build_world

    with tempfile.TemporaryDirectory(prefix="temporal-selftest-") as tmp:
        world = await build_world(Path(tmp))
        verifier = TemporalLineageVerifier(world)
        findings = Findings()
        await verifier.verify_learning_export(
            findings, manifest_artifact_id=world.base_export
        )
        await verifier.verify_learning_export(
            findings, manifest_artifact_id=world.incremental_export
        )
        await verifier.verify_scene_analytics(
            findings,
            dataset_id=DATASET,
            dataset_version=VERSION,
            table_uris=world.scene_tables,
        )
        report = report_of(verifier, findings)
        controls: list[dict[str, Any]] = []
        if findings.ok:
            other = world.aligned_entries["rec-b-0"][1]
            controls += await run_aligned_controls(
                world,
                AlignedTarget(
                    world.aligned["rec-a-0"],
                    other_scope=list(other.aligned_episode.clock_scope),
                    other_episode_id=other.source_revision.episode_id,
                    other_source_artifact_id=other.source_revision.source_artifact_id,
                    other_source_sha256=other.source_revision.source_manifest_sha256,
                    other_source_uri=other.source_revision.episode_manifest_uri,
                ),
            )
            for export in (world.base_export, world.incremental_export):
                controls += await run_learning_controls(world, LearningTarget(export))
            controls += await run_scene_controls(
                world, SceneTarget(DATASET, VERSION, world.scene_tables)
            )
        return _emit(
            report,
            controls,
            note="synthetic platform state: proves the checks, not the platform",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selftest")
    p = sub.add_parser("verify")
    p.add_argument("--aligned-artifact", action="append")
    p.add_argument("--learning-export", action="append")
    p.add_argument("--analytics-job", action="append")
    args = parser.parse_args(argv)
    if args.command == "selftest":
        return asyncio.run(_selftest())
    if not (args.aligned_artifact or args.learning_export or args.analytics_job):
        parser.error("name at least one artifact or Job to verify")
    return asyncio.run(_verify(args))


if __name__ == "__main__":
    sys.exit(main())
