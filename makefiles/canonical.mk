# --------------------
# Canonical L1/L2 baseline (docs/development/canonical-baseline.md)
#
# canonical-bootstrap -- developer/test orchestration, not a Pipeline: the
#                        prepared reference-corpus recordings -> one RobotRun per
#                        fixture -> canonical Scenes -> canonical Episodes ->
#                        validate/profile -> verify. create-or-verify: reuses a
#                        registered RobotRun (which must pin the locked
#                        recording), converges on registered revisions, fails
#                        loudly on a changed producer. Converts no source data:
#                        run `make reference-data-bootstrap` first. Builds nothing
#                        derived (no labels, views, ScenarioSets, predictions,
#                        evaluations or exports).
# canonical-verify    -- read-only re-check of the same baseline through the
#                        API; creates and mutates nothing.
#
# REFERENCE_SCOPE (default smoke-1; nuscenes-mini-full-10) or FIXTURE selects the
# fixtures; the baseline is the golden reference contract's recording_import baseline
# whatever the selection (smoke-1 = scene-0061 of it). A BASELINE_ID of another name
# registers non-contract RobotRuns and needs DISPOSABLE_RUNTIME=1.
# Independent of `local-reset`, which only destroys state.
# --------------------

FIXTURE ?=

CANONICAL_ENV = $(E2E_ENV) REFERENCE_SCOPE=$(REFERENCE_SCOPE) $(if $(FIXTURE),FIXTURE=$(FIXTURE))

.PHONY: canonical-bootstrap
canonical-bootstrap:
	$(COMPOSE) --profile acquisition build dataset-acquisition
	chmod +x scripts/canonical/*.sh
	$(CANONICAL_ENV) scripts/canonical/canonical_bootstrap.sh

.PHONY: canonical-verify
canonical-verify:
	chmod +x scripts/canonical/*.sh
	$(CANONICAL_ENV) scripts/canonical/canonical_verify.sh
