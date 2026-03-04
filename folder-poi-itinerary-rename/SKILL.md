---
name: folder-poi-itinerary-rename
description: Use when renaming day-based media folders using geo-tagged photos/videos, itinerary-ordered location sets, and landmark-first POI landmark names.
---

# Folder POI Itinerary Rename

Rename day folders like `2024_09_18` to `2024_09_18_POI1_POI2_...` using sampled GPS media and Nearby POI resolution.
Supports recursive root runs and writes a JSON report for verification.
Supports resumable multi-day runs with a persistent state ledger.

## Required Inputs

- An input path (single day folder or a root to scan recursively).
- LocationIQ API key (environment variable or CLI argument).

## Required Sub-Skills

- **REQUIRED SUB-SKILL:** Use `photo-gps-from-exif` to prioritize robust EXIF extraction via `exiftool`.
- **REQUIRED SUB-SKILL:** Use `locationiq-nearby-poi` to resolve POI landmark names for clustered location sets.

## Rules

1. Only files with GPS metadata are eligible for sampling.
2. Sample 100% of eligible files.
3. Group sampled coordinates into location sets using geo-first clustering.
4. Resolve one landmark name per set from Nearby POI with priority: landmark > city > street.
5. If the best-scored label is a numeric street name (for example `181st Avenue`), prefer a city label when available before falling back to that street label.
6. Order landmark names by set start time (itinerary order).
7. Deduplicate repeated landmark names globally while preserving first occurrence.
8. Do not enforce a hard limit on unique landmark names.
9. When invoking `opencode` in this workflow, pass `--variant medium`.

## Usage

Dry-run (default), single folder:

```bash
python3 scripts/rename_folder_with_poi_itinerary.py "/path/to/2024_09_18"
```

Dry-run (default), recursive root with report:

```bash
python3 scripts/rename_folder_with_poi_itinerary.py "/path/to/root" --report-json "folder_poi_itinerary_rename_report.json"
```

Apply rename, recursive root with report:

```bash
python3 scripts/rename_folder_with_poi_itinerary.py "/path/to/root" --apply --report-json "folder_poi_itinerary_rename_report.json"
```

Apply rename with resumable state (recommended for rate-limited multi-day runs):

```bash
python3 scripts/rename_folder_with_poi_itinerary.py "/path/to/root" --apply \
  --report-json "folder_poi_itinerary_rename_report.json" \
  --state-json "folder_poi_itinerary_rename_state.json" \
  --error-retry-max 2 \
  --no-landmark-retry-max 1
```

Optional arguments:

- `--key` (or `LOCATIONIQ_API_KEY`)
- `--report-json` (default `folder_poi_itinerary_rename_report.json`)
- `--state-json` (default `folder_poi_itinerary_rename_state.json`)
- `--error-retry-max` (default `2`)
- `--no-landmark-retry-max` (default `1`)
- `--ratio` (default `1.0`)
- `--threshold-m` (default `2000`)
- `--radius` (default `1000`)
- `--region` (`us1` or `eu1`)

## Resume Semantics

- Freeze only applied renames: folders already renamed are not recomputed.
- Folders with `error` are retried up to `--error-retry-max` times.
- Folders with `skipped-no-landmark-name-proposed` are retried up to `--no-landmark-retry-max` times.
- After retry cap is reached, folder status becomes retry-exhausted and is skipped in later runs.
- If LocationIQ returns `429` with `Rate Limited Day`, stop the run gracefully and persist pending folders for resume.
- If LocationIQ returns `429` with `Rate Limited Second`/`Rate Limited Minute`, retry with bounded backoff; if retries are exhausted, stop gracefully (do not continue with partial single-service data).
- If LocationIQ returns unknown/ambiguous `429`, use Balance API as a secondary signal and treat `balance.day < 100` as exhausted; stop gracefully. Balance values can lag under continuous API traffic.
- Press `Ctrl+C` once for graceful stop: current folder finishes, state/report flush, and remaining folders resume next run.
- Press `Ctrl+C` twice for immediate abort.

## JSON Report

Each run writes a JSON report with summary counts and per-folder statuses. Key verification fields:

- `summary.renamed_count`
- `summary.already_landmark_named_count`
- `summary.no_landmark_name_proposed_count`
- `no_landmark_name_proposed_paths` (list of folders)

Additional troubleshooting fields include candidate/eligible counts, no-GPS count, failure count, and per-folder details.
Resume diagnostics include processed-this-run counts, frozen-skip counts, retry counts, and exhausted-path lists.
Interruption diagnostics include `interrupted`, `interrupt_source`, `pending_folder_ids`, and coverage-check fields.

## Safety

- Never hardcode API keys.
- Keep dry-run as default behavior.
- If target folder name already exists, use numeric suffix to avoid destructive rename.
