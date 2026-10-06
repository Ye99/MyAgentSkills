#!/usr/bin/env python3
"""Independently verify an rsync filtered copy and write an inventory report.

Walks SRC without following links, re-applies the "- pattern" rules from the
rsync filter file (it does not trust rsync), and checks that every file the
rules keep exists in DST with identical size and mtime. Also checks that DST
holds no extra files and no empty directories.

Writes into DST:
  _COPY_REPORT.md                 summary, rules, skipped trees, verdict
  _COPY_REPORT_skipped_files.tsv  every skipped file: path, size, reason

Each rule is labelled with the nearest preceding "# ---- Category ----"
comment in the filter file, so the report says why a thing was skipped.

Exit status: 0 = PASS, 1 = FAIL.
"""
import argparse
import collections
import datetime
import os
import re
import sys

REPORT = "_COPY_REPORT.md"
SKIPPED_TSV = "_COPY_REPORT_skipped_files.tsv"
DEFAULT_IGNORE = ("_COPY_REPORT*", "agentreviewguide.md")
# Skips that never mean lost content: a folder losing only these is not reported.
NOISE = re.compile(r"^(desktop\.ini|thumbs\.db|ehthumbs.*\.db)$", re.I)


def glob_to_regex(glob: str) -> str:
    """rsync-style glob: '*' and '?' never cross '/', '**' does, '[..]' is a class.

    As in rsync (unlike git), '/**/' needs at least one directory: 'a/**/b'
    does not match 'a/b'. Write both 'a/b' and 'a/**/b' to cover every depth.
    """
    out, i = "", 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**", i):
            out += ".*"
            i += 1
        elif c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        elif c == "[":
            j = glob.index("]", i)
            out += glob[i:j + 1]
            i = j
        else:
            out += re.escape(c)
        i += 1
    return out


def readable(rule: str) -> str:
    """'*.[Ee][Xx][Ee]' -> '*.exe' for display."""
    return re.sub(r"\[(.)(.)\]", lambda m: m.group(2).lower(), rule)


class Rules:
    """Exclude rules parsed from an rsync merge-filter file."""

    def __init__(self, lines):
        self.text = []                # (category, pattern) in file order
        self.category = {}
        self.dirs, self.paths, self.names = [], [], []
        cat = "Uncategorised"
        for line in lines:
            line = line.rstrip("\n")
            m = re.match(r"^#\s*-{2,}\s*(.*?)\s*-{2,}\s*$", line)
            if m:
                cat = m.group(1)
                continue
            if not line.startswith("- "):
                continue
            pat = line[2:]
            self.text.append((cat, pat))
            self.category[pat] = cat
            is_dir = pat.endswith("/")
            body = pat[:-1] if is_dir else pat
            if body.startswith("/"):
                rx = re.compile("^" + glob_to_regex(body[1:]) + "$")      # anchored at root
            elif "/" in body or "**" in body:
                rx = re.compile("(^|/)" + glob_to_regex(body) + "$")      # rsync: matches a path tail
            else:
                rx = None                                                 # plain basename pattern
            if is_dir:
                self.dirs.append((pat, rx or re.compile("(^|/)" + glob_to_regex(body) + "$")))
            elif rx:
                self.paths.append((pat, rx))
            else:
                self.names.append((pat, re.compile("^" + glob_to_regex(body) + "$")))

    @classmethod
    def from_file(cls, path):
        with open(path, encoding="utf-8") as fh:
            return cls(fh)

    def dir_rule(self, rel_dir):
        return next((r for r, c in self.dirs if c.search(rel_dir)), None)

    def file_rule(self, rel_file):
        name = rel_file.rsplit("/", 1)[-1]
        return next((r for r, c in self.paths if c.search(rel_file)), None) \
            or next((r for r, c in self.names if c.match(name)), None)


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def ignored_in_dst(name, patterns):
    return any(re.match("^" + glob_to_regex(p) + "$", name) for p in patterns)


