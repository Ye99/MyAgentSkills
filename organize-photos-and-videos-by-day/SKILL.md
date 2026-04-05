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
3. A pre-scan checks if any media files lack GPS. If so, `--recording-timezone` is required (fail-fast). If all media have GPS, the flag is optional.
4. Timestamp fallback chain:
   - EXIF/media capture datetime (with GPS or recording-timezone conversion)
   - nearest adjacent same-sequence media file with embedded capture datetime (bounded same-folder filename heuristic)
   - file creation time (treated as recording-local wall time when `--recording-timezone` is provided)
   - file mtime (treated as recording-local wall time when `--recording-timezone` is provided)
5. If EXIF datetime is naive and GPS UTC tags exist, use GPS UTC + offline timezone conversion.
6. If EXIF datetime has offset but no GPS, convert to recording-timezone (handles QuickTimeUTC system-tz mismatch).
7. If EXIF datetime is naive and GPS UTC tags do not exist, keep EXIF datetime as local-naive.
8. If the script falls back to file creation time or file mtime and `--recording-timezone` is provided, do not timezone-convert those filesystem timestamps; preserve their wall-clock date/time as recording-local fallback values.
9. If a media file lacks embedded capture datetime but an adjacent same-sequence sibling in the same folder has one, prefer that sibling's embedded local capture datetime over filesystem timestamps.
10. Copy media without metadata rewrite (high-fidelity copy), never move/delete source.
11. Collision policy: deterministic suffix `_col001`, `_col002`, ... (name collision, different content)
12. Do not hardcode media extensions. Classify via metadata MIME/FileType.
13. Unknown signatures are cached; unresolved unknown signatures default to media-candidate to avoid loss.
14. Explicit non-media exclusions (never copied, never signature-looked-up):
   - `*.url`
   - `*.ini`
   - `*.bk`
   - `*.sav`
   - `*.db`
   - `*.log`
   - `*.txt` (ffprobe falsely classifies plain text as `tty/ansi video`)

## Dependencies

- `exiftool`
- `timezonefinder` (offline timezone polygons)
- Python `zoneinfo` (IANA timezone rules)
- Optional but recommended for unknown-signature triage: `ffprobe` (from `ffmpeg`)

## Usage

Dry-run report:

```bash
python3 scripts/organize_media_by_local_date.py "/path/to/source_root" "/path/to/destination_root" \
    --recording-timezone Asia/Shanghai \
    --report organize_media_report.json
```

Apply copy:

```bash
python3 scripts/organize_media_by_local_date.py "/path/to/source_root" "/path/to/destination_root" \
    --recording-timezone Asia/Shanghai \
    --apply --report organize_media_report.json
```

Optional:

- `--recording-timezone <IANA>` — timezone where media was recorded (e.g. `Asia/Shanghai`). Required when media files lack GPS; script scans and exits with count if needed.
- `--signature-cache <path>`
- `--workers <n>`
- `--verbose`

## Running the Script — No Timeouts

**Do NOT use a fixed timeout when running this script.** The script emits deterministic progress to stderr during the main scan/copy loop:

```
[progress] processing 96 entries...
[progress] 1/96 (1%)
[progress] 2/96 (2%)
...
[progress] 96/96 (100%)
```

After the main loop reaches `100%`, the script may enter longer end phases such as verification, report writing, and cache saving. Those phases now emit explicit status lines:

```
[phase] verification started
[phase] verification: preparing shadow tree
[phase] verification: building destination index
[phase] verification: hashing destination files
[phase] verification: comparing source files
[phase] verification complete
[phase] writing report
[phase] saving signature cache
[done] report written: /path/to/report.json
```

Agent monitoring rule:
- `100%` on the entry counter is **not** the terminal signal.
- `[phase] ...` lines indicate post-loop work is still active.
- Treat `[done] report written: ...` plus process exit as the true completion signal.

Use the progress and phase lines to know the script is alive and to estimate completion. Let the process run until it exits naturally. A timeout will kill an otherwise healthy run mid-copy or mid-verification and can leave the destination/report state ambiguous.

## Unknown Signature Workflow (Auto + AI Fallback)

The organizer now auto-triages unknown signatures with `ffprobe` during the run and writes decisions into `--signature-cache`:

- if `ffprobe` shows any `video` or `audio` stream, classify as `media`
- if `ffprobe` shows no streams, invalid data, or only non-media stream types, classify as `non_media`

Only unresolved cases are emitted in `unknown_signatures_needing_ai_lookup`.

1. Run dry-run/apply and inspect `unknown_signatures_needing_ai_lookup` in report.
2. For any remaining unresolved signature, try `ffprobe` manually on the example file path:

```bash
ffprobe -v error -show_entries format=format_name:stream=codec_type,codec_name -of json "/path/to/example"
```

3. If `ffprobe` reports `video` or `audio` stream(s), classify as `media` in cache.
4. If it reports no streams, invalid data, or only non-media stream types (for example subtitle/text), classify as `non_media`.
5. Only if still uncertain, perform internet lookup once via AI agent.
6. Re-run organizer; cache avoids repeated lookups.

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
