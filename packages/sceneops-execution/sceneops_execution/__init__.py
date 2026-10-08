"""Execution of durable Jobs and Pipelines.

PostgreSQL is authoritative for Job and PipelineRun state; Celery carries identifiers
only. ``jobs`` and ``pipelines`` hold the services that create and dispatch them, the
worker-side lease, result and recovery mechanics; ``executions`` holds the dispatch
backends and the recovery pass. The processes are ``apps/api`` (submission) and
``apps/worker`` (``JobRunner`` and the ``PipelineOrchestrator``).
"""
