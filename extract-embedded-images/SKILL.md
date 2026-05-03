---
name: extract-embedded-images
description: >-
  Use when a markdown note has embedded base64 image data URLs and the user wants
  them extracted into a sibling assets folder, with Obsidian wiki-link image
  embeds replacing reference-style inline images. Triggers on requests like
  "extract embedded images", "move base64 images to assets folder",
  "externalize base64 images", or "make this note easier to edit".
---

# Extract Embedded Images

Inverse of `convert-external-images`. Use when a markdown note has grown unwieldy because base64-encoded images were inlined into it, and the user wants those images moved back out to a sibling assets folder so the note becomes editable again.

## When to use

- The note ends with a long block of `[refname]: <data:image/png;base64,...>` definitions and the body uses `![alt][refname]` references to them.
- The user mentions Obsidian, OCI-style notes, `OCI.assets`, or comparing against other notes that use `![[X.assets/imageN.png]]` wiki-link image syntax.
- The user wants the note to load fast in an editor or to diff cleanly in git.

This skill is the preferred replacement for `convert-external-images` for users who have decided embedded base64 hurts more than it helps (slow editor load, large diffs, hard to swap an image).

## What it produces

Given `Foo.md` containing:

```markdown
Some text ![][image1] more text.

[image1]: <data:image/png;base64,iVBORw0KGgo...>
```

It produces:

- `Foo.assets/image1.png` (decoded bytes)
- `Foo.md` rewritten to:

```markdown
Some text ![[Foo.assets/image1.png]] more text.
```

The base64 reference definitions are removed from the note. Alt text other than empty/`image` is trimmed and preserved as an Obsidian alias: `![[Foo.assets/image1.png|my alt]]`.

## How to run

```bash
python scripts/extract_images.py /absolute/path/to/Note.md
```

Useful flags:

- `--dry-run` — show what would be written/rewritten without touching the note or filesystem.
- `--keep-defs` — leave the base64 reference definitions in place (rare; usually you want them gone).
- `--force` — overwrite existing extracted asset files. Without this, the script refuses to overwrite assets.
- `--assets-dir /custom/path` — override the default `<NoteBase>.assets` location. The custom directory must stay inside the note's directory so Obsidian wikilinks resolve reliably.

The script:

1. Scans for single-line `[ref]: <data:image/EXT;base64,DATA>` definitions outside fenced and inline code spans.
2. Validates each reference name is filename-safe, then decodes each image into `<NoteBase>.assets/<ref>.<ext>` (normalizing `jpeg`→`jpg`, `svg+xml`→`svg`).
3. Rewrites every matching reference-style image in the body to `![[<NoteBase>.assets/<ref>.<ext>]]` (or with a `|alt` alias when the alt is meaningful). Full (`![alt][ref]`), collapsed (`![ref][]`), and shortcut (`![ref]`) reference images are supported. Unknown refs are left unchanged and are not included in the rewritten count.
4. Removes the base64 reference definitions (unless `--keep-defs`).
5. Warns when embedded definitions have no matching inline `![...][ref]` use; it still extracts them so the image is not lost when definitions are removed.
6. **Verifies** every expected asset file exists and is non-empty *before* writing the cleaned note. If any decode failed, the note is left untouched so the original base64 is not lost.

## Safety notes

- The script never writes the note if any expected asset file is missing or empty after decode — the original base64 stays intact, so you can re-run.
- The script refuses to overwrite existing asset files unless `--force` is supplied. Use `--force` only when you intentionally want reruns to replace files in the assets folder.
- Custom `--assets-dir` values outside the note directory are rejected because parent-traversing Obsidian wikilinks are unreliable.
- Asset bytes are staged in temporary files before final renames, so decode/safety failures do not leave the note pointing at missing files.
- Unsafe reference names, duplicate definitions, malformed base64, empty decoded assets, and output filename collisions abort before writing the note.
- Use fenced code blocks for literal examples of embedded image syntax that should not be transformed.
- If final note writing fails after assets were staged, newly written assets are rolled back where possible.
- Run `--dry-run` first on unfamiliar notes to confirm the reference count looks right.
- If the user has the note open in Obsidian, ask them to close it first or expect Obsidian to reload it.
- If a reference is used inside a markdown link, e.g. `[![][image6]](https://...)`, the rewrite produces `[![[...]]](https://...)`. Obsidian renders this; other Markdown renderers may not. Mention this to the user only if such a case is present.

## Verifying after the run

After running, confirm:

- `ls <NoteBase>.assets/` shows the expected files.
- `grep -c 'data:image' Note.md` returns 0 (unless `--keep-defs` was used).
- The increase in `grep -c '!\[\[' Note.md` matches the script's reported rewrite count. If the note already had wikilinks, compare before/after counts instead of using the final total.

## Relationship to convert-external-images

This skill replaces `convert-external-images` for users who prefer external assets. Don't run both on the same note — they undo each other. The two skills exist because the right answer depends on whether portability (single-file note) or editability (small note + assets folder) matters more for a given workflow.
