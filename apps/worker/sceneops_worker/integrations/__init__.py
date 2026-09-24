"""Isolated, SDK-bound external-integration runtimes (SceneOps V2 Request
4.4+).

Code under this package maps ``sceneops_core.integration_runtime``'s frozen
``IntegrationRequest``/``IntegrationResult`` contract (Request 4.1/4.1A) onto
a concrete external SDK (nuScenes, ...). It never opens a DB session, never
imports ``sceneops-db``, and never writes an ``ArtifactRecord`` or mutates
``DatasetVersion``/``Scene``/``Episode`` state -- the main worker (job
handlers under ``sceneops_worker.jobs``) remains solely responsible for
that, using this package's ``IntegrationResult.produced_artifacts``. It may
use external SDKs and ``ArtifactStore`` freely.

This mirrors the boundary ``sceneops_analytics.external_adapters.lerobot``
already proved for EXPORT (Request 3.3/4.2) -- this package is the INGEST
counterpart, staying inside ``apps/worker`` for now because
``nuscenes-devkit`` is already an ``apps/worker`` dependency and moving it
into its own isolated project/container (the way
``tools/lerobot-integration`` isolates ``lerobot``) is Request 4.5's job,
not this one's (Request 4.4 §5).
"""
