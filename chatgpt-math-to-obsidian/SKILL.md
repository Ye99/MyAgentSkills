---
name: chatgpt-math-to-obsidian
description: Use when a Markdown note pasted from ChatGPT shows raw LaTeX instead of rendered math - formulas wrapped in bare `[ ... ]` lines or `(\text{...})` parentheses, or in `\[ ... \]` / `\( ... \)` - and it must render in Obsidian.
---

# ChatGPT Math to Obsidian

## Purpose

Text copied out of ChatGPT loses its math delimiters. Display math arrives as a bare
`[` / `]` pair on their own lines and inline math as `(...)`, sometimes escaped as
`\[ ... \]` / `\( ... \)`. Obsidian renders none of these, so the note shows raw LaTeX.
This skill rewrites them into Obsidian MathJax (`$$...$$` and `$...$`) without touching
prose, links, tables, code fences, or inline code.

## Symptoms

```
[
Q_{\text{it}} \cdot K_{\text{cat}}
]

the token produces a Query vector (Q_{\text{it}}).
```

should be

```
$$
Q_{\text{it}} \cdot K_{\text{cat}}
$$

the token produces a Query vector $Q_{\text{it}}$.
```

## Usage

```bash
python3 scripts/fix_chatgpt_math.py --check note.md   # unified diff, writes nothing, exit 1 if changes pending
python3 scripts/fix_chatgpt_math.py note.md           # rewrite in place
```

Always run `--check` first and read the diff. The script is idempotent: a second run
produces no further changes, and it is safe to re-run on a note that has already been
converted and then edited — the usual case when a note grows by repeated pasting.

## Conversion Rules

1. **Display math** — a line that is exactly `[` (or `\[`), a body, and a closing `]` (or `\]`)
   becomes `$$ ... $$`, but only when the body contains LaTeX (a `\command` or a `_{`/`^{` group).
2. **Inline math** — `(...)` or `\(...\)` containing LaTeX becomes `$...$`, with surrounding
   whitespace trimmed.
3. **Trailing two-space markdown hard breaks are stripped inside math blocks only.** They are
   invisible to MathJax and make the source noisy. Prose keeps its hard breaks.
4. Multi-line formula bodies keep every line; MathJax treats the newlines as spaces.
5. **A collapsed row separator is restored.** ChatGPT renders the LaTeX `\\` that ends a
   `bmatrix` row as a single trailing backslash plus a hard break. Stripping the break under
   rule 3 would leave `a\`, which is not a row separator and silently breaks the matrix, so
   the second backslash is put back whenever the break hid an odd number of them.
6. **Existing `$...$` spans are math, not prose.** Neither the parentheses inside them nor a
   prose `(...)` wrapped around one is rewritten. This is what makes re-runs safe.
7. **An unclosed `$$` is a typo, not a block.** Entering display mode on it would strip the
   markdown hard breaks from every remaining line of the note, so a `$$` with no closing
   partner is left as ordinary text.

## Guardrails (why the naive one-liner fails)

- **Never run the inline `(...)` rule inside a math block.** `P(\text{next token})` has real
  parentheses; converting them yields `P$\text{next token}$` and breaks the formula. The script
  tracks `$$` state — including blocks converted earlier in the same pass and blocks already
  present in the file.
- **Require a LaTeX marker.** Without it, ordinary prose parentheses, `- [ ]` checkboxes, and
  markdown link syntax get mangled.
- **Skip fenced code blocks and inline code spans.** Backslashes there are literal.
- **Leave unclosed or non-LaTeX brackets alone** rather than guessing.
- **Never drop content.** Verify with `git diff` that only delimiters and trailing spaces moved,
  and that the line count is unchanged.
- **Re-running must never corrupt.** The tool has to be a fixed point on its own output. The
  regression that motivated rules 6 and 7: `$H^{(\ell-1)}$ (shape: $[n, d]$)` became
  `$H^{$\ell-1$}$ $shape: $[n, d]$$` on the second run, because the inline rule saw the LaTeX
  inside an already-converted span. Note that neither a character-preservation check nor an
  idempotency check detects this class of damage — only the tests do. Keep them green.

## Verification

```bash
python3 -m pytest tests -q                  # 35 cases: conversion, guardrails, re-run safety, CLI
                                            # (no pytest? python3 -m venv .venv && .venv/bin/pip install pytest)
git diff --stat note.md                     # line count must be unchanged
git diff note.md                            # every hunk should be a delimiter swap only
```

Then open the note in Obsidian and confirm the formulas render.

## Paste Artifacts This Tool Deliberately Does Not Fix

These need a human decision, because repairing them means supplying content that is not in the
file. Fix them by hand after running the script, then re-run `--check` to confirm it is clean.

- **`# [` at the start of a formula.** A lone `=` line under text is a Markdown *setext*
  heading, so a formula pasted as `[` / `H^{(l)}` / `=` / `\begin{bmatrix}` gets normalized to
  an ATX heading and the `=` is swallowed:

  ```
  # [
  H^{(l)}

  \begin{bmatrix}
  ```

  Restore the `=`, drop the `#`, and wrap in `$$`. The script leaves these alone because
  guessing where the `=` went is inventing content.
- **A lone `$` in prose** (currency, shell variables) is read as the start of a math span and
  can shield the rest of the line from conversion. It is never corrupted, just skipped —
  convert that formula by hand.

## Applying by Hand

If a note has only one or two formulas, editing directly is fine — but keep rules 1–3 and the
guardrails. The failure mode seen in practice was a global regex that also rewrote the
parentheses *inside* display blocks; the state-tracking pass exists to prevent exactly that.
