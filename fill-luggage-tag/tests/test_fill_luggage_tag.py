import importlib.util
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject, RectangleObject
from reportlab.pdfgen import canvas


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fill_luggage_tag.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fill_luggage_tag", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_synthetic_tag_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica", 8)
    labels = [
        ("Name:", 190, 385),
        ("Address:", 190, 368),
        ("Telephone:", 190, 318),
    ]
    for label, x, y in labels:
        c.drawString(x, y, label)
        c.line(x + 45, y - 2, 500, y - 2)
    c.drawString(260, 250, "Norwegian Test Ship")
    c.save()


def make_two_page_tag_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    for page in (1, 2):
        c.setFont("Helvetica", 8)
        c.drawString(190, 385, "Address:")
        c.line(235, 383, 500, 383)
        c.drawString(260, 250, f"Page {page}")
        c.showPage()
    c.save()


def make_rotated_pdf(source_path: Path, rotated_path: Path, degrees: int = 90) -> None:
    reader = PdfReader(str(source_path))
    writer = PdfWriter()
    page = reader.pages[0]
    page.rotate(degrees)
    writer.add_page(page)
    with rotated_path.open("wb") as f:
        writer.write(f)


def make_inherited_rotated_pdf(source_path: Path, rotated_path: Path, degrees: int = 90) -> None:
    reader = PdfReader(str(source_path))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    pages = writer._root_object["/Pages"].get_object()
    pages[NameObject("/Rotate")] = NumberObject(degrees)
    for page in writer.pages:
        page.pop("/Rotate", None)
    with rotated_path.open("wb") as f:
        writer.write(f)


def make_cropped_pdf(source_path: Path, cropped_path: Path) -> None:
    reader = PdfReader(str(source_path))
    writer = PdfWriter()
    page = reader.pages[0]
    page[NameObject("/CropBox")] = RectangleObject([36, 36, 576, 756])
    writer.add_page(page)
    with cropped_path.open("wb") as f:
        writer.write(f)


def make_nonzero_origin_pdf(source_path: Path, shifted_path: Path) -> None:
    reader = PdfReader(str(source_path))
    writer = PdfWriter()
    page = reader.pages[0]
    shifted_box = RectangleObject([10, 10, 622, 802])
    page[NameObject("/MediaBox")] = shifted_box
    page[NameObject("/CropBox")] = RectangleObject([10, 10, 622, 802])
    writer.add_page(page)
    with shifted_path.open("wb") as f:
        writer.write(f)


def test_inspect_pdf_finds_luggage_tag_labels(tmp_path):
    module = load_module()
    pdf_path = tmp_path / "synthetic-tag.pdf"
    make_synthetic_tag_pdf(pdf_path)

    result = module.inspect_pdf(pdf_path)

    assert result["pages"] == [{"page": 0, "width": 612.0, "height": 792.0}]
    assert [field["label"] for field in result["fields"]] == [
        "Name",
        "Address",
        "Telephone",
    ]
    assert all(field["page"] == 0 for field in result["fields"])
    assert all(field["x"] > 190 for field in result["fields"])
    assert all(field["y"] > 300 for field in result["fields"])
    assert all(field["font_size"] == 9.6 for field in result["fields"])


def test_inspect_pdf_warns_about_geometry_fill_will_reject(tmp_path):
    module = load_module()
    source_path = tmp_path / "synthetic-tag.pdf"
    cropped_path = tmp_path / "cropped-tag.pdf"
    make_synthetic_tag_pdf(source_path)
    make_cropped_pdf(source_path, cropped_path)

    result = module.inspect_pdf(cropped_path)

    assert any("CropBox" in warning for warning in result["warnings"])
    assert any("fill will reject" in warning for warning in result["warnings"])


