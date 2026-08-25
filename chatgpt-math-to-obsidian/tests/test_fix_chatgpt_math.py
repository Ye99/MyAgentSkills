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


# --- Marker-less display bodies ----------------------------------------------
# ChatGPT emits plenty of display math with no `\command` and no `_{`/`^{`
# group: `n=11`, `128K-103K=25K`. The bracket-pair structure is already strong
# evidence of math, so a body made only of symbols and digits qualifies even
# without a LaTeX marker. Prose in brackets still must not.

def test_marker_less_arithmetic_body_is_converted():
    assert convert("[  \nn=11  \n]\n") == "$$\nn=11\n$$\n"


def test_marker_less_body_with_grouped_digits():
    assert convert("[  \nn=100,000  \n]\n") == "$$\nn=100,000\n$$\n"


def test_marker_less_multi_term_arithmetic():
    src = "[  \n2K+20K+80K+1K=103K  \n]\n"
    assert convert(src) == "$$\n2K+20K+80K+1K=103K\n$$\n"


@pytest.mark.parametrize("src", [
    "[\nplain text in brackets\n]\n",
    "[\n- item one\n- item two\n]\n",
    "[\nsee the docs\n]\n",
])
def test_prose_in_brackets_is_still_untouched(src):
    """A word of three or more letters means prose, not a formula."""
    assert convert(src) == src


def test_marker_less_conversion_is_idempotent():
    once = convert("[  \nn=11  \n]\n")
    assert convert(once) == once


# --- `# [` setext-heading artifact -------------------------------------------
# A formula pasted as `[` / `H^{(30)}` / `=` / body / `]` is normalized by the
# markdown renderer into an ATX heading: the `=` is a setext underline, so it is
# swallowed and `# ` is prepended. The underline character is determined by the
# heading level, so restoring `=` reconstructs rather than invents.

def test_setext_artifact_is_repaired():
    src = "# [  \nH^{(30)}\n\n\\begin{bmatrix}  \na\\  \nb  \n\\end{bmatrix}  \n]\n"
    assert convert(src) == (
        "$$\nH^{(30)}\n=\n\\begin{bmatrix}\na\\\\\nb\n\\end{bmatrix}\n$$\n"
    )


def test_setext_body_may_contain_a_literal_bracketed_vector():
    """Depth tracking: the inner `]` must not be mistaken for the block's end."""
    src = "# [  \nh_{\\text{cat}}\n\n[  \n0.72  \n]  \n]\n"
    assert convert(src) == "$$\nh_{\\text{cat}}\n=\n[\n0.72\n]\n$$\n"
    assert convert(src).count("\n") == src.count("\n")


def test_setext_repair_is_idempotent():
    src = "# [  \nH^{(30)}\n\n\\begin{bmatrix}  \na  \n\\end{bmatrix}  \n]\n"
    once = convert(src)
    assert convert(once) == once


@pytest.mark.parametrize("src", [
    "### [Building effective agents](https://example.com)\n",
    "## [MCP](https://modelcontextprotocol.io/introduction)\n",
    "# [a link](https://example.com) in a heading\n",
])
def test_headings_containing_links_are_untouched(src):
    """The `[` must be alone on the heading line; a link heading is not math."""
    assert convert(src) == src


def test_heading_bracket_without_latex_body_is_untouched():
    src = "# [  \nsome words\n\nmore prose\n]\n"
    assert convert(src) == src


# --- Lost-backslash spacing commands -----------------------------------------
# `0.72,\;` loses its backslash in the paste and arrives as `0.72,;`. The
# surviving character identifies the command that lost the backslash, so the
# repair is determined, not guessed.

def test_lost_backslash_thin_space_is_restored():
    assert convert("$$\n0.72,,\n$$\n") == "$$\n0.72,\\,\n$$\n"


def test_lost_backslash_thick_space_is_restored():
    src = "$$\nQ_{\\text{new}}K_1^T,;\n$$\n"
    assert convert(src) == "$$\nQ_{\\text{new}}K_1^T,\\;\n$$\n"


def test_lost_backslash_medium_space_is_restored():
    assert convert("$$\na,:\n$$\n") == "$$\na,\\:\n$$\n"


def test_lost_backslash_repair_applies_in_converted_display_blocks():
    assert convert("[  \n\\ldots,,  \n]\n") == "$$\n\\ldots,\\,\n$$\n"


def test_lost_backslash_repair_is_idempotent():
    once = convert("$$\n0.72,,\n$$\n")
    assert convert(once) == once


def test_spacing_repair_does_not_touch_prose():
    src = "Wait, ; that is odd, , really.\n"
    assert convert(src) == src


# --- Inline rule must not swallow prose or code ------------------------------
# Every line below is real text from a notes repo that the inline rule mangled.
# A `\command` or `_{` group inside parentheses is not sufficient evidence of
# inline math: prose, function calls, and printf format strings all contain one.

@pytest.mark.parametrize("src", [
    # Function application: the identifier belongs to the formula, so breaking
    # it out as `exp$z_{t,i}$` is always wrong.
    "the softmax: P(y\\_t \\= w\\_i | y\\_{\\<t}, x) \\= softmax(z\\_{t,i}).\n",
    "computes P(\\text{next token}\\mid x) here\n",
    "we get exp(z\\_{t,i}) / Σ\\_{j=1}^{|V|} exp(z\\_{t,j}).\n",
    # Code: printf format strings and shell snippets.
    "          fmt.Printf(\"%s\\n\", slice\\[i\\])\n",
    "(to fix \\n) curl http://localhost:8000/generate\n",
    # Prose that happens to contain a LaTeX command.
    "The model’s **parameters** (its weights, θ\\thetaz) stay fixed.\n",
    "* Stop sequences: hard brakes to end cleanly (e.g., stop at “\\n\\nUser:”).\n",
    # Prose parentheses wrapping an escaped-markdown bracket group.
    "mapping their range (\\[f\\_{\\min}, f\\_{\\max}\\]) into a smaller range\n",
])
def test_inline_rule_leaves_prose_and_code_alone(src):
    assert convert(src) == src


def test_function_application_is_not_inline_math():
    """`exp(z_{t,i})` -> `exp$z_{t,i}$` orphans the function name."""
    assert convert("exp(z_{t,i})\n") == "exp(z_{t,i})\n"


def test_backslash_n_is_not_a_latex_command():
    """`\\n` is a C escape, not LaTeX; it must not qualify as a marker."""
    assert convert("printf (\\n) here\n") == "printf (\\n) here\n"
    assert convert("a tab (\\t) there\n") == "a tab (\\t) there\n"


def test_quoted_text_in_parens_is_not_inline_math():
    assert convert("call f(\"%s\\theta\") now\n") == "call f(\"%s\\theta\") now\n"


def test_multiple_prose_words_in_parens_is_not_inline_math():
    src = "the value (its weights, \\theta) stays\n"
    assert convert(src) == src


def test_prose_words_inside_text_command_are_still_math():
    """`\\text{next token}` is math despite containing prose words."""
    src = "the term (\\text{next token}\\mid x) here\n"
    assert convert(src) == "the term $\\text{next token}\\mid x$ here\n"


def test_genuine_inline_math_still_converts():
    src = "a Query vector (Q_{\\text{it}}), and (\\theta) changed.\n"
    assert convert(src) == "a Query vector $Q_{\\text{it}}$, and $\\theta$ changed.\n"
