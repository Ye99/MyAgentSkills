---
name: fill-luggage-tag
description: Use when filling printable luggage tag PDFs, cruise baggage tags, airline bag tags, or similar PDF/image-backed forms where blanks such as Name, Address, Telephone must be identified, user values collected, typed text overlaid, and original PDF page size and print scale preserved.
---

# Fill Luggage Tag

Fill a printable luggage tag PDF by adding typed answers on top of the existing tag. Preserve the input PDF's page geometry; never recreate, resize, or rasterize the page as the final output.

## Privacy Rule

Luggage tags usually contain PII. Do not add the user's input PDF, filled output PDF, rendered previews, answer JSON, or temp files to git. Keep generated artifacts in the skill-local ignored directory at `$SKILL_DIR/tmp/fill-luggage-tag/` unless the user explicitly names an output path. Before staging or committing this skill, verify no PDF or filled tag artifact is staged.

This skill includes a local `.gitignore` for common private/generated artifacts. Keep that file with the skill when copying or installing it elsewhere.

## Getting the Input PDF

If the user only has a browser page or printable tag page, ask them to create the input PDF first: open the tag page, choose Print, select the system PDF option, save the file locally, then provide the saved PDF path. Tell them not to use scaling options such as "fit to page"; use the default/actual-size print scale so the filled output keeps the same tag size.

- macOS: in the Print dialog, open the `PDF` menu and choose `Save as PDF`. Video walkthrough: [Print To PDF in MacOS](https://www.youtube.com/watch?v=BbyHxnOR9ZY).
- Windows: in the Print dialog, choose `Microsoft Print to PDF`, then click `Print` and save the file. Video walkthrough: [How to Save a Web Page as Pdf in Windows 11](https://www.youtube.com/watch?v=jI8frQAzzFI).
- Linux: in the Print dialog, choose `Print to File` or `Save to File`, select PDF, then save the file. Video walkthrough: [How to print to PDF in Ubuntu 18.04](https://www.youtube.com/watch?v=Qm6PqrBM_08).

## Workflow

1. Ask the user for the input PDF path if they have not provided it.
2. Create the working directory:
   ```bash
   SKILL_DIR="/absolute/path/to/fill-luggage-tag"
   WORK_DIR="$SKILL_DIR/tmp/fill-luggage-tag"
   OUTPUT_PDF="$WORK_DIR/FilledLuggageTag.pdf"
   mkdir -p "$WORK_DIR"
   ```
3. Inspect the PDF:
   ```bash
   SCRIPT="$SKILL_DIR/scripts/fill_luggage_tag.py"
   python "$SCRIPT" inspect "$INPUT_PDF" \
     --fields-json "$WORK_DIR/fields.json"
   ```
4. Identify fillable blanks from the inspection output and rendered page. Treat `fields.json` as a starting point, not final truth: add one field coordinate for every visible blank line that should receive text, especially repeated `Address` lines. If text-layer inspection misses labels because the tag artwork is an image, render the PDF and visually inspect the blanks:
   ```bash
   XDG_CACHE_HOME="$WORK_DIR/fontconfig-cache" \
   pdftoppm -png -r 150 "$INPUT_PDF" "$WORK_DIR/input"
   ```
5. Ask the user for each blank value. Ask only for fields visible on the tag, commonly `Name`, `Address` lines, and `Telephone`.
6. Create `$WORK_DIR/answers.json` with the user's answers. For repeated labels such as address lines, use a list:
   ```json
   {
     "Name": "Example Traveler",
     "Address": ["123 Example Ave", "Sample City ST 12345"],
     "Telephone": "555 010 2222"
   }
   ```
7. Fill the PDF:
   ```bash
   python "$SCRIPT" fill "$INPUT_PDF" "$OUTPUT_PDF" \
     --fields-json "$WORK_DIR/fields.json" \
     --answers-json "$WORK_DIR/answers.json"
   ```
   `$OUTPUT_PDF` must differ from `$INPUT_PDF`; never overwrite the original tag. If the command reports a rotated PDF, ask the user to print/save the tag again with rotation 0 before filling.
8. Verify page count, page size, rotation, expected text, and rendered visual placement before delivering. Treat any non-zero verify exit as a stop signal:
   ```bash
   python "$SCRIPT" verify "$INPUT_PDF" "$OUTPUT_PDF" \
     --expect-text "Example Traveler" \
     --expect-text "123 Example Ave" \
     --expect-text "Sample City ST 12345" \
     --expect-text "555 010 2222"
   pdfinfo "$INPUT_PDF"
   pdfinfo "$OUTPUT_PDF"
   ```

## Field Coordinates

`fields.json` is an array, or an object with a `fields` array. Coordinates are PDF points measured from the bottom-left corner of the page:

```json
[
  {"label": "Name", "page": 0, "x": 232, "y": 371.2, "font_size": 9.6},
  {"label": "Address", "page": 0, "x": 240, "y": 357.3, "font_size": 9.6},
  {"label": "Address", "page": 0, "x": 240, "y": 343.8, "font_size": 9.6},
  {"label": "Telephone", "page": 0, "x": 246, "y": 316.6, "font_size": 9.6}
]
```

If deriving positions from a rendered PNG, convert pixels to PDF points:

```text
x_points = x_pixels * page_width_points / render_width_pixels
y_points = page_height_points - (y_pixels * page_height_points / render_height_pixels)
```

These formulas assume a zero-origin page with `CropBox` equal to `MediaBox`. The helper rejects PDFs where those boxes differ or where either box has a non-zero lower-left origin; ask the user to print/save the tag again as a plain PDF before filling.

Use `max_width` on a field when long text must shrink to fit a line:

```json
{"label": "Address", "page": 0, "x": 240, "y": 357.3, "font_size": 9.6, "max_width": 250}
```

The default overlay font is `Helvetica-Bold` at `9.6` points, a ReportLab base PDF font. Use `9.6` points for hand-measured fields unless visual QA shows the text must shrink to fit a specific line. The helper rejects characters that this font would draw with fallback glyphs, such as many CJK/Cyrillic names and characters such as U+0141. If the tag must contain broader Unicode text, configure an embedded Unicode font before filling.

## Scale Preservation Checks

The output PDF must keep the same page count, media box, crop box, rotation, and visual tag size as the input. The helper overlays vector text with `pypdf` and `reportlab`; it does not resize the page. If verification shows a different page size, crop box, or page count, discard the output and fix the field/merge process before responding.

The helper rejects input PDFs with non-zero `/Rotate` values because visible coordinates and PDF merge coordinates can otherwise disagree. If a source PDF is rotated, ask the user to reprint/save it with rotation 0 or normalize it before filling.

When rendering for QA, render input and output at the same DPI and compare dimensions. For US letter at 150 DPI, expect `1275 x 1650` pixels.

## Dependencies

Use the bundled Codex workspace Python when available because it includes `reportlab`, `pypdf`, `pdfplumber`, and Pillow. Use Poppler's `pdfinfo` and `pdftoppm` for page geometry and visual review. If any are missing, install only the missing dependency after confirming it is needed.

## Common Mistakes

- Do not use the source GIF or artwork image if the user provides a printed PDF with cruise/stateroom/date overlays; use the PDF as input.
- Do not assume the blanks are only `Name`, `Address`, and `Telephone`; inspect the actual tag.
- Do not paste PII values into committed tests, README examples, or skill fixtures.
- Do not rely only on text extraction. Image-backed labels require rendered visual inspection.
- Do not claim the file is ready until the latest verification confirms unchanged page geometry and readable text placement.
