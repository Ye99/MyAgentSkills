---
name: folder-poi-itinerary-rename
description: Use when renaming day-based media folders using geo-tagged photos/videos, itinerary-ordered location sets, and landmark-first POI landmark names.
---

# Folder POI Itinerary Rename

Rename folders like `2024_09_18` to `2024_09_18_POI1_POI2_...` using sampled GPS media and Nearby POI resolution.

## Required Inputs

- A folder path containing photos/videos.
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

Dry-run (default):

```bash
python3 scripts/rename_folder_with_poi_itinerary.py "/path/to/2024_09_18"
```

Apply rename:

```bash
python3 scripts/rename_folder_with_poi_itinerary.py "/path/to/2024_09_18" --apply
```

Optional arguments:

- `--key` (or `LOCATIONIQ_API_KEY`)
- `--ratio` (default `1.0`)
- `--threshold-m` (default `300`)
- `--radius` (default `1000`)
- `--region` (`us1` or `eu1`)

## Safety

- Never hardcode API keys.
- Keep dry-run as default behavior.
- If target folder name already exists, use numeric suffix to avoid destructive rename.
