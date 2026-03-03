# Folder POI Ctrl+C Resume Safety Design

## Goal

Support graceful interruption with Ctrl+C so long multi-day runs can stop safely and resume later without wasted completed work, state corruption, missed folders, or media-file risk.

## Scope

- Trigger: Ctrl+C (`SIGINT`) only.
- First Ctrl+C requests graceful stop.
- Second Ctrl+C forces immediate exit.

## Behavior

### Graceful Stop

- On first `SIGINT`, set a shutdown flag.
- Do not start any new folder once flag is set.
- Allow current folder to finish its processing/rename path.
- Persist state atomically and write final report with interruption metadata.

### Forced Stop

- If a second `SIGINT` is received, exit immediately with non-zero status.

## Data Integrity

- State ledger writes remain atomic (`.tmp` + `replace`).
- State persisted after each folder result and at graceful termination.
- Report includes interruption details:
  - `interrupted`
  - `interrupt_source` (`signal`)
  - `last_completed_folder_id`
  - `pending_folder_ids`

## No-Miss Coverage

- Build deterministic discovered folder ID set each run.
- Compute terminal + pending sets from outcomes and state.
- Enforce invariant:
  - `discovered_ids == terminal_ids U pending_ids`
- If invariant fails, set `coverage_check_failed=true` and exit non-zero.

## Media Safety

- Media files remain read-only during processing.
- Only folder rename operation mutates filesystem.
- Interruption does not write into photo/video files.

## Validation Plan

- Unit tests for:
  - first/second SIGINT behavior
  - interruption metadata in report
  - no-miss coverage invariant
  - resume continuity after interruption
- Scenario run on prior date range/data with induced Ctrl+C and resume.
