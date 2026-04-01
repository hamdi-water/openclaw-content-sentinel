# Internal API Contracts

This repo now carries a stronger phase-1 internal API aligned with the `v6.1` cahier des charges and the GenViral-like section of the PDF.

## Intent

- OpenClaw remains the control plane: scheduler, Telegram, browser automation, and operator workflow.
- The internal API is the stable data and job facade sitting beside OpenClaw, not a replacement for it.
- The goal is useful parity with a GenViral-like internal layer:
  - canonical runs
  - drafts
  - assets
  - candidates
  - approvals
  - publish jobs
  - proofs
  - schedules
  - analytics
  - bounded replays

## Contract Files

- `contracts/internal-api-v1.yaml`
- `contracts/run-create-request.schema.json`
- `contracts/publish-job-request.schema.json`
- `contracts/run-record.schema.json`

## Runtime Surface

Implemented phase-1 endpoints now include:

- `GET/POST /v1/projects`
- `GET/POST/PATCH /v1/brand-profiles`
- `GET/POST/PATCH /v1/campaigns`
- `GET/POST/PATCH /v1/templates`
- `GET/POST/PATCH /v1/sources`
- `POST /v1/sources/ingest`
- `GET/POST /v1/runs`
- `POST /v1/candidates`
- `POST /v1/approvals`
- `GET/PATCH /v1/drafts/{run_id}`
- `GET /v1/assets/{run_id}`
- `GET /v1/assets/{run_id}/{asset_path}`
- `GET/POST /v1/render-jobs`
- `GET/POST /v1/publish-jobs`
- `GET /v1/publish-jobs/{job_id}`
- `GET /v1/proofs/{run_id}`
- `POST /v1/replays`
- `GET/PATCH /v1/schedules`
- `GET /v1/analytics`
- `GET /v1/analytics/snapshots`
- `GET /v1/events`
- `POST /v1/webhooks/test`

## Canonical Versioning

- Route version: `/v1`
- Payload contract version: `schema_version`
- Publish adapter version: `publish_adapter_version`
- Prompt / retrieval / scoring / approval versions are carried on run records

## Idempotency and Audit

- `Idempotency-Key` is required for `POST /v1/publish-jobs`
- Each publish job is persisted with:
  - `job_id`
  - `run_id`
  - `platform`
  - `idempotency_key`
  - `canonical_post_hash`
  - `publish_target_hash`
  - `adapter_version`
  - `state`
  - `proof_id`
  - `final_url`
- Every API request is logged with:
  - `request_id`
  - `actor`
  - `method`
  - `path`
  - `status_code`
  - `latency_ms`
  - `idempotency_key`

## Persistence

The phase-1 API persists control-plane objects under `workspace-data/api/`:

- `projects.json`
- `brand_profiles.json`
- `campaigns.json`
- `templates.json`
- `candidates.json`
- `approvals.json`
- `render_jobs.json`
- `publish_jobs.json`
- `analytics_snapshots.json`
- `replays.json`
- `request_log.json`
- `event_log.json`
- `schedules.json`

This is intentionally same-repo and zero-cost. It complements the canonical run truth in:

- `workspace-data/runs/*/run.json`
- `workspace-data/ledger/run_ledger.sqlite3`

## Design Notes

- `POST /v1/runs` can create a shell run or execute the whole pipeline.
- `POST /v1/render-jobs` materializes social cards, cover cards, and simple carousels from the same zero-cost render layer.
- `POST /v1/approvals` persists the human decision and updates the run state.
- `POST /v1/publish-jobs` stages jobs idempotently; when `submit=true`, it can dispatch browser publishing inline for the MVP.
- `PATCH /v1/schedules` persists desired operator state and can apply safe changes such as pause/resume and brief promotion.
- `GET /v1/proofs/{run_id}` exposes the canonical `proof_pack`.
- `GET /v1/analytics` captures a persisted snapshot for later release and proof analysis.
- `POST /v1/replays` supports bounded replay of safe internal stages such as refresh, package, drafts, and review.

## Current Limits

- The internal API is still same-repo and single-workspace, not multi-tenant.
- Publish workers are inline MVP execution paths, not a dedicated queue service yet.
- Long-horizon proof targets remain an operations/time problem, not an API surface problem.
