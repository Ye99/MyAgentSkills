---
name: deskew-crop-scanned-document
description: Use when a scanned passport, ID card, certificate, receipt, or other printed page image is tilted, needs straightening, rotating upright, or cropping to remove scanner background or white borders, and the result must keep its original print size (DPI) and not grow much larger in file size than the scan.
---

# Deskew and Crop a Scanned Document

Straighten a scan by levelling its **printed text lines**, crop to the document's real edges, and save **one JPG** at the scan's DPI, quality and chroma subsampling, never larger than the source.

## Why Text Lines, Not Page Edges

Booklets (passports) and curled paper lie at slightly different angles per page, and the page edge is often rounded or shadowed. Levelling an edge, or the whole page at once, leaves the main text visibly tilted (e.g. 0.98° edge vs 0.70° true). Fit baselines of **long machine-printed lines** — a passport/ID MRZ, a table row, a paragraph — on the page the user cares about. The tool reports one angle per line; two or more lines must agree within ~0.1°.

## Privacy

Scans of IDs are PII. Keep previews in a scratch/temp directory, write output next to the source (never overwrite it), and never copy scans, previews, names, or document numbers into this skill, git, or notes.

## Workflow

`T="python3 $SKILL_DIR/scripts/deskew_crop.py"`, `PV=<scratch dir>`.

1. **Size/DPI:** `$T info scan.jpg`. DPI comes from the JPEG, else a sibling scanner PDF of the same name. If `null`, ask the user (scanners usually use 300/600/1200: `width_px / dpi` should equal the scanned paper width, e.g. 8.5 in letter, 8.27 in A4). Never guess silently.
2. **Look:** `$T grid scan.jpg $PV/g.png` and view it. Gridlines are labelled in full-resolution pixels. If the document is upside down or sideways (not a small tilt), rotate by 90/180/270 first and use that image.
3. **Angle:** pick a region around 2+ long printed lines on the key page (fully inside, not cut at the region edge):
   `$T angle scan.jpg --region x0,y0,x1,y1` → per-line angles + `median` + `spread`. Use `median` if `spread` ≤ 0.1; else tighten the region to cleaner lines. Verify: rerun with `--angle <median>` → median ≈ 0 (±0.05).
4. **Crop box:** `$T grid scan.jpg $PV/r.png --angle A` and read a rough box at the document's outer edges, then `$T edges scan.jpg --angle A --rough x0,y0,x1,y1`. A side with `step` < ~8 is low-contrast — inspect it with `grid --angle A --region` zoomed on that side and set it by eye. Include every page (lower and upper pages may not align; take the outermost edge of each side). Exclude scanner background, shadows, and lid/cover rim.
5. **Apply:** `$T apply scan.jpg scan_cropped.jpg --angle A --box x0,y0,x1,y1 --preview-dir $PV [--dpi N]`.
6. **Verify by viewing** `$PV/*_corners.jpg` and `*_whole.jpg`: no clipped document edge in any corner, no background strip, text level. Report `size_in` (physical print size), `bytes` vs `source_bytes`, and `quality`.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Angle from the page edge or the whole page | Fit printed text lines on the key page |
| One page's text level, other page still tilted | Expected for booklets; level the page the user cares about and say so |
| Crop box from one page's edges | Outermost edge of all pages per side; check every corner preview |
| Saving at quality 95 / no subsampling | `apply` matches source quality and subsampling, capped at source bytes |
| Resizing or writing no DPI | Never resample; `apply` keeps pixels 1:1 and writes the DPI, so print size = px / DPI |
| Extra outputs (PDF, PNG) | One JPG unless the user asks |
