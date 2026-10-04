"""Database-free SceneOps integrations.

``sceneops_integrations.recording`` publishes finalized L1 recordings
(MCAP + RobotRunManifest), checks L1 conformance, and is the one reader
through which canonicalization reads a resolved recording's messages.

Never opens a DB session, never imports ``sceneops-db``, and never writes
an ``ArtifactRecord`` or mutates ``DatasetVersion``/``Scene``/``Episode``
state -- worker job handlers remain solely responsible for that.
"""
