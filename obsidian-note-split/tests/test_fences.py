"""Code fences and YAML frontmatter follow CommonMark/Obsidian rules, not a naive toggle."""
import importlib.util
import json
import pathlib
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "split_note.py"
spec = importlib.util.spec_from_file_location("split_note", SCRIPT)
split_note = importlib.util.module_from_spec(spec)
spec.loader.exec_module(split_note)

B3, T3 = "`" * 3, "~" * 3


def test_inner_shorter_or_other_char_fence_does_not_close():
    lines = ["````md", B3, "## code", "````", "## real"]
    assert split_note.fence_flags(lines) == [True, True, True, True, False]
    lines = [T3, B3, "## code", T3, "## real"]
    assert split_note.fence_flags(lines) == [True, True, True, True, False]


def test_closing_fence_with_info_string_does_not_close():
    lines = [B3 + "py", "## code", B3 + "py", "## code", B3, "## real"]
    assert split_note.fence_flags(lines) == [True, True, True, True, True, False]


def test_four_space_indent_is_not_a_fence():
    assert split_note.fence_flags(["    " + B3, "## real"]) == [False, False]


def test_crlf_lines_close_fences():
    assert split_note.fence_flags([B3 + "\r", "## code\r", B3 + "\r", "## real\r"]) == [True, True, True, False]


def test_frontmatter_lines_are_protected():
    lines = ["---", "# yaml comment", "---", "## real"]
    assert split_note.fence_flags(lines) == [True, True, True, False]
    # a horizontal rule later in the note is not frontmatter
    assert split_note.fence_flags(["text", "---", "# h", "---"]) == [False, False, False, False]


SOURCE = "\n".join([
    "---", "tags: [x]", "# yaml comment", "---",
    "### Alpha", "",
    "````md", B3, "## still code", "````", "",
    "## Beta", "", "Beta body, see [[#Alpha]].", "",
]) + "\n"


def test_split_keeps_code_and_frontmatter_verbatim(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Big Note.md").write_text(SOURCE, encoding="utf-8")
    lines = SOURCE.split("\n")
    beta = lines.index("## Beta") + 1
    n = len(lines) - 1
    plan = {"source": "Big Note.md", "date": "2026-01-01", "notes": [
        {"name": "Big Note Alpha", "description": "Alpha.", "tags": [], "segments": [[1, beta - 1, 1]]},
        {"name": "Big Note Beta", "description": "Beta.", "tags": [], "segments": [[beta, n, 0]]},
    ]}
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(vault), "--plan", str(p), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    alpha = (out / "notes" / "Big Note Alpha.md").read_text(encoding="utf-8")
    assert "\n# yaml comment\n" in alpha
    assert "\n## still code\n" in alpha
    assert "\n## Alpha\n" in alpha
    index = (out / "notes" / "Big Note.md").read_text(encoding="utf-8")
    assert "[[Big Note Beta#Beta|Beta]]" in index

