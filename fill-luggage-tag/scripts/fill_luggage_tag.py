#!/usr/bin/env python3
"""Inspect and fill printable luggage-tag PDFs without changing page scale."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Any

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase.pdfmetrics import getFont, stringWidth, unicode2T1
from reportlab.pdfgen import canvas


DEFAULT_FONT = "Helvetica-Bold"
DEFAULT_FONT_SIZE = 9.5
BOX_TOLERANCE = 0.001


def normalize_label(text: str) -> str:
    return text.strip().rstrip(":").strip()


def inspect_pdf(input_pdf: str | Path) -> dict[str, Any]:
    """Return page geometry and likely fillable text labels from a PDF text layer."""
    input_pdf = Path(input_pdf)
    fields: list[dict[str, Any]] = []
    pages: list[dict[str, float | int]] = []

    with pdfplumber.open(input_pdf) as pdf:
        for page_index, page in enumerate(pdf.pages):
            pages.append(
                {
                    "page": page_index,
                    "width": round(float(page.width), 3),
                    "height": round(float(page.height), 3),
                }
            )
            for word in page.extract_words(x_tolerance=1, y_tolerance=3):
                text = word["text"].strip()
                if not text.endswith(":"):
                    continue
                label = normalize_label(text)
                if not label or not any(ch.isalpha() for ch in label):
                    continue
                fields.append(
                    {
                        "label": label,
                        "page": page_index,
                        "x": round(float(word["x1"]) + 8.0, 3),
                        "y": round(float(page.height) - float(word["bottom"]) + 1.0, 3),
                        "font_size": DEFAULT_FONT_SIZE,
                    }
                )

    fields.sort(key=lambda f: (f["page"], -float(f["y"]), float(f["x"]), f["label"]))
    return {
        "pages": pages,
        "fields": fields,
        "warnings": _input_geometry_warnings(PdfReader(str(input_pdf))),
    }


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(data: Any, path: str | Path | None = None) -> None:
    text = json.dumps(data, indent=2, sort_keys=False)
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def coerce_fields(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        data = data.get("fields", data)
    if not isinstance(data, list):
        raise ValueError("Fields JSON must be a list or an object with a 'fields' list")
    fields = []
    for index, field in enumerate(data):
        if not isinstance(field, dict):
            raise ValueError(f"Field {index} must be an object")
        for key in ("label", "page", "x", "y"):
            if key not in field:
                raise ValueError(f"Field {index} is missing '{key}'")
        fields.append(field)
    return fields


def _answer_for(label: str, answers: dict[str, Any], counters: dict[str, int]) -> str:
    if label not in answers:
        raise ValueError(f"Missing answer for field '{label}'")

    value = answers[label]
    if isinstance(value, list):
        answer_index = counters[label]
        counters[label] += 1
        if answer_index >= len(value):
            raise ValueError(f"Missing answer line {answer_index + 1} for field '{label}'")
        return str(value[answer_index])

    counters[label] += 1
    if counters[label] > 1:
        raise ValueError(
            f"Field '{label}' appears multiple times; provide a list of answers for repeated fields"
        )
    return str(value)


def _fit_font_size(text: str, font_name: str, size: float, max_width: float | None) -> float:
    if not max_width or not text:
        return size
    current = size
    while current > 5 and stringWidth(text, font_name, current) > max_width:
        current -= 0.5
    if stringWidth(text, font_name, current) > max_width:
        raise ValueError(f"Text does not fit within max_width {max_width}: {text!r}")
    return current


def _unsupported_characters_for_font(text: str, font_name: str) -> list[str]:
    if not text:
        return []
    font = getFont(font_name)
    if getattr(font, "_dynamicFont", 0) or getattr(font, "_multiByte", 0):
        return []

    unsupported: list[str] = []
    cursor = 0
    for fragment_font, encoded in unicode2T1(text, [font]):
        fragment_text = text[cursor : cursor + len(encoded)]
        if fragment_font.fontName != font.fontName:
            unsupported.extend(fragment_text)
        cursor += len(encoded)
    return unsupported


def _format_characters(characters: list[str]) -> str:
    unique_characters = list(dict.fromkeys(characters))
    return ", ".join(
        f"{character!r} (U+{ord(character):04X})" for character in unique_characters
    )


def _page_rotation(page: Any) -> int:
    return int(getattr(page, "rotation", 0) or 0) % 360


def _box_tuple(box: Any) -> tuple[float, float, float, float]:
    values = tuple(float(value) for value in box)
    if len(values) != 4:
        raise ValueError(f"Expected a 4-value PDF page box, got {values!r}")
    return values


def _box_values_match(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return all(
        abs(left_value - right_value) <= BOX_TOLERANCE
        for left_value, right_value in zip(left, right)
    )


def _format_box(box: tuple[float, float, float, float]) -> str:
    return "[" + ", ".join(f"{value:g}" for value in box) + "]"


def _page_box_issues(page: Any) -> list[str]:
    media_box = _box_tuple(page.mediabox)
    crop_box = _box_tuple(page.cropbox)
    issues: list[str] = []
    if not _box_values_match(media_box, crop_box):
        issues.append(
            f"CropBox {_format_box(crop_box)} differs from MediaBox {_format_box(media_box)}"
        )
    for box_name, box in (("MediaBox", media_box), ("CropBox", crop_box)):
        if abs(box[0]) > BOX_TOLERANCE or abs(box[1]) > BOX_TOLERANCE:
            issues.append(
                f"{box_name} lower-left origin is ({box[0]:g}, {box[1]:g}), not (0, 0)"
            )
    return issues


def _rotated_pages(reader: PdfReader) -> list[tuple[int, int]]:
    return [
        (page_index, rotation)
        for page_index, page in enumerate(reader.pages)
        if (rotation := _page_rotation(page)) != 0
    ]


def _unsupported_page_boxes(reader: PdfReader) -> list[tuple[int, list[str]]]:
    return [
        (page_index, issues)
        for page_index, page in enumerate(reader.pages)
        if (issues := _page_box_issues(page))
    ]


def _input_geometry_warnings(reader: PdfReader) -> list[str]:
    warnings: list[str] = []
    for page_index, rotation in _rotated_pages(reader):
        warnings.append(
            f"Page {page_index + 1} has /Rotate {rotation}; fill will reject this PDF."
        )
    for page_index, issues in _unsupported_page_boxes(reader):
        warnings.append(
            f"Page {page_index + 1} has unsupported page box geometry "
            f"({'; '.join(issues)}); fill will reject this PDF."
        )
    return warnings


def fill_pdf(
    input_pdf: str | Path,
    output_pdf: str | Path,
    fields: list[dict[str, Any]],
    answers: dict[str, Any],
) -> None:
    """Overlay answers onto a PDF while preserving each page's original media box."""
    input_pdf = Path(input_pdf)
    output_pdf = Path(output_pdf)
    if input_pdf.resolve() == output_pdf.resolve():
        raise ValueError("Input and output PDF paths must differ")
    fields = coerce_fields(fields)

    reader = PdfReader(str(input_pdf))
    rotated_pages = _rotated_pages(reader)
    if rotated_pages:
        pages = ", ".join(
            f"{page_index + 1} (/Rotate {rotation})"
            for page_index, rotation in rotated_pages
        )
        raise ValueError(
            "Rotated PDFs are not supported; print or normalize the PDF with rotation 0 "
            f"before filling. Rotated page(s): {pages}"
        )
    unsupported_page_boxes = _unsupported_page_boxes(reader)
    if unsupported_page_boxes:
        pages = "; ".join(
            f"page {page_index + 1}: {', '.join(issues)}"
            for page_index, issues in unsupported_page_boxes
        )
        raise ValueError(
            "Unsupported PDF page box geometry; use a PDF with CropBox equal to "
            "MediaBox and lower-left origin (0, 0) before filling. "
            f"Unsupported page(s): {pages}"
        )

    writer = PdfWriter(clone_from=str(input_pdf))
    fields_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    counters: dict[str, int] = defaultdict(int)
    for field in fields:
        page_index = int(field["page"])
        if page_index < 0 or page_index >= len(reader.pages):
            raise ValueError(f"Field '{field['label']}' references missing page {page_index}")
        assigned_field = dict(field)
        assigned_field["text"] = _answer_for(str(field["label"]), answers, counters)
        fields_by_page[page_index].append(assigned_field)

    field_labels = {str(field["label"]) for field in fields}
    for page_index, page in enumerate(writer.pages):
        page_fields = fields_by_page.get(page_index, [])
        if page_fields:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            packet = BytesIO()
            overlay_canvas = canvas.Canvas(packet, pagesize=(width, height))
            for field in page_fields:
                text = str(field["text"])
                font_name = str(field.get("font", DEFAULT_FONT))
                font_size = float(field.get("font_size", DEFAULT_FONT_SIZE))
                unsupported_characters = _unsupported_characters_for_font(text, font_name)
                if unsupported_characters:
                    raise ValueError(
                        f"Field '{field['label']}' contains characters "
                        f"{_format_characters(unsupported_characters)} that font "
                        f"'{font_name}' cannot render; use text supported by the "
                        "default PDF font or configure an embedded Unicode font."
                    )
                font_size = _fit_font_size(
                    text,
                    font_name,
                    font_size,
                    float(field["max_width"]) if "max_width" in field else None,
                )
                overlay_canvas.setFont(font_name, font_size)
                overlay_canvas.drawString(float(field["x"]), float(field["y"]), text)
            overlay_canvas.save()
            packet.seek(0)
            overlay = PdfReader(packet)
            page.merge_page(overlay.pages[0])

    for label, value in answers.items():
        if label not in field_labels:
            raise ValueError(f"Unused answer for unknown field '{label}'")
        if isinstance(value, list) and counters[label] < len(value):
            raise ValueError(
                f"Unused answer line {counters[label] + 1} for field '{label}'; "
                "add another field coordinate or remove the extra answer"
            )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as f:
        writer.write(f)


