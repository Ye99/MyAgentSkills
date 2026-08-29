---
name: externalize-image-and-extract-text
description: >-
  Use when a Markdown or Obsidian note has an embedded, pasted, root-level, or
  already-external image that should live in an assets folder with a working
  wikilink. Also use when the user explicitly asks to OCR, transcribe, or
  extract the text of an image into the note.
---

# Externalize Image And Extract Text

This skill moves note images into a stable assets folder and fixes their links.
It transcribes an image's text **only when the user asks for it**.

## The Default Is Externalize Only

**Transcription is opt-in.** A run that only moves images and rewrites wikilinks
is a complete, correct run. Do not add an `#### Image text` block, an OCR dump,
a bullet transcription, a caption, or an alt-text description unless the trigger
below fires.

**Transcribe only if the user's request contains an explicit text-extraction
ask** — one of: `OCR`, `transcribe`, `extract text`, `read the text`,
`text from the image`, `caption`, `describe the image`, or a direct instruction
naming the image's contents ("pull the table out of that screenshot").

That predicate is the whole test. Read the request; if none of those appear,
externalize and stop.

**No nuance clauses.** These do NOT authorize transcription:

- the image is dense with text, a table, a slide, or a chart
- the note would "read better" with the text inline
- the image might not render, or the user might want it searchable later
- accessibility, or the reader might be on a device that can't load images
- you already read the image to pick a filename
- a previous run of this skill in the same session transcribed images

If you believe transcription would genuinely help, **say so in one sentence and
offer it** — do not write it.

## Choose the Right Skill

| Scenario | Use |
| --- | --- |
| Note contains `[ref]: <data:image/...;base64,...>` and the user only wants assets extracted | `extract-embedded-images` |
| Note embeds an image and the user wants the image **replaced** by editable Markdown | `rewrite-obsidian-image-notes` |
| Note image should be moved to assets with a working link | this skill (default mode) |
| User explicitly asked to OCR / transcribe / extract the image's text | this skill (with transcription) |

## Workflow

1. Locate the note and target image references with `rg`.
2. **Decide the mode**: check the user's request against the trigger list above.
   Default is externalize only.
3. Normalize image storage:
   - If the note contains base64 `data:image` reference definitions, run
     `extract-embedded-images` first. Use `--dry-run` on unfamiliar notes, then
     run the conversion and verify assets exist.
   - If the note embeds a root-level pasted image such as
     `![[Pasted image 20260517214504.png]]`, move it into
     `<NoteBase>.assets/` with a non-colliding descriptive filename, then update
     the wikilink.
   - If the image is sourced from outside the vault (`~/Documents`, `~/Downloads`,
     a scanner or camera dump), move it into `<NoteBase>.assets/` under a
     descriptive filename the same way. Outside the vault is not a reason to copy.
   - If the image is already in a suitable assets folder, leave it in place,
     but still rename it if its filename does not describe its contents.
4. Fix layout only where the move breaks it — e.g. an embed trailing a list item
   or run together with a paragraph gets its own block. Nothing else.
5. **Stop here unless transcription was requested.** Run the verification below.

### Transcription Steps (only when requested)

6. View each image at original detail. Use OCR tooling when available; otherwise
   visually transcribe only text that can be read reliably.
7. Verify the extracted text against the image. Do not invent missing words,
   table cells, labels, or numbers.
8. Insert the extracted text immediately after the relevant image embed unless
   surrounding context clearly calls for another placement.

## Move, Never Copy

Every image this skill relocates is **moved**, and the run is not finished while
a second copy of it exists on disk.

Use `mv`. If the workflow made a copy for any reason, the run has one more
required step: delete the source, then confirm the source path is gone.

```bash
mv ~/Documents/scan.jpg Note.assets/password-lock-instructions.jpg

# If you copied instead, confirm the two are identical before deleting:
md5sum ~/Documents/scan.jpg Note.assets/password-lock-instructions.jpg
rm -f ~/Documents/scan.jpg   # -f: an `rm -i` alias otherwise prompts and
                             # silently deletes nothing in a non-interactive shell
ls ~/Documents/scan.jpg      # must report "No such file or directory"
```

