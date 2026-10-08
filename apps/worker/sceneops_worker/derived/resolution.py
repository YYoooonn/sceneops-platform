"""Resolution of pinned derived revisions (ADR-007 §33.1).

A derived workflow never resolves "the latest" of anything. Each helper here
takes a pin (an artifact id and checksum, or a record that holds them),
verifies that the ArtifactRecord it names exists and agrees, reads the bytes
through the pinned checksum and parses them strictly. A pin that does not
resolve exactly is an error, never a fallback.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.common.derived_ids import prediction_manifest_artifact_id
from sceneops_core.inference.schemas import (
    DetectionPredictionManifest,
    PredictionRevisionRef,
)
from sceneops_core.inference.schemas.runs import InferenceRunRecord
from sceneops_core.labels import LabelSetManifest, LabelSetRef
from sceneops_core.sample_views import SampleViewRef, SceneSampleViewManifest
from sceneops_core.scenarios import ScenarioSetManifest, ScenarioSetRef
from sceneops_core.scenarios.schemas.records import ScenarioSetRecord
from sceneops_core.scenes.schemas import SceneManifest

from sceneops_worker.core.context import WorkerContext

from sceneops_derived.manifests import DerivedManifestIntegrityError


class LegacyDerivedRecordError(ValueError):
    """A record written before derived manifests were pinned has no pinned
    revision; rebuild it instead of guessing."""


async def resolve_label_set(
    context: WorkerContext, ref: LabelSetRef
) -> LabelSetManifest:
    artifact = await context.artifact_record_store.get(ref.manifest_artifact_id)
    if (
        artifact is None
        or artifact.kind != ArtifactKind.LABEL_SET_MANIFEST.value
        or artifact.checksum != ref.manifest_checksum
        or artifact.owner_id != ref.label_set_id
    ):
        raise DerivedManifestIntegrityError(
            f"label set revision {ref.label_set_id}@{ref.manifest_checksum} "
            f"is not registered as {ref.manifest_artifact_id}"
        )
    manifest = await context.derived_store.read_label_set(
        uri=artifact.uri, checksum=ref.manifest_checksum, size_bytes=artifact.size_bytes
    )
    if manifest.label_set_id != ref.label_set_id:
        raise DerivedManifestIntegrityError(
            f"label set {ref.manifest_artifact_id} holds {manifest.label_set_id!r}, "
            f"pinned as {ref.label_set_id!r}"
        )
    return manifest


@dataclass(frozen=True)
class ResolvedView:
    ref: SampleViewRef
    view: SceneSampleViewManifest
    scene: SceneManifest
    dataset_id: str | None
    dataset_version: str | None


async def resolve_sample_view(
    context: WorkerContext, ref: SampleViewRef
) -> ResolvedView:
    """The exact view revision ``ref`` pins and the exact Scene revision that
    view pins."""
    artifact = await context.artifact_record_store.get(ref.manifest_artifact_id)
    if (
        artifact is None
        or artifact.kind != ArtifactKind.SCENE_SAMPLE_VIEW_MANIFEST.value
        or artifact.checksum != ref.manifest_checksum
        or artifact.owner_id != ref.scene_id
    ):
        raise DerivedManifestIntegrityError(
            f"sample view {ref.scene_id}@{ref.manifest_checksum} is not "
            f"registered as {ref.manifest_artifact_id}"
        )
    view = await context.derived_store.read_sample_view(
        uri=artifact.uri, checksum=ref.manifest_checksum, size_bytes=artifact.size_bytes
    )
    if view.scene.scene_id != ref.scene_id:
        raise DerivedManifestIntegrityError(
            f"sample view {ref.manifest_artifact_id} describes scene "
            f"{view.scene.scene_id!r}, pinned as {ref.scene_id!r}"
        )
    scene_artifact = await context.artifact_record_store.get(
        view.scene.manifest_artifact_id
    )
    if (
        scene_artifact is None
        or scene_artifact.kind != ArtifactKind.SCENE_MANIFEST.value
        or scene_artifact.checksum != view.scene.manifest_checksum
    ):
        raise DerivedManifestIntegrityError(
            f"scene revision {view.scene.manifest_artifact_id} pinned by sample "
            f"view {ref.manifest_artifact_id} is not registered"
        )
    scene = await context.scene_artifact_store.read_pinned_manifest(
        uri=scene_artifact.uri,
        checksum=view.scene.manifest_checksum,
        size_bytes=scene_artifact.size_bytes,
    )
    return ResolvedView(
        ref=ref,
        view=view,
        scene=scene,
        dataset_id=artifact.dataset_id,
        dataset_version=artifact.dataset_version,
    )


@dataclass(frozen=True)
class ResolvedScenarioSet:
    ref: ScenarioSetRef
    manifest: ScenarioSetManifest
    record: ScenarioSetRecord


async def resolve_scenario_set(
    context: WorkerContext, scenario_set_id: str
) -> ResolvedScenarioSet:
    record = await context.scenario_store.get(scenario_set_id)
    if record is None:
        raise ValueError(
            f"ScenarioSet not found: {scenario_set_id!r}. "
            "Ensure mine_scenarios has completed successfully."
        )
    if not record.manifest_artifact_id or not record.manifest_checksum:
        raise LegacyDerivedRecordError(
            f"ScenarioSet {scenario_set_id!r} pins no manifest revision; it "
            "predates revision-pinned ScenarioSets, so mine it again"
        )
    artifact = await context.artifact_record_store.get(record.manifest_artifact_id)
    if (
        artifact is None
        or artifact.kind != ArtifactKind.SCENARIO_SET_MANIFEST.value
        or artifact.checksum != record.manifest_checksum
        or artifact.scenario_set_id != scenario_set_id
    ):
        raise DerivedManifestIntegrityError(
            f"ScenarioSet {scenario_set_id!r} pins manifest artifact "
            f"{record.manifest_artifact_id}@{record.manifest_checksum}, which is "
            "not registered"
        )
    manifest = await context.derived_store.read_scenario_set(
        uri=artifact.uri,
        checksum=record.manifest_checksum,
        size_bytes=artifact.size_bytes,
    )
    if manifest.scenario_set_id != scenario_set_id:
        raise DerivedManifestIntegrityError(
            f"ScenarioSet manifest {artifact.artifact_id} holds "
            f"{manifest.scenario_set_id!r}, pinned as {scenario_set_id!r}"
        )
    return ResolvedScenarioSet(
        ref=ScenarioSetRef(
            scenario_set_id=scenario_set_id,
            manifest_artifact_id=record.manifest_artifact_id,
            manifest_checksum=record.manifest_checksum,
        ),
        manifest=manifest,
        record=record,
    )


@dataclass(frozen=True)
class ResolvedPrediction:
    ref: PredictionRevisionRef
    manifest: DetectionPredictionManifest
    run: InferenceRunRecord


async def resolve_prediction_revision(
    context: WorkerContext,
    *,
    inference_run_id: str,
    expected_checksum: str | None,
) -> ResolvedPrediction:
    """The prediction manifest revision of a succeeded inference run. With
    ``expected_checksum`` the run must have published exactly that
    revision; without it the revision the run recorded is used and returned
    so the caller can pin it."""
    run = await context.runs.inference.get(inference_run_id)
    if run is None:
        raise ValueError(f"Inference run not found: {inference_run_id}")
    if run.prediction_manifest_uri is None or not run.prediction_manifest_checksum:
        raise LegacyDerivedRecordError(
            f"Inference run {inference_run_id!r} pins no prediction manifest "
            "revision; it predates revision-pinned predictions, so run it again"
        )
    if (
        expected_checksum is not None
        and expected_checksum != run.prediction_manifest_checksum
    ):
        raise DerivedManifestIntegrityError(
            f"Inference run {inference_run_id!r} published prediction manifest "
            f"{run.prediction_manifest_checksum}, not the pinned {expected_checksum}"
        )
    artifact_id = prediction_manifest_artifact_id(
        inference_run_id=inference_run_id, checksum=run.prediction_manifest_checksum
    )
    artifact = await context.artifact_record_store.get(artifact_id)
    if (
        artifact is None
        or artifact.kind != ArtifactKind.PREDICTION_MANIFEST.value
        or artifact.checksum != run.prediction_manifest_checksum
    ):
        raise DerivedManifestIntegrityError(
            f"prediction manifest {artifact_id} of run {inference_run_id!r} is not "
            "registered"
        )
    manifest = await context.derived_store.read_prediction_manifest(
        uri=artifact.uri,
        checksum=run.prediction_manifest_checksum,
        size_bytes=artifact.size_bytes,
    )
    if manifest.inference_run_id != inference_run_id:
        raise DerivedManifestIntegrityError(
            f"prediction manifest {artifact_id} holds run "
            f"{manifest.inference_run_id!r}, not {inference_run_id!r}"
        )
    return ResolvedPrediction(
        ref=PredictionRevisionRef(
            inference_run_id=inference_run_id,
            manifest_artifact_id=artifact_id,
            manifest_checksum=run.prediction_manifest_checksum,
        ),
        manifest=manifest,
        run=run,
    )


__all__ = [
    "LegacyDerivedRecordError",
    "ResolvedPrediction",
    "ResolvedScenarioSet",
    "ResolvedView",
    "resolve_label_set",
    "resolve_prediction_revision",
    "resolve_sample_view",
    "resolve_scenario_set",
]
