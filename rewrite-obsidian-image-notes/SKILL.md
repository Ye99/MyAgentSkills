---
name: rewrite-obsidian-image-notes
description: >-
  Use when an Obsidian Markdown note embeds a pasted image that primarily
  contains text, tables, lists, slides, diagrams, or screenshots, and the user
  wants the note rewritten so the image content becomes editable Markdown.
---

# Rewrite Obsidian Image Notes

Use this skill when a note relies on an embedded screenshot or pasted image for
important content and the user wants that content represented directly in the
Markdown note.

## Workflow

1. Find the note that embeds the target image with `rg`.
2. Inspect the surrounding note context so the replacement matches the note's
   tone, heading level, and indentation style.
3. View the image at original detail. If text is dense or small, use OCR when
   available, then verify the result visually against the image.
   - If OCR is unavailable, incomplete, low confidence, or cannot be verified
     visually, stop and report an error to the user.
   - Do not proceed with a rewrite when any material text, table cell, label,
     diagram node, or list item cannot be read reliably.
4. Replace the image embed with concise Markdown:
   - Use headings only when the surrounding note already needs them.
   - Preserve ordered list structure when the source image has numbered items.
   - Convert slide bullets into Markdown bullets.
   - Expand abbreviations only when the image or surrounding note makes the
     expansion clear.
   - Keep Obsidian wikilinks and embeds valid if any remain.
5. Leave the source image file in place unless the user explicitly asks to
   delete or move it. Other notes may still reference it.
6. Verify the note no longer references the target image and that the rewritten
   section is readable as plain Markdown.

## Failure behavior

If the image cannot be reliably transcribed, do not modify the note. Report:

- the image path,
- the note path,
- what prevented reliable OCR or visual verification,
- whether any partial transcription was produced but intentionally not applied.

Keep the original image embed intact so there is no silent data loss.

## Safety

- Do not invent missing text. Uncertain source content is a blocking error, not
  a reason to rewrite with `(unclear)` placeholders.
- Do not summarize away details when the image is being used as source content;
  first transcribe faithfully, then lightly edit for readability.
- Do not remove or replace the image embed unless every meaningful part of the
  image has been captured in Markdown.
- Do not run image extraction or base64 conversion skills unless the note
  actually contains inline `data:image/...;base64` content.