def test_fill_pdf_preserves_page_size_and_embeds_answer_text(tmp_path):
    module = load_module()
    pdf_path = tmp_path / "synthetic-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_synthetic_tag_pdf(pdf_path)

    fields = [
        {"label": "Name", "page": 0, "x": 245, "y": 385, "font_size": 9},
        {"label": "Address", "page": 0, "x": 245, "y": 368, "font_size": 9},
        {"label": "Address", "page": 0, "x": 245, "y": 351, "font_size": 9},
        {"label": "Telephone", "page": 0, "x": 245, "y": 318, "font_size": 9},
    ]
    answers = {
        "Name": "Example Traveler",
        "Address": ["123 Example Ave", "Sample City ST 12345"],
        "Telephone": "555 010 2222",
    }

    module.fill_pdf(pdf_path, output_path, fields, answers)

    original = PdfReader(str(pdf_path))
    filled = PdfReader(str(output_path))
    assert tuple(filled.pages[0].mediabox) == tuple(original.pages[0].mediabox)
    text = filled.pages[0].extract_text()
    assert "Example Traveler" in text
    assert "123 Example Ave" in text
    assert "Sample City ST 12345" in text
    assert "555 010 2222" in text


def test_fill_pdf_requires_answers_for_all_fields(tmp_path):
    module = load_module()
    pdf_path = tmp_path / "synthetic-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_synthetic_tag_pdf(pdf_path)

    fields = [{"label": "Name", "page": 0, "x": 245, "y": 385, "font_size": 9}]

    with pytest.raises(ValueError, match="Missing answer"):
        module.fill_pdf(pdf_path, output_path, fields, {})


def test_fill_pdf_rejects_unused_answer_lines(tmp_path):
    module = load_module()
    pdf_path = tmp_path / "synthetic-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_synthetic_tag_pdf(pdf_path)

    fields = [{"label": "Address", "page": 0, "x": 245, "y": 368, "font_size": 9}]
    answers = {"Address": ["123 Example Ave", "Sample City ST 12345"]}

    with pytest.raises(ValueError, match="Unused answer"):
        module.fill_pdf(pdf_path, output_path, fields, answers)


def test_coerce_fields_accepts_inspect_object_shape():
    module = load_module()
    fields = [
        {"label": "Name", "page": 0, "x": 245, "y": 385},
        {"label": "Telephone", "page": 0, "x": 245, "y": 318},
    ]

    assert module.coerce_fields({"fields": fields}) == fields


def test_fill_pdf_refuses_to_overwrite_input_pdf(tmp_path):
    module = load_module()
    pdf_path = tmp_path / "synthetic-tag.pdf"
    make_synthetic_tag_pdf(pdf_path)

    fields = [{"label": "Name", "page": 0, "x": 245, "y": 385, "font_size": 9}]
    answers = {"Name": "Example Traveler"}

    with pytest.raises(ValueError, match="must differ"):
        module.fill_pdf(pdf_path, pdf_path, fields, answers)


def test_fill_pdf_rejects_unknown_answer_fields(tmp_path):
    module = load_module()
    pdf_path = tmp_path / "synthetic-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_synthetic_tag_pdf(pdf_path)

    fields = [{"label": "Name", "page": 0, "x": 245, "y": 385, "font_size": 9}]
    answers = {"Name": "Example Traveler", "Cabin": "1234"}

    with pytest.raises(ValueError, match="unknown field"):
        module.fill_pdf(pdf_path, output_path, fields, answers)


def test_repeated_answers_follow_field_json_order_across_pages(tmp_path):
    module = load_module()
    pdf_path = tmp_path / "two-page-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_two_page_tag_pdf(pdf_path)

    fields = [
        {"label": "Address", "page": 1, "x": 245, "y": 385, "font_size": 9},
        {"label": "Address", "page": 0, "x": 245, "y": 385, "font_size": 9},
    ]
    answers = {"Address": ["Second page address", "First page address"]}

    module.fill_pdf(pdf_path, output_path, fields, answers)

    filled = PdfReader(str(output_path))
    assert "First page address" in filled.pages[0].extract_text()
    assert "Second page address" in filled.pages[1].extract_text()


