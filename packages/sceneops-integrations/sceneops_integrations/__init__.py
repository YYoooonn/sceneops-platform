"""SDK-bound external-dataset integration runtimes for SceneOps.

Maps ``sceneops_core.integration_runtime``'s frozen ``IntegrationRequest``/
``IntegrationResult`` contract (Request 4.1/4.1A) onto concrete external
SDKs (nuScenes, ...). Depends only on ``sceneops-core``/``sceneops-storage``
at the package level -- every external SDK import is lazy, so this package
never needs to be excluded from any environment the way the SDKs
themselves do (see ``sceneops_integrations.nuscenes`` and
``tools/nuscenes-integration``, SceneOps V2 Request 4.5).

Never opens a DB session, never imports ``sceneops-db``, and never writes
an ``ArtifactRecord`` or mutates ``DatasetVersion``/``Scene``/``Episode``
state -- the main platform (worker job handlers) remains solely
responsible for that, using each runtime's ``IntegrationResult.
produced_artifacts``.
"""
