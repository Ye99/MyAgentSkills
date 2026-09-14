#!/usr/bin/env python3
"""Inventory a large Obsidian note before planning a split.

usage:
  outline.py NOTE.md [--vault DIR]            headings, sizes, links, inbound references, secret-like strings
  outline.py NOTE.md --skim [--from N --to M] first line of every paragraph (to see what a long section really holds)

Headings rarely describe everything under them in notes that grew over time, so read the skim
output for any section longer than ~60 lines before deciding where it belongs.
"""
import argparse, collections, pathlib, re, sys, urllib.parse

FENCE = re.compile(r"^\s*(```|~~~)")
HEAD = re.compile(r"^(#{1,6}) (.*)$")
WIKI = re.compile(r"(!?)\[\[([^\]|]*?)(#[^\]|]*)?(\|[^\]]*)?\]\]")
MDLINK = re.compile(r"\]\(([^)\s]*)\)")
SECRETS = [
    ("telegram-bot-token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password-or-key-value", re.compile(r"(?i)\b(password|passwd|passphrase|api[_-]?key|secret|token)\b[^:=\n]{0,40}[:=]\s*\S{6,}")),
]


def fence_map(lines):
    out, fence = [], False
    for l in lines:
        if FENCE.match(l):
            out.append(True); fence = not fence; continue
        out.append(fence)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("note")
    ap.add_argument("--vault", help="vault root, to find inbound references (default: parent of the note)")
    ap.add_argument("--skim", action="store_true")
    ap.add_argument("--from", dest="start", type=int, default=1)
    ap.add_argument("--to", dest="end", type=int, default=10**9)
    a = ap.parse_args()
    note = pathlib.Path(a.note)
    lines = note.read_text(encoding="utf-8").split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    fenced = fence_map(lines)

    if a.skim:
        prev = ""
        for i, l in enumerate(lines, 1):
            if a.start <= i <= a.end and l.strip() and not prev.strip():
                s = MDLINK.sub("]", WIKI.sub("[[…]]", l))
                print(f"{i}: {s[:90]}")
            prev = l
        return

    print(f"{note}: {len(lines)} lines, {note.stat().st_size} bytes, code-fence lines: {sum(1 for l in lines if FENCE.match(l))}")
    heads = [(i, len(m.group(1)), m.group(2)) for i, l in enumerate(lines, 1)
             if not fenced[i - 1] and (m := HEAD.match(l))]
    print("\n== headings (line: level text  [lines until next heading])")
    for k, (i, lvl, t) in enumerate(heads):
        nxt = heads[k + 1][0] if k + 1 < len(heads) else len(lines) + 1
        print(f"{i:6}: {'#' * lvl} {t[:100]}  [{nxt - i}]")
    if heads and heads[0][0] > 1:
        print(f"(lines 1-{heads[0][0] - 1} come before the first heading)")

    dup = {t: [i for i, _, tt in heads if tt == t] for t, c in collections.Counter(t for _, _, t in heads).items() if c > 1}
    print("\n== duplicate heading texts (Obsidian links resolve to the FIRST one)")
    for t, where in dup.items():
        print(f"  {where}  {t[:90]}")
    if not dup:
        print("  none")

    print("\n== links in the note (outside code): same-note heading links / other notes / assets")
    same, other, assets = [], collections.Counter(), collections.Counter()
    for i, l in enumerate(lines, 1):
        if fenced[i - 1]:
            continue
        for m in WIKI.finditer(l):
            target, frag = m.group(2), m.group(3)
            if not target and frag:
                same.append((i, frag[1:]))
            elif re.search(r"\.(png|jpe?g|gif|svg|webp|pdf|mp4|heic)$", target, re.I):
                assets[target.split("/")[0]] += 1
            elif target:
                other[target + (frag or "")] += 1
        for m in MDLINK.finditer(l):
            if m.group(1).startswith("#"):
                same.append((i, urllib.parse.unquote(m.group(1)[1:])))
    print(f"  same-note heading links: {len(same)}")
    for i, f in same[:30]:
        print(f"    L{i}: #{f[:90]}")
    print(f"  links to other notes: {sum(other.values())} ({len(other)} distinct)")
    for t, c in other.most_common(15):
        print(f"    {c}x {t[:90]}")
    print(f"  asset links by folder: {dict(assets)}  (keep asset folder names unchanged when splitting)")

    vault = pathlib.Path(a.vault) if a.vault else note.parent
    stem = note.stem
    print(f"\n== inbound references to '{stem}' from other notes in {vault}")
    pat_wiki = re.compile(r"\[\[(?:[^\]|#]*/)?" + re.escape(stem) + r"(?:\.md)?(#[^\]|]*)?(\|[^\]]*)?\]\]")
    pat_md = re.compile(r"\]\((?:[^)\s]*/)?" + re.escape(urllib.parse.quote(stem)) + r"\.md(#[^)]*)?\)|\]\((?:[^)\s]*/)?"
                        + re.escape(stem) + r"\.md(#[^)]*)?\)")
    found = 0
    for p in sorted(vault.rglob("*.md")):
        if p.resolve() == note.resolve() or any(part.startswith(".") for part in p.relative_to(vault).parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if stem not in text and urllib.parse.quote(stem) not in text:
            continue
        for n, l in enumerate(text.split("\n"), 1):
            for m in list(pat_wiki.finditer(l)) + list(pat_md.finditer(l)):
                found += 1
                kind = "HEADING LINK (must be rewritten)" if "#" in m.group(0) else "note link (index keeps it valid)"
                print(f"  {p.relative_to(vault)}:{n}: {m.group(0)[:100]}  <- {kind}")
    if not found:
        print("  none")

    print("\n== secret-like strings (informational only: a split moves text verbatim and never redacts or edits it;"
          "\n   just avoid echoing the values into commit messages, skills or chat)")
    hits = 0
    for i, l in enumerate(lines, 1):
        for name, rx in SECRETS:
            if rx.search(l):
                hits += 1
                print(f"  L{i}: {name}")
    if not hits:
        print("  none")


if __name__ == "__main__":
    sys.exit(main())
