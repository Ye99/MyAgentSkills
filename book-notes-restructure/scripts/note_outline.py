#!/usr/bin/env python3
"""Inventory the sections of a Markdown note so a regrouping plan can be written.

    note_outline.py NOTE.md [--from-line N] [--json out.json] [--preview CHARS]

Prints one row per heading: index, line, level, body line range, size, and a
preview of the body. `--from-line` restricts the inventory to the region being
restructured (for example, where a book's notes start inside a larger note).

Body ranges are 1-based and inclusive, and are what a plan's `lines` entries use.
Code fences are tracked so `#` inside a fence is never mistaken for a heading.
"""
import argparse
import json
import re
import sys

HEADING = re.compile(r'^(#{1,6}) (.*)$')


def parse_sections(lines, start=1):
    """Return heading sections with 1-based inclusive body ranges."""
    secs, in_code = [], False
    for i in range(start - 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith('```'):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = HEADING.match(lines[i])
        if m:
            secs.append({'line': i + 1, 'level': len(m.group(1)), 'title': m.group(2).strip()})
    for n, s in enumerate(secs):
        nxt = secs[n + 1]['line'] if n + 1 < len(secs) else len(lines) + 1
        s['body_from'] = s['line'] + 1
        s['body_to'] = nxt - 1
        if s['body_to'] < s['body_from']:
            s['body_to'] = s['body_from'] - 1
    return secs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('note')
    ap.add_argument('--from-line', type=int, default=1)
    ap.add_argument('--json')
    ap.add_argument('--preview', type=int, default=130)
    args = ap.parse_args()

    lines = open(args.note, encoding='utf-8').read().split('\n')
    secs = parse_sections(lines, args.from_line)

    for n, s in enumerate(secs):
        body = [l for l in lines[s['body_from'] - 1:s['body_to']] if l.strip()]
        size = s['body_to'] - s['body_from'] + 1
        print(f"[{n:3d}] L{s['line']:<6d} h{s['level']} body={s['body_from']}-{s['body_to']} "
              f"({size:4d} ln) {s['title'][:80]}")
        if args.preview > 0 and body:
            print(f"        | {' '.join(body)[:args.preview]}")

    if args.json:
        json.dump(secs, open(args.json, 'w'), indent=1)
        print(f"\nwrote {args.json} ({len(secs)} sections)")


if __name__ == '__main__':
    sys.exit(main())
