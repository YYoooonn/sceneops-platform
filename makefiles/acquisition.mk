# --------------------
# External dataset acquisition tool
# --------------------
#
# tools/dataset-acquisition converts an external dataset into L1 acquisition
# input: a finalized, sensor-bearing ROS 2 MCAP (ADR-007 §29.13). It is its
# own uv project with its own uv.lock, outside the root workspace, and
# depends on no SceneOps package (I-36, enforced by its
# tests/test_import_boundary.py). Its output enters SceneOps only through
# the Recording Publisher and REGISTER_ROBOT_RUN.

.PHONY: acquisition-sync
acquisition-sync:
	cd tools/dataset-acquisition && uv sync --group dev --locked

.PHONY: acquisition-lock
acquisition-lock:
	cd tools/dataset-acquisition && uv lock

# Unit tests on a synthetic nuScenes dataroot, the import-boundary test, and
# the real nuScenes v1.0-mini conversion test (skipped when
# data/raw/nuscenes is absent).
.PHONY: acquisition-test
acquisition-test:
	cd tools/dataset-acquisition && uv run --locked pytest -v

# The tool's container image, built from tools/dataset-acquisition alone (its
# own uv.lock; no SceneOps source in the build context). Run one-shot through
# compose/acquisition.yaml:
#   docker compose --env-file .env.local --profile acquisition run --rm \
#     dataset-acquisition nuscenes --dataroot /input/nuscenes \
#     --source-unit scene-0061 --output /recordings/scene-0061.mcap
.PHONY: acquisition-image
acquisition-image:
	$(COMPOSE) --profile acquisition build dataset-acquisition dataset-replay

# I-36 at container level: the image contains, exposes and loads no SceneOps
# package.
.PHONY: acquisition-image-check
acquisition-image-check:
	$(COMPOSE) --profile acquisition run --rm -T --entrypoint python \
		dataset-acquisition - < scripts/checks/acquisition_image_boundary.py
	$(COMPOSE) --profile acquisition run --rm -T --entrypoint python \
		dataset-replay - < scripts/checks/acquisition_image_boundary.py

# Black-box batch acquisition E2E: dataset-acquisition container -> MCAP
# (Docker volume) -> recording-publisher container (L1 conformance +
# publication) -> FastAPI (POST /robot-runs:register, Job polling,
# RobotRun / Artifact reads, ingest_robot_states) -> idempotent retry.
# The host needs only Docker Compose, curl, jq and the API port.
# Prerequisite: `make local-up` with images built from the current tree.
# Creates a RobotRun with a fresh robot_run_id each run (its deliverable,
# not a leak); no Scene or Episode is built.
.PHONY: e2e-batch-acquisition
e2e-batch-acquisition: acquisition-image
	chmod +x scripts/e2e/e2e_batch_acquisition.sh
	SOURCE_UNIT=$(or $(SCENE),scene-0061) API_BASE_URL=$(API_BASE_URL) ENV_FILE=$(ENV_FILE) \
	scripts/e2e/e2e_batch_acquisition.sh
