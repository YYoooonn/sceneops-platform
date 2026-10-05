# Quality and Run Records

## 1. Run records: the shared pattern

Scene, Episode, and Scenario each have a "run records" table
(`scene_run_records`, `episode_run_records`, `scenario_run_records`) that is
**type-discriminated and append-only**: every validate/profile/mining/
readiness execution inserts a new row; nothing is ever updated in place.
"Latest run" is always `ORDER BY created_at DESC LIMIT 1`, applied
consistently across every domain's repository
(`packages/sceneops-db/sceneops_db/postgres/{scenes,episodes,scenarios}.py`).

Each run record is scoped either to a whole job (the `*_id` foreign key is
`None`, meaning "aggregate across everything this job checked") or to a
single item (`scene_id`/`episode_id` set, meaning "just this one"). A
`validate_scene`/`validate_episode` job run typically writes both: one
job-level aggregate row, and one per-item row for each scene/episode it
checked.

`InferenceRun`/`EvaluationRun` are a different shape — each is its own
single row (not an append-only "run record" pattern), since an inference
or evaluation run is inherently a one-off, individually addressable
execution rather than a recurring quality check against a stable record.

## 2. Scene quality and readiness

Readiness is derived, never stored: `sceneops_core.scenes.readiness` takes
the newest succeeded `SceneValidationRunRecord` **that assessed the Scene's
current manifest revision** (`manifest_artifact_id` + `manifest_checksum`
on the run equal the SceneRecord's). Runs of any other revision are ignored,
so replacing a Scene's manifest resets it to `unknown` until the new
revision is validated.

```text
readiness = UNKNOWN   if no succeeded validation run of the current revision exists
          | BLOCKED   if should_block_pipeline, or validation_status in {failed, error}
          | WARNING   if validation_status == "warning"
          | READY     if validation_status == "ready"
          | UNKNOWN   otherwise
```

The API (`apps/api/app/domains/scenes/quality.py`), scenario mining and the
detection readiness gate all use this one derivation. The dataset-level API
reads the latest current-revision runs in one query
(`SceneRunRepository.latest_succeeded_for_current_revisions`). Detection
checks the revisions its sample views pin and refuses to run if any of
them is `blocked`.

Quality describes canonical Scenes only: validation readiness, observed
channels and counts. Ground truth and detection selectability are not Scene
properties; they belong to label sets and sample views
([Derived layer](./derived-layer.md)).

Dataset-level quality (`GET /datasets/{id}/versions/{v}/quality`) is an
aggregate over every scene's quality in that version — readiness buckets
(blocked only when every Scene is blocked), observed channels and counts. Per-scene quality is separately paginable via
`GET /datasets/{id}/versions/{v}/scenes/quality`.

## 3. Episode quality and readiness

`apps/api/app/domains/episodes/quality.py` follows the same shape, derived
purely from the latest succeeded `EpisodeValidationRunRecord` of the
Episode's **current** manifest revision (profile data enriches the response
but doesn't drive readiness):

```text
readiness = UNKNOWN   if no validation run exists
          | BLOCKED   if should_block_pipeline, or validation_status in {failed, error}
          | WARNING   if validation_status == "warning"
          | READY     if validation_status == "ready"
          | UNKNOWN   otherwise
```

Episode has no `selectable_for_*` concept yet — there is no downstream
consumer (equivalent to Scene's detection evaluation) that selects episodes
by quality today. `EpisodeRecord` has no status (see
[Episode domain](./episode-domain.md) §5). Like Scene run records, a
per-episode run record pins the `manifest_artifact_id` + `manifest_checksum`
it assessed (CHECK `ck_episode_run_records_revision_pin`); a replaced
Episode reports `unknown` until its new revision is validated.

## 4. Scenario curation and readiness

Scenario curation (`mine_scenarios -> score_scenario_readiness`) selects
samples from pinned sample views by explicit criteria into an immutable
`ScenarioSet` revision, then scores each member's readiness for downstream
use. It is two stages of `scene_ml_evaluation` and is exercised by
`make e2e-scene-ml`. See [Derived layer](./derived-layer.md) §4.

`ScenarioStatus` exists in the enum, but no job writes it and there is no
per-scenario DB row or repository (see [Data model](./data-model.md) §6).

`scenario_run_records` (mining/readiness) follow the same append-only
run-record pattern as §1. The readiness run carries dedicated aggregate
columns: `ready_count`/`warning_count`/`blocked_count`/`average_score`.

## 5. Detection evaluation lineage

`predict_detection` runs on a ScenarioSet revision or explicit sample views
and records the ScenarioSet and view revisions it ran, plus the checksum of
its prediction manifest, on the `InferenceRun`. `evaluate_detection` records
the prediction and label set revisions it scored (`inputs` in the evaluation
manifest). A result is therefore traceable to exact revisions of every input;
see [Derived layer](./derived-layer.md) §5.
