#!/usr/bin/env python3
"""Fill a PDF form by drawing text into the page content, and verify the result.

Text written as FreeText annotations without an /AP appearance stream shows in
Poppler-based Linux viewers but is blank in Chrome, Edge, and Firefox. This
helper merges a vector text overlay into each page's content stream instead,
so every viewer, printer, and text extractor sees the values.

fields.json uses the same shape as the Anthropic pdf skill's non-fillable flow:

    {
      "pages": [{"page_number": 1, "pdf_width": 612, "pdf_height": 792}],
      "form_fields": [
        {"page_number": 1, "description": "Name",
         "entry_bounding_box": [37, 206, 576, 219],
         "entry_text": {"text": "Jane Doe", "font_size": 10}}
      ]
    }

Boxes are [x0, top, x1, bottom] measured from the page's top-left corner, in
PDF points (pages[].pdf_width/pdf_height) or in rendered-image pixels
(pages[].image_width/image_height). Single-character values such as "X" are
centered in their box by default, which suits checkboxes; set
entry_text.align to "left" or "center" to override.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

FONT = "Helvetica"
DEFAULT_FONT_SIZE = 10
# Annotation subtypes that never need an appearance stream to be useful.
AP_EXEMPT = {"/Link", "/Popup"}


class FillError(Exception):
    pass


def load_fields(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or "form_fields" not in data:
        raise FillError("fields.json must be an object with a 'form_fields' array")
    return data


def _page_box(page: Any) -> tuple[float, float, float, float]:
    return tuple(float(v) for v in page.mediabox)  # type: ignore[return-value]


def _check_page_geometry(page: Any, number: int) -> None:
    rotation = int(page.get("/Rotate", 0) or 0) % 360
    if rotation:
        raise FillError(f"page {number} has /Rotate {rotation}; save it unrotated before filling")
    x0, y0, _, _ = _page_box(page)
    if x0 or y0:
        raise FillError(f"page {number} MediaBox origin is ({x0}, {y0}); expected (0, 0)")


def _scale_for(page_info: dict[str, Any], width: float, height: float) -> tuple[float, float]:
    if "pdf_width" in page_info:
        return width / float(page_info["pdf_width"]), height / float(page_info["pdf_height"])
    if "image_width" in page_info:
        return width / float(page_info["image_width"]), height / float(page_info["image_height"])
    raise FillError(
        f"page {page_info.get('page_number')} needs pdf_width/pdf_height or image_width/image_height"
    )


def _check_encodable(text: str, description: str) -> None:
    # Helvetica is a base-14 font with WinAnsi encoding; other characters render as boxes.
    try:
        text.encode("cp1252")
    except UnicodeEncodeError as exc:
        bad = text[exc.start : exc.end]
        raise FillError(
            f"field '{description}' contains {bad!r}, which base-14 {FONT} cannot draw; "
            "register an embedded Unicode TTF font before filling"
        ) from None


def _draw_field(c: canvas.Canvas, field: dict[str, Any], sx: float, sy: float, height: float) -> None:
    entry = field.get("entry_text") or {}
    text = str(entry.get("text", ""))
    if not text:
        return
    description = field.get("description", field.get("field_label", "?"))
    _check_encodable(text, description)

    x0, top, x1, bottom = field["entry_bounding_box"]
    x0, x1 = x0 * sx, x1 * sx
    top, bottom = top * sy, bottom * sy
    box_w = x1 - x0

    size = float(entry.get("font_size", DEFAULT_FONT_SIZE))
    while size > 4 and stringWidth(text, FONT, size) > box_w - 2:
        size -= 0.25
    width = stringWidth(text, FONT, size)
    align = entry.get("align") or ("center" if len(text) == 1 else "left")

    c.setFont(FONT, size)
    if align == "center":
        x = (x0 + x1) / 2 - width / 2
        # Helvetica cap height is ~0.72 em; center caps vertically in the box.
        baseline = height - (top + bottom) / 2 - size * 0.36
    else:
        x = x0 + 1
        baseline = height - top - size * 0.95
    c.drawString(x, baseline, text)


def fill_pdf(input_pdf: str | Path, fields_json: str | Path, output_pdf: str | Path) -> int:
    if Path(input_pdf).resolve() == Path(output_pdf).resolve():
        raise FillError("output path must differ from the input; never overwrite the original form")
    data = load_fields(fields_json)
    reader = PdfReader(str(input_pdf))
    page_infos = {int(p["page_number"]): p for p in data.get("pages", [])}

    by_page: dict[int, list[dict[str, Any]]] = {}
    for field in data["form_fields"]:
        by_page.setdefault(int(field["page_number"]), []).append(field)
    for number in by_page:
        if not 1 <= number <= len(reader.pages):
            raise FillError(f"field references page {number}; PDF has {len(reader.pages)} pages")
        if number not in page_infos:
            raise FillError(f"fields.json 'pages' has no entry for page {number}")

    writer = PdfWriter(clone_from=reader)
    drawn = 0
    for number, page in enumerate(writer.pages, start=1):
        fields = by_page.get(number)
        if fields:
            _check_page_geometry(page, number)
            _, _, width, height = _page_box(page)
            sx, sy = _scale_for(page_infos[number], width, height)
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(width, height))
            for field in fields:
                _draw_field(c, field, sx, sy, height)
                drawn += 1
            c.save()
            buf.seek(0)
            page.merge_page(PdfReader(buf).pages[0])

    with open(output_pdf, "wb") as f:
        writer.write(f)
    return drawn


def verify_pdf(
    output_pdf: str | Path,
    input_pdf: str | Path | None = None,
    expect_text: list[str] | None = None,
) -> list[str]:
    """Return a list of problems that would make the PDF look wrong in some viewer."""
    problems: list[str] = []
    out = PdfReader(str(output_pdf))

    if input_pdf is not None:
        src = PdfReader(str(input_pdf))
        if len(src.pages) != len(out.pages):
            problems.append(f"page count changed: {len(src.pages)} -> {len(out.pages)}")
        for i, (a, b) in enumerate(zip(src.pages, out.pages), start=1):
            if _page_box(a) != _page_box(b):
                problems.append(f"page {i} MediaBox changed: {_page_box(a)} -> {_page_box(b)}")

    for i, page in enumerate(out.pages, start=1):
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            subtype = annot.get("/Subtype")
            if subtype in AP_EXEMPT or "/AP" in annot:
                continue
            label = annot.get("/Contents") or annot.get("/T") or ""
            problems.append(
                f"page {i}: {subtype} annotation {label!r} has no /AP appearance stream "
                "(blank in browser PDF viewers)"
            )

    root = out.trailer["/Root"]
    acroform = root.get("/AcroForm")
    if acroform is not None and bool(acroform.get_object().get("/NeedAppearances", False)):
        problems.append(
            "AcroForm sets /NeedAppearances; browsers may not regenerate field "
            "appearances, so flatten the fields instead"
        )

    if expect_text:
        # extract_text reads page content streams only, never annotations, which
        # is the same content a browser viewer is guaranteed to draw.
        content = "\n".join(page.extract_text() or "" for page in out.pages)
        squashed = " ".join(content.split())
        for text in expect_text:
            if " ".join(text.split()) not in squashed:
                problems.append(f"expected text not in page content: {text!r}")
    return problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    fill_cmd = sub.add_parser("fill", help="Draw field values into page content")
    fill_cmd.add_argument("input_pdf")
    fill_cmd.add_argument("fields_json")
    fill_cmd.add_argument("output_pdf")

    verify_cmd = sub.add_parser("verify", help="Check a filled PDF renders in every viewer")
    verify_cmd.add_argument("output_pdf")
    verify_cmd.add_argument("--input", dest="input_pdf", help="Original PDF, to compare page geometry")
    verify_cmd.add_argument("--expect-text", action="append", default=[], help="Value that must be in page content")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "fill":
            drawn = fill_pdf(args.input_pdf, args.fields_json, args.output_pdf)
            print(f"Drew {drawn} fields into page content: {args.output_pdf}")
            return 0
        problems = verify_pdf(args.output_pdf, args.input_pdf, args.expect_text)
    except FillError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for problem in problems:
        print(f"FAIL: {problem}")
    if problems:
        return 1
    print("OK: all values are in page content; no annotations depend on viewer-generated appearances")
    return 0


if __name__ == "__main__":
    sys.exit(main())