def inventory(src, dst, rules, ignore=DEFAULT_IGNORE):
    r = collections.defaultdict(list)
    r["skipped_dirs"] = collections.OrderedDict()
    src_n = src_b = copied_n = copied_b = 0

    for root, dirs, files in os.walk(src, followlinks=False):
        rel_root = os.path.relpath(root, src)
        rel_root = "" if rel_root == "." else rel_root.replace(os.sep, "/")
        keep = []
        for d in sorted(dirs):
            rel = f"{rel_root}/{d}" if rel_root else d
            full = os.path.join(root, d)
            if os.path.islink(full):
                r["links"].append((rel + "/", os.readlink(full)))
                continue
            hit = rules.dir_rule(rel)
            if not hit:
                keep.append(d)
                continue
            n = b = 0
            for r2, d2, f2 in os.walk(full, followlinks=False):
                for e in d2 + f2:
                    p = os.path.join(r2, e)
                    if os.path.islink(p):
                        r["links_excluded"].append((os.path.relpath(p, src).replace(os.sep, "/"), os.readlink(p)))
                for f in f2:
                    p = os.path.join(r2, f)
                    if os.path.islink(p):
                        continue
                    try:
                        s = os.lstat(p).st_size
                    except OSError:
                        s = 0
                    n += 1
                    b += s
                    r["skipped"].append((os.path.relpath(p, src).replace(os.sep, "/"), s, f"dir {hit}"))
            r["skipped_dirs"][rel + "/"] = (n, b, hit)
            src_n += n
            src_b += b
        dirs[:] = keep
        for f in sorted(files):
            rel = f"{rel_root}/{f}" if rel_root else f
            full = os.path.join(root, f)
            if os.path.islink(full):
                r["links"].append((rel, os.readlink(full)))
                continue
            st = os.lstat(full)
            src_n += 1
            src_b += st.st_size
            hit = rules.file_rule(rel)
            if hit:
                r["skipped"].append((rel, st.st_size, f"file {hit}"))
                continue
            try:
                dst_st = os.stat(os.path.join(dst, rel))
            except OSError:
                r["problems"].append((rel, st.st_size, "MISSING in destination"))
                continue
            if dst_st.st_size != st.st_size:
                r["problems"].append((rel, st.st_size, f"SIZE differs (dest {dst_st.st_size})"))
            elif int(dst_st.st_mtime) != int(st.st_mtime):
                r["problems"].append((rel, st.st_size, "MTIME differs"))
            copied_n += 1
            copied_b += st.st_size

    dst_n = dst_b = 0
    for root, dirs, files in os.walk(dst):
        rel_root = os.path.relpath(root, dst)
        top = rel_root == "."
        files = [f for f in files if not (top and ignored_in_dst(f, ignore))]
        if not dirs and not files and not top:
            r["empty_dst"].append(rel_root)
        for f in files:
            p = os.path.join(root, f)
            if os.path.islink(p):
                r["dst_links"].append(os.path.relpath(p, dst))
                continue
            dst_n += 1
            dst_b += os.lstat(p).st_size

    r.update(src_n=src_n, src_b=src_b, copied_n=copied_n, copied_b=copied_b, dst_n=dst_n, dst_b=dst_b)
    r["emptied_dirs"] = emptied_dirs(dst, r["skipped"])
    return r


def emptied_dirs(dst, skipped):
    """Outermost source folders that exist only because of files the type rules
    skipped (so they are absent from DST), ignoring desktop.ini/Thumbs.db-only ones."""
    per_dir = collections.defaultdict(list)
    for rel, s, why in skipped:
        if why.startswith("file") and "/" in rel:
            per_dir[rel.rsplit("/", 1)[0]].append((rel, s))
    lost = {d: f for d, f in per_dir.items()
            if not os.path.isdir(os.path.join(dst, d))
            and any(not NOISE.match(p.rsplit("/", 1)[-1]) for p, _ in f)}
    roots = collections.defaultdict(list)            # climb to the outermost absent ancestor
    for d, files in lost.items():
        while "/" in d and not os.path.isdir(os.path.join(dst, d.rsplit("/", 1)[0])):
            d = d.rsplit("/", 1)[0]
        roots[d].extend(files)
    return sorted(roots.items())


def verdict(r, checksum_diffs):
    return not (r["problems"] or r["empty_dst"] or r["dst_links"] or checksum_diffs) \
        and r["copied_n"] == r["dst_n"] and r["copied_b"] == r["dst_b"]


def read_lines(path, keep):
    if not path:
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        return [l.rstrip("\n") for l in fh if keep(l)]