def test_fill_pdf_rejects_text_that_cannot_fit_max_width(tmp_path):
    module = load_module()
    pdf_path = tmp_path / "synthetic-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_synthetic_tag_pdf(pdf_path)

    fields = [
        {"label": "Name", "page": 0, "x": 245, "y": 385, "font_size": 9, "max_width": 1}
    ]
    answers = {"Name": "Example Traveler"}

    with pytest.raises(ValueError, match="does not fit"):
        module.fill_pdf(pdf_path, output_path, fields, answers)


def test_fill_pdf_rejects_text_default_font_cannot_render(tmp_path):
    module = load_module()
    pdf_path = tmp_path / "synthetic-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_synthetic_tag_pdf(pdf_path)

    fields = [{"label": "Name", "page": 0, "x": 245, "y": 385, "font_size": 9}]
    answers = {"Name": "Łukasz"}

    with pytest.raises(ValueError, match="cannot render"):
        module.fill_pdf(pdf_path, output_path, fields, answers)


def test_fill_pdf_allows_default_font_supported_non_ascii_text(tmp_path):
    module = load_module()
    pdf_path = tmp_path / "synthetic-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_synthetic_tag_pdf(pdf_path)

    fields = [{"label": "Name", "page": 0, "x": 245, "y": 385, "font_size": 9}]
    answers = {"Name": "Žižek"}

    module.fill_pdf(pdf_path, output_path, fields, answers)

    assert "Žižek" in PdfReader(str(output_path)).pages[0].extract_text()


def test_fill_pdf_rejects_rotated_input_pdf(tmp_path):
    module = load_module()
    source_path = tmp_path / "synthetic-tag.pdf"
    rotated_path = tmp_path / "rotated-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_synthetic_tag_pdf(source_path)
    make_rotated_pdf(source_path, rotated_path)

    fields = [{"label": "Name", "page": 0, "x": 245, "y": 385, "font_size": 9}]
    answers = {"Name": "Example Traveler"}

    with pytest.raises(ValueError, match="Rotated PDFs are not supported"):
        module.fill_pdf(rotated_path, output_path, fields, answers)


def test_fill_pdf_rejects_inherited_rotated_input_pdf(tmp_path):
    module = load_module()
    source_path = tmp_path / "synthetic-tag.pdf"
    rotated_path = tmp_path / "inherited-rotated-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_synthetic_tag_pdf(source_path)
    make_inherited_rotated_pdf(source_path, rotated_path)

    field = [{"label": "Name", "page": 0, "x": 245, "y": 385, "font_size": 9}]

    raw_page = PdfReader(str(rotated_path)).pages[0]
    assert raw_page.get_inherited("/Rotate") == 90
    with pytest.raises(ValueError, match="Rotated PDFs are not supported"):
        module.fill_pdf(rotated_path, output_path, field, {"Name": "Example Traveler"})


def test_fill_pdf_rejects_cropbox_different_from_mediabox(tmp_path):
    module = load_module()
    source_path = tmp_path / "synthetic-tag.pdf"
    cropped_path = tmp_path / "cropped-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_synthetic_tag_pdf(source_path)
    make_cropped_pdf(source_path, cropped_path)

    raw_page = PdfReader(str(cropped_path)).pages[0]
    assert tuple(float(v) for v in raw_page.cropbox) != tuple(
        float(v) for v in raw_page.mediabox
    )
    fields = [{"label": "Name", "page": 0, "x": 245, "y": 385, "font_size": 9}]

    with pytest.raises(ValueError, match="CropBox"):
        module.fill_pdf(cropped_path, output_path, fields, {"Name": "Example Traveler"})


def test_fill_pdf_rejects_nonzero_page_box_origin(tmp_path):
    module = load_module()
    source_path = tmp_path / "synthetic-tag.pdf"
    shifted_path = tmp_path / "shifted-origin-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_synthetic_tag_pdf(source_path)
    make_nonzero_origin_pdf(source_path, shifted_path)

    raw_page = PdfReader(str(shifted_path)).pages[0]
    assert tuple(float(v) for v in raw_page.mediabox)[:2] == (10.0, 10.0)
    assert tuple(float(v) for v in raw_page.cropbox)[:2] == (10.0, 10.0)
    fields = [{"label": "Name", "page": 0, "x": 245, "y": 385, "font_size": 9}]

    with pytest.raises(ValueError, match="origin"):
        module.fill_pdf(shifted_path, output_path, fields, {"Name": "Example Traveler"})


