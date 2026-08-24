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


# --- Re-run safety -----------------------------------------------------------
# The note these skills run on is edited and re-converted repeatedly, so
# convert() must be a no-op on its own output. Existing `$...$` spans are
# math, not prose: neither the parentheses inside them nor the prose
# parentheses around them may be rewritten.

def test_parens_inside_existing_inline_math_are_left_alone():
    src = "Input: $H^{(\\ell-1)}$ here\n"
    assert convert(src) == src


def test_prose_parens_wrapping_inline_math_are_left_alone():
    src = "shape (really: $d_{\\text{model}}$) ok\n"
    assert convert(src) == src


def test_left_right_parens_inside_inline_math_are_left_alone():
    src = "$\\text{softmax}\\left(\\frac{a}{b}\\right)V_1$\n"
    assert convert(src) == src


def test_multiple_math_spans_and_prose_parens_on_one_line():
    src = "$H^{(\\ell)}$ (shape: $[n, d_{\\text{model}}]$) and (plain) text\n"
    assert convert(src) == src


def test_inline_conversion_still_happens_beside_existing_math():
    src = "$\\theta$ and (Q_{\\text{it}}) here\n"
    assert convert(src) == "$\\theta$ and $Q_{\\text{it}}$ here\n"


def test_idempotent_on_realistic_converted_prose():
    src = (
        "Input: $H^{(\\ell-1)}$ (shape: $[n, d_{\\text{model}}]$)\n"
        "\n"
        "$$\nP(\\text{next token}\\mid \\text{context})\n$$\n"
        "\n"
        "- Often $d_{head} = \\frac{d_{\\text{model}}}{h}$.\n"
    )
    once = convert(src)
    assert once == src
    assert convert(once) == once


# --- Unclosed `$$` must not eat the rest of the file -------------------------

def test_unclosed_dollar_block_does_not_strip_hard_breaks():
    """A stray `$$` must not turn the remainder of the note into display math."""
    src = "Intro.\n\n$$\nx = 1\n\nprose hard break  \nsecond line  \nend\n"
    assert convert(src) == src


def test_closed_dollar_block_still_trims_its_own_hard_breaks():
    src = "$$\nx = 1  \n$$\n\nprose  \n"
    assert convert(src) == "$$\nx = 1\n$$\n\nprose  \n"


# --- Collapsed `\\` row separators -------------------------------------------
# ChatGPT renders a LaTeX row separator `\\` as a single `\` followed by a
# markdown hard break. Stripping the hard break without restoring the second
# backslash leaves `a\`, which is not a row separator and breaks the matrix.

def test_collapsed_row_separator_is_restored():
    src = "[  \n\\begin{bmatrix}  \na\\  \nb\\  \nc  \n\\end{bmatrix}  \n]\n"
    assert convert(src) == (
        "$$\n\\begin{bmatrix}\na\\\\\nb\\\\\nc\n\\end{bmatrix}\n$$\n"
    )


def test_intact_row_separator_is_left_alone():
    src = "[  \n\\alpha\\\\  \n\\beta  \n]\n"
    assert convert(src) == "$$\n\\alpha\\\\\n\\beta\n$$\n"


def test_row_separator_repair_is_idempotent():
    src = "[  \n\\alpha\\  \n\\beta  \n]\n"
    once = convert(src)
    assert convert(once) == once


def test_row_separator_repair_applies_inside_existing_dollar_blocks():
    src = "$$\na\\  \nb\n$$\n"
    assert convert(src) == "$$\na\\\\\nb\n$$\n"


def test_trailing_backslash_outside_math_is_untouched():
    src = "curl http://localhost:11434/api/embeddings \\\n  -d '{}'\n"
    assert convert(src) == src
