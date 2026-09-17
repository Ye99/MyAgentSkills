---
name: book-notes-restructure
description: Restructure reading notes in place so they follow the source material's real chapter and section hierarchy, regrouping blocks that the reading order scattered, moving every line verbatim, marking interleaved look-ups the reader added themselves, and proving no data loss. Use when notes taken while reading a book, paper, course, or manual have become a flat or jumbled pile of headings, when chapters appear more than once or out of order, when the reader wants their notes aligned to the source's table of contents, or when a note mixes quoted source text with the reader's own AI/web look-ups and the two can no longer be told apart.
---

# Book Notes Restructure

Reading notes drift out of shape because reading is not linear: the reader jumps
back a chapter, pastes a look-up mid-topic, and leaves every heading at the same
depth. This skill rebuilds the note against the source's own outline without
changing a word of it.

Two halves, kept apart. **Judgment** (you): read the note, decide which chapter
each block belongs to and whether it came from the source or from the reader's
own look-up. **Mechanics** (scripts): move the lines, wrap the look-ups, and
prove nothing was lost. Write the judgment down as a plan file; let the scripts
do every edit.

## Ground rules

- **Move, never reword.** Blocks are relocated and re-nested verbatim. Do not
  summarize, translate, reflow, deduplicate, or tidy grammar. Do not fix typos in
  the same pass — see [Typos](#typos).
- **The reader's own interleaved material stays where it is.** Look-ups the
  reader pasted while reading belong next to the passage that prompted them, not
  collected into an appendix. Regrouping moves a look-up only along with the
  source passage it explains.
- **Checkpoint before converting.** The raw note must exist as its own commit, or
  a bad conversion cannot be reverted without losing the paste.
- **Verification is not optional.** `verify_verbatim.py` must pass before the
  result is committed. Never hand-edit the output to make a check pass.
- **Ask before reordering.** Regrouping moves large blocks. Confirm the approach
  with the reader first — see [Decide the reshape](#2-decide-the-reshape).

## Scripts

All in `scripts/`; each documents its usage at the top. Standard library only.

| Script | Purpose |
|---|---|
| `epub_outline.py BOOK.epub [--doc ch03] [--max-level N]` | The source's real chapter/section outline, in spine order. Reads the EPUB; extracts nothing |
| `note_outline.py NOTE.md [--from-line N] [--json out.json]` | One row per heading: index, line, level, body line range, size, preview |
| `restructure_note.py NOTE.md --plan plan.json [--dry-run]` | Rebuilds the region from the plan; refuses to write if a non-blank line would be dropped or used twice |
| `verify_verbatim.py BEFORE.md AFTER.md --from-line N` | Four independent no-data-loss checks; must print `VERIFICATION PASSED` |

Run the tests after changing a script: `python3 -m pytest scripts/tests -q`.

## Workflow

### 1. Preconditions and checkpoint

The note must be in a git repo. Commit the raw note on its own first:

```bash
git add NOTE.md && git commit -m "Paste <topic> reading notes"
```

Then run the cleanup skills that operate on raw pastes, each as its own commit,
**before** restructuring — they are line-local, so doing them first keeps the
restructure diff readable:

| Symptom | Skill |
|---|---|
| Root-level `Pasted image ....png` embeds | `externalize-image-and-extract-text` |
| Raw LaTeX in bare `[ ... ]` / `( ... )` | `chatgpt-math-to-obsidian` |
| Literal `\#` or `*` pseudo-headings | `markdown-topic-normalizer` |

After the math skill reports clean, sweep for the artifact it deliberately
leaves — see [Glued render artifacts](#glued-render-artifacts).

### 2. Decide the reshape

Get both outlines: `epub_outline.py` for the source, `note_outline.py` for the
note. Compare them and show the reader a table of which note regions map to which
chapter, then ask how to fix the chapter relation. The answer changes everything
downstream, and only they can give it:

- **regroup** into source order, moving each look-up with the passage it
  explains — the real cleanup, but a large diff;
- **in place**, fixing only heading levels and inserting chapter headings where
  content transitions, so a chapter may appear more than once;
- **partial**, merging only the chapters that got split.

Also ask how look-ups should be marked (callout, tag, or nothing) and how much
citation detail source-derived blocks need.

### 3. Classify every section

For each section decide **chapter** and **kind** (`source` or `lookup`). Cheap
signals, all worth confirming by reading:

- source-derived blocks quote figure numbers, cite the book's own section names,
  and carry reader-style abbreviations and typos;
- look-ups read like an answer — second person, "Exactly.", "Think of it like
  this", a definition followed by a worked example;
- a section whose body changes voice partway is **two** blocks. Split it at the
  line where the voice changes and give each half its own plan item.

### 4. Write the plan

`plan.json` format is documented in the `restructure_note.py` docstring: an
ordered list of items, each with `level`, `title`, `kind`, and the original
`lines` to move. An item with no `lines` emits a heading only — that is how
chapter headings are created.

Depth budget, the constraint that decides `level` everywhere else: Markdown stops
at `######`. If the source gets `##`, chapters get `###`, leaving `####`/`#####`/
`######` for three levels of section nesting. Check the deepest existing nest
before choosing, and promote the source title to `##` when the note leaves it
stranded at `###` among unrelated entries.

Two rules that keep the plan honest:

- **Cover every line.** Every non-blank line in the region belongs to exactly one
  item; the script refuses to write otherwise. A line folded into a heading goes
  in `absorbed_lines`, and any leftover text on it is rescued with `prepend`.
- **Cut only at blank lines or heading boundaries**, never mid-paragraph,
  mid-list, mid-table, or inside a code fence.

### 5. Build, verify, review

```bash
cp NOTE.md /tmp/before.md
python3 scripts/restructure_note.py NOTE.md --plan plan.json
python3 scripts/verify_verbatim.py /tmp/before.md NOTE.md --from-line N
```

`verify_verbatim.py` does not read the plan, so it is an independent check rather
than a restatement of what the plan intended:

| Check | Proves |
|---|---|
| A. blocks | every body block of the original reappears as an exact, contiguous run — relocation is fine, rewording is not |
| B. multiset | ignoring order, the non-heading lines are the same set — nothing dropped or duplicated |
| C. fences | code block interiors are byte-identical, which matters for ASCII diagrams inside callouts |
| D. links | image embeds and heading wikilinks still resolve |

Expect B to report a drop in **blank** line count; block edges get trimmed. A
non-blank difference is a real finding — usually a heading absorption you meant
to make, and you must confirm it line by line before accepting it.

Then read the rebuilt outline and spot-check a callout containing a table and one
containing a code fence.

### 6. Report and commit

Tell the reader what moved between chapters, what the checks proved, and what you
deliberately left alone. Commit the restructure as one commit, separate from the
cleanup commits, and push if the repo has a remote and they asked for it.

## Marking the reader's look-ups

Keep the heading **outside** the callout and wrap only the body:

```markdown
#### A question the reader looked up
> [!note]+ My lookup
> Answer text, quoted verbatim.
```

Headings inside a callout drop out of the outline pane, which defeats the point of
fixing the nesting. `[!note]+` renders expanded and stays collapsible; `[!note]-`
starts collapsed, which hides most of the note by default — ask which they want.

Inside a callout every line needs the `> ` prefix, blank lines become a bare `>`,
and an existing blockquote becomes `> > `. Tables, math blocks, and code fences
all survive this as long as the prefix is on every line, which
`restructure_note.py` handles.

## Glued render artifacts

A chat UI's *rendered* math and its *source* both land in the paste, concatenated
with no separator, and math-conversion scripts correctly refuse to guess at them:

```
explore alternatives+evaluate them+focus on better ones\boxed{\text{explore alternatives} + ...}
P(next reasoning step∣current state)P(\text{next reasoning step}\mid\text{current state})
WiW_i = accumulated reward for child ii
```

The rendered half is a duplicate of the LaTeX half, so deleting it loses nothing.
Keep the LaTeX, wrap it in `$$...$$` (or `$...$` inline), drop the prefix.

Find them by searching for a **non-breaking space** (`\xa0`), which the rendered
half is full of and the LaTeX half never contains — plain-space searches and exact
string edits both miss these lines for that reason. Doubled letters and digits
(`ii`, `WiW`, `3010`) are the other tell. Fix them by hand, then re-run the math
skill's `--check` until it exits clean.

## Typos

Typo fixes are a **separate commit after** the restructure, never mixed into it —
a verbatim-move diff has to stay reviewable as a pure move.

Then fix them only if the reader asks. Do not silently correct a note's wording.
When they do ask, sweep the whole region rather than fixing the few you noticed:

```bash
aspell --lang=en --mode=none list < extracted-prose.txt | sort -u
```

Strip code fences, math blocks, inline code, URLs, and image embeds before
feeding text to `aspell`, or the jargon drowns the signal. Triage the output by
hand: most hits will be legitimate domain terms. Leave terminology slips
(a wrong-but-real term) alone and raise them with the reader instead — those
change meaning, not spelling.

## Gotchas

- **A heading that another note links to must keep its exact text.** Check for
  inbound `[[Note#Heading]]` links before renaming; check A of the verifier only
  covers links inside the file.
- **Whitespace-only lines** (a line of spaces) become a bare `>` inside a callout.
  Renders the same, but it is why check B reports a blank-line delta.
- **Chat pastes indent continuation lines with four spaces.** Those lines are
  blank for Markdown purposes but not byte-identical; do not treat a diff in them
  as content loss.
- **`cp` is often aliased to `cp -i`** and will stall on an overwrite prompt in a
  non-interactive shell; use `\cp -f` or write to a fresh path.
- **Section indices shift the moment you split one.** Build the plan from line
  ranges, not from indices captured before a split.
- **Related skills**: `obsidian-note-split` when the note should become several
  notes instead of one restructured note; `markdown-obsidian-linker` when heading
  links need to resolve after renaming.