def test_verify_pdf_reports_rotation_mismatch(tmp_path):
    module = load_module()
    source_path = tmp_path / "synthetic-tag.pdf"
    rotated_path = tmp_path / "rotated-tag.pdf"
    make_synthetic_tag_pdf(source_path)
    make_rotated_pdf(source_path, rotated_path)

    result = module.verify_pdf(source_path, rotated_path, [])

    assert result["ok"] is False
    assert result["page_rotations_match"] == [False]


def test_verify_pdf_reports_cropbox_mismatch(tmp_path):
    module = load_module()
    source_path = tmp_path / "synthetic-tag.pdf"
    cropped_path = tmp_path / "cropped-tag.pdf"
    make_synthetic_tag_pdf(source_path)
    make_cropped_pdf(source_path, cropped_path)

    result = module.verify_pdf(source_path, cropped_path, [])

    assert result["ok"] is False
    assert result["page_cropboxes_match"] == [False]


def test_verify_command_returns_nonzero_when_expected_text_missing(tmp_path):
    module = load_module()
    pdf_path = tmp_path / "synthetic-tag.pdf"
    output_path = tmp_path / "filled-tag.pdf"
    make_synthetic_tag_pdf(pdf_path)

    fields = [{"label": "Name", "page": 0, "x": 245, "y": 385, "font_size": 9}]
    answers = {"Name": "Example Traveler"}
    module.fill_pdf(pdf_path, output_path, fields, answers)

    status = module.main(
        [
            "verify",
            str(pdf_path),
            str(output_path),
            "--expect-text",
            "Missing Person",
        ]
    )

    assert status == 1


def test_skill_local_gitignore_blocks_private_and_generated_artifacts():
    skill_dir = SCRIPT_PATH.parents[1]
    gitignore = skill_dir / ".gitignore"

    assert gitignore.is_file()
    patterns = gitignore.read_text(encoding="utf-8").splitlines()
    for pattern in [
        "*.pdf",
        "*.png",
        "*.gif",
        "*answers*.json",
        "*fields*.json",
        "tmp/",
        "__pycache__/",
        ".pytest_cache/",
    ]:
        assert pattern in patterns


def test_skill_local_gitignore_documents_pdf_fixture_override():
    skill_dir = SCRIPT_PATH.parents[1]
    gitignore_text = (skill_dir / ".gitignore").read_text(encoding="utf-8")

    assert "git add -f" in gitignore_text
    assert "sanitized fixtures" in gitignore_text


def test_skill_workflow_does_not_leave_skill_dir_placeholder():
    skill_md = SCRIPT_PATH.parents[1] / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")

    assert "<skill-dir>" not in text
    assert 'SKILL_DIR="' in text
    assert 'WORK_DIR="$SKILL_DIR/tmp/fill-luggage-tag"' in text
    assert 'mkdir -p "$WORK_DIR"' in text
    assert 'OUTPUT_PDF="$WORK_DIR/FilledLuggageTag.pdf"' in text
    assert '--fields-json "$WORK_DIR/fields.json"' in text
    assert '--answers-json "$WORK_DIR/answers.json"' in text
    assert '--expect-text "Example Traveler"' in text
    assert '--expect-text "123 Example Ave"' in text
    assert '--expect-text "Sample City ST 12345"' in text
    assert '--expect-text "555 010 2222"' in text


def test_skill_documents_default_font_character_limit():
    skill_md = SCRIPT_PATH.parents[1] / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")

    assert "Helvetica-Bold" in text
    assert "`9.6` points" in text
    assert '"font_size": 9.6' in text
    assert "embedded Unicode font" in text
