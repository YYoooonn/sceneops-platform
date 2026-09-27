# --------------------
# nuScenes integration
# --------------------
#
# sceneops_integrations.nuscenes (packages/sceneops-integrations) is the
# SDK-bound INGEST runtime -- it covers both the raw-log ingestion path and
# direct SceneManifest ingest with ground-truth annotations (see
# scene_ingest.py's own docstring). It deliberately does NOT declare
# `nuscenes-devkit` as its own dependency. `apps/worker` does not depend on
# `nuscenes-devkit` at all either -- both nuScenes job handlers run through
# this HTTP service instead of importing the SDK in-process, so isolating
# nuScenes here was never primarily about a version conflict (unlike
# LeRobot's): it's about giving the CONTAINER image below a minimal,
# reproducible, DB/Celery-free dependency closure. tools/nuscenes-integration/
# is a separate, non-workspace-member uv project with its own independent
# uv.lock that depends on the *existing* sceneops-core/sceneops-storage/
# sceneops-integrations code via editable path sources, plus
# nuscenes-devkit directly -- nothing is duplicated, only re-locked in
# isolation. See tools/nuscenes-integration/README.md for the full
# rationale.

.PHONY: nuscenes-sync
nuscenes-sync:
	cd tools/nuscenes-integration && uv sync --group dev --locked

.PHONY: nuscenes-lock
nuscenes-lock:
	cd tools/nuscenes-integration && uv lock

# Runs packages/sceneops-integrations/tests/ from this project's own
# isolated venv (nuscenes-devkit installed there, never in the base
# workspace venv -- `make test` skips these cleanly via
# pytest.importorskip("nuscenes"), same convention `make lerobot-test`
# already uses for lerobot).
.PHONY: nuscenes-test
nuscenes-test:
	cd tools/nuscenes-integration && uv run pytest ../../packages/sceneops-integrations/tests/ -v

.PHONY: nuscenes-image
nuscenes-image:
	docker build -f tools/nuscenes-integration/Dockerfile -t sceneops-platform/nuscenes-integration:local .

# Minimal container-level smoke test -- build image -> start container ->
# parse a real IntegrationRequest -> run real nuscenes-devkit against the
# real /data/raw/nuscenes v1.0-mini fixture -> write raw-log artifacts to a
# real MinIO ArtifactStore -> verify a valid IntegrationResult, including
# reading the written artifacts back out of MinIO directly. Exercises the
# container's CLI entrypoint (mode=raw_log), not the HTTP service that's the
# image's default command -- see scripts/e2e/smoke_nuscenes_container.sh's
# own header for the full two-process split and prerequisites
# (`make local-up`, `make nuscenes-image`).
#
# An engineering/transport-boundary check, not a domain workflow -- lives in
# the smoke-* namespace alongside smoke-lerobot-container/smoke-api (see
# makefiles/e2e.mk's "Smoke" section).
.PHONY: smoke-nuscenes-container
smoke-nuscenes-container:
	chmod +x scripts/e2e/smoke_nuscenes_container.sh
	MINIO_ROOT_USER=$(MINIO_ROOT_USER) MINIO_ROOT_PASSWORD=$(MINIO_ROOT_PASSWORD) MINIO_BUCKET=$(MINIO_BUCKET) \
	scripts/e2e/smoke_nuscenes_container.sh
