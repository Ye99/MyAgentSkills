---
name: audit-indexed-media-downloads
description: Use when a downloaded lecture, course, or conference folder contains indexed MP4 videos, unindexed or duplicated SRT subtitles, copy-suffix files such as (1), suspected index gaps, or uncertain media completeness.
---

# Audit Indexed Media Downloads

## Overview

Treat indexed MP4 names as the local authority for subtitle prefixes and SHA-256 equality as the only automatic basis for deletion. Build and review a complete dry-run plan before changing files.

## Workflow

1. Resolve the exact target directory. Do not use a broad directory such as a home folder.
2. From this skill directory, run:

   ```bash
   python3 scripts/audit_indexed_media.py /exact/media/directory --media-check scan
   ```

3. Review every proposed rename/deletion and all `CONFLICT`, missing-pair, index-gap, partial-download, and media-check findings.
4. If any conflict appears, stop. Do not rename or delete anything.
5. Only when the user has explicitly authorized the proposed mutations, apply them:

   ```bash
   python3 scripts/audit_indexed_media.py /exact/media/directory --media-check scan --apply
   ```

6. Re-run the dry-run command. Exit status `0` means the local set is clean; `1` means unresolved completeness findings; `2` means a conflict or operational error.

## Matching and deletion contract

- The helper removes a leading numeric index prefix of any width (`1. `, `001. `, `00001. `), language suffix such as `.en`, a proven copy suffix such as `(1)`, Unicode presentation differences, and dash/spacing differences only for title comparison.
- A trailing `(n)` counts as a copy suffix **only when the un-suffixed sibling filename exists in the same directory**, because that sibling is what a download manager was copying. Otherwise the parentheses belong to the title - a release year (`AlexNet (2012)`), a lesson number (`Lesson 3.2 - FAQ (3)`) or a domain number (`AI Threat Landscapes (10)`).
- Videos and subtitles pair on the index **number**, so a set that mixes widths (`001.` with `00001.`) still pairs. Reported gaps and rename targets keep the widest index width observed in the directory.
- Lesson assets are video (`.mp4`) or audio (`.mp3`, `.m4a`, `.m4b`); audiobooks pair with subtitles exactly as video courses do.
- A language suffix may carry a separated region subtag (`.zh tw`, `.pt-br`), which is a caption variant of the same lesson, not an orphan.
- A directory with no subtitle files at all reports `SUBTITLES: none present` once instead of listing every asset as missing a caption. Partial coverage still reports each missing item.
- An unindexed SRT is renamed only when its normalized title matches exactly one indexed, unsuffixed MP4.
- Never infer an unindexed video's number from a numeric gap. Obtain it from an authoritative playlist or download manifest.
- An existing destination with different content blocks the entire apply operation.
- Duplicate deletion requires identical SHA-256 content. The keeper preference is indexed, then unsuffixed, then shortest deterministic name.
- `--apply` permanently deletes proven duplicates; it does not move them to Trash. Confirm authorization immediately before using it.
- Same-content files with different normalized titles require manual review and are not deleted automatically.

## Completeness boundary

`ffprobe` verifies containers, duration metadata, and the presence of an audio stream. A missing video stream is not an error: publishers ship audio-only lessons, including podcast episodes packaged as `.mp4`. `--media-check scan` additionally reads every packet with `ffmpeg`. These checks establish local readability, not that the provider published no additional items. Proving remote completeness requires an authoritative item count, playlist, manifest, expected sizes/checksums, or downloader archive.

## Common mistakes

| Mistake | Correct response |
|---|---|
| Deleting `(1)` based on its name | Require an identical SHA-256 hash |
| Filling `006` because `005` and `007` exist | Report the gap; consult the source listing |
| Treating a title match as proof of duplicate content | Use titles for pairing and hashes for deletion |
| Reading any trailing `(n)` as a copy marker | Require the un-suffixed sibling to exist; otherwise it is part of the title |
| Assuming a 3-digit `NNN.` index | Indexes come in any width; pair on the number, not the literal prefix |
| Claiming full download completeness from `ffprobe` | State “locally valid; source completeness unproven” without source metadata |
| Reading an index gap as a missing lesson | Chapter headings occupy numbers without producing a file; confirm against the publisher's table of contents |
| Reporting every asset as missing a caption | A title with no subtitles at all was published without them; only partial coverage is a gap |

## Requirements

Python 3 is required. `ffprobe` is required for `probe`/`scan`; `ffmpeg` is additionally required for `scan`. Use `--media-check none` only when the user does not request integrity validation.