def verify_pdf(
    input_pdf: str | Path,
    output_pdf: str | Path,
    expected_text: list[str],
) -> dict[str, Any]:
    before = PdfReader(str(input_pdf))
    after = PdfReader(str(output_pdf))
    result: dict[str, Any] = {
        "input_pages": len(before.pages),
        "output_pages": len(after.pages),
        "page_sizes_match": [],
        "page_cropboxes_match": [],
        "page_rotations_match": [],
        "input_page_boxes_supported": [],
        "unsupported_input_geometry": {},
        "expected_text": {},
    }
    max_pages = max(len(before.pages), len(after.pages))
    for index in range(max_pages):
        if index >= len(before.pages) or index >= len(after.pages):
            result["page_sizes_match"].append(False)
            result["page_cropboxes_match"].append(False)
            result["page_rotations_match"].append(False)
            result["input_page_boxes_supported"].append(False)
            continue
        page = before.pages[index]
        after_page = after.pages[index]
        issues = _page_box_issues(page)
        result["input_page_boxes_supported"].append(not issues)
        if issues:
            result["unsupported_input_geometry"][str(index + 1)] = issues
        result["page_sizes_match"].append(
            _box_values_match(_box_tuple(page.mediabox), _box_tuple(after_page.mediabox))
        )
        result["page_cropboxes_match"].append(
            _box_values_match(_box_tuple(page.cropbox), _box_tuple(after_page.cropbox))
        )
        result["page_rotations_match"].append(_page_rotation(page) == _page_rotation(after_page))
    text = "\n".join(page.extract_text() or "" for page in after.pages)
    for value in expected_text:
        result["expected_text"][value] = value in text
    result["ok"] = (
        result["input_pages"] == result["output_pages"]
        and all(result["page_sizes_match"])
        and all(result["page_cropboxes_match"])
        and all(result["page_rotations_match"])
        and all(result["input_page_boxes_supported"])
        and all(result["expected_text"].values())
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_cmd = subparsers.add_parser("inspect", help="Find likely fillable labels")
    inspect_cmd.add_argument("input_pdf")
    inspect_cmd.add_argument("--fields-json", help="Write detected field JSON to this path")

    fill_cmd = subparsers.add_parser("fill", help="Fill a PDF from field and answer JSON")
    fill_cmd.add_argument("input_pdf")
    fill_cmd.add_argument("output_pdf")
    fill_cmd.add_argument("--fields-json", required=True)
    fill_cmd.add_argument("--answers-json", required=True)

    verify_cmd = subparsers.add_parser("verify", help="Check page geometry and expected text")
    verify_cmd.add_argument("input_pdf")
    verify_cmd.add_argument("output_pdf")
    verify_cmd.add_argument("--expect-text", action="append", default=[])

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "inspect":
        result = inspect_pdf(args.input_pdf)
        dump_json(result, args.fields_json)
        if args.fields_json:
            print(f"Wrote field suggestions to {args.fields_json}")
        return 0

    if args.command == "fill":
        fields = coerce_fields(load_json(args.fields_json))
        answers = load_json(args.answers_json)
        if not isinstance(answers, dict):
            raise ValueError("Answers JSON must be an object")
        fill_pdf(args.input_pdf, args.output_pdf, fields, answers)
        print(f"Wrote filled PDF to {args.output_pdf}")
        return 0

    if args.command == "verify":
        result = verify_pdf(args.input_pdf, args.output_pdf, args.expect_text)
        dump_json(result)
        return 0 if result["ok"] else 1

    parser.error(f"Unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
