#!/usr/bin/env python3
"""Prove a restructured note kept every line verbatim.

    verify_verbatim.py BEFORE.md AFTER.md --from-line N [--callout MARKER]

Undoes the presentation-only changes in AFTER (drop headings, strip one level of
callout quoting), then runs four checks. Exits non-zero, printing the offending
lines, if any fails. It does not read the plan, so it is an independent check
rather than a restatement of what the restructure intended.

  A. blocks    every body block of BEFORE reappears in AFTER as an exact,
               contiguous run of lines (relocation is fine, rewording is not)
  B. multiset  ignoring order, the non-heading lines of BEFORE and AFTER are the
               same, so nothing was dropped or duplicated
  C. fences    code block interiors are byte-identical (whitespace-sensitive)
  D. links     image embeds and heading wikilinks in AFTER still resolve
"""
import argparse
import pathlib
import re
import sys
from collections import Counter

HEADING = re.compile(r'^#{1,6} ')


def unquote(lines, marker):
    out = []
    for l in lines:
        if l.startswith(marker):
            continue
        if l == '>':
            out.append('')
        elif l.startswith('> '):
            out.append(l[2:])
        else:
            out.append(l)
    return out


def drop_headings(lines):
    out, in_code = [], False
    for l in lines:
        if l.strip().startswith('```'):
            in_code = not in_code
            out.append(l)
            continue
        if not in_code and HEADING.match(l):
            continue
        out.append(l)
    return out


def sections(lines):
    """Body blocks of a note region, trimmed of edge blanks."""
    heads, in_code = [], False
    for i, l in enumerate(lines):
        if l.strip().startswith('```'):
            in_code = not in_code
            continue
        if not in_code and HEADING.match(l):
            heads.append(i)
    blocks, bounds = [], heads + [len(lines)]
    if heads and heads[0] > 0:
        blocks.append(lines[:heads[0]])
    for n, h in enumerate(heads):
        blocks.append(lines[h + 1:bounds[n + 1]])
    if not heads:
        blocks.append(lines)
    out = []
    for b in blocks:
        b = list(b)
        while b and not b[0].strip():
            b.pop(0)
        while b and not b[-1].strip():
            b.pop()
        if b:
            out.append(b)
    return out


def find(hay, needle, start=0):
    n = len(needle)
    for i in range(start, len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return i
    return -1


def fence_bodies(lines):
    out, cur, inside = [], [], False
    for l in lines:
        if l.strip().startswith('```'):
            if inside:
                out.append('\n'.join(cur))
                cur = []
            inside = not inside
            continue
        if inside:
            cur.append(l)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('before')
    ap.add_argument('after')
    ap.add_argument('--from-line', type=int, required=True)
    ap.add_argument('--callout', default='> [!note]+')
    ap.add_argument('--vault', default='.', help='root for resolving image embeds')
    args = ap.parse_args()

    start = args.from_line
    before = pathlib.Path(args.before).read_text(encoding='utf-8').split('\n')[start - 1:]
    after_raw = pathlib.Path(args.after).read_text(encoding='utf-8').split('\n')[start - 1:]
    after = drop_headings(unquote(after_raw, args.callout))

    failures = []

    # A. every BEFORE block reappears verbatim (possibly split into relocated pieces)
    multi = 0
    for blk in sections(before):
        if find(after, blk) != -1:
            continue
        work, pieces, ok = list(blk), 0, True
        while work:
            if not work[0].strip():
                work.pop(0)
                continue
            best = 0
            for n in range(len(work), 0, -1):
                if work[:n][-1].strip() and find(after, work[:n]) != -1:
                    best = n
                    break
            if best == 0:
                failures.append(f'A. not verbatim, first unmatched line: {work[0][:120]!r}')
                ok = False
                break
            work = work[best:]
            pieces += 1
        if ok:
            multi += 1
    print(f'A. blocks: all matched verbatim ({multi} arrived as >1 relocated piece)'
          if not [f for f in failures if f.startswith('A.')] else 'A. blocks: FAILED')

    # B. multiset of non-heading lines, whitespace-only lines normalised
    norm = lambda L: Counter('' if not l.strip() else l for l in L if not HEADING.match(l))
    cb, ca = norm(before), norm(after)
    only_b = {k: v for k, v in (cb - ca).items() if k.strip()}
    only_a = {k: v for k, v in (ca - cb).items() if k.strip()}
    if only_b or only_a:
        failures.append(f'B. only in BEFORE: {list(only_b)[:5]} | only in AFTER: {list(only_a)[:5]}')
        print('B. multiset: FAILED')
    else:
        blanks = (cb.get('', 0), ca.get('', 0))
        print(f'B. multiset: identical non-blank lines (blank lines {blanks[0]} -> {blanks[1]})')

    # C. code fence interiors
    fb, fa = Counter(fence_bodies(before)), Counter(fence_bodies(after))
    if fb != fa:
        failures.append(f'C. code blocks differ: {len(fb - fa)} lost, {len(fa - fb)} new')
        print('C. fences: FAILED')
    else:
        print(f'C. fences: {sum(fb.values())} code blocks byte-identical')

    # D. links still resolve
    text = pathlib.Path(args.after).read_text(encoding='utf-8')
    root = pathlib.Path(args.vault)
    bad_img = [t for t in re.findall(r'!\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]', text)
               if not (root / t.strip()).exists()]
    heads = {m.group(1).strip() for m in re.finditer(r'^#{1,6} (.*)$', text, re.M)}
    bad_ref = [r for r in re.findall(r'(?<!!)\[\[#([^\]|]+?)(?:\|[^\]]*)?\]\]', text)
               if r.strip() not in heads]
    if bad_img or bad_ref:
        failures.append(f'D. broken image embeds: {bad_img[:5]} | broken heading links: {bad_ref[:5]}')
        print('D. links: FAILED')
    else:
        print('D. links: all image embeds and heading links resolve')

    if failures:
        print('\nVERIFICATION FAILED')
        for f in failures:
            print('  ' + f)
        return 1
    print('\nVERIFICATION PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
