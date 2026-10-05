# --------------------
# Canonical L1/L2 baseline (docs/development/canonical-baseline.md)
#
# canonical-bootstrap -- developer/test orchestration, not a Pipeline: dataset
#                        fixture -> RobotRun -> canonical Scenes -> canonical
#                        Episodes -> validate/profile -> verify. create-or-
#                        verify: reuses a registered RobotRun, converges on
#                        registered revisions, fails loudly on a changed
#                        producer. Builds nothing derived (no labels, views,
#                        ScenarioSets, predictions, evaluations or exports).
# canonical-verify    -- read-only re-check of the same baseline through the
#                        API; creates and mutates nothing.
#
# BASELINE_ID (default `canonical`) names the baseline; SCENE selects the
# nuScenes scene. Independent of `local-reset`, which only destroys state.
# --------------------

.PHONY: canonical-bootstrap
canonical-bootstrap: acquisition-image
	chmod +x scripts/canonical/*.sh
	$(E2E_ENV) scripts/canonical/canonical_bootstrap.sh

.PHONY: canonical-verify
canonical-verify:
	chmod +x scripts/canonical/*.sh
	$(E2E_ENV) scripts/canonical/canonical_verify.sh
