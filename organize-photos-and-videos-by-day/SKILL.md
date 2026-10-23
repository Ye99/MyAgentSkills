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
   - same-folder, same-stem file with embedded capture datetime (sidecars such as GoPro `.THM`)
   - nearest adjacent same-sequence media file with embedded capture datetime (bounded same-folder filename heuristic)
   - file creation time (treated as recording-local wall time when `--recording-timezone` is provided)
   - file mtime (treated as recording-local wall time when `--recording-timezone` is provided)
5. If EXIF datetime is naive and GPS UTC tags exist, use GPS UTC + offline timezone conversion.
6. If EXIF datetime has offset but no GPS, convert to recording-timezone (handles QuickTimeUTC system-tz mismatch).
7. If EXIF datetime is naive and GPS UTC tags do not exist, keep EXIF datetime as local-naive.
8. If the script falls back to file creation time or file mtime and `--recording-timezone` is provided, do not timezone-convert those filesystem timestamps; preserve their wall-clock date/time as recording-local fallback values.
9. If `--camera-timezone` is given (camera clock set to a different zone than where media was recorded, e.g. a GoPro left on home time), read embedded timestamps not backed by GPS time as camera-clock wall time in that zone, then convert to the recording timezone. This covers naive EXIF times and QuickTime times (exiftool reads the raw value as UTC; the script recovers the raw value first). EXIF times that carry their own `OffsetTime*` tag and GPS UTC times are left unchanged.
10. If a media file lacks embedded capture datetime, borrow one before using filesystem timestamps: first from a same-folder file with the same stem (e.g. GoPro `GX010001.THM` takes `GX010001.MP4`'s corrected time; case-insensitive; ties pick the lowest extension), else from the nearest same-extension sibling in the same numbered sequence.
11. Copy media without metadata rewrite (high-fidelity copy), never move/delete source.
12. Collision policy: deterministic suffix `_col001`, `_col002`, ... (name collision, different content)
13. Do not hardcode media extensions. Classify via metadata MIME/FileType.
14. Unknown signatures are cached; unresolved unknown signatures default to media-candidate to avoid loss.
15. Explicit non-media exclusions (never copied, never signature-looked-up):
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

## Setup Check (Run First)

Before the first dry run on a machine, check both required dependencies:

```bash
exiftool -ver
~/.venvs/organize-media/bin/python -c "import timezonefinder; print('timezonefinder ok')"
```

- **`exiftool -ver` fails** → install it. This needs `sudo`; if `sudo` is blocked inside an agent sandbox (e.g. "no new privileges"), ask the user to run it in their own terminal, then re-run `exiftool -ver`.

  ```bash
  sudo apt install libimage-exiftool-perl ffmpeg    # Debian/Ubuntu
  brew install exiftool ffmpeg                      # macOS
  ```

- **The venv check fails** (no such file, or `ModuleNotFoundError`) → create the venv and install `timezonefinder`. No `sudo` needed; the agent can do this itself. It downloads a ~33 MB data package, so do not use a short timeout.

  ```bash
  python3 -m venv ~/.venvs/organize-media
  ~/.venvs/organize-media/bin/pip install timezonefinder
  ```

  If `python3 -m venv` reports `ensurepip is not available`, ask the user to run `sudo apt install python3-venv` first.

Always run the script with `~/.venvs/organize-media/bin/python`, not `python3`. System Python on recent Debian/Ubuntu refuses `pip install` ("externally managed environment"), and it will not see the venv's `timezonefinder`.

## Before Running: Confirm Time Zones With the User

The script cannot detect a camera clock set to the wrong zone; files store only a wall-clock reading. Before the first dry run, settle both zones with the user:

1. **Show sample raw clock times.** These are the camera's clock readings, with no zone applied:

   ```bash
   exiftool -api QuickTimeUTC=0 -r -q -q -n -T -FileName -DateTimeOriginal -CreateDate -GPSLatitude "/path/to/source_root" \
       | awk -F'\t' '$2!="-" || $3!="-"' | sort | sed -n '1,5p;$p'
   ```

   Columns: file, photo time, video time, GPS latitude (`-` = no GPS). Pick a few files spread across the set (first, last, one per day).
2. **Ask where the media was filmed.** Map the answer to an IANA name (`Asia/Shanghai`, not `CST`; `America/Los_Angeles`, not `PDT`) → `--recording-timezone`. One zone per run: if the trip crossed zones, split the source by leg and run once per leg.
3. **Ask whether the camera clock was set to that zone.** Show the sample times and ask whether they match when the user remembers filming (e.g. "GX010005 reads 03:00 on June 1 — were you filming around then?").
   - Yes → omit `--camera-timezone`.
   - No (e.g. left on home time) → ask which zone the clock was on → `--camera-timezone <IANA>`.
   - Unsure → convert a sample time both ways and let the user pick the one that matches their memory.
4. **Skip what GPS already answers.** If every file has GPS, `--recording-timezone` is optional, and files with GPS time ignore the camera clock. Still ask about the clock if any file lacks GPS.
5. **Check the dry-run report** before `--apply`: `timestamp_source` contains `camera-tz` for corrected files.

## Usage

Dry-run report:

```bash
~/.venvs/organize-media/bin/python scripts/organize_media_by_local_date.py "/path/to/source_root" "/path/to/destination_root" \
    --recording-timezone Asia/Shanghai \
    --report organize_media_report.json
```

Apply copy:

```bash
~/.venvs/organize-media/bin/python scripts/organize_media_by_local_date.py "/path/to/source_root" "/path/to/destination_root" \
    --recording-timezone Asia/Shanghai \
    --apply --report organize_media_report.json
```

Optional:

- `--recording-timezone <IANA>` — timezone where media was recorded (e.g. `Asia/Shanghai`). Required when media files lack GPS; script scans and exits with count if needed.
- `--camera-timezone <IANA>` — timezone the camera clock was set to, when it differs from `--recording-timezone` (e.g. GoPro left on `America/Los_Angeles` while filming in `Asia/Shanghai`). Requires `--recording-timezone`. Both zones are needed: the camera stores only a wall-clock reading, and the correction is the gap between the two zones.
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
