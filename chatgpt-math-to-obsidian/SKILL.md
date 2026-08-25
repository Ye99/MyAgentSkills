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

### Commit the raw paste first

**Checkpoint the paste in git before converting anything.** The paste and the conversion
must be two separate commits. That is what makes `git diff` the verification tool below,
and it is the only thing that makes the conversion revertible without losing the paste.

```bash
git add note.md && git commit -m "Paste <topic> from ChatGPT"   # 1. checkpoint, raw
python3 scripts/fix_chatgpt_math.py --check note.md             # 2. read the diff
python3 scripts/fix_chatgpt_math.py note.md                     # 3. apply
git diff --stat note.md                                         # 4. verify: line count unchanged
git diff note.md                                                #    every hunk a delimiter swap
git add note.md && git commit -m "Render <topic> math for Obsidian"  # 5. commit the fix
```

Squashing the two into one commit destroys the property being bought: with a single
commit there is no tree in which the paste exists unconverted, so a bad conversion can
only be repaired by hand. Revert with `git checkout HEAD~1 -- note.md`.

**Do not skip the checkpoint because the working tree is dirty.** Commit the paste on its
own (`git add note.md` only) and leave unrelated changes alone. If the note genuinely
cannot be committed yet, copy it to a scratch file first and diff against that instead —
never convert with no recoverable copy of the paste.

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
8. **A display body needs no LaTeX marker.** `n=11` and `128K-103K=25K` are formulas with no
   `\command` and no `_{`/`^{` group. The bare `[` / `]` pair is itself strong evidence of
   display math, so an operator or a digit is enough — but a word of three or more letters
   means prose, and prose in brackets stays untouched. The inline `(...)` rule keeps the
   stricter marker requirement and adds vetoes of its own (rule 11): parentheses are far too
   common in prose to relax them.
9. **A `# [` heading is a swallowed setext `=`.** See below.
10. **A spacing command that lost its backslash is restored.** `0.72,\;` arrives as `0.72,;`.
    The surviving character names the command, so `,,` → `,\,`, `,;` → `,\;`, `,:` → `,\:`.
    Applied inside math only.
11. **An inline `(...)` span must survive five vetoes.** A LaTeX marker alone is not evidence
    of math — function calls, string literals, and prose all carry one. See below.

### Rule 9: the `# [` setext artifact

A formula pasted as `[` / `H^{(30)}` / `=` / `\begin{bmatrix}` / `]` has its `=` read as a
*setext underline*: the renderer consumes the `=` line and prepends `# ` to the paragraph.
What lands in the note is

```
# [
H^{(30)}

\begin{bmatrix}
```

The `=` is **reconstructed, not guessed** — a setext `#` underline is always `=`, and the
blank line marks exactly where it was. The repair restores the `=`, drops the `#`, and wraps
in `$$`. Three conditions must all hold, or the block is left alone:

- the `[` is **alone** on the heading line — `### [Building agents](url)` is a link, not math
- the heading level is `#` (whose underline is `=`); `##` and deeper never come from setext
- **exactly one** blank line sits inside the block — that is the slot the `=` came out of

The closing `]` is found by **bracket depth**, not by taking the first one, because the body
may contain a literal bracketed vector on its own lines:

```
$$
h_{\text{cat}}
=
[
0.72,\,
0.15,\,
]
$$
```

Stopping at the inner `]` would close the block early and strand the outer one.

### Rule 11: what disqualifies an inline `(...)` span

A LaTeX marker inside parentheses is **not** sufficient evidence of inline math. Prose,
function calls, and printf strings all contain one, and the original rule mangled all three.
Five vetoes now apply; any one of them leaves the span alone:

| Veto | Example it rejects | Why |
|---|---|---|
| Preceded by an identifier character | `exp(z_{t,i})`, `P(y_t \mid x)` | Function application — converting orphans the name as `exp$z_{t,i}$` |
| Contains a quote | `Printf("%s\n", x)` | A string literal is code |
| Contains `\[` or `\]` | `(\[f_{\min}, f_{\max}\])` | Escaped markdown, not a formula |
| Two or more ordinary words remain | `(its weights, \theta)` | Parentheses holding a sentence |
| Marker is only `\n` / `\t` | `(to fix \n)` | C escapes, not LaTeX — see below |

The word count is taken **after** stripping `\text{...}`, `\mathrm{...}` and friends, because
those legitimately hold prose: `(\text{next token}\mid x)` still converts.

`LATEX_HINT` requires **two** letters after the backslash. `\n` and `\t` appear constantly in
format strings and shell snippets, and every LaTeX command that matters here is longer.

