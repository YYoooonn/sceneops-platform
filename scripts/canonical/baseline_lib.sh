#!/usr/bin/env bash
# baseline_lib.sh — identity and verification of a canonical L1/L2 baseline.
#
# A baseline is: one RobotRun per source unit (acquired from the dataset
# fixture, registered through the production path) and, in one DatasetVersion,
# the canonical Scenes and Episodes built from those RobotRuns, validated and
# profiled. It contains nothing derived (no labels, sample views, ScenarioSets,
# predictions, evaluations or learning exports): those are L3 workflows run on
# top of a baseline.
#
# Sourced by canonical_bootstrap.sh and canonical_verify.sh after
# scripts/e2e/lib.sh. Every read goes through the FastAPI control plane.

BASELINE_ID="${BASELINE_ID:-canonical}"
SOURCE_VERSION="${SOURCE_VERSION:-v1.0-mini}"
# Space-separated nuScenes scenes; one RobotRun each.
SOURCE_UNITS="${SOURCE_UNITS:-${SOURCE_UNIT:-scene-0061}}"
ROBOT_ID="${ROBOT_ID:-robot-$BASELINE_ID}"
DATASET_ID="${DATASET_ID:-sceneops-$BASELINE_ID}"
DATASET_VERSION="${DATASET_VERSION:-baseline}"

# baseline_run_id <source-unit>
baseline_run_id() {
  echo "run-$BASELINE_ID-$1"
}

# baseline_verify <api>
# Read-only. Checks the baseline through supported APIs and artifact pins and
# prints one JSON summary on stdout; exits non-zero on the first violation.
baseline_verify() {
  local api="$1" unit run_id run recording scenes episodes version
  local scene_count episode_count run_ids="[]"

  for unit in $SOURCE_UNITS; do
    run_id="$(baseline_run_id "$unit")"
    run="$(api_get "$api" "/robot-runs/$run_id" 2>/dev/null)" \
      || fail "baseline RobotRun $run_id is not registered"
    recording="$(api_get "$api" "/artifacts/$(echo "$run" | jq -r '.robotRun.recordingArtifactId')" | jq -c '.artifact')"
    echo "$recording" | jq -e '.checksum | startswith("sha256:")' >/dev/null \
      || fail "recording of $run_id pins no sha256 checksum"
    run_ids="$(echo "$run_ids" | jq -c --arg r "$run_id" '. + [$r]')"
  done

  scenes="$(api_get "$api" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500")"
  episodes="$(api_get "$api" "/episodes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION&limit=500")"
  scene_count="$(echo "$scenes" | jq '.scenes | length')"
  episode_count="$(echo "$episodes" | jq '.episodes | length')"
  [ "$scene_count" -ge 1 ] || fail "baseline $DATASET_ID/$DATASET_VERSION has no Scene"
  [ "$episode_count" -ge 1 ] || fail "baseline $DATASET_ID/$DATASET_VERSION has no Episode"

  echo "$scenes" | jq -e --argjson runs "$run_ids" \
    '[.scenes[].robotRunId] | all(. as $r | $runs | index($r) != null)' >/dev/null \
    || fail "a Scene points at a RobotRun outside the baseline"
  echo "$episodes" | jq -e --argjson runs "$run_ids" \
    '[.episodes[].robotRunId] | all(. as $r | $runs | index($r) != null)' >/dev/null \
    || fail "an Episode points at a RobotRun outside the baseline"

  # Every record pins exactly the checksum of its manifest ArtifactRecord.
  local id checksum artifact pins
  pins="$({
    echo "$scenes" | jq -r '.scenes[] | "\(.manifestArtifactId) \(.manifestChecksum)"'
    echo "$episodes" | jq -r '.episodes[] | "\(.manifestArtifactId) \(.manifestChecksum)"'
  })"
  while read -r id checksum; do
    artifact="$(api_get "$api" "/artifacts/$id" | jq -c '.artifact')"
    [ "$(echo "$artifact" | jq -r '.checksum')" = "$checksum" ] \
      || fail "manifest artifact $id does not carry the checksum its record pins"
  done <<<"$pins"

  # Validated and profiled at the current revision, and ready.
  local scene_id episode_id quality
  for scene_id in $(echo "$scenes" | jq -r '.scenes[].sceneId'); do
    quality="$(api_get "$api" "/scenes/$scene_id/quality")"
    echo "$quality" | jq -e '.validation != null and .profile != null and .readiness == "ready"' >/dev/null \
      || fail "Scene $scene_id is not validated, profiled and ready at its current revision"
  done
  for episode_id in $(echo "$episodes" | jq -r '.episodes[].episodeId'); do
    quality="$(api_get "$api" "/episodes/$episode_id/quality")"
    echo "$quality" | jq -e '.validation != null and .profile != null and .readiness == "ready"' >/dev/null \
      || fail "Episode $episode_id is not validated, profiled and ready at its current revision"
  done

  version="$(api_get "$api" "/datasets/$DATASET_ID/versions/$DATASET_VERSION" | jq -c '.version')"
  [ "$(echo "$version" | jq -r '.scene.sceneCount')" = "$scene_count" ] \
    || fail "DatasetVersion scene summary differs from the registered Scenes"
  [ "$(echo "$version" | jq -r '.episode.episodeCount')" = "$episode_count" ] \
    || fail "DatasetVersion episode summary differs from the registered Episodes"

  jq -cn --arg id "$BASELINE_ID" --arg robot "$ROBOT_ID" --arg ds "$DATASET_ID" \
    --arg dv "$DATASET_VERSION" --argjson runs "$run_ids" \
    --argjson scenes "$scenes" --argjson episodes "$episodes" '{
      baseline_id: $id, robot_id: $robot, dataset_id: $ds, dataset_version: $dv,
      robot_run_ids: $runs,
      scene_count: ($scenes.scenes | length),
      scene_ids: [$scenes.scenes[].sceneId],
      episode_count: ($episodes.episodes | length),
      episode_ids: [$episodes.episodes[].episodeId]}'
}
