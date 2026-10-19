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
