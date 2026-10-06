#!/usr/bin/env python3
"""Turn a yt-dlp json3 caption file into a SubRip file VLC can play."""

import json
import sys
from pathlib import Path


def cue_text(event):
    raw = "".join(segment.get("utf8", "") for segment in (event.get("segs") or []))
    return " ".join(raw.replace("\n", " ").split())


def srt_time(milliseconds):
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def cues_from_payload(payload):
    spoken = []
    for event in payload.get("events", []):
        text = cue_text(event)
        if not text:
            continue
        spoken.append((
            int(event.get("tStartMs") or 0),
            int(event.get("dDurationMs") or 0),
            text,
        ))
    cues = []
    for index, (start, duration, text) in enumerate(spoken):
        if index + 1 < len(spoken):
            end = spoken[index + 1][0]
        else:
            end = start + (duration or 2000)
        if duration and start + duration < end:
            end = start + duration
        if end <= start:
            end = start + 800
        cues.append((start, end, text))
    return cues


def render(cues):
    blocks = [
        f"{number}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n"
        for number, (start, end, text) in enumerate(cues, start=1)
    ]
    return "\n".join(blocks)


def main(argv):
    if len(argv) != 3:
        print(f"usage: {argv[0]} captions.json3 output.srt", file=sys.stderr)
        return 2
    payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    Path(argv[2]).write_text(render(cues_from_payload(payload)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
