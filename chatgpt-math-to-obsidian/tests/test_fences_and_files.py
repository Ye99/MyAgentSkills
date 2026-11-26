"""Code fences, frontmatter, line endings and safe writes."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fix_chatgpt_math  # noqa: E402
from fix_chatgpt_math import convert, main  # noqa: E402

B3, T3 = "`" * 3, "~" * 3
MATH = "(\\theta) changed\n"


@pytest.mark.parametrize("src", [
    # a shorter backtick run inside a 4-backtick fence is code, not a closer
    "````md\n" + B3 + "\n" + MATH + "````\n",
    # a backtick run inside a tilde fence is code
    T3 + "\n" + B3 + "\n" + MATH + T3 + "\n",
    # a closer must have nothing after it
    B3 + "py\n" + B3 + "py\n" + MATH + B3 + "\n",
])
def test_math_inside_code_is_left_alone(src):
    assert convert(src) == src


def test_math_after_a_properly_closed_fence_is_converted():
    src = "````\n" + B3 + "\n````\n" + MATH
    assert convert(src) == "````\n" + B3 + "\n````\n$\\theta$ changed\n"


def test_four_space_indent_does_not_open_a_fence():
    assert convert("    " + B3 + "\n" + MATH) == "    " + B3 + "\n$\\theta$ changed\n"


def test_frontmatter_is_left_alone():
    src = "---\ntitle: (\\theta) notes\n# comment (\\alpha)\n---\n" + MATH
    assert convert(src) == "---\ntitle: (\\theta) notes\n# comment (\\alpha)\n---\n$\\theta$ changed\n"


def test_crlf_file_keeps_crlf(tmp_path):
    p = tmp_path / "n.md"
    p.write_bytes(b"a (\\theta) b\r\nnext\r\n")
    assert main([str(p)]) == 0
    assert p.read_bytes() == b"a $\\theta$ b\r\nnext\r\n"


def test_crlf_file_with_nothing_to_fix_is_not_rewritten(tmp_path):
    p = tmp_path / "n.md"
    p.write_bytes(b"plain\r\ntext\r\n")
    assert main([str(p), "--check"]) == 0


def test_failed_write_leaves_the_original_intact(tmp_path, monkeypatch):
    p = tmp_path / "n.md"
    p.write_text("a (\\theta) b\n", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(fix_chatgpt_math.os, "replace", boom)
    with pytest.raises(OSError):
        main([str(p)])
    assert p.read_text(encoding="utf-8") == "a (\\theta) b\n"
    assert [x.name for x in tmp_path.iterdir()] == ["n.md"]
