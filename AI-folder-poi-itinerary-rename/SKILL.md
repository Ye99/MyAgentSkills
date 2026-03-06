---
name: ai-folder-poi-itinerary-rename
description: Use when renaming day-based travel media folders from EXIF GPS while avoiding reverse geocoding services and preferring landmark-first names inferred from geographic knowledge.
---

# AI Folder POI Itinerary Rename

Rename day folders like `2025_07_24` to itinerary-ordered landmark names by scanning all media files, extracting GPS/timestamps, and inferring landmark tokens from general geographic knowledge.

## Required Sub-Skills

- **REQUIRED SUB-SKILL:** Use `photo-gps-from-exif` to extract GPS coordinates from `.jpg`, `.jpeg`, `.heic`, `.mov`, `.mp4`, `.m4v`.

## Hard Rules

1. Do not use `folder-poi-itinerary-rename`.
2. Do not call reverse geocoding services or geo-lookup APIs.
3. Infer nearby place names from general geographic knowledge.
4. Prefer well-known landmark names over settlement/region/country names.
5. Do not hardcode place lists for one country; keep the workflow world-wide.

## Feature: Full Media Scan + Itinerary Order Rename

1. Scan all media files in the target day folder recursively.
2. Collect GPS coordinates and capture timestamps.
3. Sort media in capture-time order.
4. Infer landmark token candidates for the sorted path.
5. Keep unique landmarks in first-seen itinerary order.
6. Rename folder with comma-joined landmark tokens.
7. If confidence is low, return `UnknownLandmark` and skip it from folder name.
8. Cluster location sets using distance only (`--cluster-distance-m`), no time-gap rule.
9. For merged multi-family/multi-continent same-day media, keep a single renamed day folder; do not split by country.
10. Keep mixed-country place names in one time-ordered itinerary; interleaving places is expected.
11. For `ratio == 1.0` (ratio is constrained to `(0, 1]`), pre-trim sampled clusters to at most `--max-landmarks` (default `8`) before inference using largest location sets first (first-seen tie-break); if unique valid landmarks are still below cap, infer additional clusters as fallback. For `ratio < 1.0`, do not pre-trim before inference.
12. Use exponential backoff retries for landmark inference; on exhausted retries, stop safely and write state/report files for resume.
13. Record persistent landmark-inference failures in both files: report keeps summary, state keeps detailed failure log for troubleshooting and resume.
14. Accept either a single day folder (`YYYY_MM_DD*`) or a tree root (for example `/Pictures`) and process all day folders under it.
15. At tree-run end, perform integrity checks: math reconciliation of folder counts plus filesystem cross-check of renamed target folders.

## Name Format

- `YYYY_MM_DD_L1,L2,L3`
- Example: `2025_07_24_Magnusarfoss,Fjarargljufur,VikChurch,Skogafoss,Seljalandsfoss`

## Script Usage

Dry-run (default):

```bash
python3 scripts/rename_folder_by_ai_itinerary.py "/path/to/2025_07_24"
```

Dry-run for a tree root (for example `/Pictures/2025`):

```bash
python3 scripts/rename_folder_by_ai_itinerary.py "/path/to/Pictures/2025" --ratio 0.05
```

Dry-run with quick sampling (5%):

```bash
python3 scripts/rename_folder_by_ai_itinerary.py "/path/to/2025_07_24" --ratio 0.05
```

Dry-run with max landmark cap:

```bash
python3 scripts/rename_folder_by_ai_itinerary.py "/path/to/2025_07_24" --max-landmarks 8
```

Resume from previous failed run:

```bash
python3 scripts/rename_folder_by_ai_itinerary.py "/path/to/2025_07_24"
```

Apply rename:

```bash
python3 scripts/rename_folder_by_ai_itinerary.py "/path/to/2025_07_24" --apply
```

Defaults:

- `--ratio 1.0` (100% sampling, must be in `(0, 1]`)
- `--cluster-distance-m 2000`
- `--opencode-timeout-sec 180`
- `--opencode-max-attempts 5`
- `--opencode-initial-backoff-sec 3.0` (exponential)
- `--max-landmarks 8`
- `--inference-workers 3` (parallel landmark inference workers)

## Verification

- Report folders renamed/planned/skipped.
- Report files missing GPS separately.
- Check `media_without_gps_count`, `media_without_gps_ratio`, and `media_without_gps_examples` for quick data-quality visibility and concrete file examples.
- Confirm `used_reverse_geocoding` is `false` in output.
- For tree runs, check `.ai-itinerary-tree-report.json` (summary) and `.ai-itinerary-tree-state.json` (detailed per-folder log).
- Confirm integrity check fields show `passed: true`, `math_logic_ok: true`, and `target_folder_count_ok: true` for apply runs.
