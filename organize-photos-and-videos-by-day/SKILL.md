---
name: organize-photos-and-videos-by-day
description: Use when organizing very large media trees into day folders using offline geo-timezone conversion with no-loss verification.
---

# Organize Photos and Videos by Day

Organize a source folder tree into `destination/%Y/%Y_%m_%d` using capture time with timezone-aware local-date correction.

## Required Sub-Skills

- **REQUIRED SUB-SKILL:** Use `find-missing-files` for final content-hash verification.
- **REQUIRED SUB-SKILL:** Use `photo-gps-from-exif` when validating GPS/time metadata behavior.

## When to Use

- Existing import/classification tools are unreliable.
- You need large-scale organization (hundreds of thousands of files).
- You must avoid API calls for timezone lookup and still convert UTC/GMT capture timestamps to local date.
- You need explicit no-loss verification and an audit report.

## Core Rules

1. Inputs are `source_root` and `destination_root`.
2. Target layout is `%Y/%Y_%m_%d`.
3. Timestamp fallback chain:
   - EXIF/media capture datetime
   - file creation time
   - file mtime
4. If EXIF datetime is naive and GPS UTC tags exist, use GPS UTC + offline timezone conversion.
5. If EXIF datetime is naive and GPS UTC tags do not exist, keep EXIF datetime as local-naive.
6. Copy media without metadata rewrite (high-fidelity copy), never move/delete source.
7. Collision policy: deterministic suffix `_dup001`, `_dup002`, ...
8. Do not hardcode media extensions. Classify via metadata MIME/FileType.
9. Unknown signatures are cached; unresolved unknown signatures default to media-candidate to avoid loss.
10. Explicit non-media exclusions (never copied, never signature-looked-up):
   - `*.url`
   - `*.bk`
   - `*.sav`
   - `*.db`
   - `*.log`

## Dependencies

- `exiftool`
- `timezonefinder` (offline timezone polygons)
- Python `zoneinfo` (IANA timezone rules)

## Usage

Dry-run report:

```bash
python3 scripts/organize_media_by_local_date.py "/path/to/source_root" "/path/to/destination_root" --report organize_media_report.json
```

Apply copy:

```bash
python3 scripts/organize_media_by_local_date.py "/path/to/source_root" "/path/to/destination_root" --apply --report organize_media_report.json
```

Optional:

- `--signature-cache <path>`
- `--workers <n>`
- `--verbose`

## Unknown Signature Workflow (AI + Cache)

1. Run dry-run and inspect `unknown_signatures_needing_ai_lookup` in report.
2. For each unique signature, perform internet lookup once via AI agent.
3. Add classification to signature cache (`media` or `non_media`).
4. Re-run organizer; cache avoids repeated lookups.

Known explicit non-media exclusions above are skipped before this workflow.

## Verification and Reporting

JSON report includes full source paths for:

- `media_copy_failed`
- `missed_media_files`
- `non_media_not_copied`

Success criteria in apply mode:

- no `missed_media_files`
- no `media_copy_failed`
- verification pass using `find-missing-files` content-hash methodology