Genuine pasted inline math — `(Q_{\text{it}})`, `(\theta)`, `(d_{\text{model}})` — is preceded
by a space and holds no sentence, so it passes all five.

**Measured effect.** Across one notes repo of ~900 markdown files, a repo-wide `--check`
flagged 4 files before these vetoes and **every hunk was damage**; afterwards it flags 0.
Meanwhile a raw ChatGPT paste run through the script once now reproduces, byte for byte, a
result that previously needed three script runs plus hand repair.

Scoping is still the cheaper habit: run it on **the file you just pasted into**, read the
`--check` diff, and keep the commit-the-paste-first step.

## Guardrails (why the naive one-liner fails)

- **Never run the inline `(...)` rule inside a math block.** `P(\text{next token})` has real
  parentheses; converting them yields `P$\text{next token}$` and breaks the formula. The script
  tracks `$$` state — including blocks converted earlier in the same pass and blocks already
  present in the file.
- **Require a LaTeX marker for the inline rule.** Without it, ordinary prose parentheses,
  `- [ ]` checkboxes, and markdown link syntax get mangled. Display blocks may instead
  qualify on shape (rule 8), because a lone `[` line is not something prose produces.
- **A three-letter word vetoes a display block.** This is what keeps `[` / `- item one` / `]`
  and `[` / `plain text in brackets` / `]` out of rule 8. Prefer leaving a real formula
  unconverted to converting a list.
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
python3 -m pytest tests -q                  # 69 cases: conversion, guardrails, re-run safety, CLI
                                            # (no pytest? python3 -m venv .venv && .venv/bin/pip install pytest)
git diff --stat note.md                     # line count must be unchanged
git diff note.md                            # every hunk should be a delimiter swap only
python3 scripts/fix_chatgpt_math.py --check note.md   # must exit 0: idempotent
```

The line count is the cheap check and it is a real one — every rule here swaps delimiters in
place, so **any** change in line count means content moved or vanished. Rule 9 is the only
rule that alters a line's *content* (a blank line becomes `=`), and it preserves the count too.

To prove nothing was lost rather than merely counting lines, strip every delimiter from both
sides and compare:

```bash
norm(){ sed -e 's/^[[:space:]]*#\? *\[[[:space:]]*$//' -e 's/^[[:space:]]*\][[:space:]]*$//' "$1" \
        | tr -d '$\\' | sed -e 's/[[:space:]]*$//'; }
diff <(git show HEAD~1:./note.md | norm /dev/stdin) <(norm note.md)
```

The only differences should be the `=` signs rule 9 restored. Anything else is content loss —
`git checkout HEAD~1 -- note.md` and investigate.

Then open the note in Obsidian and confirm the formulas render.

## Paste Artifacts This Tool Deliberately Does Not Fix

These need a human decision, because repairing them means supplying content that is not in the
file. Fix them by hand after running the script, then re-run `--check` to confirm it is clean.

- **A lone `$` in prose** (currency, shell variables) is read as the start of a math span and
  can shield the rest of the line from conversion. It is never corrupted, just skipped —
  convert that formula by hand.
- **A marker-less inline span**, such as `(n)` or `(x_i)`. Rule 8 relaxes the marker
  requirement for *display* blocks because the `[` / `]` pair is itself evidence; inline
  parentheses have no such structure, and `(n)` is ordinary prose far more often than it is
  math. Wrap these by hand.
- **A display body that mixes prose and formula**, such as `[` / `n=100,000 tokens` / `]`.
  Rule 8's three-letter-word veto rejects it and rule 1 finds no LaTeX marker, so it is left
  alone. Decide whether the word belongs inside the math or outside it, then wrap by hand.
- **A `##` or deeper heading holding a lone `[`.** Setext underlines only ever produce `#`
  (from `=`) or `##` (from `-`), and `-` is not a plausible line in a pasted formula, so
  anything below `#` is treated as a real heading.

Formerly on this list, now automated — do not re-fix these by hand:

| Artifact | Rule | Why it is reconstruction, not invention |
|---|---|---|
| `# [` with a swallowed `=` | 9 | A `#` setext underline is always `=`, and the blank line marks its slot |
| `,,` / `,;` / `,:` inside math | 10 | The surviving character names the spacing command that lost its backslash |
| `[` / `n=11` / `]` | 8 | The bracket pair is the evidence; no marker needed |

## Applying by Hand

If a note has only one or two formulas, editing directly is fine — but keep rules 1–3 and the
guardrails. The failure mode seen in practice was a global regex that also rewrote the
parentheses *inside* display blocks; the state-tracking pass exists to prevent exactly that.
