---
name: folder-poi-itinerary-rename
description: Use when renaming day-based media folders using geo-tagged photos/videos, itinerary-ordered location sets, and landmark-first POI landmark names.
---

# Folder POI Itinerary Rename

Rename day folders like `2024_09_18` to `2024_09_18_POI1_POI2_...` using sampled GPS media and Nearby POI resolution.
Supports recursive root runs and writes a JSON report for verification.

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
5. Order landmark names by set start time (itinerary order).
6. Deduplicate repeated landmark names globally while preserving first occurrence.
7. Do not enforce a hard limit on unique landmark names.

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

Optional arguments:

- `--key` (or `LOCATIONIQ_API_KEY`)
- `--report-json` (default `folder_poi_itinerary_rename_report.json`)
- `--ratio` (default `1.0`)
- `--threshold-m` (default `300`)
- `--radius` (default `1000`)
- `--region` (`us1` or `eu1`)

## JSON Report

Each run writes a JSON report with summary counts and per-folder statuses. Key verification fields:

- `summary.renamed_count`
- `summary.already_landmark_named_count`
- `summary.no_landmark_name_proposed_count`
- `no_landmark_name_proposed_paths` (list of folders)

Additional troubleshooting fields include candidate/eligible counts, no-GPS count, failure count, and per-folder details.

## Safety

- Never hardcode API keys.
- Keep dry-run as default behavior.
- If target folder name already exists, use numeric suffix to avoid destructive rename.
