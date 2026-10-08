"""Acquisition lifecycle of RobotRuns (ADR-008).

``reconciliation`` observes and classifies what the capture volume, the ArtifactStore and
PostgreSQL say about each run, and (``recovery``) acts on the eligible states within a
bounded budget; ``status`` derives the operational report from the same facts;
``lifecycle`` classifies the objects under the RobotRun root. ``registration`` submits
``REGISTER_ROBOT_RUN`` through the normal Job path. Every function is a stateless
one-shot; the processes that run them are ``apps/api`` (registration) and ``apps/worker``
(``sceneops-worker acquisition ...``).
"""
