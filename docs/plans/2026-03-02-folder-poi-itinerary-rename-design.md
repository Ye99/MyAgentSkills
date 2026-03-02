# Folder POI Itinerary Rename Design

## Goal

Create a third skill that renames a date-like media folder by appending ordered POI labels derived from sampled geo-tagged photos/videos.

## Requirements

1. Scan a folder for photos/videos and extract GPS metadata.
2. Sample 60% of GPS-bearing media only (non-GPS files do not count in denominator).
3. Group sampled coordinates into location sets (geo-first clustering).
4. Resolve one label per set via LocationIQ Nearby POI.
5. Prefer label priority: landmark > city > street.
6. Order labels by itinerary time (earliest set first).
7. Deduplicate repeated labels globally while preserving first occurrence.
8. Rename folder from `YYYY_MM_DD` to `YYYY_MM_DD_POI1_POI2_...`.

## Architecture

- Skill: `folder-poi-itinerary-rename`
- Script: `scripts/rename_folder_with_poi_itinerary.py`
- Tests: `scripts/tests/test_rename_folder_with_poi_itinerary.py`

Workflow:

1. Extract media GPS+time using `exiftool -j -n`.
2. Filter to files with `GPSLatitude` and `GPSLongitude`.
3. Deterministically sample `ceil(0.6 * eligible_count)`.
4. Cluster sampled coordinates by distance threshold (geo-first).
5. Sort clusters by earliest capture time.
6. Call LocationIQ Nearby for each cluster centroid.
7. Choose label using priority rules.
8. Normalize labels, dedupe, and build target folder name.
9. Dry-run by default; optional `--apply` renames folder.

## Edge Cases

- No GPS-bearing files: fail fast with clear message.
- Multiple sets resolve to same label: append once only.
- Missing landmark-level POI: fallback to city then street.
- API errors/429: retry with backoff and continue with best effort.
- Rename collision: add numeric suffix.

## Security

- API key read from `LOCATIONIQ_API_KEY` or `--key`.
- Never log full API key.
- Do not store secrets in skill files.
