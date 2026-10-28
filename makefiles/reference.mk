# --------------------
# Reference corpus (config/reference/<corpus>/, integrations/dataset-acquisition)
#
# reference-data-bootstrap -- fingerprint the source, materialize the locked MCAPs
#                             of a scope into data/reference/<corpus>/recordings/
#                             (write-once, git-ignored), verify them against
#                             corpus.lock.json and check L1 conformance.
#                             UPDATE_LOCK=1 re-locks what the current source and
#                             tool produce; nothing else ever writes the lock.
# reference-data-verify    -- the same checks, read-only.
#
# Both run one-shot containers only (--no-deps): they never start or touch
# PostgreSQL, MinIO, Redis or Kafka.
#
# REFERENCE_SCOPE: smoke-1 (default) = scene-0061; nuscenes-mini-full-10 = all
# ten nuScenes v1.0-mini scenes.
# --------------------

REFERENCE_SCOPE ?= smoke-1
UPDATE_LOCK     ?=

.PHONY: reference-data-bootstrap
reference-data-bootstrap:
	$(COMPOSE) --profile acquisition build dataset-acquisition
	chmod +x tools/reference/*.sh
	ENV_FILE=$(ENV_FILE) REFERENCE_SCOPE=$(REFERENCE_SCOPE) UPDATE_LOCK=$(UPDATE_LOCK) \
		tools/reference/reference_data.sh bootstrap

.PHONY: reference-data-verify
reference-data-verify:
	chmod +x tools/reference/*.sh
	ENV_FILE=$(ENV_FILE) REFERENCE_SCOPE=$(REFERENCE_SCOPE) \
		tools/reference/reference_data.sh verify

# --------------------
# Pinned raw sources (config/reference/<source>/source.json, tools/reference/raw_source.py)
#
# reference-source-fetch   -- download the locked files of SOURCE missing under its
#                             local_root (data/raw/..., git-ignored) at the pinned
#                             upstream revision and verify the local tree against
#                             source.lock.json. UPDATE_LOCK=1 re-locks from the
#                             upstream listing; nothing else ever writes the lock.
# reference-source-verify  -- the same local checks, offline and read-only.
#
# Standard library only; no container, no stack.
# --------------------

SOURCE     ?= lerobot-pusht
RAW_SOURCE := python3 -I tools/reference/raw_source.py

.PHONY: reference-source-fetch
reference-source-fetch:
	@$(RAW_SOURCE) fetch --source $(SOURCE) $(if $(UPDATE_LOCK),--update-lock)

.PHONY: reference-source-verify
reference-source-verify:
	@$(RAW_SOURCE) verify --source $(SOURCE)

# --------------------
# Golden reference contract (config/reference/<corpus>/reference_contract.json,
# docs/development/reference-contract.md)
#
# reference-contract-verify    -- read-only: exactly the contract's RobotRuns
#                                 (each fixture x {recording_import,
#                                 streaming_acquisition}), their Scenes and
#                                 Episodes, against the corpus lock. Reports the
#                                 contract and the non-contract RobotRuns.
# reference-contract-bootstrap -- converge on the contract by composing
#                                 canonical-bootstrap and streaming-bootstrap
#                                 with the contract's identities: reuse what is
#                                 complete, create or recover what is missing,
#                                 fail loudly on a conflicting identity. Needs
#                                 `make local-up` and the prepared corpus.
# The contract is valid when its 20 RobotRuns, Scenes and Episodes are. State outside it
# is reported, not failed: non-contract RobotRuns, derived test state (the fixed
# sceneops-test-* DatasetVersions of the REFERENCE_DERIVED workflows) and foreign
# datasets, each apart. REQUIRE_PRISTINE=1 makes both commands also fail on any of
# them: the check of a freshly reset reference environment. Every report carries a
# compact `state`. Both print one JSON report on stdout (progress on stderr).
# --------------------

REQUIRE_PRISTINE ?=

ifneq ($(REQUIRE_CLEAN),)
$(error REQUIRE_CLEAN was renamed REQUIRE_PRISTINE (docs/development/reference-contract.md))
endif

# The object store the source-grounded verifier reads (read-only).
REFERENCE_VERIFY_ENV := API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) \
	MINIO_ENDPOINT_URL="http://localhost:$${MINIO_API_PORT:-9000}" \
	MINIO_ROOT_USER=$(MINIO_ROOT_USER) MINIO_ROOT_PASSWORD=$(MINIO_ROOT_PASSWORD)

.PHONY: reference-contract-verify
reference-contract-verify:
	@ENV_FILE=$(ENV_FILE) $(REFERENCE_VERIFY_ENV) \
		python3 tools/reference/reference_contract.py verify $(if $(REQUIRE_PRISTINE),--require-pristine)

.PHONY: reference-contract-bootstrap
reference-contract-bootstrap:
	@MAKE=$(MAKE) ENV_FILE=$(ENV_FILE) API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) \
		python3 tools/reference/reference_contract.py bootstrap $(if $(REQUIRE_PRISTINE),--require-pristine)

# --------------------
# Source-grounded verification (tools/reference/reference_verify.py,
# docs/development/reference-verification.md). Expectations are derived from the
# raw inputs by the documented rules, never from platform output.
#
# reference-verify-sources   -- raw nuScenes tables and files <-> locked recordings
#                               <-> corpus lock <-> reference labels, then the
#                               source negative controls. Needs data/raw/nuscenes
#                               (NUSCENES_DATAROOT) and the prepared corpus; no stack.
# reference-expectations     -- re-derive reference_expectations.json from the
#                               locked recordings and compare it with the committed
#                               file; WRITE=1 replaces it. No stack.
# reference-verify-selftest  -- a synthetic platform state built from the derivation
#                               passes and every platform negative control is
#                               detected (proves the checks, not the platform). No stack.
# reference-verify           -- the running platform's contract state against the
#                               derivation, then the platform negative controls
#                               (read-only; DEEP=1 also hashes every payload object;
#                               MODE=recording_import|streaming_acquisition verifies
#                               one ingestion mode, FIXTURE=<id> one fixture and its
#                               baseline-<fixture> DatasetVersion; neither is the
#                               contract's acceptance). `reference-contract-verify` runs it.
# --------------------

WRITE ?=
DEEP  ?=
MODE  ?=
REFERENCE_VERIFY := uv run --no-sync python -I tools/reference/reference_verify.py

.PHONY: reference-verify-sources
reference-verify-sources:
	@$(REFERENCE_VERIFY) sources $(if $(FIXTURE),--fixture $(FIXTURE))

.PHONY: reference-expectations
reference-expectations:
	@$(REFERENCE_VERIFY) derive $(if $(WRITE),--write)

.PHONY: reference-verify-selftest
reference-verify-selftest:
	@$(REFERENCE_VERIFY) selftest

.PHONY: reference-verify
reference-verify:
	@$(REFERENCE_VERIFY_ENV) $(REFERENCE_VERIFY) verify $(if $(DEEP),--deep) $(if $(MODE),--mode $(MODE)) \
		$(if $(FIXTURE),--fixture $(FIXTURE))

# --------------------
# LeRobot Episodes from the pinned pusht snapshot (tools/reference/lerobot_verify.py,
# docs/development/reference-verification.md "LeRobot Episodes"). The expectations are
# derived from the raw tree at data/raw/lerobot/pusht by the documented rules.
#
# reference-verify-lerobot-selftest  -- a synthetic state built from the derivation
#                                       passes and every negative control is detected
#                                       [EPISODE=<n>]. No stack.
# reference-verify-lerobot           -- the platform's Episodes of SNAPSHOT_ID against
#                                       the derivation, then the negative controls
#                                       (read-only) [EPISODE=<n>] [DATASET=<id>/<version>]
# --------------------

LEROBOT_VERIFY := uv run --no-sync python -I tools/reference/lerobot_verify.py \
	$(if $(EPISODE),--episode $(EPISODE))

.PHONY: reference-verify-lerobot-selftest
reference-verify-lerobot-selftest:
	@$(LEROBOT_VERIFY) selftest

.PHONY: reference-verify-lerobot
reference-verify-lerobot:
	@test -n "$(SNAPSHOT_ID)" || { echo "SNAPSHOT_ID=<snapshot id> is required"; exit 2; }
	@$(REFERENCE_VERIFY_ENV) $(LEROBOT_VERIFY) verify --snapshot-id $(SNAPSHOT_ID) \
		$(if $(DATASET),--dataset $(DATASET))

# --------------------
# nuScenes Scenes from the scene-0061 slice snapshot (tools/reference/nuscenes_scene_verify.py,
# docs/development/reference-verification.md "nuScenes Scenes"). The expectations are
# derived from the raw tree at data/raw/nuscenes by the documented rules.
#
# reference-verify-nuscenes-scene-selftest  -- a synthetic state built from the derivation
#                                              passes and every negative control is
#                                              detected. No stack.
# reference-verify-nuscenes-scene           -- the platform's Scenes of SNAPSHOT_ID against
#                                              the derivation, then the negative controls
#                                              (read-only) [UNIT_KEY=<scene token>]
#                                              [DATASET=<id>/<version>]
# --------------------

NUSCENES_SCENE_VERIFY := uv run --no-sync python -I tools/reference/nuscenes_scene_verify.py \
	$(if $(UNIT_KEY),--unit-key $(UNIT_KEY))

.PHONY: reference-verify-nuscenes-scene-selftest
reference-verify-nuscenes-scene-selftest:
	@$(NUSCENES_SCENE_VERIFY) selftest

.PHONY: reference-verify-nuscenes-scene
reference-verify-nuscenes-scene:
	@test -n "$(SNAPSHOT_ID)" || { echo "SNAPSHOT_ID=<snapshot id> is required"; exit 2; }
	@$(REFERENCE_VERIFY_ENV) $(NUSCENES_SCENE_VERIFY) verify --snapshot-id $(SNAPSHOT_ID) \
		$(if $(DATASET),--dataset $(DATASET))

# --------------------
# Mixed-source DatasetVersions (tools/reference/mixed_source_verify.py,
# docs/development/reference-verification.md "Mixed-source DatasetVersions"). The
# expected composition of config/reference/mixed-sources/composition.json is derived
# from the locked scene-0061 recording and the raw nuScenes and pusht trees.
#
# reference-verify-mixed-expectations  -- derive and compare with
#                                         config/reference/mixed-sources/expectations.json
#                                         (WRITE=1: replace the file). No stack.
# reference-verify-mixed-selftest      -- a synthetic composition (by pins and by
#                                         selector) passes and every negative control is
#                                         detected. No stack.
# reference-verify-mixed               -- the platform's DatasetVersion DATASET against
#                                         the derivation, then the negative controls
#                                         (read-only) DATASET=<id>/<version>
# --------------------

MIXED_SOURCE_VERIFY := uv run --no-sync python -I tools/reference/mixed_source_verify.py

.PHONY: reference-verify-mixed-expectations
reference-verify-mixed-expectations:
	@$(MIXED_SOURCE_VERIFY) derive $(if $(WRITE),--write)

.PHONY: reference-verify-mixed-selftest
reference-verify-mixed-selftest:
	@$(MIXED_SOURCE_VERIFY) selftest

.PHONY: reference-verify-mixed
reference-verify-mixed:
	@test -n "$(DATASET)" || { echo "DATASET=<dataset_id>/<version> is required"; exit 2; }
	@$(REFERENCE_VERIFY_ENV) $(MIXED_SOURCE_VERIFY) verify --dataset $(DATASET)
