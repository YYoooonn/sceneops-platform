# --------------------
# nuScenes integration (SceneOps V2 Request 4.4/4.5)
# --------------------
#
# sceneops_integrations.nuscenes (packages/sceneops-integrations) is the
# SDK-bound INGEST runtime extracted from apps/worker in Request 4.4. It
# deliberately does NOT declare `nuscenes-devkit` as its own dependency --
# nuscenes-devkit remains, for now, also a direct apps/worker dependency
# (its separate legacy/direct SceneManifest ingestion path still imports
# it -- see apps/worker/pyproject.toml's own comment), so unlike LeRobot's
# isolation this is not about dodging a numpy conflict: it's about giving
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

.PHONY: nuscenes-image
nuscenes-image:
	docker build -f tools/nuscenes-integration/Dockerfile -t sceneops-platform/nuscenes-integration:local .

# Minimal container-level smoke test (Request 4.5 §6) -- build image ->
# start container -> parse a real IntegrationRequest -> run real
# nuscenes-devkit against the real /data/raw/nuscenes v1.0-mini fixture ->
# write raw-log artifacts to a real MinIO ArtifactStore -> verify a valid
# IntegrationResult, including reading the written artifacts back out of
# MinIO directly. See scripts/e2e/nuscenes_container_smoke.sh's own header
# for the full two-process split and prerequisites (`make local-up`,
# `make nuscenes-image`).
.PHONY: nuscenes-container-smoke
nuscenes-container-smoke:
	chmod +x scripts/e2e/nuscenes_container_smoke.sh
	MINIO_ROOT_USER=$(MINIO_ROOT_USER) MINIO_ROOT_PASSWORD=$(MINIO_ROOT_PASSWORD) MINIO_BUCKET=$(MINIO_BUCKET) \
	scripts/e2e/nuscenes_container_smoke.sh
