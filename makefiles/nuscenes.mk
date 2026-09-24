# --------------------
# nuScenes integration (SceneOps V2 Request 4.4/4.5/4.6/4.6A/4.6B)
# --------------------
#
# sceneops_integrations.nuscenes (packages/sceneops-integrations) is the
# SDK-bound INGEST runtime extracted from apps/worker (Request 4.4,
# raw-log; Request 4.6B, direct SceneManifest ingest with ground-truth
# annotations -- see scene_ingest.py's own docstring for why that second
# capability was migrated rather than deleted). It deliberately does NOT
# declare `nuscenes-devkit` as its own dependency. As of Request 4.6B,
# `apps/worker` no longer depends on `nuscenes-devkit` at all either --
# both nuScenes job handlers run through this HTTP service instead of
# importing the SDK in-process, so isolating nuScenes here was never
# primarily about a version conflict (unlike LeRobot's): it's about giving
# the CONTAINER image below a minimal, reproducible, DB/Celery-free
# dependency closure. tools/nuscenes-integration/ is a separate,
# non-workspace-member uv project with its own independent uv.lock that
# depends on the *existing* sceneops-core/sceneops-storage/
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

# Minimal container-level smoke test (Request 4.5 §6) -- build image ->
# start container -> parse a real IntegrationRequest -> run real
# nuscenes-devkit against the real /data/raw/nuscenes v1.0-mini fixture ->
# write raw-log artifacts to a real MinIO ArtifactStore -> verify a valid
# IntegrationResult, including reading the written artifacts back out of
# MinIO directly. Exercises the container's CLI entrypoint (mode=raw_log),
# not the HTTP service that's the image's default command as of Request
# 4.6A -- see scripts/e2e/nuscenes_container_smoke.sh's own header for the
# full two-process split and prerequisites (`make local-up`,
# `make nuscenes-image`).
.PHONY: nuscenes-container-smoke
nuscenes-container-smoke:
	chmod +x scripts/e2e/nuscenes_container_smoke.sh
	MINIO_ROOT_USER=$(MINIO_ROOT_USER) MINIO_ROOT_PASSWORD=$(MINIO_ROOT_PASSWORD) MINIO_BUCKET=$(MINIO_BUCKET) \
	scripts/e2e/nuscenes_container_smoke.sh
