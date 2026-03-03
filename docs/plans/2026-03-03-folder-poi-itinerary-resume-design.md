# Folder POI Itinerary Resume Design

## Goal

Enable multi-day, resumable execution of folder POI itinerary rename runs under API rate limits while preserving correctness, avoiding wasted work on completed folders, and producing outcomes equivalent to a continuous run.

## Selected Approach

Approach A: persistent state ledger with resume semantics.

- Freeze only applied renames.
- Recompute non-applied outcomes on later runs, bounded by retry rules.
- Keep per-folder lifecycle history in a state file and per-run reporting in report JSON.

## Architecture

### State Ledger

Add a persistent JSON ledger (`--state-json`) storing folder-level lifecycle state.

Per folder record:

- folder id and path metadata
- latest status
- attempt counters
- retry counters for error/no-landmark outcomes
- last proposed/apply paths and last error
- timestamps for first seen / last attempt / last success

Write strategy:

- atomic write via temp file + rename
- persist after each folder outcome

### Resume and Recompute Rules

- Applied/renamed folders are frozen and never recomputed.
- Already-landmark-named folders are skipped and recorded.
- Non-applied outcomes are resumable with bounded retries:
  - error: max 2 retries after initial attempt
  - skipped-no-landmark-name-proposed: max 1 retry after initial attempt
- Once capped, state transitions to terminal exhausted statuses.

### Continuous-Run Equivalence

- Discovery remains deterministic and sorted.
- Folder state transitions are monotonic and persisted.
- Resume decisions are driven by state + on-disk reconciliation.
- Report generation includes both this-run activity and cumulative counters for auditability.

## CLI and Data Contracts

Add CLI flags:

- `--state-json` (default `folder_poi_itinerary_rename_state.json`)
- `--error-retry-max` (default `2`)
- `--no-landmark-retry-max` (default `1`)

Report additions:

- processed_this_run_count
- skipped_frozen_applied_count
- retried_error_count
- retried_no_landmark_count
- error_retry_exhausted_paths
- no_landmark_retry_exhausted_paths

Preserve current required fields:

- renamed count
- already-landmark-named count
- no-landmark-proposed count + path list

## Error Handling and Safety

- If state file is missing: initialize new state.
- If state file is malformed: fail fast with actionable message.
- If a folder was renamed outside state, reconcile using filename shape and mark as frozen-applied.
- Never overwrite existing destination names without current collision-safe suffix behavior.

## Test Strategy

- Unit tests for retry gate logic and terminal exhausted statuses.
- Unit tests for freeze-only-applied behavior.
- Unit tests for resume recomputation eligibility.
- Unit tests for state atomic persistence and reload behavior.
- Scenario test comparing:
  1) one continuous run
  2) split runs across days with resume
  and asserting equivalent final folder outcomes.
