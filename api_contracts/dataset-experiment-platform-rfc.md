# Dataset and experiment platform RFC

Status: draft for M0 review  
Linear: [P-TH-67](https://linear.app/future-agi/project/dataset-and-experiment-platform-20-6328dd2a78cd)  
Owner: Tech  
Target: 2026-10-30 contract decision

## Decision

Add three durable primitives and keep the rest as adapters or projections:

1. `DatasetVersion`: an immutable view of the cases used for evaluation.
2. `ExperimentRun`: one execution of a task against one `DatasetVersion`.
3. `ExperimentRunItem`: one case/trial result, including output, status, metrics, trace identity, and score references.

The existing `Dataset -> Row -> Cell` tables remain readable and writable during migration. New APIs write the new records first and expose a compatibility projection for existing UI and SDK consumers.

## Why this shape

Future AGI already has typed tables, prompt and GraphVersion experiments, evaluator versions, Temporal workers, trace analysis, and annotation queues. The missing contract is stable identity and immutable run lineage. Adding separate objects for every competitor feature would create a larger system without solving that root problem.

## Minimal data model

```text
DatasetVersion
  id, dataset_id, parent_version_id, created_at, created_by
  content_hash, description, metadata, source_revision

DatasetVersionItem
  version_id, example_id, external_id, input, expected, metadata
  source_trace_id, source_span_id, content_hash, split_names

ExperimentRun
  id, experiment_id, dataset_version_id, task_kind
  status, created_at, started_at, completed_at, metadata
  model_config, prompt_version_id, agent_version_id, evaluator_versions

ExperimentRunItem
  run_id, example_id, trial_number, status, output, error
  trace_id, root_span_id, latency_ms, token_usage, cost, score_refs
```

`DatasetVersionItem` is a projection over existing rows during migration. We do not duplicate all cell tables in the first release.

## Stable identity

- `example_id` is generated once and survives dataset versions.
- `external_id` is optional and is unique within a dataset.
- `content_hash` is computed from canonical input, expected output, metadata, and attachment references.
- Re-upload with the same external ID updates the next version; it does not mutate a past version.
- Re-upload without an external ID can match by content hash or create a new example, according to an explicit request option.

## Task adapters

The run contract accepts adapters for:

- prompt/model execution
- Future AGI GraphVersion execution
- caller-provided Python/TypeScript task functions
- externally produced results uploaded by example ID
- trace-linked production or remote agent execution

The core does not add another workflow engine. Existing Temporal/Celery execution remains an adapter and stores results through `ExperimentRunItem`.

## Analysis derived from run items

The first analysis API should read only the three new primitives and return:

- completeness and error counts
- score averages, pass rates, min/max, and percentiles
- per-case baseline/candidate deltas
- improved, regressed, missing, duplicate, and invalid cases
- metadata/split filters
- trial mean and variance when trials exist
- trace links for failed cases

Advanced significance tests, custom query languages, and dedicated analytics tables are deferred until the simple query path is measured and proven insufficient.

## Evidence envelope

Scores may carry an optional JSON envelope in the first release:

```json
{
  "evidence_id": "ev_...",
  "evaluator_version_id": "...",
  "judge_trace_id": "...",
  "authority": "observed",
  "confidence": "high",
  "limitations": [],
  "status": "assessed"
}
```

`status` must preserve `assessed`, `unknown`, `not_assessed`, `unsupported`, and `error`. A missing score cannot silently become a zero.

## Compatibility and rollout

1. Add tables and read-only projections.
2. Backfill `DatasetVersion` from the current dataset state without changing row/cell IDs.
3. Write new experiment runs alongside the existing snapshot/cell path.
4. Compare projection output against the legacy experiment API.
5. Move new UI/SDK/MCP calls to the run contract behind a feature flag.
6. Retire only the duplicated paths after migration receipts and rollback evidence exist.

### M1 compatibility read API

The first adapter exposes the version projection without changing legacy dataset
writes.  Both endpoints require authentication and apply the existing
organization/workspace dataset scope:

```text
GET /model-hub/datasets/{dataset_id}/versions/
GET /model-hub/datasets/{dataset_id}/versions/{version_id}/?limit=100&offset=0
```

The list response returns immutable version metadata and an active-item count.
The detail response returns a bounded, deterministic page of active
`DatasetVersionItem` records, including `example_id`, `content_hash`,
`legacy_row`, and optional `source_trace_id`/`source_span_id` provenance.
Deleted versions, deleted items, datasets outside the request tenant, and
unknown IDs are not exposed.  `limit` is constrained to 1–500 and `offset` is
non-negative.  This slice intentionally does not infer `input` or `expected`
from legacy cells and does not add a split or analytics table; a later
backfill must define that mapping explicitly before it writes records.

## Non-goals

- no new warehouse
- no new orchestration engine
- no per-feature result tables
- no mandatory sandbox provider
- no full statistical package before stable run identity works
