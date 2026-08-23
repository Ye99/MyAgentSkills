#!/usr/bin/env python3
"""Convert ChatGPT-pasted LaTeX in Markdown notes into Obsidian MathJax.

Text copied out of ChatGPT loses its math delimiters: display math arrives as a
bare `[` / `]` pair around the formula and inline math as `(...)`. Obsidian then
renders the raw LaTeX source. This rewrites those into `$$...$$` and `$...$`
without touching prose, links, code fences, or inline code spans.

Idempotent: running it twice produces no further changes.
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

# A LaTeX command (\text, \theta, \cdot ...) or a sub/superscript group.
LATEX_HINT = re.compile(r"\\[A-Za-z]+|[_^]\{")

FENCE = re.compile(r"^\s*(?:```|~~~)")
OPEN_DISPLAY = re.compile(r"^\s*\\?\[\s*$")
CLOSE_DISPLAY = re.compile(r"^\s*\\?\]\s*$")

# `(...)`, or an escaped `\( ... \)`, whose contents look like LaTeX.
INLINE = re.compile(r"\\?\(([^()\n]*?)\\?\)")
CODE_SPAN = re.compile(r"`[^`\n]*`")


def _looks_like_latex(text: str) -> bool:
    return bool(LATEX_HINT.search(text))


def _convert_inline(line: str) -> str:
    """Rewrite inline math in `line`, leaving inline code spans alone."""
    out = []
    pos = 0
    for span in CODE_SPAN.finditer(line):
        out.append(INLINE.sub(_inline_sub, line[pos:span.start()]))
        out.append(span.group(0))
        pos = span.end()
    out.append(INLINE.sub(_inline_sub, line[pos:]))
    return "".join(out)


def _inline_sub(match: re.Match) -> str:
    body = match.group(1).strip()
    if not body or not _looks_like_latex(body):
        return match.group(0)
    return f"${body}$"


def convert(text: str) -> str:
    lines = text.split("\n")
    out = []
    i = 0
    in_fence = False
    in_display = False

    while i < len(lines):
        line = lines[i]

        if FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue

        if in_fence:
            out.append(line)
            i += 1
            continue

        if in_display:
            # Inside an already-converted `$$` block: only trim the trailing
            # two-space markdown hard breaks, which mean nothing to MathJax.
            if line.strip() == "$$":
                in_display = False
                out.append("$$")
            else:
                out.append(line.rstrip())
            i += 1
            continue

        if line.strip() == "$$":
            in_display = True
            out.append("$$")
            i += 1
            continue

        if OPEN_DISPLAY.match(line):
            body, end = _read_display_body(lines, i + 1)
            if end is not None and any(_looks_like_latex(b) for b in body):
                out.append("$$")
                out.extend(b.rstrip() for b in body)
                out.append("$$")
                i = end + 1
                continue

        out.append(_convert_inline(line))
        i += 1

    return "\n".join(out)


def _read_display_body(lines, start):
    """Return (body_lines, index_of_closing_bracket) or (body, None)."""
    body = []
    j = start
    while j < len(lines):
        if CLOSE_DISPLAY.match(lines[j]):
            return body, j
        if not lines[j].strip():
            break  # a blank line means this was never a math block
        body.append(lines[j])
        j += 1
    return body, None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="Markdown files to fix")
    parser.add_argument("--check", action="store_true",
                        help="print a diff and exit 1 if changes are needed; write nothing")
    args = parser.parse_args(argv)

    changed = False
    for path in args.paths:
        original = path.read_text(encoding="utf-8")
        updated = convert(original)
        if original == updated:
            continue
        changed = True
        if args.check:
            sys.stdout.writelines(difflib.unified_diff(
                original.splitlines(True), updated.splitlines(True),
                f"a/{path}", f"b/{path}"))
        else:
            path.write_text(updated, encoding="utf-8")
            print(f"updated {path}")
    return 1 if (changed and args.check) else 0


if __name__ == "__main__":
    sys.exit(main())
