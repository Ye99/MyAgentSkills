#!/usr/bin/env python3
"""Turn a yt-dlp json3 caption file into timestamped paragraphs."""

import json
import sys
from pathlib import Path

PAUSE_MS = 1500


def cues_from_payload(payload):
    for event in payload.get("events", []):
        segments = event.get("segs") or []
        text = "".join(segment.get("utf8", "") for segment in segments)
        text = " ".join(text.replace("\n", " ").split())
        if not text:
            continue
        yield int(event.get("tStartMs", 0)), text


def paragraphs_from_cues(cues, pause_ms=PAUSE_MS):
    paragraphs = []
    current_start = None
    parts = []
    previous_start = None
    for start_ms, text in cues:
        if current_start is None:
            current_start = start_ms
            parts = [text]
        elif previous_start is not None and start_ms - previous_start >= pause_ms:
            paragraphs.append((current_start, " ".join(parts)))
            current_start = start_ms
            parts = [text]
        else:
            parts.append(text)
        previous_start = start_ms
    if parts:
        paragraphs.append((current_start, " ".join(parts)))
    return paragraphs


def format_timestamp(start_ms):
    total_seconds = start_ms // 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"
    return f"[{minutes:02d}:{seconds:02d}]"


def render(paragraphs):
    lines = [
        f"{format_timestamp(start_ms)} {text}" for start_ms, text in paragraphs
    ]
    return "\n\n".join(lines) + ("\n" if lines else "")


def main(argv):
    if len(argv) != 2:
        print(f"usage: {argv[0]} captions.json3", file=sys.stderr)
        return 2
    payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    sys.stdout.write(render(paragraphs_from_cues(cues_from_payload(payload))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
