# Photo & Video Organizing Workflow

This document describes the end-to-end pipeline for turning a messy, possibly huge, possibly
multi-source pile of photos and videos into a clean archive organized as
`destination/%Y/%Y_%m_%d_Landmark1,Landmark2,...`, with no-loss guarantees at every step.

It is composed entirely of existing skills in this repo — this file just records the order they
run in and why.

## Pipeline overview

```
(0) split-gopro-video          [optional — only if raw GoPro footage needs trimming first]
        |
        v
(1) dedup-copy                 [optional — only when consolidating multiple overlapping sources]
        |
        v
(2) organize-photos-and-videos-by-day   <-- core step
        |  (uses photo-gps-from-exif + find-missing-files internally)
        v
(3) AI-folder-poi-itinerary-rename
        |  (uses photo-gps-from-exif internally)
        v
(4) find-missing-files          [optional final cross-check, if you want an independent audit]
```

## Step 0 — Split GoPro footage (optional)

**Skill:** [`split-gopro-video`](split-gopro-video/SKILL.md)

If the source includes raw GoPro chapters (`GX010001.MP4`, etc.) that need trimming into named
clips before archiving, do this first so step 2 operates on final clips, not raw chapters.

- Concatenates multi-chapter recordings before cutting.
- Cuts on keyframe boundaries with `ffmpeg -c copy` — preserves original HEVC codec, ~70–100 Mbps
  bitrate, and the GPMF GPS/telemetry track. Drops only the `tmcd` timecode track (incompatible
  with MP4 muxing).
- Patches `mvhd`/`tkhd`/`mdhd` timestamps on each segment via `exiftool` so capture time is
  correct for step 2's dating logic.
- Verifies with a full ffmpeg decode pass and `streamhash` comparison against the source range.

Feed the resulting segment files into step 1 or step 2 as the effective source.

## Step 1 — Deduplicate across sources (optional)

**Skill:** [`dedup-copy`](dedup-copy/SKILL.md)

Use this when combining photos/videos pulled from multiple devices, cloud backups, or old drives
that likely overlap. Skip it if the source is already a single, non-redundant tree.

1. `jdupes -r -o name <source>` finds content-identical duplicate sets.
2. `dedup_and_copy.py` copies one "keeper" per set — scored by penalizing copy markers
   (`(1)`, `_copy`), edit markers (`edited`, `backup`), hidden directories, and excess path
   length/depth — into a deduped staging directory, logging every decision to JSON.
3. `verify_dedup_copy.py` independently re-checks the copy: arithmetic reconciliation, every
   source file accounted for, every keeper present, SHA-256 spot-checks, and no unexpected extras
   in the target.

Output of this step becomes the `source_root` for step 2.

## Step 2 — Organize into day folders (core step)

**Skill:** [`organize-photos-and-videos-by-day`](organize-photos-and-videos-by-day/SKILL.md)
(internally requires [`photo-gps-from-exif`](photo-gps-from-exif/SKILL.md) and
[`find-missing-files`](find-missing-files/SKILL.md))

```bash
python3 scripts/organize_media_by_local_date.py "<source_root>" "<destination_root>" \
    --recording-timezone Asia/Shanghai \
    --report organize_media_report.json
# review report, then:
python3 scripts/organize_media_by_local_date.py "<source_root>" "<destination_root>" \
    --recording-timezone Asia/Shanghai \
    --apply --report organize_media_report.json
```

- **Copies** (never moves/deletes) every media file from `source_root` into
  `destination_root/%Y/%Y_%m_%d`, keyed off the correct **local capture date**.
- Date resolution is offline and timezone-aware:
  1. EXIF/media capture datetime, corrected to local time using GPS coordinates (offline
     `timezonefinder` + `zoneinfo` — no network calls) when GPS is present.
  2. If EXIF has a UTC offset but no GPS, converts using `--recording-timezone`.
  3. Falls back to an adjacent sibling file's embedded capture time (same folder, same shooting
     sequence).
  4. Falls back to file creation time, then file mtime — treated as recording-local wall time when
     `--recording-timezone` is given.
- `--recording-timezone` is **required** if any file in the source lacks GPS (the script pre-scans
  and fails fast); optional if every file has GPS.
