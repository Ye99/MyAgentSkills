---
name: split-gopro-video
description: >-
  Use when splitting, trimming, cutting, clipping, or extracting time-range segments from GoPro
  MP4 footage, especially when the user wants stream-copy quality, preserved GPS/telemetry data,
  original bitrate, or files such as GX*.MP4 and DCIM/100GOPRO recordings.
---

# GoPro Video Splitter

Split GoPro MP4 files into named time-range segments with full fidelity — original HEVC codec, bitrate, GPMF GPS/telemetry track, and accurate per-segment timestamps all preserved.

Prefer mature open-source tools (`ffmpeg`, `ffprobe`, `exiftool`) over custom container-rewriting code in this workflow.

## Why this matters

GoPro files have four streams: video, audio, a timecode track, and a GPMF track carrying GPS coordinates, gyroscope, and accelerometer data. A naive `ffmpeg -map 0 -c copy` breaks because the timecode track (`tmcd`) is incompatible with MP4 muxing. And any re-encoding approach (kdenlive, HandBrake presets, etc.) destroys the original ~70–100 Mbps bitrate. This skill threads both needles.

## Prerequisites

```bash
sudo apt install ffmpeg exiftool
```

## Step 1 — Probe the source

Before cutting, verify the stream layout and capture the recording timestamp:

```bash
ffprobe -v quiet \
  -show_entries stream=index,codec_name,codec_type,codec_tag_string \
  -of json <source.MP4>
```

Typical GoPro stream layout:

| Index | Codec | Tag | What to do |
|-------|-------|-----|------------|
| 0 | hevc | hvc1 | keep |
| 1 | aac | mp4a | keep |
| 2 | data | tmcd | **skip** — breaks MP4 muxer |
| 3 | bin_data | gpmd | **keep** — GPS + sensors |

Also grab the source creation time — you'll need it to set correct timestamps on each segment:

```bash
exiftool -api largefilesupport=1 <source.MP4> | grep "Create Date"
```

## Step 2 — Find keyframe boundaries (required)

Stream copy must start and end on keyframe boundaries to avoid visual corruption (snow/macroblocking) at segment boundaries. GoPro records a keyframe every ~1 second. Always resolve the user's timestamps to actual keyframe timestamps before cutting.

```bash
# Find all keyframes near a boundary time (e.g. around 2:35 = 155s)
ffprobe -select_streams v \
  -read_intervals "00:02:20%00:02:50" \
  -show_frames \
  ~/source.MP4 2>/dev/null | \
  awk '/key_frame=1/{kf=1} kf && /best_effort_timestamp_time/{print $0; kf=0}'
```

**Keyframe alignment rules — no data loss:**

| Scenario | Rule |
|----------|------|
| First segment starts at 0 | Always start at 0 — no adjustment needed |
| Boundary between two consecutive segments | Snap to the **first keyframe at or after** the user's timestamp. Both segments share this boundary — no gap, no overlap |
| Last segment's end (or single-segment end) | Snap to the **first keyframe at or after** the user's end time — clip is slightly longer, no data lost |

**Example:** User asks for 0→2:35 and 2:35→6:18. Keyframes are at 154.154s and 155.155s.
- Segment 1: `0` → `155.155s` (snapped forward to next keyframe after 2:35)
- Segment 2: `155.155s` → first keyframe at or after 6:18

This ensures no frame is dropped at the boundary.

## Step 3 — Cut each segment

Use explicit stream mapping for the probed video, audio, and `gpmd` streams while excluding
`tmcd`. For the typical layout above, that is `0:0 0:1 0:3`.

```bash
# Segment 1: 0 to keyframe boundary (e.g. 155.155s)
ffmpeg -y -ss 0 -i source.MP4 \
  -to 155.155 \
  -map 0:0 -map 0:1 -map 0:3 \
  -c copy \
  Segment1.MP4

# Segment 2: keyframe boundary to end (duration = end_keyframe - 155.155)
ffmpeg -y -ss 155.155 -i source.MP4 \
  -to <duration> \
  -map 0:0 -map 0:1 -map 0:3 \
  -c copy \
  Segment2.MP4
```

**Why `-ss` before `-i`:** Fast input seeking — jumps directly to the keyframe, no decode overhead.

**Why `-to` is a duration here:** When `-ss` is an input option, `-to` on the output is relative to the seek point, not the original file start.

