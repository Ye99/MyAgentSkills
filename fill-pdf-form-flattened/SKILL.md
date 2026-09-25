---
name: fill-pdf-form-flattened
description: Use when filling out any PDF form for someone else to receive — applications, government or election forms, school, medical, HR, or insurance forms — especially when the result will be emailed, uploaded, or opened in Chrome, Edge, Firefox, or a phone. Writes values into the page content so the filled form never shows up blank in browser PDF viewers, and verifies it. Also use when a filled PDF "looks empty" in a browser but shows text in a desktop viewer, or before sending a PDF someone else filled with annotations.
---

# Fill PDF Form (Flattened)

Fill a PDF form so the values are part of the page itself, then prove it before handing the file over.

## Why this exists

A form filled with FreeText annotations that lack an `/AP` appearance stream looks fine in Poppler-based Linux viewers (Evince, Okular, Papers) but shows **blank** in Chrome, Edge, and Firefox, which only draw annotations that carry their own appearance. The same trap exists for AcroForm fields filled with `/NeedAppearances true`. The recipient, such as an elections office or HR, usually opens attachments in a browser, so the user ends up sending an empty form without knowing it.

The fix is to draw the values as vector text in each page's content stream ("flattening"). Every viewer, printer, and text extractor then sees them. The trade-off is that the values are no longer editable fields, which is what you want for a submitted form.

## Workflow

`SCRIPT` is `scripts/fill_pdf_flat.py` in this skill. It needs `pypdf` and `reportlab`. If they are missing, install them in a venv in the scratchpad rather than system-wide.

1. **Find field positions.** Use the Anthropic `pdf` skill's FORMS.md to locate fields: `check_fillable_fields.py`, then `extract_form_structure.py` for labels, lines, and checkbox rectangles, plus a rendered page image. Build `fields.json` in that skill's format (`pages[].pdf_width/pdf_height`, `form_fields[].entry_bounding_box` = `[x0, top, x1, bottom]` from the top-left). Use the checkbox rectangles as they are. The script centers single-character values such as `X` in their box, so don't hand-nudge them.
   - If the PDF has real fillable AcroForm fields, filling those fields is fine. Flatten afterwards, for example with `writer.update_page_form_field_values(page, values, flatten=True)` in pypdf, and still run step 3. A fillable form filled with `NeedAppearances` alone fails verification for the reason above.
2. **Fill into page content.** Never use `fill_pdf_form_with_annotations.py` for a deliverable; that script is what produces the blank-in-browser file.
   ```bash
   python "$SCRIPT" fill input.pdf fields.json output.pdf
   ```
   The output path must differ from the input. Keep the user's blank original.
3. **Verify.** A non-zero exit means don't deliver:
   ```bash
   python "$SCRIPT" verify output.pdf --input input.pdf \
     --expect-text "Jane Q Doe" --expect-text "01/02/2030"
   ```
   It fails on any annotation without an `/AP` stream, on `/NeedAppearances`, on changed page count or size, and on expected values missing from the page content. Page content is exactly what a browser is guaranteed to draw. Pass two or three distinctive values, such as the name, a date, and an email.
4. **Look at it.** Render with `pdftoppm -r 90 -png -singlefile output.pdf preview`, then read the image. Check that every value sits on its line and that each X sits inside its box. Fix `fields.json` and repeat steps 2–4 if needed.

## Checking a PDF someone already filled

If the user asks why a filled PDF is blank in the browser, or wants to check one before sending, run only `verify` on it. If it reports annotations without `/AP`, rebuild the file with this workflow from the blank original. To make it render everywhere, the values have to go into the page content.

## Limits

- The overlay font is base-14 Helvetica, which covers Latin-1/cp1252 only. The script refuses CJK, Cyrillic, and other characters rather than drawing empty boxes. For those, register an embedded TTF with reportlab first.
- Pages with `/Rotate` or a non-zero MediaBox origin are refused, because the coordinates would land in the wrong place. Save the PDF unrotated (print to PDF) first.
- Text that is too wide shrinks to fit its box, down to a minimum of 4 pt. If it shrinks a lot, widen the box instead.

## Privacy

Filled forms carry PII. Keep `fields.json`, renders, and outputs out of git: this skill's `.gitignore` covers them. Never put real values in tests or examples.
