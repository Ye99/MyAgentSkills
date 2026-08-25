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
# Two letters minimum: `\n` and `\t` are C escapes that appear constantly in
# printf strings and shell snippets, and every LaTeX command that matters here
# is longer. Accepting them made format strings look like math.
LATEX_HINT = re.compile(r"\\[A-Za-z]{2,}|[_^]\{")

FENCE = re.compile(r"^\s*(?:```|~~~)")
OPEN_DISPLAY = re.compile(r"^\s*\\?\[\s*$")
CLOSE_DISPLAY = re.compile(r"^\s*\\?\]\s*$")

# A formula body with no LaTeX marker at all: `n=11`, `128K-103K=25K`. A bare
# `[` / `]` pair is already strong evidence of display math, so an operator or a
# digit is enough -- but a word of three or more letters means prose, not a
# formula, and prose in brackets must stay untouched.
MATH_SIGN = re.compile(r"[=+\-*/^<>]|\d")
PROSE_WORD = re.compile(r"[A-Za-z]{3,}")

# `[` alone on an ATX heading line. A formula pasted as `[` / `H^{(30)}` / `=` /
# body / `]` has its `=` read as a setext underline: the renderer swallows it and
# prepends `# `. The underline character follows from the heading level, so the
# `=` is reconstructed, not guessed. Only `#` (whose underline is `=`) qualifies;
# a heading that merely starts a markdown link is not math.
SETEXT_OPEN = re.compile(r"^#[ \t]+\\?\[\s*$")

# A LaTeX spacing command that lost its backslash in the paste: `0.72,\;`
# arrives as `0.72,;`, rendering as a stray semicolon. These only ever land at
# the end of a line inside a `$$` block, where MathJax already joins lines with
# a space -- so the spacing is a no-op and the stray character is deleted rather
# than reconstructed. Deleting needs no guess about which command it was. The
# comma before it is a list separator and is kept.
LOST_SPACING = re.compile(r",[,;:]")
# The same spacing command with its backslash intact, at the end of a line of
# math. An undo or a re-paste restores this form, and it is the same no-op, so
# it is dropped too -- otherwise the cleanup cannot be repeated. The lookbehind
# keeps the trailing `\\` of a matrix row separator out of reach; mid-line
# spacing is real and is left alone.
TRAILING_SPACING = re.compile(r"(?:(?<!\\)\\[,;:!])+$")

# `(...)`, or an escaped `\( ... \)`, whose contents look like LaTeX.
INLINE = re.compile(r"\\?\(([^()\n]*?)\\?\)")

# A LaTeX marker inside parentheses is not sufficient evidence of inline math:
# prose, function calls, and printf strings all contain one. These veto it.
# Quotes mean a string literal; an escaped `\[` means markdown, not a formula.
QUOTE = re.compile("[\"'\u201c\u201d\u2018\u2019]")
ESCAPED_BRACKET = re.compile(r"\\[\[\]]")
# `\text{...}` legitimately holds prose words, so strip those groups (and every
# other command) before looking for the words that betray an English sentence.
TEXT_GROUP = re.compile(
    r"\\(?:text|textrm|textbf|textit|mathrm|mathbf|operatorname)\s*\{[^{}]*\}")
COMMAND = re.compile(r"\\[A-Za-z]+")
WORD = re.compile(r"[A-Za-z]{2,}")
# Regions the inline rule must not touch: code spans, and math that is already
# converted. A `$...$` span is math, so its parentheses belong to the formula,
# and a prose `(...)` wrapping one must not be swallowed either. Without this,
# a second run over a converted note rewrites `$H^{(\ell)}$` into `$H^{$\ell$}$`.
PROTECTED = re.compile(r"`[^`\n]*`|\$[^$\n]+\$")


def _looks_like_latex(text: str) -> bool:
    return bool(LATEX_HINT.search(text))


def _looks_like_math_body(text: str) -> bool:
    """True for a formula body carrying no LaTeX marker, false for prose."""
    return bool(MATH_SIGN.search(text)) and not PROSE_WORD.search(text)


def _is_math_block_body(body) -> bool:
    if not body:
        return False
    if any(_looks_like_latex(b) for b in body):
        return True
    return all(_looks_like_math_body(b) for b in body)


