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


def _split(tmp_path, source_bytes, segments):
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "Big Note.md").write_bytes(source_bytes)
    plan = {"source": "Big Note.md", "date": "2026-01-01", "notes": [
        {"name": f"Big Note {k}", "description": f"{k}.", "tags": [], "segments": segs}
        for k, segs in segments.items()]}
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(SCRIPT), "--vault", str(vault), "--plan", str(p), "--out", str(out)],
                       capture_output=True, text=True)
    return r, out


LINES = SOURCE.split("\n")
BETA = LINES.index("## Beta") + 1
LAST = len(LINES) - 1
FM_END = 4


def test_split_keeps_code_verbatim_and_frontmatter_stays_with_the_index(tmp_path):
    r, out = _split(tmp_path, SOURCE.encode(), {"Alpha": [[FM_END + 1, BETA - 1, 1]], "Beta": [[BETA, LAST, 0]]})
    assert r.returncode == 0, r.stdout + r.stderr
    alpha = (out / "notes" / "Big Note Alpha.md").read_text(encoding="utf-8")
    assert "\n## still code\n" in alpha
    assert "\n## Alpha\n" in alpha
    assert "yaml comment" not in alpha
    index = (out / "notes" / "Big Note.md").read_text(encoding="utf-8")
    # the source's tags and aliases survive: its frontmatter opens the index note, unchanged
    assert index.startswith("---\ntags: [x]\n# yaml comment\n---\n# Big Note\n")
    assert "[[Big Note Beta#Beta|Beta]]" in index


def test_a_segment_over_the_source_frontmatter_is_refused(tmp_path):
    r, out = _split(tmp_path, SOURCE.encode(), {"Alpha": [[1, BETA - 1, 1]], "Beta": [[BETA, LAST, 0]]})
    assert r.returncode != 0
    assert "frontmatter" in r.stdout
    assert not (out / "notes").exists()


def test_crlf_source_keeps_crlf_and_records_the_raw_hash(tmp_path):
    import hashlib
    raw = SOURCE.replace("\n", "\r\n").encode()
    r, out = _split(tmp_path, raw, {"Alpha": [[FM_END + 1, BETA - 1, 1]], "Beta": [[BETA, LAST, 0]]})
    assert r.returncode == 0, r.stdout + r.stderr
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    # the writer compares raw bytes, so the splitter must record the raw bytes' hash
    assert report["read_sha256"]["Big Note.md"] == hashlib.sha256(raw).hexdigest()
    for name in ("Big Note Alpha.md", "Big Note Beta.md", "Big Note.md"):
        data = (out / "notes" / name).read_bytes()
        assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b""), name
