---
name: download-youtube
description: >-
  Download a YouTube video or channel with yt-dlp, and write a SubRip .srt
  caption in the video's spoken language next to the video so VLC loads it.
  Use this whenever the
  user pastes a youtube.com or youtu.be URL and asks to download, save, or grab
  the video, audio, subtitles, captions, or a transcript, including a channel,
  matching-language captions, or a transcript turned into a note. Also use it
  when they say "the same way as last time" after a YouTube download. Skip it
  for editing, trimming, or splitting a video file that is already on disk.
---

# Download YouTube video and captions

yt-dlp is open source (the Unlicense). Use a local virtualenv copy. Do not `pip install` it into the system Python: Debian and Ubuntu reject that with `externally-managed-environment`.

Two details are easy to get wrong, and both produce a file that looks successful but is the wrong media:

- YouTube lists many dubbed audio tracks next to the original. The format id suffix changes per video, so `140-1` is not always the original. Read the format table and pick the row marked `original`.
- Playlist/HLS rows marked `Untested` are a fallback. Prefer the `https` DASH row at the same resolution.

Save the file under `$HOME/Downloads`. A video does not belong in a notes or code git repo.

## Setup

Create the tool once per machine. `/tmp/ytdlp-venv` is disposable; recreate it when the binary is missing.

```bash
if [ ! -x /tmp/ytdlp-venv/bin/yt-dlp ]; then
  python3 -m venv /tmp/ytdlp-venv
  /tmp/ytdlp-venv/bin/pip install -q yt-dlp
fi
```

YouTube extraction wants a JavaScript runtime. Deno is often absent. Node is enough. Discover it; do not hardcode a home directory or a version path.

```bash
NODE="$(command -v node || true)"
if [ -z "$NODE" ] && [ -d "$HOME/.nvm/versions/node" ]; then
  NODE="$(find "$HOME/.nvm/versions/node" -type f -path '*/bin/node' | sort -V | tail -1)"
fi
```

Pass `--js-runtimes "node:$NODE"` on every yt-dlp command when `NODE` is set. Without a runtime, yt-dlp warns that some formats may be missing.

`ffmpeg` must be on `PATH` so video and audio can be merged.

If a command fails because the sandbox cannot create user namespaces (`Failed to unshare namespaces` / `EPERM`), rerun that same command with unrestricted permissions. That is a sandbox limitation, not a yt-dlp failure.

## Download the video

List formats before choosing ids:

```bash
/tmp/ytdlp-venv/bin/yt-dlp --js-runtimes "node:$NODE" \
  --print "%(title)s" --print "%(duration_string)s" --print "%(channel)s" \
  -F "VIDEO_URL"
```

Default, unless the user names another resolution:

- Video: `https` MP4, `avc1`, height 1080. H.264 plays everywhere. Use a 4K or AV1 row only when the user asks for it.
- Audio: `https` M4A whose info line says `original` and whose codec is the medium AAC (`mp4a.40.2`, about 128k). Skip rows that say `dubbed`. Skip the low-bitrate original if a medium original exists.

```bash
/tmp/ytdlp-venv/bin/yt-dlp --js-runtimes "node:$NODE" \
  -f "VIDEO_ID+AUDIO_ID" --merge-output-format mp4 \
  -o "$HOME/Downloads/%(title)s [%(id)s].%(ext)s" \
  "VIDEO_URL"
```

Report the merged path and size. yt-dlp deletes the separate video and audio files after a successful merge.

Also write one SubRip sidecar for that video, in the spoken language, in the same directory as that mp4. The file name is the mp4 name with `.srt` instead of `.mp4`. VLC autoloads that name. Captions stay a separate file. Do not mux them into the mp4.

## Download a channel

Format ids change per video, so do not reuse one video's ids for the channel. Use a selector that encodes the same rules: `https` `avc1` at 1080, plus the medium original AAC. Fall back to a lower `https` `avc1` height when 1080 is missing, and to a non-dubbed medium AAC when no row is labeled `original`.

Put the channel in its own directory under `$HOME/Downloads`. The caption sidecars use that same directory. A bulk pass sleeps between requests so YouTube does not answer HTTP 429. Record finished videos in a video archive. That archive is not the caption archive.

```bash
SEL='bv*[height=1080][ext=mp4][vcodec^=avc1][protocol=https]+ba[acodec^=mp4a.40.2][protocol=https][format_note*=original]/bv*[height<=1080][ext=mp4][vcodec^=avc1][protocol=https]+ba[acodec^=mp4a.40.2][protocol=https][format_note*=original]/bv*[height<=1080][ext=mp4][vcodec^=avc1][protocol=https]+ba[acodec^=mp4a.40.2][protocol=https][format_note!*=DRC][format_note!*=dubbed]'
/tmp/ytdlp-venv/bin/yt-dlp --js-runtimes "node:$NODE" \
  -f "$SEL" --merge-output-format mp4 \
  --ignore-errors --no-overwrites \
  --sleep-requests 2 --retries 15 --retry-sleep "http:exp=2:45" \
  --download-archive "$HOME/Downloads/CHANNEL/archive.txt" \
  -o "$HOME/Downloads/CHANNEL/%(title)s [%(id)s].%(ext)s" \
  "CHANNEL_VIDEOS_URL"
```

