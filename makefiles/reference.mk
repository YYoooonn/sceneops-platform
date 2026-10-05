# --------------------
# Reference corpus (config/reference/<corpus>/, tools/dataset-acquisition)
#
# reference-data-bootstrap -- fingerprint the source, materialize the batch MCAPs
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
