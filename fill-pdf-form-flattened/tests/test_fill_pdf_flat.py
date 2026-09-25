import importlib.util
import json
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText
from pypdf.generic import BooleanObject, DictionaryObject, NameObject
from reportlab.pdfgen import canvas

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fill_pdf_flat.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fill_pdf_flat", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_module()


@pytest.fixture
def form_pdf(tmp_path):
    path = tmp_path / "form.pdf"
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica", 9)
    c.drawString(40, 792 - 100, "Name")
    c.line(40, 792 - 125, 576, 792 - 125)
    c.rect(40, 792 - 160, 11, 11)
    c.drawString(56, 792 - 158, "Yes")
    c.save()
    return path


def write_fields(tmp_path, fields, page_info=None):
    path = tmp_path / "fields.json"
    page_info = page_info or {"page_number": 1, "pdf_width": 612, "pdf_height": 792}
    path.write_text(json.dumps({"pages": [page_info], "form_fields": fields}))
    return path


def field(text, box, **entry):
    return {"page_number": 1, "description": text, "entry_bounding_box": box, "entry_text": {"text": text, **entry}}


def test_fill_puts_values_in_page_content_and_verifies(tmp_path, form_pdf):
    fields = write_fields(tmp_path, [field("Jane Q Doe", [40, 108, 576, 124]), field("X", [40, 149, 51, 160])])
    out = tmp_path / "out.pdf"

    assert mod.fill_pdf(form_pdf, fields, out) == 2

    page = PdfReader(str(out)).pages[0]
    assert not page.get("/Annots")
    assert "Jane Q Doe" in page.extract_text()
    assert mod.verify_pdf(out, form_pdf, ["Jane Q Doe", "X"]) == []


def test_verify_flags_freetext_without_appearance_stream(tmp_path, form_pdf):
    # Regression: this is how the blank-in-browser PDF was produced.
    writer = PdfWriter(clone_from=str(form_pdf))
    writer.add_annotation(0, FreeText(text="Jane Q Doe", rect=(40, 668, 576, 684)))
    bad = tmp_path / "annotated.pdf"
    writer.write(str(bad))

    problems = mod.verify_pdf(bad, form_pdf, ["Jane Q Doe"])

    assert any("no /AP appearance stream" in p for p in problems)
    assert any("expected text not in page content" in p for p in problems)


def test_verify_flags_need_appearances(tmp_path, form_pdf):
    writer = PdfWriter(clone_from=str(form_pdf))
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject(
        {NameObject("/NeedAppearances"): BooleanObject(True)}
    )
    path = tmp_path / "needs.pdf"
    writer.write(str(path))

    assert any("NeedAppearances" in p for p in mod.verify_pdf(path))


def test_verify_flags_changed_page_size(tmp_path, form_pdf):
    other = tmp_path / "a4.pdf"
    c = canvas.Canvas(str(other), pagesize=(595, 842))
    c.showPage()
    c.save()

    assert any("MediaBox changed" in p for p in mod.verify_pdf(other, form_pdf))


def test_image_coordinates_scale_to_points(tmp_path, form_pdf):
    # Same Name box as above, expressed in a 1224x1584 render (2x).
    fields = write_fields(
        tmp_path,
        [field("Jane Q Doe", [80, 216, 1152, 248], font_size=10)],
        {"page_number": 1, "image_width": 1224, "image_height": 1584},
    )
    out = tmp_path / "out.pdf"
    mod.fill_pdf(form_pdf, fields, out)

    assert mod.verify_pdf(out, form_pdf, ["Jane Q Doe"]) == []


def test_long_text_shrinks_to_fit_box(tmp_path, form_pdf):
    text = "A very long street address that cannot fit"
    fields = write_fields(tmp_path, [field(text, [40, 108, 140, 124], font_size=10)])
    out = tmp_path / "out.pdf"
    mod.fill_pdf(form_pdf, fields, out)

    assert mod.verify_pdf(out, form_pdf, [text]) == []


def test_refuses_to_overwrite_input(tmp_path, form_pdf):
    fields = write_fields(tmp_path, [field("Jane", [40, 108, 576, 124])])
    with pytest.raises(mod.FillError, match="must differ"):
        mod.fill_pdf(form_pdf, fields, form_pdf)


def test_rejects_characters_helvetica_cannot_draw(tmp_path, form_pdf):
    fields = write_fields(tmp_path, [field("漢字", [40, 108, 576, 124])])
    with pytest.raises(mod.FillError, match="cannot draw"):
        mod.fill_pdf(form_pdf, fields, tmp_path / "out.pdf")


def test_rejects_rotated_page(tmp_path, form_pdf):
    writer = PdfWriter(clone_from=str(form_pdf))
    writer.pages[0].rotate(90)
    rotated = tmp_path / "rotated.pdf"
    writer.write(str(rotated))
    fields = write_fields(tmp_path, [field("Jane", [40, 108, 576, 124])])

    with pytest.raises(mod.FillError, match="Rotate"):
        mod.fill_pdf(rotated, fields, tmp_path / "out.pdf")


def test_cli_verify_exit_codes(tmp_path, form_pdf):
    fields = write_fields(tmp_path, [field("Jane Q Doe", [40, 108, 576, 124])])
    out = tmp_path / "out.pdf"

    assert mod.main(["fill", str(form_pdf), str(fields), str(out)]) == 0
    assert mod.main(["verify", str(out), "--input", str(form_pdf), "--expect-text", "Jane Q Doe"]) == 0
    assert mod.main(["verify", str(out), "--expect-text", "Missing Value"]) == 1
