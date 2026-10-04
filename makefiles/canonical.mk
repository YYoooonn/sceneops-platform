# --------------------
# Canonical development baseline (v0.0)
#
# canonical-bootstrap — create-or-verify the frozen v0.0 baseline family
#                        (sceneops-scenes / sceneops-episodes /
#                        sceneops-canonical, all version v0.0) from real
#                        nuScenes source data + the running local stack.
#                        Never mutates an already-matching baseline; fails
#                        loudly (never auto-repairs) on partial/mismatched
#                        state. See scripts/canonical/canonical_bootstrap.sh
#                        and docs/development/canonical-baseline.md.
# canonical-verify     — read-only re-check of the same v0.0 contract; never
#                        creates or mutates anything.
#
# Independent of `local-reset` on purpose: local-reset's job is destroying
# generated SceneOps state, canonical-bootstrap's job is (re)creating the
# canonical development baseline on top of a running stack. See
# docs/development/canonical-baseline.md for the full contract.
#
# UNAVAILABLE until ADR-007 implementation step 11: the v0.0 baseline was
# built by the removed dataset_scene_ingestion pipeline; both scripts exit 3
# with a message until the baseline is regenerated from recordings.
# --------------------

.PHONY: canonical-bootstrap
canonical-bootstrap:
	chmod +x scripts/canonical/canonical_bootstrap.sh
	API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) scripts/canonical/canonical_bootstrap.sh

.PHONY: canonical-verify
canonical-verify:
	chmod +x scripts/canonical/canonical_verify.sh
	API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) scripts/canonical/canonical_verify.sh