- Classifies files by real MIME/FileType (via metadata/`ffprobe`), not by extension, so
  misnamed or extensionless media isn't lost. Unknown signatures are auto-triaged with `ffprobe`
  and cached; unresolved ones default to "keep as media candidate" to avoid data loss.
- Explicitly never copies known non-media junk (`.url`, `.ini`, `.bk`, `.sav`, `.db`, `.log`,
  `.txt`).
- Name collisions (same name, different content) get deterministic `_col001`, `_col002` suffixes.
- **Verification is built in**: apply mode isn't considered successful unless there are zero
  `missed_media_files`, zero `media_copy_failed`, and the `find-missing-files` content-hash
  verification pass succeeds. Full results land in the JSON `--report`.
- The script can run for a long time on large trees; it emits `[progress]`/`[phase]` lines to
  stderr — treat `[done] report written: ...` plus process exit as the real completion signal, not
  the 100% progress line.

## Step 3 — Rename day folders to itinerary landmarks

**Skill:** [`AI-folder-poi-itinerary-rename`](AI-folder-poi-itinerary-rename/SKILL.md)
(internally requires [`photo-gps-from-exif`](photo-gps-from-exif/SKILL.md))

```bash
# dry run first
python3 scripts/rename_folder_by_ai_itinerary.py "<destination_root>/2025/2025_07_24"
# or run over a whole year/tree
python3 scripts/rename_folder_by_ai_itinerary.py "<destination_root>/2025" --ratio 0.05
# then apply
python3 scripts/rename_folder_by_ai_itinerary.py "<destination_root>/2025/2025_07_24" --apply
```

- Scans all media in a day folder, extracts GPS + capture timestamps, sorts by capture time.
- Infers nearby landmark names from **general geographic knowledge** — this skill deliberately
  does **not** call any reverse-geocoding or geo-lookup API (that rules out
  [`locationiq-nearby-poi`](locationiq-nearby-poi/SKILL.md) for this step; that skill is a
  separate, unrelated reference for building API-based nearby-POI lookups).
- Prefers well-known landmarks over generic city/region/country names, keeps mixed-country
  itineraries in one folder/time order rather than splitting by country, and drops low-confidence
  guesses (`UnknownLandmark`) rather than mislabeling.
- Renames the folder to `YYYY_MM_DD_L1,L2,L3` (comma-joined, first-seen itinerary order), e.g.
  `2025_07_24_Magnusarfoss,Fjarargljufur,VikChurch,Skogafoss,Seljalandsfoss`.
- Supports resuming a failed/interrupted run and running over an entire tree (e.g. a whole year)
  at once, with a summary report plus a detailed per-folder state file, and post-run integrity
  checks (folder-count reconciliation + filesystem cross-check).

## Step 4 — Final independent audit (optional)

**Skill:** [`find-missing-files`](find-missing-files/SKILL.md)

Even though step 2 already runs this verification internally, you can re-run it standalone at any
point as an independent, content-hash-based (SHA-256) audit that the destination is a true
superset of the original source — useful after step 1's dedup, or as a final sanity check once
everything is renamed in step 3, since renaming folders doesn't change file content hashes.

```bash
python scripts/check_missing_files_between_two_folders.py \
    "<source_root>" "<destination_root>" \
    --skip-extension .THM --skip-extension .LRV \
    --output ~/missing_files.txt --verbose
```

## Design principles behind the whole pipeline

- **Never move or delete the original source** — every step copies. The source tree stays intact
  as a fallback until you're satisfied and choose to remove it yourself.
- **No-loss verification is not optional** — every copy/reorganize step (`dedup-copy`,
  `organize-photos-and-videos-by-day`) is paired with an independent content-hash check
  (`find-missing-files`), and results are written to JSON reports for audit.
- **Offline where possible** — date/timezone correction uses local GPS-to-timezone polygon data
  (`timezonefinder`) and IANA `zoneinfo`, not a network timezone API. Landmark naming uses model
  geographic knowledge, not a reverse-geocoding API call.
- **Classify by content, not filename** — media detection is signature/metadata-based so
  misnamed, renamed, or extensionless files aren't silently dropped.