## Step 4 — Fix timestamps

ffmpeg zeros all MP4 container timestamps when writing new files. You need to patch three levels of atoms — the top-level `mvhd`, the per-track `tkhd`, and the media `mdhd`:

Calculate each segment's start time = source create date + segment start offset in seconds.

```bash
exiftool -api largefilesupport=1 \
  -AllDates="YYYY:MM:DD HH:MM:SS" \
  -TrackCreateDate="YYYY:MM:DD HH:MM:SS" \
  -TrackModifyDate="YYYY:MM:DD HH:MM:SS" \
  -MediaCreateDate="YYYY:MM:DD HH:MM:SS" \
  -MediaModifyDate="YYYY:MM:DD HH:MM:SS" \
  -overwrite_original \
  Segment.MP4
```

Using only `-AllDates` is not enough — it covers `mvhd` but leaves `tkhd`/`mdhd` zeroed. All five flags are needed.

GoPro timestamps are UTC. Add seconds to get each segment's start time (e.g., a segment starting at 2:35 = 155 s after the source create date).

## Step 5 — Verify

```bash
exiftool -api largefilesupport=1 <output.MP4> \
  | grep -E "Create Date|Modify Date|Meta Format|Duration|Avg Bitrate"
```

**What to expect:**
- All date fields show the correct recording time (not `0000:00:00`)
- `Meta Format: gpmd` — GPMF/GPS track is present
- `Avg Bitrate` will read higher than the source (~100 Mbps vs ~70 Mbps) — this is normal for shorter clips due to MP4 container overhead math; the actual encoded frames are untouched

**High-confidence preservation check for supported streams:**

Use `streamhash` to compare the source cut range against the exported clip for the supported streams only: video, audio, and `gpmd` telemetry.

```bash
# Source range for segment 1
ffmpeg -v error -ss 0 -i source.MP4 \
  -to 155.155 \
  -map 0:0 -map 0:1 -map 0:3 \
  -c copy -f streamhash -hash sha256 -

# Exported segment 1
ffmpeg -v error -i Segment1.MP4 \
  -map 0:0 -map 0:1 -map 0:2 \
  -c copy -f streamhash -hash sha256 -
```

Matching hashes confirm the exported clip preserved the supported stream payloads exactly. Do not expect byte-for-byte identity for container metadata, timestamps, brands, or GoPro proprietary `udta` data.

## Important limitation — do not raw-copy `udta`

ffmpeg drops GoPro's proprietary `udta` box, which contains camera model, serial number, firmware version, and recording settings.

Do **not** try to restore `udta` by byte-splicing Python code or any other raw atom-copy approach. That can leave MP4 sample/chunk offsets inconsistent and produce files that look metadata-complete but no longer decode in VLC or ffmpeg.

Treat `udta` restoration as out of scope for this skill unless you are using a dedicated MP4 atom editor that rewrites all affected offsets correctly.

## Common pitfalls

| Mistake | Fix |
|---------|-----|
| Cutting at user's exact timestamp without keyframe alignment | Causes snow/macroblocking at segment start — always snap to keyframe at or after the boundary |
| Snapping boundary backward (to keyframe before) | Loses frames between that keyframe and the boundary — snap forward instead |
| `-map 0` instead of explicit kept streams | The `tmcd` stream breaks MP4 output — map the probed video/audio/gpmd streams explicitly and exclude `tmcd` |
| Only using `-AllDates` in exiftool | Track-level timestamps stay zeroed — add the four `-Track*` / `-Media*` flags |
| Re-encoding via kdenlive/melt/HandBrake | Drops bitrate 10–15×; use `-c copy` to preserve the original |
| Treating `-to` as an end timestamp | When `-ss` is an input option, `-to` is duration from the cut point |
| Trying to raw-copy `udta` with Python byte splicing | Can corrupt MP4 offsets and make the clip unplayable — do not do this in this workflow |
| Replacing a standard tool step with custom container-editing code | Prefer existing open-source tools unless you have a validated MP4 atom editor that preserves playback |

## What is not preserved

- **Timecode track** (`tmcd`) — dropped intentionally; it is incompatible with MP4 muxing alongside HEVC.
- **GoPro proprietary `udta` camera metadata** — not preserved by this safe ffmpeg + exiftool workflow.
