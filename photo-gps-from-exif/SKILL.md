---
name: photo-gps-from-exif
description: Use when you need latitude/longitude from image files (JPG, JPEG, HEIC, MOV) and want reliable metadata extraction with exiftool.
---

# Photo GPS From EXIF

Extract geolocation coordinates from photo/video metadata using `exiftool` as the primary tool.

## When to Use

- The user asks for coordinates from photos or videos.
- Files may include mixed formats (`.jpg`, `.jpeg`, `.heic`, `.mov`).
- You need dependable GPS parsing and consistent output.

## Why exiftool First

- It supports more metadata formats than ad hoc parsing.
- It handles Apple formats (`HEIC`, `MOV`) well.
- It can print both human-readable and numeric GPS values.

## Quick Workflow

1. Confirm target files or directory.
2. Run `scripts/extract_photo_gps.sh` for batch extraction.
3. Report files with coordinates and files without GPS separately.
4. If `exiftool` is missing, install it and re-run.

## Commands

Single file:

```bash
exiftool -n -GPSLatitude -GPSLongitude -GPSPosition "/path/to/file.jpg"
```

Directory recursive scan:

```bash
exiftool -r -ext jpg -ext jpeg -ext heic -ext mov -n -GPSLatitude -GPSLongitude -GPSPosition "/path/to/photos"
```

## Bundled Script

Use the helper script for cleaner batch output:

```bash
bash scripts/extract_photo_gps.sh "/path/to/photos"
```

What it does:
- scans supported extensions recursively
- prints decimal latitude/longitude when present
- lists files missing GPS tags

## Install exiftool

- Ubuntu/Debian: `sudo apt-get install -y libimage-exiftool-perl`
- macOS (Homebrew): `brew install exiftool`

## Common Mistakes

- Assuming every photo has GPS metadata.
- Using tools that do not fully parse HEIC/MOV metadata.
- Returning only one file result when user asked for a folder scan.