The source is deleted regardless of where it lived — inside the vault or outside
it, `~/Documents` and `~/Downloads` included. The note's assets folder becomes the
one copy of that image.

The only exception is an explicit user instruction to keep the original. Report
the leftover file when that happens, so the duplicate is a choice rather than a
side effect.

## Naming Moved Images

Renaming is part of every relocation, not just pasted-image cleanup. A
descriptive filename is part of the default mode, and it is the *only* place
image content belongs when transcription was not requested. Derive it from the
image's evident subject or the nearest heading: `needle-in-a-haystack.png`, not
`pasted-image-12.png`, `IMG_4821.jpg`, or `55LBackpackLockManual.jpg`. Keep it
kebab-case and collision-free within the assets folder.

The assets folder already carries the note's name, so do not repeat the note
name in the filename — describe what is *in* the image:
`Note.assets/password-lock-instructions.jpg`, not
`Note.assets/note-name-image-1.jpg`.

## Markdown Output (transcription mode)

Prefer a small heading followed by faithful text:

````markdown
![[Note.assets/screenshot-1.png]]

#### Image text

```text
Transcribed text exactly as read from the image.
```
````

Use normal Markdown instead of a `text` code block when the image content is
clearly a list, table, or prose that benefits from being editable:

```markdown
#### Image text

- First bullet from the screenshot
- Second bullet from the screenshot
```

For multiple images, repeat the block after each embed, or use headings such as
`#### Image 1 text` and `#### Image 2 text` when that is easier to scan.

## Removing Transcriptions

When asked to strip transcriptions an earlier run added, delete the whole block:
the `#### Image text` heading (or `#### Image N text`, `#### Image text — …`),
its body, and the blank line it introduced. Keep the embed and the surrounding
prose. Verify with `rg -n 'Image text' Note.md` returning nothing, and confirm
in the diff that only transcription lines were removed.

## Report What Changed

Every run ends with a short report to the user. The file moves are invisible
otherwise — the user sees a rendered note, not the filesystem.

The report is a bullet per image plus one closing line:

```markdown
- `~/Documents/55LBackpackLockManual.jpg` → `Note.assets/password-lock-instructions.jpg` (moved, source deleted)
- Transcribed its text into the note under `#### Image text`.

Verified: the asset exists and is non-empty, the wikilink resolves, and the
source path is gone.
```

Each bullet names the **source path**, the **destination path**, and whether the
source was deleted. State the transcription bullet only when transcription ran.
The closing line reports the Verification checks you actually ran.

Anything that did not go cleanly gets its own bullet: a source you were asked to
keep, a filename that collided, an image you left in place, text you could not
read reliably. A run with nothing to flag says so in the closing line and stops.

## Reliability Rules

- Keep the image embed unless the user explicitly asks to replace or delete it.
- Move images, never copy them. A run that leaves the source file in place has
  not finished — see the Move, Never Copy section.
- Never add transcription that was not requested. See the default rule above.
- When transcribing: stop without editing if material text cannot be read
  reliably; verify low-confidence OCR visually before writing.
- Preserve punctuation, numbers, product names, and mixed-language text when
  visible.
- Do not summarize unless the user asks for a summary. Transcribe first.
- Do not run the base64 extraction script unless the note actually contains
  inline `data:image/...;base64` content.

## Verification

After editing, check:

- each `![[...]]` image target created or changed exists and is non-empty,
- **every source path an image was moved from no longer exists** — `ls` it and
  confirm the "No such file or directory" error, rather than assuming the move
  or the delete succeeded,
- each moved image's filename describes its contents,
- the note no longer references any moved root-level pasted image path,
- `rg -n 'data:image' Note.md` is empty if base64 conversion was performed,
- **no transcription was added unless it was requested** — in default mode
  `git diff` should show link rewrites and layout fixes only,
- the inserted text appears in the expected section (transcription mode only),
- the git diff contains only the intended note and asset changes,
- the report described in Report What Changed was given to the user.
