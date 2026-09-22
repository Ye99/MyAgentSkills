---
name: download-youtube
description: >-
  Download a YouTube video or its original-language captions with the open-source
  tool yt-dlp. Use this whenever the user pastes a youtube.com or youtu.be URL and
  asks to download, save, or grab the video, audio, subtitles, captions, or a
  transcript, including when they want that transcript turned into a note. Also
  use it when they say "the same way as last time" after a YouTube download.
  Skip it for editing, trimming, or splitting a video file that is already on disk.
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

## Download original captions

List subtitle languages first. Translation tracks are named like `en-zh` and described as "English from Chinese". The original track is the short language code (`zh`, `en`, and so on) that is not a translation of another language. Download that one. Download a translation only when the user asks for one.

```bash
/tmp/ytdlp-venv/bin/yt-dlp --js-runtimes "node:$NODE" \
  --skip-download --write-subs --sub-langs ORIGINAL_LANG --sub-format json3 \
  -o "/tmp/yt-captions/%(id)s.%(ext)s" \
  "VIDEO_URL"
```

Turn the json3 file into paragraphs with the bundled script. A pause of about 1.5 seconds starts a new paragraph, which matches how auto-captions break speech.

```bash
python3 scripts/json3_to_transcript.py /tmp/yt-captions/VIDEO_ID.ORIGINAL_LANG.json3
```

The script path is relative to this skill directory.

When the user wants a note, write the transcript in the caption language. Smooth broken auto-caption grammar and use the phrase the speaker settled on, while keeping their meaning. Use Obsidian highlight (`==...==`) only for a span they asked to highlight. Put the note in the vault they are already working in. Do not copy the video file into that vault.
