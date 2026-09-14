#!/usr/bin/env python3
"""Split one Obsidian note into topic notes + an index note, from a JSON plan, with no-data-loss verification.

usage: split_note.py --vault VAULT_DIR --plan plan.json --out OUT_DIR

Never touches the vault. Writes:
  OUT_DIR/notes/<source folder>/<topic note>.md   one per plan entry
  OUT_DIR/notes/<source path>                     the source path, rewritten as the index note
  OUT_DIR/rewrites/<vault path>                   other vault notes whose heading links into the source moved
  OUT_DIR/report.json                             counts, warnings, and sha256 of every vault file read

plan.json:
{
  "source": "Folder/Big Note.md",                 vault-relative
  "date": "2026-01-31",                           recorded in frontmatter and the index intro
  "notes": [
    {"name": "Big Note Topic A", "description": "One sentence: what an agent will find here.",
     "tags": ["topic-a"],
     "segments": [[1, 40, 0], [212, 260, 1]]}     [first_line, last_line, heading_shift], 1-based inclusive
  ],
  "index": {"intro": "optional text", "h3_when_h2_fewer_than": 3}
}

Content is moved, never reworded. The only edits to original lines are:
  1. heading depth: a heading outside code fences loses `shift` leading `#` (result must stay >= H2);
  2. link targets: heading links whose heading moved to a different note get that note's name as target
     (same-note [[#H]], [[Source#H]], markdown (#h) / (Source.md#H)), inside the split notes and in other vault notes.
Added lines: YAML frontmatter, an H1 title and a one-line description quote per note, and the index note.
Trailing blank lines at the end of a topic note are dropped.

Verification (exit 1 on any failure):
  A. coverage   every original line is assigned to exactly one note (no gaps, no overlaps);
  B. undo       reading each written file back, undoing edits 1-2 reproduces the original line exactly;
  C. multiset   independently of the line mapping, the normalised non-blank lines of all outputs equal
                those of the original (nothing missing, nothing duplicated);
  D. links      every heading link written into the split notes, index and rewrites resolves to an existing heading.
"""
import argparse, collections, hashlib, json, pathlib, re, sys, urllib.parse

FENCE = re.compile(r"^\s*(```|~~~)")
HEAD = re.compile(r"^(#{1,6}) (.*)$")
WIKI = re.compile(r"(!?)\[\[([^\]|#]*)(#[^\]|]*)?(\|[^\]]*)?\]\]")
MDLINK = re.compile(r"\]\(([^)\s]+)\)")
BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]')
UNLINKABLE = re.compile(r"[\[\]|#^]")

errors, warnings = [], []
def err(m): errors.append(m)
def warn(m): warnings.append(m)


def read_lines(p):
    text = p.read_text(encoding="utf-8")
    lines = text.split("\n")
    if text.endswith("\n"):
        lines = lines[:-1]
    return text, lines


def fence_flags(lines):
    out, fence = [], False
    for l in lines:
        if FENCE.match(l):
            out.append(True); fence = not fence
        else:
            out.append(fence)
    return out


def slug(t):
    t = re.sub(r"[^\w\s-]", "", t.lower(), flags=re.UNICODE)
    return re.sub(r"\s+", "-", t.strip())


