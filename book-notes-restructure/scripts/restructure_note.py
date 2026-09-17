#!/usr/bin/env python3
"""Rebuild a note region from a regrouping plan, moving every body line verbatim.

    restructure_note.py NOTE.md --plan plan.json [--out FILE] [--dry-run]

Only three things change: heading text/level, the order of blocks, and callout
quoting around blocks marked `lookup`. Body lines themselves are copied byte for
byte, so the result can be proved verbatim by verify_verbatim.py.

Plan format (1-based inclusive line numbers, referring to the ORIGINAL note):

    {
      "start_line": 530,                      # region start; everything above is untouched
      "callout": "> [!note]+ My lookup",      # wrapper for `lookup` blocks
      "absorbed_lines": [535],                # lines intentionally folded into a heading
      "items": [
        {"level": 2, "title": "Source Title",        "lines": [531, 532], "kind": "source"},
        {"level": 3, "title": "Chapter 1. Overview", "lines": [533, 534], "kind": "source"},
        {"level": 3, "title": "Chapter 2. Details",                       "kind": "source"},
        {"level": 4, "title": "A question I looked up", "lines": [700, 780],
         "prepend": ["Text rescued from an absorbed line"], "kind": "lookup"}
      ]
    }

An item with no `lines` emits a heading only (used for chapter headings). Refuses
to write if any non-blank body line would be dropped or used twice.
"""
import argparse
import json
import re
import sys

HEADING = re.compile(r'^(#{1,6}) (.*)$')


def heading_lines(lines, start):
    """1-based line numbers of headings in the region, fence-aware."""
    out, in_code = set(), False
    for i in range(start - 1, len(lines)):
        if lines[i].strip().startswith('```'):
            in_code = not in_code
            continue
        if not in_code and HEADING.match(lines[i]):
            out.add(i + 1)
    return out


def trim(block):
    block = list(block)
    while block and not block[0].strip():
        block.pop(0)
    while block and not block[-1].strip():
        block.pop()
    return block


def quote(block, marker):
    out = [marker]
    for l in block:
        out.append('> ' + l if l.strip() else '>')
    return out


def build(lines, plan):
    start = plan['start_line']
    marker = plan.get('callout', '> [!note]+ My lookup')
    absorbed = set(plan.get('absorbed_lines', []))

    used = {}
    for n, item in enumerate(plan['items']):
        if not item.get('lines'):
            continue
        a, b = item['lines']
        if a < start or b > len(lines):
            raise SystemExit(f"item {n} ({item['title'][:40]!r}) range {a}-{b} outside region")
        for ln in range(a, b + 1):
            if ln in used:
                raise SystemExit(
                    f"line {ln} used by item {used[ln]} and item {n} ({item['title'][:40]!r})")
            used[ln] = n

    heads = heading_lines(lines, start)
    region = set(range(start, len(lines) + 1))
    body = region - heads
    dropped = sorted(ln for ln in body - set(used) - absorbed if lines[ln - 1].strip())
    if dropped:
        raise SystemExit(
            'these non-blank lines are in no item (add them to an item or to '
            f'absorbed_lines): {dropped[:20]}')

    out = []
    for item in plan['items']:
        out.append('#' * item['level'] + ' ' + item['title'])
        block = list(item.get('prepend', []))
        if item.get('lines'):
            a, b = item['lines']
            block += lines[a - 1:b]
        block = trim(block)
        if block:
            out.extend(quote(block, marker) if item.get('kind') == 'lookup' else block)
        out.append('')
    return lines[:start - 1] + out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('note')
    ap.add_argument('--plan', required=True)
    ap.add_argument('--out')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    lines = open(args.note, encoding='utf-8').read().split('\n')
    plan = json.load(open(args.plan, encoding='utf-8'))
    new = build(lines, plan)

    print(f'region starts at line {plan["start_line"]}; '
          f'{len(plan["items"])} items; {len(lines)} -> {len(new)} lines')
    if args.dry_run:
        print('dry run: nothing written')
        return 0

    target = args.out or args.note
    text = '\n'.join(new)
    if not text.endswith('\n'):
        text += '\n'
    open(target, 'w', encoding='utf-8').write(text)
    print(f'wrote {target}')
    print('now run verify_verbatim.py BEFORE AFTER --from-line N')
    return 0


if __name__ == '__main__':
    sys.exit(main())
