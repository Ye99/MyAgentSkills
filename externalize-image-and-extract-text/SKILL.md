---
name: externalize-image-and-extract-text
description: >-
  Use when a Markdown or Obsidian note has an embedded, pasted, root-level, or
  already-external image and the user wants the image kept or moved to assets
  while the visible image text is extracted into the note. Trigger on OCR,
  transcribe screenshot, extract text from image, or add image text during
  image conversion requests.
---

# Externalize Image And Extract Text

Use this skill when the user wants two outcomes in the same note workflow:

- the image remains available as an external resource or Obsidian embed, and
- the image's visible text is added as editable Markdown near that embed.

This is an orchestration skill. It chooses whether to run base64 extraction
first, then performs careful OCR or visual transcription and note edits.

## Choose the Right Skill

| Scenario | Use |
| --- | --- |
| Note contains `[ref]: <data:image/...;base64,...>` and the user only wants assets extracted | `extract-embedded-images` |
| Note embeds an image and the user wants the image replaced by editable Markdown | `rewrite-obsidian-image-notes` |
| User wants to keep/externalize the image and also add its text to the note | this skill |

If the prompt mentions "also extract text", "OCR", "transcribe the screenshot",
"put the image text into the note", or "during conversion", prefer this skill
over the narrower extraction or rewrite skills.

## Workflow

1. Locate the note and target image references with `rg`.
2. Inspect surrounding note context to choose heading level, placement, and
   whether the transcription should be a code block, bullets, table, or prose.
3. Normalize image storage only when needed:
   - If the note contains base64 `data:image` reference definitions, run
     `extract-embedded-images` first. Use `--dry-run` on unfamiliar notes, then
     run the conversion and verify assets exist.
   - If the note embeds a root-level pasted image such as
     `![[Pasted image 20260517214504.png]]`, move it into
     `<NoteBase>.assets/` with a non-colliding descriptive filename, then update
     the wikilink.
   - If the image is already in a suitable assets folder, leave it in place.
4. View each image at original detail. Use OCR tooling when available; otherwise
   visually transcribe only text that can be read reliably.
5. Verify the extracted text against the image. Do not invent missing words,
   table cells, labels, or numbers.
6. Insert the extracted text immediately after the relevant image embed unless
   surrounding context clearly calls for another placement.
7. Verify the note points to existing non-empty assets and that the transcription
   is present in the note.

## Markdown Output

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

## Reliability Rules

- Keep the image embed unless the user explicitly asks to replace or delete it.
- Stop without editing if material text cannot be read reliably.
- If OCR output is incomplete or low confidence, verify visually before writing.
- Preserve punctuation, numbers, product names, and mixed-language text when
  visible.
- Do not summarize unless the user asks for a summary. Transcribe first.
- Do not run the base64 extraction script unless the note actually contains
  inline `data:image/...;base64` content.

## Verification

After editing, check:

- each `![[...]]` image target created or changed exists and is non-empty,
- the note no longer references any moved root-level pasted image path,
- `rg -n 'data:image' Note.md` is empty if base64 conversion was performed,
- the inserted text appears in the expected section,
- the git diff contains only the intended note and asset changes.