def display(t):
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = t.replace("**", "").replace("__", "").replace("`", "").replace("\\", "")
    return t.replace("|", "/").replace("[", "(").replace("]", ")").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    vault = pathlib.Path(a.vault)
    plan = json.loads(pathlib.Path(a.plan).read_text(encoding="utf-8"))
    out = pathlib.Path(a.out)
    src_rel = pathlib.PurePosixPath(plan["source"])
    stem, folder = src_rel.stem, src_rel.parent
    src_path = vault / src_rel
    src_text, lines = read_lines(src_path)
    N = len(lines)
    fenced = fence_flags(lines)
    notes = plan["notes"]
    date = plan.get("date", "")
    read_hashes = {str(src_rel): hashlib.sha256(src_text.encode()).hexdigest()}

    # ---- plan sanity -------------------------------------------------------------------------
    names = [n["name"] for n in notes]
    for n in names:
        if not n.strip() or BAD_NAME.search(n):
            err(f"invalid note name (empty or contains \\/:*?\"<>|#^[]): {n!r}")
        if n == stem:
            err(f"note name equals the source name (the source becomes the index): {n!r}")
        if (vault / folder / f"{n}.md").exists():
            err(f"a note with this name already exists in the vault: {folder / (n + '.md')}")
    for n, c in collections.Counter(names).items():
        if c > 1:
            err(f"duplicate note name in plan: {n!r}")

    # ---- A. coverage ---------------------------------------------------------------------------
    owner, shift_of = [None] * (N + 1), [0] * (N + 1)
    for n in notes:
        for seg in n["segments"]:
            first, last, shift = seg
            if not (1 <= first <= last <= N):
                err(f"{n['name']}: segment {seg} outside 1..{N}"); continue
            for i in range(first, last + 1):
                if owner[i] is not None:
                    err(f"line {i} assigned to both {owner[i]!r} and {n['name']!r}")
                owner[i], shift_of[i] = n["name"], shift
    gaps = [i for i in range(1, N + 1) if owner[i] is None]
    if gaps:
        err(f"{len(gaps)} unassigned lines, first: {gaps[:15]}")
    if errors:
        return finish(out, {"stage": "plan"})

    # ---- heading locations in the source (Obsidian resolves duplicates to the first) -----------
    first_home = {}
    slug_home = {}
    for i in range(1, N + 1):
        if fenced[i - 1]:
            continue
        m = HEAD.match(lines[i - 1])
        if m:
            t = m.group(2)
            if t in first_home:
                warn(f"duplicate heading text {t!r} (lines {first_home[t][1]} and {i}); links resolve to the first")
            else:
                first_home[t] = (owner[i], i)
                slug_home.setdefault(slug(t), (owner[i], i))
            lvl = len(m.group(1)) - shift_of[i]
            if lvl < 2:
                err(f"line {i}: heading would become level {lvl} (< 2) with shift {shift_of[i]}")

    def home_of(heading_path):
        last = heading_path.split("#")[-1]
        hit = first_home.get(last) or first_home.get(last.strip())
        return hit[0] if hit else None

    def is_source_target(target):
        t = target[:-3] if target.endswith(".md") else target
        return t == stem or t.endswith("/" + stem) or t == str(folder / stem)

    def new_target(target, dest):
        t = target[:-3] if target.endswith(".md") else target
        prefix = t[: len(t) - len(stem)]
        return prefix + dest + (".md" if target.endswith(".md") else "")

    def rewrite_line(l, here, in_source):
        """Return (new_line, [(old_span, new_span)]). here = note this line lands in (None for other notes)."""
        reps = []
        def wiki(m):
            bang, target, frag, alias = m.group(1), m.group(2), m.group(3), m.group(4) or ""
            if not frag or not (target == "" and in_source or is_source_target(target)):
                return m.group(0)
            dest = home_of(frag[1:])
            if dest is None:
                warn(f"link to unknown heading kept as is: {m.group(0)[:80]}"); return m.group(0)
            if dest == here:
                new = f"{bang}[[{frag}{alias}]]"
            else:
                new = f"{bang}[[{new_target(target, dest) if target else dest}{frag}{alias}]]"
            if new != m.group(0):
                reps.append((m.group(0), new))
            return new
        def md(m):
            url = m.group(1)
            if "#" not in url or re.match(r"^[a-z]+://", url):
                return m.group(0)
            path, frag = url.split("#", 1)
            dpath = urllib.parse.unquote(path)
            if path == "" and in_source:
                h = urllib.parse.unquote(frag)
                hit = first_home.get(h) or slug_home.get(h) or slug_home.get(slug(h))
                dest = hit[0] if hit else None
                if dest is None or dest == here:
                    return m.group(0)
                new = f"]({urllib.parse.quote(dest)}.md#{frag})"
            elif dpath.endswith(".md") and is_source_target(dpath):
                dest = home_of(urllib.parse.unquote(frag))
                if dest is None:
                    warn(f"markdown link to unknown heading kept as is: {m.group(0)[:80]}"); return m.group(0)
                prefix = path[: len(path) - len(urllib.parse.quote(stem) + ".md")] if path.endswith(urllib.parse.quote(stem) + ".md") \
                    else path[: len(path) - len(stem + ".md")]
                new = f"]({prefix}{urllib.parse.quote(dest)}.md#{frag})"
            else:
                return m.group(0)
            if new != m.group(0):
                reps.append((m.group(0), new))
            return new
        l2 = WIKI.sub(wiki, l)
        l2 = MDLINK.sub(md, l2)
        return l2, reps

    # ---- write topic notes ---------------------------------------------------------------------
    notes_dir = out / "notes" / folder
    notes_dir.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for n in notes:
        name, desc = n["name"], n["description"].strip()
        header = ["---", "tags:"] + [f"  - {t}" for t in n.get("tags", [])] + [
            f"description: {json.dumps(desc, ensure_ascii=False)}",
            f"split-from: {json.dumps(f'{src_rel} ({date})'.strip(), ensure_ascii=False)}",
            "---", f"# {name}", "", f"> {desc} Part of the [[{stem}]] index.", ""]
        body, idx = [], []
        for first, last, shift in n["segments"]:
            for i in range(first, last + 1):
                l = lines[i - 1]
                reps = []
                if not fenced[i - 1]:
                    m = HEAD.match(l)
                    if m and shift:
                        l = "#" * (len(m.group(1)) - shift) + " " + m.group(2)
                    if "[[" in l or "](" in l:
                        l, reps = rewrite_line(l, name, True)
                body.append(l); idx.append((i, shift, reps))
        while body and body[-1].strip() == "":
            body.pop(); idx.pop()
        (notes_dir / f"{name}.md").write_text("\n".join(header + body) + "\n", encoding="utf-8")
        mapping[name] = (len(header), idx)

    # ---- index note ------------------------------------------------------------------------------
    icfg = plan.get("index", {})
    h3_limit = icfg.get("h3_when_h2_fewer_than", 3)
    intro = icfg.get("intro") or (
        f"Index of the notes split by topic from `{src_rel.name}` on {date}. Related sections that were scattered "
        f"in the original are grouped together. Each entry lists the note's main sections.")
    ix = [f"# {stem}", "", intro, ""]
    for n in notes:
        name = n["name"]
        text = (notes_dir / f"{name}.md").read_text(encoding="utf-8").split("\n")
        heads, fence, h2 = [], False, None
        for l in text:
            if FENCE.match(l):
                fence = not fence; continue
            m = None if fence else HEAD.match(l)
            if m and len(m.group(1)) in (2, 3):
                if len(m.group(1)) == 2:
                    h2 = m.group(2)
                heads.append((len(m.group(1)), m.group(2), h2))
        count = collections.Counter(t for _, t, _ in heads)
        n_h2 = sum(1 for lv, _, _ in heads if lv == 2)
        ix += [f"## [[{name}]]", "", n["description"].strip(), ""]
        for lv, t, parent in heads:
            if lv == 3 and n_h2 >= h3_limit:
                continue
            indent = "" if lv == 2 else "  "
            if UNLINKABLE.search(t) or (count[t] > 1 and (lv == 2 or parent is None or UNLINKABLE.search(parent or ""))):
                ix.append(f"{indent}- {display(t)}")
            elif count[t] > 1:
                ix.append(f"{indent}- [[{name}#{parent}#{t}|{display(t)}]]")
            else:
                ix.append(f"{indent}- [[{name}#{t}|{display(t)}]]")
        ix.append("")
    (notes_dir / f"{stem}.md").write_text("\n".join(ix).rstrip("\n") + "\n", encoding="utf-8")

    # ---- rewrite inbound heading links in other vault notes ----------------------------------------
    rewrites = {}
    for p in sorted(vault.rglob("*.md")):
        rel = p.relative_to(vault)
        if rel.as_posix() == src_rel.as_posix() or any(part.startswith(".") for part in rel.parts):
            continue
        try:
            text, olines = read_lines(p)
        except (UnicodeDecodeError, OSError):
            continue
        if stem not in text and urllib.parse.quote(stem) not in text:
            continue
        flags, changed, per_line = fence_flags(olines), False, []
        new_lines = []
        for k, l in enumerate(olines):
            reps = []
            if not flags[k] and ("[[" in l or "](" in l):
                l2, reps = rewrite_line(l, None, False)
            else:
                l2 = l
            changed |= bool(reps)
            new_lines.append(l2); per_line.append(reps)
        if changed:
            dst = out / "rewrites" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text("\n".join(new_lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")
            rewrites[rel.as_posix()] = per_line
            read_hashes[rel.as_posix()] = hashlib.sha256(text.encode()).hexdigest()

    # ---- B. undo check (from disk) -----------------------------------------------------------------
    def undo(l, reps):
        for old, new in reversed(reps):
            k = l.rfind(new)
            if k < 0:
                return None
            l = l[:k] + old + l[k + len(new):]
        return l
    seen = collections.Counter()
    for name, (hdr, idx) in mapping.items():
        got = (notes_dir / f"{name}.md").read_text(encoding="utf-8").split("\n")[:-1]
        body = got[hdr:]
        if len(body) != len(idx):
            err(f"{name}: {len(body)} body lines on disk, {len(idx)} expected"); continue
        for (i, shift, reps), g in zip(idx, body):
            seen[i] += 1
            u = undo(g, reps)
            m = None if fenced[i - 1] or u is None else HEAD.match(u)
            if m and shift:
                u = "#" * (len(m.group(1)) + shift) + " " + m.group(2)
            if u != lines[i - 1]:
                err(f"{name}: original line {i} changed beyond the allowed edits:\n    was: {lines[i-1][:100]}\n    now: {g[:100]}")
    for i in range(1, N + 1):
        if seen[i] > 1:
            err(f"original line {i} written {seen[i]} times")
        if seen[i] == 0 and lines[i - 1].strip():
            err(f"original non-blank line {i} missing: {lines[i-1][:80]}")
    for rel, per_line in rewrites.items():
        _, olines = read_lines(vault / rel)
        _, nlines = read_lines(out / "rewrites" / rel)
        if len(olines) != len(nlines):
            err(f"rewrite {rel}: line count changed"); continue
        for k, (o, nl, reps) in enumerate(zip(olines, nlines, per_line), 1):
            if undo(nl, reps) != o:
                err(f"rewrite {rel}:{k} changed beyond link targets")

    # ---- C. independent multiset check -----------------------------------------------------------------
    def norm_lines(ls):
        res, fence = [], False
        for l in ls:
            if FENCE.match(l):
                fence = not fence; res.append(l); continue
            if not fence:
                l = re.sub(r"^#{1,6} ", "# ", l)
                l = re.sub(r"(!?)\[\[[^\]|#]*#", r"\1[[#", l)
                l = re.sub(r"\]\([^)\s#]*\.md#", "](#", l)
            if l.strip():
                res.append(l)
        return collections.Counter(res)
    before = norm_lines(lines)
    after = collections.Counter()
    for name, (hdr, _) in mapping.items():
        after += norm_lines((notes_dir / f"{name}.md").read_text(encoding="utf-8").split("\n")[:-1][hdr:])
    missing, extra = before - after, after - before
    for l, c in list(missing.items())[:10]:
        err(f"multiset: missing {c}x: {l[:90]}")
    for l, c in list(extra.items())[:10]:
        err(f"multiset: extra {c}x: {l[:90]}")

    # ---- D. link resolution inside outputs ------------------------------------------------------------
    heads_of = {}
    for name in names + [stem]:
        fence, hs = False, []
        for l in (notes_dir / f"{name}.md").read_text(encoding="utf-8").split("\n"):
            if FENCE.match(l):
                fence = not fence; continue
            m = None if fence else HEAD.match(l)
            if m:
                hs.append((len(m.group(1)), m.group(2)))
        heads_of[name] = hs
    def resolves(note, path):
        parts = path.split("#")
        hs = heads_of.get(note)
        if hs is None:
            return True                                           # a note outside this split
        texts = [t for _, t in hs]
        if len(parts) == 1:
            return parts[0] in texts
        idx = [k for k, (_, t) in enumerate(hs) if t == parts[0]]
        for k in idx:                                             # nested: parent#child under that parent
            lvl = hs[k][0]
            for lv2, t2 in hs[k + 1:]:
                if lv2 <= lvl:
                    break
                if t2 == parts[-1]:
                    return True
        return False
    checked = 0
    outputs = [(n, notes_dir / f"{n}.md") for n in names + [stem]] + [(None, out / "rewrites" / r) for r in rewrites]
    for here, p in outputs:
        fence = False
        for l in p.read_text(encoding="utf-8").split("\n"):
            if FENCE.match(l):
                fence = not fence; continue
            if fence:
                continue
            for m in WIKI.finditer(l):
                target, frag = m.group(2), m.group(3)
                if not frag:
                    continue
                note = (target[:-3] if target.endswith(".md") else target).split("/")[-1] or here
                if note in heads_of:
                    checked += 1
                    if not resolves(note, frag[1:]):
                        err(f"{p.name}: broken heading link {m.group(0)[:100]}")
            for m in MDLINK.finditer(l):
                url = m.group(1)
                if "#" in url and not re.match(r"^[a-z]+://", url):
                    path, frag = url.split("#", 1)
                    note = urllib.parse.unquote(path)[:-3].split("/")[-1] if path.endswith(".md") else here
                    if note in heads_of and path:
                        checked += 1
                        if not resolves(note, urllib.parse.unquote(frag)):
                            err(f"{p.name}: broken markdown heading link {m.group(0)[:100]}")

    return finish(out, {
        "stage": "done", "source": str(src_rel), "source_lines": N, "notes": names,
        "note_bytes": {n: (notes_dir / f"{n}.md").stat().st_size for n in names + [stem]},
        "lines_placed": sum(seen.values()), "blank_lines_dropped": N - sum(seen.values()),
        "rewrites": list(rewrites), "heading_links_checked": checked, "read_sha256": read_hashes})


def finish(out, report):
    report["errors"], report["warnings"] = errors, warnings
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for w in warnings[:20]:
        print("warning:", w)
    if report.get("stage") == "done":
        print(f"source {report['source']}: {report['source_lines']} lines -> {len(report['notes'])} notes + index")
        for n, b in report["note_bytes"].items():
            print(f"  {b:8d} bytes  {n}.md")
        print(f"lines placed {report['lines_placed']}, blank lines dropped at note ends {report['blank_lines_dropped']}, "
              f"other notes rewritten {len(report['rewrites'])}, heading links checked {report['heading_links_checked']}")
    if errors:
        print(f"\nVERIFICATION FAILED ({len(errors)} errors):")
        for e in errors[:40]:
            print(" -", e)
        return 1
    print("VERIFICATION PASSED: coverage, undo, multiset and link checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
