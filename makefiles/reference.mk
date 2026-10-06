# --------------------
# Reference corpus (config/reference/<corpus>/, tools/dataset-acquisition)
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
	chmod +x scripts/reference/*.sh
	ENV_FILE=$(ENV_FILE) REFERENCE_SCOPE=$(REFERENCE_SCOPE) UPDATE_LOCK=$(UPDATE_LOCK) \
		scripts/reference/reference_data.sh bootstrap

.PHONY: reference-data-verify
reference-data-verify:
	chmod +x scripts/reference/*.sh
	ENV_FILE=$(ENV_FILE) REFERENCE_SCOPE=$(REFERENCE_SCOPE) \
		scripts/reference/reference_data.sh verify

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
# REQUIRE_CLEAN=1 makes both also fail on any RobotRun or Dataset outside the
# contract: the check of the dedicated reference environment (a general
# development platform may hold others). Every report carries a compact `state`.
# Both print one JSON report on stdout (progress on stderr).
# --------------------

REQUIRE_CLEAN ?=

.PHONY: reference-contract-verify
reference-contract-verify:
	@ENV_FILE=$(ENV_FILE) API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) \
		python3 scripts/reference/reference_contract.py verify $(if $(REQUIRE_CLEAN),--require-clean)

.PHONY: reference-contract-bootstrap
reference-contract-bootstrap:
	@MAKE=$(MAKE) ENV_FILE=$(ENV_FILE) API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) \
		python3 scripts/reference/reference_contract.py bootstrap $(if $(REQUIRE_CLEAN),--require-clean)
