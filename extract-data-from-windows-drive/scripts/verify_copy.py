#!/usr/bin/env python3
"""Independently verify an rsync filtered copy and write an inventory report.

Walks SRC without following links, re-applies the "- pattern" rules from the
rsync filter file (it does not trust rsync), and checks that every file the
rules keep exists in DST with identical size and mtime. Also checks that DST
holds no extra files and no empty directories.

Writes into DST:
  _COPY_REPORT.md                 summary, rules, skipped trees, verdict
  _COPY_REPORT_skipped_files.tsv  every skipped file: path, size, reason

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


def glob_to_regex(glob: str) -> str:
    """rsync-style glob: '*' and '?' never cross '/', '[..]' kept as a class."""
    out, i = "", 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
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
        self.text = []
        self.dirs, self.paths, self.names = [], [], []
        for line in lines:
            line = line.rstrip("\n")
            if not line.startswith("- "):
                continue
            pat = line[2:]
            self.text.append(pat)
            if pat.startswith("/") and pat.endswith("/"):
                self.dirs.append((pat, re.compile("^" + glob_to_regex(pat[1:-1]) + "$")))
            elif pat.startswith("/"):
                self.paths.append((pat, re.compile("^" + glob_to_regex(pat[1:]) + "$")))
            elif pat.endswith("/"):
                self.dirs.append((pat, re.compile("(^|/)" + glob_to_regex(pat[:-1]) + "$")))
            else:
                self.names.append((pat, re.compile("^" + glob_to_regex(pat) + "$")))

    @classmethod
    def from_file(cls, path):
        with open(path, encoding="utf-8") as fh:
            return cls(fh)

    def dir_rule(self, rel_dir):
        return next((r for r, c in self.dirs if c.search(rel_dir)), None)

    def file_rule(self, rel_file):
        name = rel_file.rsplit("/", 1)[-1]
        return next((r for r, c in self.paths if c.match(rel_file)), None) \
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
            for r2, _, f2 in os.walk(full, followlinks=False):
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
                    r["skipped"].append((os.path.relpath(p, src).replace(os.sep, "/"), s, f"dir {readable(hit)}"))
            r["skipped_dirs"][rel + "/"] = (n, b, readable(hit))
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
                r["skipped"].append((rel, st.st_size, f"file {readable(hit)}"))
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
    return r


def verdict(r, checksum_diffs):
    return not (r["problems"] or r["empty_dst"] or r["dst_links"] or checksum_diffs) \
        and r["copied_n"] == r["dst_n"] and r["copied_b"] == r["dst_b"]


def read_lines(path, keep):
    if not path:
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        return [l.rstrip("\n") for l in fh if keep(l)]


def write_report(src, dst, filter_path, rules, r, checksum_diffs, rsync_errors, notes):
    ok = verdict(r, checksum_diffs or [])
    skipped = r["skipped"]
    with open(os.path.join(dst, SKIPPED_TSV), "w", encoding="utf-8") as fh:
        fh.write("relative_path\tsize_bytes\treason\n")
        for rel, s, why in skipped:
            fh.write(f"{rel}\t{s}\t{why}\n")

    by_rule = collections.defaultdict(lambda: [0, 0])
    by_top = collections.defaultdict(lambda: [0, 0])
    for rel, s, why in skipped:
        if why.startswith("file"):
            by_rule[why[5:]][0] += 1
            by_rule[why[5:]][1] += s
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
    w(f"| Symlinks / junctions skipped | {len(r['links']):,} | – |\n")
    w("Sizes are binary units (1 GiB = 1024³ B); rsync `--stats -h` prints decimal for the same bytes.\n")
    w("## Exclusion rules (rsync filter)\n")
    w(f"From `{filter_path}`. `/x` is anchored at the source root; `[Ee][Xx][Ee]` is case-insensitive.\n")
    w("```")
    w("\n".join("- " + t for t in rules.text))
    w("```\n")
    w("## Skipped directories (whole trees)\n")
    w("| Directory | Files | Size | Rule |\n|---|---:|---:|---|")
    for d, (n, b, rule) in r["skipped_dirs"].items():
        w(f"| `{d}` | {n:,} | {human(b)} | `{rule}` |")
    w("\n## Skipped files by rule\n")
    w("| Rule | Files | Size |\n|---|---:|---:|")
    for rule, (n, b) in sorted(by_rule.items(), key=lambda x: -x[1][1]):
        w(f"| `{rule}` | {n:,} | {human(b)} |")
    w("\n### Same, grouped by top-level folder\n")
    w("| Top folder | Files | Size |\n|---|---:|---:|")
    for top, (n, b) in sorted(by_top.items(), key=lambda x: -x[1][1]):
        w(f"| `{top}` | {n:,} | {human(b)} |")
    w("\n## Skipped symlinks / junctions\n")
    w("| Link | Target |\n|---|---|")
    for rel, tgt in r["links"]:
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
    if rsync_errors is None:
        w("- rsync copy output: not supplied.")
    elif rsync_errors:
        w("- rsync copy output has error lines:\n```\n" + "\n".join(rsync_errors[:200]) + "\n```")
    else:
        w("- rsync copy output: no error lines.")
    w(f"\n## Files\n\n- `{REPORT}` — this report.\n- `{SKIPPED_TSV}` — {len(skipped):,} rows: `relative_path<TAB>size_bytes<TAB>reason`.")
    if notes:
        w("\n## Notes\n")
        with open(notes, encoding="utf-8") as fh:
            w(fh.read().rstrip())
    with open(os.path.join(dst, REPORT), "w", encoding="utf-8") as fh:
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
    ap.add_argument("--ignore", action="append", default=list(DEFAULT_IGNORE),
                    help="glob of DST top-level files that are not copied data (repeatable)")
    a = ap.parse_args(argv)

    rules = Rules.from_file(a.filter)
    r = inventory(a.src, a.dst, rules, a.ignore)
    checksum_diffs = read_lines(a.checksum_output, lambda l: l.startswith((">f", "cd", "*deleting")))
    rsync_errors = read_lines(a.rsync_output, lambda l: l.startswith("rsync") or "(code " in l)
    ok = write_report(a.src, a.dst, a.filter, rules, r, checksum_diffs, rsync_errors, a.notes)
    print(f"{'PASS' if ok else 'FAIL'} expected={r['copied_n']} files/{r['copied_b']} B "
          f"dest={r['dst_n']} files/{r['dst_b']} B problems={len(r['problems'])} "
          f"empty_dirs={len(r['empty_dst'])} skipped={len(r['skipped'])} links={len(r['links'])}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
