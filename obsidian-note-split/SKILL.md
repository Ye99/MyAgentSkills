---
name: obsidian-note-split
description: Split one oversized Obsidian note into several self-descriptive topic notes plus an index note, grouping related sections that are scattered through the original, moving text verbatim, rewriting heading links (inside the split notes and in other notes that link in), proving no data loss, and writing the result through the Obsidian CLI. Use whenever a user wants to break up, reorganize, or modularize a large, sprawling, or hard-to-search Markdown/Obsidian note, asks which notes are too big, or wants a monolithic note made easier for AI agents to search — even if they don't say "split".
---

# Obsidian Note Split

Turn one large note into topic notes an agent can find by name, without losing, duplicating, or rewording a single line.

The job has two halves. **Judgment** (you): read the note and decide which lines belong together. **Mechanics** (scripts): move the lines, fix links, and prove nothing was lost. Keep them separate: write the grouping as a plan file, let the scripts do every edit.

## Ground rules

- **Move, never reword.** Text is grouped and moved verbatim. Do not fix typos, translate, summarize, reformat, deduplicate repeated paragraphs, or redact anything — including passwords or tokens that happen to be in a private note (mention them to the user; never repeat the values in chat, commits, or skills). The only permitted edits to original lines are heading depth and link targets, both done by the script.
- **The source file name survives as the index note**, so existing links and plain-text references to it keep working.
- **Asset folders keep their names.** Embeds such as `![[Old Note.assets/img.png]]` keep resolving because notes stay in the same folder.
- **Nothing lands in the vault until verification passes**, and writes go through the Obsidian CLI so the app's link index stays current.

## Scripts

All in `scripts/`; each has `--help`-style usage at the top.

| Script | Purpose |
|---|---|
| `outline.py NOTE [--vault DIR]` | Headings with section sizes, duplicate headings, links, inbound references from other notes, secret-like strings |
| `outline.py NOTE --skim --from N --to M` | First line of every paragraph in a range — what a long section really contains |
| `split_note.py --vault DIR --plan plan.json --out OUT` | Builds all notes and link rewrites in `OUT`, runs the no-data-loss verification, never touches the vault |
| `obsidian_write.py --vault DIR --vault-name NAME --out OUT [--dry-run]` | Writes `OUT` into the vault through the Obsidian CLI and re-verifies on disk and in Obsidian |

## Workflow

### 1. Preconditions

- Obsidian is running and `obsidian vault=NAME files total` answers (the `obsidian-cli` skill covers the CLI).
- The vault is a git repo with a clean working tree for the files involved, so the pre-split state is recoverable. If it is not under version control, copy the source note somewhere safe first.
- Pull/rebase the vault repo if it has a remote, so you split the current version.

### 2. Inventory

Run `outline.py` on the note. Note especially:
- sections much longer than their heading suggests — **skim them**; notes that grew over time bury unrelated material under old headings, and continuation text often follows a subsection without any new heading;
- related material scattered in several places (the main value of the split is regrouping it);
- inbound heading links from other notes (the splitter rewrites them) and duplicate heading texts (Obsidian resolves a link to the first one).

### 3. Plan the grouping

Decide 3–12 topic notes. A good note name is what someone would type into search: a shared prefix from the original name plus the specific topic and key nouns, e.g. `Garden Irrigation Controllers Wiring and Troubleshooting` split from `Garden`. Avoid `\ / : * ? " < > | # ^ [ ]` in names.

Write `plan.json` (format in the `split_note.py` docstring). Each note lists `[first_line, last_line, heading_shift]` segments in the order they should appear; segments can come from anywhere in the source. Guidance:
- Assign **every** line, blank lines included, to exactly one note. The index holds no original text, so lines before the first heading (an opening remark, for example) must also go into a topic note.
- Set `index.intro` if the default wording does not fit, e.g. to say where the pre-split version is kept (git history, a backup copy).
- Put material that has no better home into a sensible catch-all note rather than forcing it somewhere wrong; a chronological log can stay chronological.
- Choose `heading_shift` per segment so each note's top sections become `##` (the script adds the `#` title). A continuation segment with no heading of its own uses the shift of the section it continues.
- Cut segments at blank lines or heading boundaries, never mid-paragraph, mid-list, mid-table, or inside a code fence.
- Write each description as one sentence naming what an agent will find there; it goes into frontmatter, the note's opening line, and the index.

Show the user the proposed notes (name, description, which parts of the source go where) before building, unless they already approved a plan.

### 4. Build and verify — required no-data-loss step

Run `split_note.py`. It must print `VERIFICATION PASSED`. Its checks, all run against the files it wrote:

| Check | Proves |
|---|---|
| A. coverage | every original line is assigned to exactly one note — no gaps, no overlaps |
| B. undo | reversing the allowed edits (heading depth, link targets) on each written line reproduces the original line exactly, so nothing was reworded |
| C. multiset | independently of the line mapping, all normalized non-blank lines of the outputs equal those of the original — nothing missing, nothing duplicated |
| D. links | every heading link in the notes, index, and rewritten other notes resolves |

On failure, fix the **plan** (usually an overlap, a gap, or a shift that would turn a heading into `#`) and rerun. Never hand-edit the output files to make a check pass — that bypasses the proof.

Then read a few outputs: each note's start, a moved scattered section, a rewritten link, and the index.

### 5. Write through the Obsidian CLI

Run `obsidian_write.py --dry-run` to see the write plan, then without `--dry-run`. It refuses to run if any vault file changed since the split was computed, writes topic notes first and the index last, and then checks:
- **E.** every written file is byte-identical to its verified copy;
- **F.** Obsidian's own metadata cache resolves every heading link in the written files;
- **G.** `obsidian unresolved` gains no new link targets.

If a write fails midway, the source note is still intact (the index is written last). Remove created topic notes with `obsidian vault=NAME delete path="…"` (moves them to trash) and start again from step 4.

### 6. Report and commit

Tell the user: notes created (names, sizes), what was regrouped, which other notes had links rewritten, check results, and any secret-like strings `outline.py` found (by line, not value). Update plain-text references to specific sections in other repos or scripts only if the user wants that. Commit the vault change as one commit (topic notes + index + rewritten notes) and push if the repo has a remote and the user asked for it.

## Gotchas

- **Do not write notes with `obsidian create content=…`.** It converts `\n` and `\t` in the text into control characters and cannot write them literally; code samples get corrupted silently. `obsidian_write.py` uses `obsidian eval` with base64 instead.
- **The Obsidian CLI reads stdin.** In a shell `while read` loop it swallows the remaining input; always redirect `</dev/null`.
- **One CLI argument tops out near 128 KB**; the writer sends ≤60 KB chunks cut at line boundaries (never inside a UTF-8 character).
- **Duplicate heading texts**: Obsidian links resolve to the first match. The index uses nested links (`[[Note#Section#Subsection]]`) for duplicates; headings containing `[ ] | ^ #` (for example headings that contain a Markdown link) cannot be link targets and are listed as plain text.
- **Headings are matched raw**: Obsidian stores `**bold**`, `\!` escapes, and inline links as part of the heading text, so links must use the heading exactly as written.
- **`cp` may be aliased to `cp -i`** and stall on an overwrite prompt in non-interactive shells; the writer avoids copying into the vault altogether.
- **zsh does not expand `grep --include=*.md` the way bash does**; quote the glob or run under `bash -c`.