def _convert_inline(line: str) -> str:
    """Rewrite inline math in `line`, leaving protected regions alone."""
    out = []
    pos = 0
    for span in PROTECTED.finditer(line):
        out.append(INLINE.sub(_inline_sub, line[pos:span.start()]))
        out.append(span.group(0))
        pos = span.end()
    out.append(INLINE.sub(_inline_sub, line[pos:]))
    return "".join(out)


def _is_inline_math(body: str) -> bool:
    """True only for a parenthesised span that is a formula and nothing else."""
    if not body or not _looks_like_latex(body):
        return False
    if QUOTE.search(body) or ESCAPED_BRACKET.search(body):
        return False
    # Two or more ordinary words left after the LaTeX is stripped means the
    # parentheses are holding a sentence, not a formula.
    residue = COMMAND.sub(" ", TEXT_GROUP.sub(" ", body))
    return len(WORD.findall(residue)) < 2


def _follows_identifier(match: re.Match) -> bool:
    """True for `exp(...)` / `P(...)`: a function application, not a math span.

    Converting one strands the function name outside the math: `exp(z_{t,i})`
    becomes `exp$z_{t,i}$`. Genuine pasted inline math is always preceded by a
    space or the start of the line.
    """
    start = match.start()
    return start > 0 and (match.string[start - 1].isalnum()
                          or match.string[start - 1] in "_.")


def _inline_sub(match: re.Match) -> str:
    body = match.group(1).strip()
    if not _is_inline_math(body) or _follows_identifier(match):
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
                out.append(_trim_math_line(line))
            i += 1
            continue

        if line.strip() == "$$":
            if _has_closing_display(lines, i + 1):
                in_display = True
                out.append("$$")
                i += 1
                continue
            # A stray, unclosed `$$` is a typo, not a math block. Treating it as
            # one would rstrip every remaining line and silently destroy the
            # markdown hard breaks in the rest of the note.

        if SETEXT_OPEN.match(line):
            body, end = _read_setext_body(lines, i + 1)
            if end is not None:
                out.append("$$")
                # The one blank line is where the swallowed setext `=` was.
                out.extend("=" if not b.strip() else _trim_math_line(b)
                           for b in body)
                out.append("$$")
                i = end + 1
                continue

        if OPEN_DISPLAY.match(line):
            body, end = _read_display_body(lines, i + 1)
            if end is not None and _is_math_block_body(body):
                out.append("$$")
                out.extend(_trim_math_line(b) for b in body)
                out.append("$$")
                i = end + 1
                continue

        out.append(_convert_inline(line))
        i += 1

    return "\n".join(out)


def _trim_math_line(line: str) -> str:
    """Strip a markdown hard break from a line of math.

    ChatGPT renders the LaTeX row separator `\\\\` as a single trailing
    backslash plus a two-space hard break, so removing the break naively leaves
    `a\\`, which is not a row separator and breaks the matrix. Restore the
    second backslash whenever the break hid an odd number of them.
    """
    stripped = line.rstrip()
    if stripped != line and (len(stripped) - len(stripped.rstrip("\\"))) % 2:
        stripped += "\\"
    return TRAILING_SPACING.sub("", LOST_SPACING.sub(",", stripped))


def _has_closing_display(lines, start):
    """True when a later line closes a `$$` block opened at `start - 1`."""
    return any(line.strip() == "$$" for line in lines[start:])


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


def _read_setext_body(lines, start):
    """Return (body, index_of_closing_bracket) for a `# [` artifact, or (body, None).

    Bracket depth is tracked rather than stopping at the first `]`, because the
    body may itself contain a literal bracketed vector on its own lines. Exactly
    one blank line must be present: that is the slot the setext `=` was read out
    of, and without it there is nothing to reconstruct.
    """
    body = []
    depth = 1
    j = start
    while j < len(lines):
        if OPEN_DISPLAY.match(lines[j]):
            depth += 1
        elif CLOSE_DISPLAY.match(lines[j]):
            depth -= 1
            if depth == 0:
                blanks = [b for b in body if not b.strip()]
                if len(blanks) == 1 and any(_looks_like_latex(b) for b in body):
                    return body, j
                return body, None
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
