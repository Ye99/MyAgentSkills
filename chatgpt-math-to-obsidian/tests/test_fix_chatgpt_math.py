import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from fix_chatgpt_math import convert, main  # noqa: E402


def test_display_block_becomes_dollar_dollar():
    src = "The model compares:\n\n[  \nQ_{\\text{it}} \\cdot K_{\\text{cat}}  \n]\n\nDone.\n"
    assert convert(src) == (
        "The model compares:\n\n$$\nQ_{\\text{it}} \\cdot K_{\\text{cat}}\n$$\n\nDone.\n"
    )


def test_multiline_display_block_keeps_every_line():
    src = "[  \n0.72V_{\\text{cat}}  \n+0.15V_{\\text{mat}}  \n+\\cdots  \n]\n"
    assert convert(src) == "$$\n0.72V_{\\text{cat}}\n+0.15V_{\\text{mat}}\n+\\cdots\n$$\n"


def test_escaped_display_delimiters():
    assert convert("\\[\nW \\rightarrow W'\n\\]\n") == "$$\nW \\rightarrow W'\n$$\n"


def test_inline_math():
    src = "a Query vector (Q_{\\text{it}}), and (\\theta) changed.\n"
    assert convert(src) == "a Query vector $Q_{\\text{it}}$, and $\\theta$ changed.\n"


def test_inline_math_inside_display_block_is_left_alone():
    src = "[  \nP(\\text{next token}\\mid \\text{context})  \n]\n"
    assert convert(src) == "$$\nP(\\text{next token}\\mid \\text{context})\n$$\n"


@pytest.mark.parametrize("src", [
    "See the [docs](https://example.com) (really).\n",
    "A markdown list:\n\n- [ ] todo\n",
    "Prose with (parentheses) and (a note) only.\n",
    "```python\nx = f(y_{1})  # \\text is fine here\n```\n",
    "Inline code `(\\theta)` must stay literal.\n",
    "Ends the line with two spaces  \nand keeps them.\n",
])
def test_non_math_content_is_untouched(src):
    assert convert(src) == src


def test_bracket_block_without_latex_is_untouched():
    src = "[\nplain text in brackets\n]\n"
    assert convert(src) == src


def test_unclosed_bracket_is_untouched():
    src = "[  \nQ_{\\text{it}}  \n\nnext paragraph\n"
    assert convert(src) == src


def test_idempotent():
    src = "vector (Q_{\\text{it}}):\n\n[  \n\\boxed{\\text{x}}  \n]\n"
    once = convert(src)
    assert convert(once) == once


def test_main_writes_in_place_and_check_mode_reports(tmp_path, capsys):
    note = tmp_path / "note.md"
    note.write_text("[  \n\\cdots  \n]\n", encoding="utf-8")

    assert main([str(note), "--check"]) == 1
    assert note.read_text(encoding="utf-8") == "[  \n\\cdots  \n]\n"
    assert "+$$" in capsys.readouterr().out

    assert main([str(note)]) == 0
    assert note.read_text(encoding="utf-8") == "$$\n\\cdots\n$$\n"
    assert main([str(note), "--check"]) == 0


def test_existing_dollar_block_is_not_re_parsed_as_inline_math():
    """Regression: `P(\\text{x})` inside a `$$` block must keep its parentheses."""
    src = "$$\nP(\\text{next token}\\mid \\text{context})\n$$\n"
    assert convert(src) == src


def test_existing_dollar_block_trailing_breaks_are_trimmed():
    src = "$$  \nP(\\text{a})  \n$$\n"
    assert convert(src) == "$$\nP(\\text{a})\n$$\n"


def test_bullet_list_of_inline_keys():
    src = "- **cat** \u2192 (K_{\\text{cat}})\n- **mat** \u2192 (K_{\\text{mat}})\n"
    assert convert(src) == "- **cat** \u2192 $K_{\\text{cat}}$\n- **mat** \u2192 $K_{\\text{mat}}$\n"


def test_two_inline_formulas_on_one_line():
    src = "compare (Q_{\\text{it}}) with (K_{\\text{cat}}) here\n"
    assert convert(src) == "compare $Q_{\\text{it}}$ with $K_{\\text{cat}}$ here\n"


def test_nested_parentheses_are_left_alone_rather_than_half_converted():
    src = "the term (P(x) \\cdot \\theta) stays put\n"
    assert convert(src) == src


def test_display_block_at_file_start_and_end_without_trailing_newline():
    assert convert("[\n\\cdots\n]") == "$$\n\\cdots\n$$"


def test_inline_whitespace_inside_delimiters_is_trimmed():
    assert convert("value ( \\theta ) here\n") == "value $\\theta$ here\n"
