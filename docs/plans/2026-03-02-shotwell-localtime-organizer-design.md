# Shotwell Localtime Organizer Design

## Goal

Create a skill and script that replicate Shotwell-style date classification (`%Y/%Y_%m_%d`) while correcting UTC/GMT capture times to local capture dates using offline timezone rules.

## Inputs

- `source_root`: recursive source tree containing mixed files.
- `destination_root`: organized output root.

## Classification Rules

1. Prefer EXIF/media capture time fields.
2. If EXIF time missing, fallback to file creation time.
3. If file creation time unavailable, fallback to file mtime.
4. If timestamp is naive and GPS UTC tags exist, treat GPS UTC as authoritative and convert to local timezone.
5. If timestamp is naive and GPS UTC tags do not exist, keep EXIF timestamp as local-naive.
6. If timestamp has explicit offset, convert to geolocation timezone when GPS is available.

## Timezone Conversion

- Use offline timezone polygons (`timezonefinder`) to resolve timezone from lat/lon.
- Use IANA timezone database (`zoneinfo`) for local date conversion and DST.
- Do not use online geolocation/timezone APIs.

## Media Detection (No Hardcoded Extension Allowlist)

- Primary: `exiftool` `MIMEType`/`FileType` metadata.
- Media if MIME starts with `image/` or `video/`.
- Unknown signatures use cache-backed classification.
- If signature unknown and uncached, classify conservatively as media candidate to avoid media loss and emit it for AI/internet lookup once per unique signature.

## Data Fidelity and No-Loss

- Copy media files only (never mutate source, never rewrite metadata).
- Preserve metadata/timestamps using high-fidelity copy operations.
- Collision policy: deterministic suffix (`_dup001`, `_dup002`, ...).
- Emit JSON report with full source paths for:
  - `media_copy_failed`
  - `missed_media_files`
  - `non_media_not_copied`

## Verification

- Perform path/count checks for all detected media.
- Perform content-hash verification using `find-missing-files` methodology/functions.
- Fail non-zero if any detected media file is missing in destination.
