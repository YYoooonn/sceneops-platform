"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-04

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── jobs ──────────────────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(128), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("steps", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("pipeline_run_id", sa.String(128), nullable=True),
        sa.Column("pipeline_step_run_id", sa.String(128), nullable=True),
        sa.Column("pipeline_step_name", sa.String(128), nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("max_retries", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_jobs_type_status", "jobs", ["type", "status"])
    op.create_index("ix_jobs_dataset", "jobs", ["dataset_id", "dataset_version"])
    op.create_index("ix_jobs_pipeline_run_id", "jobs", ["pipeline_run_id"])
    op.create_index("ix_jobs_pipeline_step_run_id", "jobs", ["pipeline_step_run_id"])
    op.create_index("ix_jobs_status_queued_at", "jobs", ["status", "queued_at"])
    op.create_index("ix_jobs_status_locked_at", "jobs", ["status", "locked_at"])

    # ── job_events ────────────────────────────────────────────────────────────
    op.create_table(
        "job_events",
        sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column("job_id", sa.String(128), sa.ForeignKey("jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("level", sa.String(32), nullable=False),
        sa.Column("job_type", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("step_id", sa.String(128), nullable=True),
        sa.Column("step_name", sa.String(128), nullable=True),
        sa.Column("step_status", sa.String(32), nullable=True),
        sa.Column("pipeline_run_id", sa.String(128), nullable=True),
        sa.Column("pipeline_step_run_id", sa.String(128), nullable=True),
        sa.Column("pipeline_step_name", sa.String(128), nullable=True),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("attempt", sa.Integer, nullable=True),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_job_events_job_id_created_at", "job_events", ["job_id", "created_at"])
    op.create_index("ix_job_events_level_created_at", "job_events", ["level", "created_at"])
    op.create_index("ix_job_events_type_created_at", "job_events", ["type", "created_at"])
    op.create_index("ix_job_events_pipeline_run_id_created_at", "job_events", ["pipeline_run_id", "created_at"])

    # ── pipeline_runs ─────────────────────────────────────────────────────────
    op.create_table(
        "pipeline_runs",
        sa.Column("pipeline_run_id", sa.String(128), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("model_id", sa.String(128), nullable=True),
        sa.Column("model_version", sa.String(128), nullable=True),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_pipeline_runs_type_status", "pipeline_runs", ["type", "status"])
    op.create_index("ix_pipeline_runs_dataset", "pipeline_runs", ["dataset_id", "dataset_version"])
    op.create_index("ix_pipeline_runs_model", "pipeline_runs", ["model_id", "model_version"])
    op.create_index("ix_pipeline_runs_status_created_at", "pipeline_runs", ["status", "created_at"])

    # ── pipeline_step_runs ────────────────────────────────────────────────────
    op.create_table(
        "pipeline_step_runs",
        sa.Column("pipeline_step_run_id", sa.String(128), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(128), sa.ForeignKey("pipeline_runs.pipeline_run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(128), nullable=False),
        sa.Column("step_name", sa.String(128), nullable=False),
        sa.Column("step_order", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column("depends_on_step_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_pipeline_step_runs_pipeline_run_order", "pipeline_step_runs", ["pipeline_run_id", "step_order"])
    op.create_index("ix_pipeline_step_runs_pipeline_run_step_id", "pipeline_step_runs", ["pipeline_run_id", "step_id"])
    op.create_index("ix_pipeline_step_runs_job_id", "pipeline_step_runs", ["job_id"])
    op.create_index("ix_pipeline_step_runs_status", "pipeline_step_runs", ["status"])

    # ── execution_records ─────────────────────────────────────────────────────
    op.create_table(
        "execution_records",
        sa.Column("execution_id", sa.String(128), primary_key=True),
        sa.Column("execution_backend", sa.String(64), nullable=False),
        sa.Column("execution_kind", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_execution_records_kind_resource", "execution_records", ["execution_kind", "resource_id"])
    op.create_index("ix_execution_records_backend_external", "execution_records", ["execution_backend", "external_id"])
    op.create_index("ix_execution_records_status_created_at", "execution_records", ["status", "created_at"])

    # ── datasets ──────────────────────────────────────────────────────────────
    op.create_table(
        "datasets",
        sa.Column("dataset_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("default_version", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    # ── dataset_versions ──────────────────────────────────────────────────────
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("dataset_id", sa.String(128), sa.ForeignKey("datasets.dataset_id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'registered'")),
        sa.Column("manifest_uri", sa.Text, nullable=True),
        sa.Column("scene_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("sample_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("frame_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("channels", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_dataset_id", sa.String(128), nullable=True),
        sa.Column("source_dataset_version", sa.String(128), nullable=True),
        sa.Column("latest_validation_run_id", sa.String(128), nullable=True),
        sa.Column("validation_status", sa.String(32), nullable=True),
        sa.Column("should_block_pipeline", sa.Boolean, nullable=True),
        sa.Column("validation_report_uri", sa.Text, nullable=True),
        sa.Column("latest_profile_run_id", sa.String(128), nullable=True),
        sa.Column("profile_report_uri", sa.Text, nullable=True),
        sa.Column("latest_distribution_run_id", sa.String(128), nullable=True),
        sa.Column("distribution_report_uri", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("dataset_id", "version", name="uq_dataset_versions_dataset_id_version"),
    )
    op.create_index("ix_dataset_versions_dataset_id", "dataset_versions", ["dataset_id"])
    op.create_index("ix_dataset_versions_status", "dataset_versions", ["status"])
    op.create_index("ix_dataset_versions_latest_validation_run_id", "dataset_versions", ["latest_validation_run_id"])
    op.create_index("ix_dataset_versions_latest_profile_run_id", "dataset_versions", ["latest_profile_run_id"])
    op.create_index("ix_dataset_versions_latest_distribution_run_id", "dataset_versions", ["latest_distribution_run_id"])
    op.create_index("ix_dataset_versions_validation_status", "dataset_versions", ["validation_status"])
    op.create_index("ix_dataset_versions_should_block_pipeline", "dataset_versions", ["should_block_pipeline"])

    # ── dataset_run_records ───────────────────────────────────────────────────
    op.create_table(
        "dataset_run_records",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("dataset_id", sa.String(128), nullable=False),
        sa.Column("dataset_version", sa.String(128), nullable=False),
        sa.Column("pipeline_run_id", sa.String(128), nullable=True),
        sa.Column("pipeline_step_run_id", sa.String(128), nullable=True),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("artifact_root_uri", sa.Text, nullable=True),
        sa.Column("manifest_uri", sa.Text, nullable=True),
        sa.Column("dataset_manifest_uri", sa.Text, nullable=True),
        sa.Column("report_uri", sa.Text, nullable=True),
        sa.Column("export_uri", sa.Text, nullable=True),
        sa.Column("scope", sa.String(64), nullable=True),
        sa.Column("output_format", sa.String(64), nullable=True),
        sa.Column("summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_dataset_run_records_type_status", "dataset_run_records", ["type", "status"])
    op.create_index("ix_dataset_run_records_dataset", "dataset_run_records", ["dataset_id", "dataset_version"])
    op.create_index("ix_dataset_run_records_job_id", "dataset_run_records", ["job_id"])
    op.create_index("ix_dataset_run_records_pipeline_run_id", "dataset_run_records", ["pipeline_run_id"])
    op.create_index("ix_dataset_run_records_created_at", "dataset_run_records", ["created_at"])

    # ── scenes ────────────────────────────────────────────────────────────────
    op.create_table(
        "scenes",
        sa.Column("scene_id", sa.String(128), primary_key=True),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("raw_log_id", sa.String(128), nullable=True),
        sa.Column("segment_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("origin_type", sa.String(64), nullable=False),
        sa.Column("generation_method", sa.String(64), nullable=False),
        sa.Column("parent_scene_id", sa.String(128), nullable=True),
        sa.Column("lineage", JSONB, nullable=True),
        sa.Column("generation", JSONB, nullable=True),
        sa.Column("scene_manifest_uri", sa.Text, nullable=True),
        sa.Column("world_state_manifest_uri", sa.Text, nullable=True),
        sa.Column("artifact_root_uri", sa.Text, nullable=True),
        sa.Column("sample_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("frame_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("channels", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_scenes_dataset", "scenes", ["dataset_id", "dataset_version"])
    op.create_index("ix_scenes_raw_log_id", "scenes", ["raw_log_id"])
    op.create_index("ix_scenes_origin_type", "scenes", ["origin_type"])
    op.create_index("ix_scenes_generation_method", "scenes", ["generation_method"])
    op.create_index("ix_scenes_status", "scenes", ["status"])
    op.create_index("ix_scenes_parent_scene_id", "scenes", ["parent_scene_id"])

    # ── scene_run_records ─────────────────────────────────────────────────────
    op.create_table(
        "scene_run_records",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("scene_id", sa.String(128), nullable=True),
        sa.Column("scene_manifest_uri", sa.Text, nullable=True),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("source_scene_id", sa.String(128), nullable=True),
        sa.Column("target_scene_id", sa.String(128), nullable=True),
        sa.Column("raw_log_id", sa.String(128), nullable=True),
        sa.Column("raw_log_manifest_uri", sa.Text, nullable=True),
        sa.Column("raw_log_frame_index_uri", sa.Text, nullable=True),
        sa.Column("pipeline_run_id", sa.String(128), nullable=True),
        sa.Column("pipeline_step_run_id", sa.String(128), nullable=True),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("artifact_root_uri", sa.Text, nullable=True),
        sa.Column("manifest_uri", sa.Text, nullable=True),
        sa.Column("report_uri", sa.Text, nullable=True),
        sa.Column("package_uri", sa.Text, nullable=True),
        sa.Column("world_state_manifest_uri", sa.Text, nullable=True),
        sa.Column("summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_scene_run_records_type_status", "scene_run_records", ["type", "status"])
    op.create_index("ix_scene_run_records_scene_id", "scene_run_records", ["scene_id"])
    op.create_index("ix_scene_run_records_dataset", "scene_run_records", ["dataset_id", "dataset_version"])
    op.create_index("ix_scene_run_records_source_scene_id", "scene_run_records", ["source_scene_id"])
    op.create_index("ix_scene_run_records_target_scene_id", "scene_run_records", ["target_scene_id"])
    op.create_index("ix_scene_run_records_raw_log_id", "scene_run_records", ["raw_log_id"])
    op.create_index("ix_scene_run_records_job_id", "scene_run_records", ["job_id"])
    op.create_index("ix_scene_run_records_pipeline_run_id", "scene_run_records", ["pipeline_run_id"])
    op.create_index("ix_scene_run_records_created_at", "scene_run_records", ["created_at"])

    # ── scenario_sets ─────────────────────────────────────────────────────────
    op.create_table(
        "scenario_sets",
        sa.Column("scenario_set_id", sa.String(128), primary_key=True),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("scenario_set_uri", sa.Text, nullable=True),
        sa.Column("scenario_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("tags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_scenario_sets_dataset", "scenario_sets", ["dataset_id", "dataset_version"])

    # ── scenario_run_records ──────────────────────────────────────────────────
    op.create_table(
        "scenario_run_records",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("scenario_set_id", sa.String(128), nullable=True),
        sa.Column("scenario_set_uri", sa.Text, nullable=True),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("dataset_manifest_uri", sa.Text, nullable=True),
        sa.Column("pipeline_run_id", sa.String(128), nullable=True),
        sa.Column("pipeline_step_run_id", sa.String(128), nullable=True),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("artifact_root_uri", sa.Text, nullable=True),
        sa.Column("manifest_uri", sa.Text, nullable=True),
        sa.Column("report_uri", sa.Text, nullable=True),
        sa.Column("candidate_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("selected_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("rejected_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("ready_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("blocked_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("warning_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("average_score", sa.Float, nullable=True),
        sa.Column("summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_scenario_run_records_type_status", "scenario_run_records", ["type", "status"])
    op.create_index("ix_scenario_run_records_scenario_set_id", "scenario_run_records", ["scenario_set_id"])
    op.create_index("ix_scenario_run_records_dataset", "scenario_run_records", ["dataset_id", "dataset_version"])
    op.create_index("ix_scenario_run_records_job_id", "scenario_run_records", ["job_id"])
    op.create_index("ix_scenario_run_records_pipeline_run_id", "scenario_run_records", ["pipeline_run_id"])
    op.create_index("ix_scenario_run_records_created_at", "scenario_run_records", ["created_at"])

    # ── inference_runs ────────────────────────────────────────────────────────
    op.create_table(
        "inference_runs",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False, server_default="inference"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("dataset_id", sa.String(128), nullable=False),
        sa.Column("dataset_version", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("dataset_manifest_uri", sa.Text, nullable=True),
        sa.Column("inference_backend", sa.String(64), nullable=False, server_default="mock"),
        sa.Column("predictions_root_uri", sa.Text, nullable=True),
        sa.Column("prediction_manifest_uri", sa.Text, nullable=True),
        sa.Column("sample_count", sa.Integer, nullable=True),
        sa.Column("prediction_count", sa.Integer, nullable=True),
        sa.Column("pipeline_run_id", sa.String(128), nullable=True),
        sa.Column("pipeline_step_run_id", sa.String(128), nullable=True),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("artifact_root_uri", sa.Text, nullable=True),
        sa.Column("manifest_uri", sa.Text, nullable=True),
        sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_inference_runs_status", "inference_runs", ["status"])
    op.create_index("ix_inference_runs_dataset", "inference_runs", ["dataset_id", "dataset_version"])
    op.create_index("ix_inference_runs_model", "inference_runs", ["model_id", "model_version"])
    op.create_index("ix_inference_runs_job_id", "inference_runs", ["job_id"])
    op.create_index("ix_inference_runs_pipeline_run_id", "inference_runs", ["pipeline_run_id"])
    op.create_index("ix_inference_runs_created_at", "inference_runs", ["created_at"])

    # ── evaluation_runs ───────────────────────────────────────────────────────
    op.create_table(
        "evaluation_runs",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False, server_default="evaluation"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("dataset_id", sa.String(128), nullable=False),
        sa.Column("dataset_version", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=True),
        sa.Column("model_version", sa.String(128), nullable=True),
        sa.Column("inference_run_id", sa.String(128), nullable=True),
        sa.Column("predictions_root_uri", sa.Text, nullable=True),
        sa.Column("evaluator_id", sa.String(128), nullable=False, server_default="center-distance"),
        sa.Column("task_type", sa.String(64), nullable=False, server_default="detection"),
        sa.Column("sample_count", sa.Integer, nullable=True),
        sa.Column("evaluation_manifest_uri", sa.Text, nullable=True),
        sa.Column("metrics_uri", sa.Text, nullable=True),
        sa.Column("pipeline_run_id", sa.String(128), nullable=True),
        sa.Column("pipeline_step_run_id", sa.String(128), nullable=True),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("artifact_root_uri", sa.Text, nullable=True),
        sa.Column("manifest_uri", sa.Text, nullable=True),
        sa.Column("summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("class_metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])
    op.create_index("ix_evaluation_runs_task_type", "evaluation_runs", ["task_type"])
    op.create_index("ix_evaluation_runs_dataset", "evaluation_runs", ["dataset_id", "dataset_version"])
    op.create_index("ix_evaluation_runs_model", "evaluation_runs", ["model_id", "model_version"])
    op.create_index("ix_evaluation_runs_inference_run_id", "evaluation_runs", ["inference_run_id"])
    op.create_index("ix_evaluation_runs_job_id", "evaluation_runs", ["job_id"])
    op.create_index("ix_evaluation_runs_pipeline_run_id", "evaluation_runs", ["pipeline_run_id"])
    op.create_index("ix_evaluation_runs_created_at", "evaluation_runs", ["created_at"])

    # ── label_runs ────────────────────────────────────────────────────────────
    op.create_table(
        "label_runs",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("scene_id", sa.String(128), nullable=True),
        sa.Column("scene_manifest_uri", sa.Text, nullable=True),
        sa.Column("output_scene_manifest_uri", sa.Text, nullable=True),
        sa.Column("output_label_uri", sa.Text, nullable=True),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("dataset_manifest_uri", sa.Text, nullable=True),
        sa.Column("output_dataset_id", sa.String(128), nullable=True),
        sa.Column("output_dataset_version", sa.String(128), nullable=True),
        sa.Column("output_dataset_manifest_uri", sa.Text, nullable=True),
        sa.Column("labeler_id", sa.String(128), nullable=True),
        sa.Column("labeler_backend", sa.String(64), nullable=False, server_default="vlm"),
        sa.Column("sample_count", sa.Integer, nullable=True),
        sa.Column("labeled_sample_count", sa.Integer, nullable=True),
        sa.Column("labeled_scene_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("annotation_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("pipeline_run_id", sa.String(128), nullable=True),
        sa.Column("pipeline_step_run_id", sa.String(128), nullable=True),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("artifact_root_uri", sa.Text, nullable=True),
        sa.Column("manifest_uri", sa.Text, nullable=True),
        sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("class_metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_label_runs_type_status", "label_runs", ["type", "status"])
    op.create_index("ix_label_runs_scene_id", "label_runs", ["scene_id"])
    op.create_index("ix_label_runs_dataset", "label_runs", ["dataset_id", "dataset_version"])
    op.create_index("ix_label_runs_job_id", "label_runs", ["job_id"])
    op.create_index("ix_label_runs_pipeline_run_id", "label_runs", ["pipeline_run_id"])
    op.create_index("ix_label_runs_created_at", "label_runs", ["created_at"])

    # ── models ────────────────────────────────────────────────────────────────
    op.create_table(
        "models",
        sa.Column("model_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    # ── model_versions ────────────────────────────────────────────────────────
    op.create_table(
        "model_versions",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("model_id", sa.String(128), sa.ForeignKey("models.model_id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("backend", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("model_uri", sa.Text, nullable=True),
        sa.Column("endpoint_url", sa.Text, nullable=True),
        sa.Column("artifact_manifest_uri", sa.Text, nullable=True),
        sa.Column("runtime", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("model_id", "version", name="uq_model_versions_model_id_version"),
    )
    op.create_index("ix_model_versions_model_id", "model_versions", ["model_id"])
    op.create_index("ix_model_versions_task_type", "model_versions", ["task_type"])
    op.create_index("ix_model_versions_status", "model_versions", ["status"])

    # ── artifacts_refs ────────────────────────────────────────────────────────
    op.create_table(
        "artifacts_refs",
        sa.Column("artifact_id", sa.String(128), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("uri", sa.Text, nullable=False),
        sa.Column("backend", sa.String(64), nullable=True),
        sa.Column("owner_type", sa.String(64), nullable=True),
        sa.Column("owner_id", sa.String(128), nullable=True),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("scene_id", sa.String(128), nullable=True),
        sa.Column("scenario_set_id", sa.String(128), nullable=True),
        sa.Column("run_id", sa.String(128), nullable=True),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column("pipeline_run_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("media_type", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("checksum", sa.String(255), nullable=True),
    )
    op.create_index("ix_artifact_refs_kind", "artifacts_refs", ["kind"])
    op.create_index("ix_artifact_refs_run_id", "artifacts_refs", ["run_id"])
    op.create_index("ix_artifact_refs_job_id", "artifacts_refs", ["job_id"])
    op.create_index("ix_artifact_refs_pipeline_run_id", "artifacts_refs", ["pipeline_run_id"])
    op.create_index("ix_artifact_refs_dataset", "artifacts_refs", ["dataset_id", "dataset_version"])
    op.create_index("ix_artifact_refs_scene_id", "artifacts_refs", ["scene_id"])
    op.create_index("ix_artifact_refs_owner", "artifacts_refs", ["owner_type", "owner_id"])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("artifacts_refs")
    op.drop_table("model_versions")
    op.drop_table("models")
    op.drop_table("label_runs")
    op.drop_table("evaluation_runs")
    op.drop_table("inference_runs")
    op.drop_table("scenario_run_records")
    op.drop_table("scenario_sets")
    op.drop_table("scene_run_records")
    op.drop_table("scenes")
    op.drop_table("dataset_run_records")
    op.drop_table("dataset_versions")
    op.drop_table("datasets")
    op.drop_table("execution_records")
    op.drop_table("pipeline_step_runs")
    op.drop_table("pipeline_runs")
    op.drop_table("job_events")
    op.drop_table("jobs")
