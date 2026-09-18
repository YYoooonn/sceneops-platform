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

`apps/api/app/domains/scenes/quality.py` derives readiness live from
`SceneRecord` + the latest `SceneValidationRunRecord`/`SceneProfileRunRecord`
— it is not stored anywhere as its own column:

```text
readiness = UNKNOWN   if scene.status not in {VALIDATED, PROFILED}
          | UNKNOWN   if no validation run exists
          | BLOCKED   if should_block_pipeline, or validation_status in {failed, error}
          | WARNING   if validation_status == "warning"
          | READY     if validation_status == "ready"
          | UNKNOWN   otherwise
```

`selectable_for_detection` is a separate derived boolean (not identical to
`readiness == READY`): it independently checks scene status, validation
block/failure, *and* GT presence (`has_ground_truth` + `annotation_count >
0`, sourced from `SceneRecord` fields that `register_scene` sets from the
manifest). A scene can be `readiness=READY` and still not selectable if it
has no ground truth — `exclusion_reasons` on the response lists exactly
which of these checks failed (`scene_not_ready`, `validation_missing`,
`validation_blocked`, `missing_ground_truth`).

Dataset-level quality (`GET /datasets/{id}/versions/{v}/quality`) is an
aggregate over every scene's quality in that version — readiness buckets,
GT coverage ratio, selectable/non-selectable counts, observed channels,
exclusion-reason histogram. Per-scene quality is separately paginable via
`GET /datasets/{id}/versions/{v}/scenes/quality`.

## 3. Episode quality and readiness

`apps/api/app/domains/episodes/quality.py` follows the same shape, derived
purely from the latest `EpisodeValidationRunRecord` (profile data enriches
the response but doesn't drive readiness):

```text
readiness = UNKNOWN   if no validation run exists
          | BLOCKED   if should_block_pipeline, or validation_status in {failed, error}
          | WARNING   if validation_status == "warning"
          | READY     if validation_status == "ready"
          | UNKNOWN   otherwise
```

Episode has no `selectable_for_*` concept yet — there is no downstream
consumer (equivalent to Scene's detection evaluation) that selects episodes
by quality today. `EpisodeRecord.status` never participates in this
computation (see [Episode domain](./episode-domain.md) §4) — this is the
one deliberate structural difference from Scene, where `scene.status` is
part of the readiness gate.

## 4. Scenario status and readiness

Scenario curation (`mine_scenarios -> score_scenario_readiness`) mines
candidate scenes from `SceneRecord` metadata into a `ScenarioSet` artifact,
then scores each candidate's readiness for downstream use (evaluation,
reconstruction, pseudo-labeling). It is implemented and E2E-tested, but
still marked `experimental=True` — see
[Reserved architecture and current limitations](./reserved-and-limitations.md).

Each scenario candidate inside the `ScenarioSet` artifact carries a
`ScenarioStatus`:

```text
CANDIDATE -> SELECTED | REJECTED -> EXPORTED
          -> DEPRECATED
```

This is **artifact-level state, read and written only inside the mining/
scoring JSON payload** — there is no per-scenario DB row or repository
today (see [Data model](./data-model.md) §6). Documenting it here only to
the extent it's actually implemented: `mine_scenarios` assigns
`CANDIDATE`/`REJECTED` based on predicate matches, `score_scenario_readiness`
computes a readiness score per selected candidate. `EXPORTED`/`DEPRECATED`
exist in the enum but are not currently written by any job — do not infer
an export or deprecation workflow exists from the enum's presence alone.

`scenario_run_records` (mining/readiness) follow the same append-only
run-record pattern as §1. The readiness run carries dedicated aggregate
columns: `ready_count`/`warning_count`/`blocked_count`/`average_score`.

## 5. Detection evaluation and ScenarioSet lineage

When a `ScenarioSet` is provided to detection evaluation, only scenes it
selected are evaluated; scenes outside it are skipped with reason
`not_in_scenario_set`. Within the selected set, Scene's own quality filters
(§2) still apply. Both the `InferenceRun` and `EvaluationRun` record the
`scenario_set_id` used, so a result can always be traced back to exactly
which curated selection produced it.
