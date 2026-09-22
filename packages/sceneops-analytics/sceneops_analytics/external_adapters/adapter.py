"""ExternalDatasetAdapter: the shared base contract every concrete external
format (LeRobot, RLDS, ...) implements (SceneOps V2 Request 3.1, write
lifecycle refined in Request 3.1A).

::

    SceneOpsDataset (Request 2.7B)
            -> ExternalDatasetAdapter.export(dataset, ExternalExportConfig)
            -> ExternalDatasetAdapter.open_writer(config)
            -> ExternalDatasetWriter.initialize()
            -> ExternalEpisode / ExternalStep (framework-neutral IR)
            -> ExternalDatasetWriter.write_episode() x N (export order)
            -> ExternalDatasetWriter.finalize()
            -> ExternalExportReport

export() owns everything format-independent -- sourcing from SceneOpsDataset
(never AlignedEpisodeArtifact/raw Parquet/SequenceSampler directly),
EpisodeRef/step-order preservation, one uniform FeatureSchema across the
whole export, the write lifecycle, and semantic-loss/report bookkeeping. A
concrete subclass supplies only ``format_name``, ``format_version``,
``semantic_capabilities``, and ``open_writer`` -- the last of which returns
an ExternalDatasetWriter (see writer.py) that does the format-specific
initialize/write/finalize work. Request 3.1/3.1A define no concrete
subclass.

A structural note for whoever implements the first concrete adapter: v1
dense projection (sceneops_core.episodes.learning, Request 2.7A §9) supports
exactly one MissingFeaturePolicy -- ERROR. A required channel that is ever
status=MISSING, or a channel absent from the Episode's declared set
(ABSENT), already raises FeatureMissingError/FeatureAbsentError inside
SceneOpsDataset.get_window() before this module ever sees a step. So no
ExternalStep this contract produces can carry a MISSING/ABSENT value today,
and RESOLVED vs INTERPOLATED collapses into the same plain float during
dense projection -- SemanticField.SIGNAL_STATUS is therefore lost at the
2.7A/2.7B boundary unconditionally, independent of which adapter or target
format is used. Every concrete adapter should classify SIGNAL_STATUS as
MappingKind.UNSUPPORTED (or LOSSY_EXPLICIT if it adds its own masking) for
this reason, not because of a limitation in the target format itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from sceneops_analytics.learning_dataset import EpisodeMetadata, SceneOpsDataset
from sceneops_core.episodes.learning import EpisodeRef

from .enums import MappingKind, SemanticField, UnsupportedSemanticPolicy
from .errors import ExternalFeatureSchemaMismatchError, UnsupportedSemanticError
from .schemas import (
    ExternalEpisode,
    ExternalExportConfig,
    ExternalExportReport,
    ExternalStep,
    SemanticLoss,
)
from .validation import (
    validate_episode_count,
    validate_episode_ref_traceability,
    validate_step_count,
    validate_step_ordering,
)
from .writer import ExternalDatasetWriter


class ExternalDatasetAdapter(ABC):
    """Base contract for one external-format export (SceneOps V2 Request
    3.1). Construct a subclass instance and call ``await
    adapter.export(dataset, config)``."""

    @property
    @abstractmethod
    def format_name(self) -> str:
        """Target format identifier, e.g. "lerobot"/"rlds" (not defined by
        this request -- no concrete subclass exists yet)."""

    @property
    @abstractmethod
    def format_version(self) -> str:
        """Target format version this adapter writes."""

    @abstractmethod
    def semantic_capabilities(self) -> Mapping[SemanticField, MappingKind]:
        """Every SemanticField this format can/can't represent (Request 3.1
        §5). A field omitted from the returned mapping is treated as
        MappingKind.UNSUPPORTED -- silence is never interpreted as
        "lossless"."""

    @abstractmethod
    def open_writer(self, config: ExternalExportConfig) -> ExternalDatasetWriter:
        """Create a new ExternalDatasetWriter session for one ``export()``
        call (Request 3.1A). Called exactly once per export(), before
        ExternalDatasetWriter.initialize(). A fresh writer per call keeps
        per-export mutable state (open files, shard counters, accumulated
        stats, ...) from leaking across separate export() calls on the same
        adapter instance. Request 3.1/3.1A define no concrete
        implementation."""

    async def export(
        self, dataset: SceneOpsDataset, config: ExternalExportConfig
    ) -> ExternalExportReport:
        """Project ``dataset`` into this adapter's target format under
        ``config`` (Request 3.1 §3/§4/§6/§7; write lifecycle per Request
        3.1A). Sources exclusively from SceneOpsDataset -- never
        AlignedEpisodeArtifact, raw Parquet, or SequenceSampler.

        Semantic-loss resolution happens before ``open_writer``/
        ``initialize()`` -- an export that would violate
        ``unsupported_semantic_policy=FAIL`` never touches the write target
        at all. If any writer step raises, that exception propagates
        immediately and no later lifecycle step runs -- in particular, a
        failed ``write_episode()`` means ``finalize()`` is never called.
        """
        semantic_losses = self._resolve_semantic_losses(config)

        episode_refs = (
            list(config.episode_refs)
            if config.episode_refs is not None
            else dataset.episodes()
        )

        writer = self.open_writer(config)
        await writer.initialize()

        episodes: list[ExternalEpisode] = []
        feature_schema = None
        for ref in episode_refs:
            metadata = dataset.get_episode(ref)  # EpisodeNotFoundError if unexposed

            # An Episode with zero steps can never contribute a step to this
            # export -- skip both schema-resolution and get_window for it,
            # exactly as SequenceSampler (Request 2.7C) never schema-checks
            # an Episode too short to ever contribute a window.
            if metadata.step_count == 0:
                steps: list[ExternalStep] = []
            else:
                schema = await dataset.resolve_feature_schema(ref, config.projection)
                if feature_schema is None:
                    feature_schema = schema
                elif schema != feature_schema:
                    raise ExternalFeatureSchemaMismatchError(
                        f"{ref!r} resolves config.projection to a "
                        "FeatureSchema incompatible with earlier episodes "
                        f"in this export (observation_dim="
                        f"{schema.observation_dim} vs "
                        f"{feature_schema.observation_dim}, action_dim="
                        f"{schema.action_dim} vs {feature_schema.action_dim})"
                    )
                steps = await self._project_episode_steps(
                    dataset, ref, metadata, config
                )

            episode = ExternalEpisode(
                episode_ref=ref,
                task=metadata.task,
                outcome=metadata.outcome,
                steps=steps,
            )
            validate_step_ordering(episode)
            await writer.write_episode(episode)
            episodes.append(episode)

        validate_episode_ref_traceability(episodes, dataset.episodes())
        await writer.finalize()

        manifest = dataset.learning_manifest
        report = ExternalExportReport(
            format_name=self.format_name,
            format_version=self.format_version,
            source_dataset_id=manifest.dataset_id,
            source_dataset_version=manifest.dataset_version,
            source_export_id=manifest.export_id,
            source_episode_refs=list(episode_refs),
            exported_episode_count=len(episodes),
            exported_step_count=sum(len(episode.steps) for episode in episodes),
            feature_schema=feature_schema,
            semantic_losses=semantic_losses,
        )
        validate_episode_count(report)
        validate_step_count(report, episodes)
        return report

    def _resolve_semantic_losses(
        self, config: ExternalExportConfig
    ) -> list[SemanticLoss]:
        capabilities = self.semantic_capabilities()
        losses: list[SemanticLoss] = []
        for field in SemanticField:
            mapping = capabilities.get(field, MappingKind.UNSUPPORTED)
            if mapping is MappingKind.LOSSLESS:
                continue
            if (
                mapping is MappingKind.UNSUPPORTED
                and config.unsupported_semantic_policy is UnsupportedSemanticPolicy.FAIL
            ):
                raise UnsupportedSemanticError(
                    f"{self.format_name} v{self.format_version} cannot "
                    f"represent {field.value!r} and "
                    "unsupported_semantic_policy=FAIL"
                )
            losses.append(
                SemanticLoss(
                    field=field,
                    mapping=mapping,
                    detail=(
                        f"{self.format_name} v{self.format_version} declares "
                        f"{field.value!r} as {mapping.value!r}"
                    ),
                )
            )
        return losses

    async def _project_episode_steps(
        self,
        dataset: SceneOpsDataset,
        ref: EpisodeRef,
        metadata: EpisodeMetadata,
        config: ExternalExportConfig,
    ) -> list[ExternalStep]:
        if metadata.step_count == 0:
            return []
        window = await dataset.get_window(
            ref,
            0,
            metadata.step_count,
            config.projection,
            missing_policy=config.missing_policy,
        )
        return [
            ExternalStep(
                step_index=i,
                timestamp_us=window.timestamps_us[i],
                observation=window.observation[i],
                action=window.action[i],
            )
            for i in range(metadata.step_count)
        ]


__all__ = ["ExternalDatasetAdapter"]