def write_report(out_dir, src, dst, filter_path, rules, r, checksum_diffs, rsync_errors, notes):
    ok = verdict(r, checksum_diffs or [])
    skipped = r["skipped"]
    cat = lambda why: rules.category.get(why.split(" ", 1)[1], "") if " " in why else ""
    pattern = lambda why: why.split(" ", 1)[1]
    with open(os.path.join(out_dir, SKIPPED_TSV), "w", encoding="utf-8") as fh:
        fh.write("relative_path\tsize_bytes\treason\tcategory\n")
        for rel, s, why in skipped:
            fh.write(f"{rel}\t{s}\t{readable(why)}\t{cat(why)}\n")

    by_rule = collections.defaultdict(lambda: [0, 0])
    by_top = collections.defaultdict(lambda: [0, 0])
    for rel, s, why in skipped:
        if why.startswith("file"):
            by_rule[pattern(why)][0] += 1
            by_rule[pattern(why)][1] += s
            top = rel.split("/")[0] if "/" in rel else "(drive root)"
            by_top[top][0] += 1
            by_top[top][1] += s

    L = []
    w = L.append
    w(f"# Copy report: {src} → {dst}\n")
    w(f"- Generated: {datetime.datetime.now().isoformat(timespec='seconds')}")
    w(f"- **Verification result: {'PASS' if ok else 'FAIL — see Problems'}**\n")
    w("## Summary\n")
    w("| | Files | Size |\n|---|---:|---:|")
    w(f"| Regular files on source (links not followed) | {r['src_n']:,} | {human(r['src_b'])} ({r['src_b']:,} B) |")
    w(f"| Expected in destination per rules | {r['copied_n']:,} | {human(r['copied_b'])} ({r['copied_b']:,} B) |")
    w(f"| Present in destination (report files excluded) | {r['dst_n']:,} | {human(r['dst_b'])} ({r['dst_b']:,} B) |")
    w(f"| Skipped by rules | {len(skipped):,} | {human(sum(s for _, s, _ in skipped))} |")
    w(f"| Symlinks / junctions skipped (outside excluded trees) | {len(r['links']):,} | – |")
    w(f"| Symlinks / junctions inside excluded trees | {len(r['links_excluded']):,} | – |")
    w(f"| Folders absent from destination because type rules took all their content | {len(r['emptied_dirs']):,} | – |\n")
    w("Sizes are binary units (1 GiB = 1024³ B); rsync `--stats -h` prints decimal for the same bytes.\n")
    w("## Exclusion rules (rsync filter)\n")
    w(f"From `{filter_path}`. `/x` is anchored at the source root; a pattern with an inner `/` matches a path tail; "
      "`**` crosses `/`; `[Ee][Xx][Ee]` is case-insensitive.\n")
    w("| Category | Pattern |\n|---|---|")
    for c, t in rules.text:
        w(f"| {c} | `{t}` |")
    w("\n## Skipped directories (whole trees)\n")
    w("| Directory | Files | Size | Rule | Category |\n|---|---:|---:|---|---|")
    for d, (n, b, rule) in r["skipped_dirs"].items():
        w(f"| `{d}` | {n:,} | {human(b)} | `{readable(rule)}` | {rules.category.get(rule, '')} |")
    w("\n## Skipped files by rule\n")
    w("| Rule | Category | Files | Size |\n|---|---|---:|---:|")
    for rule, (n, b) in sorted(by_rule.items(), key=lambda x: -x[1][1]):
        w(f"| `{readable(rule)}` | {rules.category.get(rule, '')} | {n:,} | {human(b)} |")
    w("\n### Same, grouped by top-level folder\n")
    w("| Top folder | Files | Size |\n|---|---:|---:|")
    for top, (n, b) in sorted(by_top.items(), key=lambda x: -x[1][1]):
        w(f"| `{top}` | {n:,} | {human(b)} |")
    w("\n## Folders that lost all their content to file-type rules\n")
    w("Outermost folders that are absent from the destination because every file in them (and below) was skipped "
      "by a file rule. Folders that held only `desktop.ini`/`Thumbs.db` are left out. "
      "Review these first: a self-contained program or download lives here.\n")
    if r["emptied_dirs"]:
        w("| Folder | Files | Size | Examples |\n|---|---:|---:|---|")
        for d, files in r["emptied_dirs"]:
            ex = ", ".join(f"`{p.rsplit('/', 1)[-1]}`" for p, _ in files if not NOISE.match(p.rsplit("/", 1)[-1]))
            ex = ex if len(ex) < 160 else ex[:157] + "…"
            w(f"| `{d}/` | {len(files):,} | {human(sum(s for _, s in files))} | {ex} |")
    else:
        w("None.")
    w("\n## Skipped symlinks / junctions\n")
    w("| Link | Target |\n|---|---|")
    for rel, tgt in r["links"]:
        w(f"| `{rel}` | `{tgt}` |")
    if r["links_excluded"]:
        w("\nInside excluded trees (never visited by rsync):\n")
        w("| Link | Target |\n|---|---|")
        for rel, tgt in r["links_excluded"]:
            w(f"| `{rel}` | `{tgt}` |")
    w("\n## Problems\n")
    if r["problems"]:
        w("| File | Size | Problem |\n|---|---:|---|")
        for rel, s, p in r["problems"]:
            w(f"| `{rel}` | {s:,} | {p} |")
    else:
        w("- Size/mtime check: every expected file is present with identical size and mtime.")
    w(f"- Count/bytes match: {'yes' if (r['copied_n'], r['copied_b']) == (r['dst_n'], r['dst_b']) else 'NO'}")
    w(f"- Empty directories in destination: {len(r['empty_dst'])}" + "".join(f"\n  - `{e}`" for e in r["empty_dst"]))
    w(f"- Symlinks in destination: {len(r['dst_links'])}")
    if checksum_diffs is None:
        w("- Content checksum pass: not supplied.")
    elif checksum_diffs:
        w("- Content checksum pass reported differences:\n```\n" + "\n".join(checksum_diffs[:500]) + "\n```")
    else:
        w("- Content checksum pass (`rsync -rcn --delete`): no differences, no extra files.")
    if r.get("checksum_dirs"):
        w("- Folders the checksum pass would create (empty after pruning; junction-only on the source, not a difference):"
          + "".join(f"\n  - `{l.split(' ', 1)[1]}`" for l in r["checksum_dirs"]))
    if rsync_errors is None:
        w("- rsync copy output: not supplied.")
    elif rsync_errors:
        w("- rsync copy output has error lines:\n```\n" + "\n".join(rsync_errors[:200]) + "\n```")
    else:
        w("- rsync copy output: no error lines.")
    w(f"\n## Files\n\n- `{REPORT}` — this report.\n- `{SKIPPED_TSV}` — {len(skipped):,} rows: "
      "`relative_path<TAB>size_bytes<TAB>reason<TAB>category`.")
    if notes:
        w("\n## Notes\n")
        with open(notes, encoding="utf-8") as fh:
            w(fh.read().rstrip())
    with open(os.path.join(out_dir, REPORT), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--filter", required=True, help="rsync merge-filter file used for the copy")
    ap.add_argument("--checksum-output", help="output of: rsync -rcn -i --delete ... (empty file = no differences)")
    ap.add_argument("--rsync-output", help="console output of the copy run")
    ap.add_argument("--notes", help="Markdown file appended as a Notes section (decisions, reasons)")
    ap.add_argument("--output-dir", help="where to write the report and TSV (default: DST). "
                    "Reviewers: point this elsewhere to leave the original report untouched")
    ap.add_argument("--ignore", action="append", default=list(DEFAULT_IGNORE),
                    help="glob of DST top-level files that are not copied data (repeatable)")
    a = ap.parse_args(argv)

    out_dir = a.output_dir or a.dst
    os.makedirs(out_dir, exist_ok=True)
    rules = Rules.from_file(a.filter)
    r = inventory(a.src, a.dst, rules, a.ignore)
    # File transfers and deletions are real differences. "cd+++" only means rsync -m
    # would recreate a folder whose sole entries were skipped junctions; the
    # empty-directory and file checks above already cover folders.
    checksum_diffs = read_lines(a.checksum_output, lambda l: l.startswith((">f", "<f", "*deleting")))
    r["checksum_dirs"] = read_lines(a.checksum_output, lambda l: l.startswith("cd")) or []
    rsync_errors = read_lines(a.rsync_output, lambda l: l.startswith("rsync") or "(code " in l)
    ok = write_report(out_dir, a.src, a.dst, a.filter, rules, r, checksum_diffs, rsync_errors, a.notes)
    print(f"{'PASS' if ok else 'FAIL'} expected={r['copied_n']} files/{r['copied_b']} B "
          f"dest={r['dst_n']} files/{r['dst_b']} B problems={len(r['problems'])} "
          f"empty_dirs={len(r['empty_dst'])} skipped={len(r['skipped'])} links={len(r['links'])}"
          f"+{len(r['links_excluded'])} emptied_dirs={len(r['emptied_dirs'])}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