When the user asks for a date cutoff, add `--dateafter YYYYMMDD` and `--break-match-filters "upload_date >= YYYYMMDD"`. The videos tab is newest-first, so the break stops the walk at the first older video. Do not put a language test in `--break-match-filters`.

## Matching-language captions

List subtitle languages and the spoken language before choosing a code:

```bash
/tmp/ytdlp-venv/bin/yt-dlp --js-runtimes "node:$NODE" \
  --skip-download --list-subs --print "%(language)s" \
  "VIDEO_URL"
```

YouTube's usual captions are automatic. Pass both `--write-subs` and `--write-auto-subs`. `--write-subs` alone skips automatic captions.

Pick the track that matches the spoken language. A subtitle row labeled "Original" is not proof of that language: a Chinese video can still list `en-orig` as "English (Original)". Translation tracks look like `en-zh` or say "English from Chinese". Download a translation only when the user asks for that language.

| Spoken `language` | Caption code |
|---|---|
| `en`, `en-US`, `en-GB`, any `en-…` | `en-orig`. If that code is absent, plain `en`. |
| `zh-Hant`, `zh-TW` | `zh-Hant` |
| `zh-Hans`, `zh-CN`, or exact `zh` | `zh-Hans` |
| anything else | that language's `*-orig` code when listed, otherwise its plain code |

One code per video. Do not also save the other Chinese script, or the plain `en` track when `en-orig` exists.

Download SubRip. yt-dlp inserts the language code, so this template first writes `Title [id].en-orig.srt` or `Title [id].zh-Hant.srt`:

```bash
/tmp/ytdlp-venv/bin/yt-dlp --js-runtimes "node:$NODE" \
  --skip-download --write-subs --write-auto-subs \
  --sub-langs "CAPTION_CODE" --sub-format srt \
  --no-overwrites \
  -o "$HOME/Downloads/%(title)s [%(id)s].%(ext)s" \
  "VIDEO_URL"
```

Then rename that file to the mp4's name with `.srt`. For `Title [id].mp4`, the caption VLC loads is `Title [id].srt` in the same directory. Use the same directory as the mp4. For a single video that is `$HOME/Downloads`. For a channel that is `$HOME/Downloads/CHANNEL`.

One spoken language becomes that single `Title [id].srt`. Two spoken languages stay separate as `Title [id].en.srt` and `Title [id].zh.srt`. Do not leave only the `.en-orig.srt` or `.zh-Hant.srt` name: that is not the name VLC autoloads.

A json3 file already on disk is not a player subtitle. Convert it with the bundled script. The script drops newline-only events. Each cue starts at `tStartMs` and ends at the earlier of its duration and the next cue.

```bash
python3 scripts/json3_to_srt.py "Title [id].en-orig.json3" "Title [id].srt"
```

On a channel, `--sub-langs` cannot change per video. Make one pass per spoken language. `--match-filters` skips a video in another language. Do not put the language test in `--break-match-filters`: that stops the whole channel at the first non-match. Use `--break-match-filters` only for a date cutoff the user asked for, on a newest-first videos tab. Keep caption ids in their own archive file. The video archive skips videos that already downloaded and then writes no caption.

`--skip-download` does not write that archive unless `--force-write-archive` is also set. Without the force flag the archive stays empty, and the next run fetches every caption again.

```bash
/tmp/ytdlp-venv/bin/yt-dlp --js-runtimes "node:$NODE" \
  --skip-download --write-subs --write-auto-subs \
  --sub-langs "zh-Hant" --sub-format srt \
  --match-filters "language ^= zh-Hant" --match-filters "language = zh-TW" \
  --ignore-errors --no-overwrites \
  --sleep-requests 2 --retries 15 --retry-sleep "http:exp=2:45" \
  --download-archive "$HOME/Downloads/CHANNEL/subs-zh-Hant.archive.txt" \
  --force-write-archive \
  -o "$HOME/Downloads/CHANNEL/%(title)s [%(id)s].%(ext)s" \
  "CHANNEL_VIDEOS_URL"
```

Repeat that command, still including `--force-write-archive`, with `--sub-langs "en-orig"` and `--match-filters "language ^= en"`, and with `--sub-langs "zh-Hans"` for `zh-Hans`, `zh-CN`, and exact `zh`. Several subtitle passes at once, on top of video downloads, get HTTP 429. Run one caption pass at a time and keep the sleep.

After each pass, rename `Title [id].zh-Hant.srt`, `Title [id].zh-Hans.srt`, or `Title [id].en-orig.srt` to `Title [id].srt` when that video has one spoken language. Report the renamed path. A 429 on one video is a retry, not a reason to switch that video to another language.

## Transcript note

The `.srt` file is for playback. A transcript note still starts from json3. Download that format only when the user wants a note (`--sub-format json3`), or keep a json3 file already downloaded. Turn it into paragraphs with the bundled script. A pause of about 1.5 seconds starts a new paragraph, which matches how auto-captions break speech.

```bash
python3 scripts/json3_to_transcript.py "/path/to/Title [VIDEO_ID].en-orig.json3"
```

The script path is relative to this skill directory.

When the user wants a note, write the transcript in the caption language. Smooth broken auto-caption grammar and use the phrase the speaker settled on, while keeping their meaning. Use Obsidian highlight (`==...==`) only for a span they asked to highlight. Put the note in the vault they are already working in. Do not copy the video file into that vault.
