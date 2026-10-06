import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
FILTER = SKILL / "scripts" / "windows-data.filter"
spec = importlib.util.spec_from_file_location("verify_copy", SKILL / "scripts" / "verify_copy.py")
verify = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(verify)

needs_rsync = pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync not installed")


def touch(root: Path, rel: str, data: bytes = b"x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


@pytest.fixture(scope="module")
def rules():
    return verify.Rules.from_file(FILTER)


@pytest.mark.parametrize(
    ("rel", "skipped"),
    [
        ("pagefile.sys", True),
        ("HIBERFIL.SYS", True),
        ("Tools/setup.EXE", True),
        ("Projects/app/bin/lib.Dll", True),
        ("Photos/Thumbs.db", True),
        ("Photos/Desktop.ini", True),
        ("Users/alice/NTUSER.DAT", True),
        ("Users/alice/NTUSER.DAT{guid}.TM.blf", True),
        ("Photos/IMG_0001.JPG", False),
        ("Users/alice/Documents/report.docx", False),
        ("Users/alice/AppData/Local/Microsoft/Outlook/mail.pst", False),
        ("Projects/app/installer.cab", False),
        ("Projects/app/run.bat", False),
    ],
)
def test_file_rules(rules, rel, skipped):
    assert (rules.file_rule(rel) is not None) is skipped


@pytest.mark.parametrize(
    ("rel", "skipped"),
    [
        ("Windows", True),
        ("Program Files", True),
        ("Program Files (x86)", True),
        ("ProgramData", True),
        ("$Recycle.Bin", True),
        ("System Volume Information", True),
        ("Users/Default", True),
        ("Users/alice/AppData/Local/Temp", True),
        ("Users/alice/AppData/Local/Google/Chrome/User Data/Default/Cache", True),
        ("Windows.old/Windows", True),
        ("Windows.old/Users", False),
        ("Users/alice/AppData/Roaming", False),
        ("Users/alice/AppData/Local/Google/Chrome/User Data/Default", False),
        ("Data/Windows", False),          # anchored: only the root Windows/ is system
        ("Data/Program Files", False),
        ("Users/Default Projects", False),
    ],
)
def test_dir_rules(rules, rel, skipped):
    assert (rules.dir_rule(rel) is not None) is skipped


def build_drive(src: Path) -> None:
    touch(src, "pagefile.sys", b"p" * 50)
    touch(src, "Windows/System32/kernel32.dll")
    touch(src, "Program Files (x86)/App/app.exe")
    touch(src, "Program Files (x86)/App/manual.pdf")
    touch(src, "Photos/2019/IMG_0001.JPG", b"jpegdata")
    touch(src, "Photos/2019/Thumbs.db")
    touch(src, "Projects/tool/bin/Debug/tool.exe")
    touch(src, "Projects/tool/Program.cs", b"class P {}")
    touch(src, "Users/alice/Documents/notes.txt", b"hello")
    touch(src, "Users/alice/AppData/Roaming/App/profile.db", b"db")
    touch(src, "Users/alice/AppData/Local/Temp/junk.tmp")
    touch(src, "Users/alice/NTUSER.DAT")
    (src / "Users/bob/Documents").mkdir(parents=True)       # only a junction inside
    os.symlink("../Music", src / "Users/bob/Documents/My Music")
    (src / "Empty/Nested").mkdir(parents=True)
    os.symlink("./Users", src / "Documents and Settings")


def rsync_copy(src: Path, dst: Path) -> None:
    subprocess.run(
        ["rsync", "-rt", "--no-links", "-m", f"--filter=merge {FILTER}", f"{src}/", f"{dst}/"],
        check=True, capture_output=True,
    )


@needs_rsync
def test_copy_then_verify_passes_after_pruning_empty_dirs(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    build_drive(src)
    rsync_copy(src, dst)

    assert (dst / "Photos/2019/IMG_0001.JPG").read_bytes() == b"jpegdata"
    assert (dst / "Projects/tool/Program.cs").exists()
    assert (dst / "Users/alice/AppData/Roaming/App/profile.db").exists()
    for gone in ("pagefile.sys", "Windows", "Program Files (x86)", "Projects/tool/bin",
                 "Users/alice/AppData/Local/Temp", "Users/alice/NTUSER.DAT", "Empty",
                 "Documents and Settings"):
        assert not (dst / gone).exists(), gone

    rules = verify.Rules.from_file(FILTER)
    r = verify.inventory(str(src), str(dst), rules)
    # rsync -m keeps a dir whose only entry is a skipped junction
    assert r["empty_dst"] == ["Users/bob/Documents"]
    assert not verify.verdict(r, [])

    subprocess.run(["find", str(dst), "-mindepth", "1", "-type", "d", "-empty", "-delete"], check=True)
    r = verify.inventory(str(src), str(dst), rules)
    assert verify.verdict(r, [])
    assert r["copied_n"] == r["dst_n"] == 4
    assert {rel.rstrip("/") for rel, _ in r["links"]} == {"Documents and Settings", "Users/bob/Documents/My Music"}


@needs_rsync
def test_report_files_and_failures(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    build_drive(src)
    rsync_copy(src, dst)
    subprocess.run(["find", str(dst), "-mindepth", "1", "-type", "d", "-empty", "-delete"], check=True)
    empty = tmp_path / "checksum.txt"
    empty.write_text("")

    assert verify.main([str(src), str(dst), "--filter", str(FILTER), "--checksum-output", str(empty)]) == 0
    tsv = (dst / verify.SKIPPED_TSV).read_text().splitlines()
    assert "Projects/tool/bin/Debug/tool.exe\t1\tfile *.exe\tExecutables / libraries / drivers / installers" in tsv
    assert any(l.startswith("Windows/System32/kernel32.dll\t") for l in tsv)
    assert "PASS" in (dst / verify.REPORT).read_text()

    # Report files and the review guide in DST are not counted as extra data.
    (dst / "agentreviewguide.md").write_text("guide")
    assert verify.main([str(src), str(dst), "--filter", str(FILTER)]) == 0

    (dst / "Users/alice/Documents/notes.txt").unlink()
    assert verify.main([str(src), str(dst), "--filter", str(FILTER)]) == 1
    assert "MISSING in destination" in (dst / verify.REPORT).read_text()

    (dst / "Users/alice/Documents/notes.txt").write_text("hello")
    (dst / "extra.txt").write_text("stray")
    assert verify.main([str(src), str(dst), "--filter", str(FILTER)]) == 1


def test_checksum_differences_fail(tmp_path):
    out = tmp_path / "c.txt"
    out.write_text(">fc.t...... Photos/a.jpg\n")
    diffs = verify.read_lines(str(out), lambda l: l.startswith((">f", "cd", "*deleting")))
    r = {"problems": [], "empty_dst": [], "dst_links": [], "copied_n": 1, "dst_n": 1, "copied_b": 1, "dst_b": 1}
    assert not verify.verdict(r, diffs)
    assert verify.verdict(r, [])


@pytest.mark.parametrize(
    ("glob", "path", "matches"),
    [
        ("a/**/b.cab", "a/b.cab", False),         # rsync: '/**/' needs a directory
        ("a/**/b.cab", "a/x/y/b.cab", True),
        ("a/**/b.cab", "ab.cab", False),
        ("a/*.cab", "a/x/b.cab", False),
        ("**.part", "x/y/z.iso.part", True),
    ],
)
def test_double_star(glob, path, matches):
    import re
    assert bool(re.match("^" + verify.glob_to_regex(glob) + "$", path)) is matches


def test_unanchored_slash_pattern_matches_path_tail_not_basename():
    rules = verify.Rules(["- foo/bar.txt\n", "- /CD/**/*.[Cc][Aa][Bb]\n"])
    assert rules.file_rule("x/foo/bar.txt") == "foo/bar.txt"
    assert rules.file_rule("foo/bar.txt") == "foo/bar.txt"
    assert rules.file_rule("x/bar.txt") is None          # basename alone must not match
    assert rules.file_rule("xfoo/bar.txt") is None
    assert rules.file_rule("CD/SOFTWARE/EN/DATA1.CAB") == "/CD/**/*.[Cc][Aa][Bb]"
    assert rules.file_rule("CD/TOP.CAB") is None             # matches rsync, see parity test
    assert rules.file_rule("Other/DATA1.CAB") is None


def test_categories_come_from_section_headers(rules):
    assert rules.category["/Windows/"] == "Windows system / boot / recovery"
    assert rules.category["*.[Ee][Xx][Ee]"] == "Executables / libraries / drivers / installers"


@needs_rsync
def test_links_in_excluded_trees_emptied_dirs_and_output_dir(tmp_path):
    src, dst, out = tmp_path / "src", tmp_path / "dst", tmp_path / "review"
    build_drive(src)
    (src / "ProgramData").mkdir(exist_ok=True)
    os.symlink("./x", src / "ProgramData/Templates")
    touch(src, "Downloads/Setup.EXE")                 # folder with only an installer
    touch(src, "Downloads/desktop.ini")
    touch(src, "Music/desktop.ini")                   # noise-only folder: not reported
    touch(src, "Tools/CD/SETUP.EXE")
    touch(src, "Tools/CD/SUB/lib.dll")
    rsync_copy(src, dst)
    subprocess.run(["find", str(dst), "-mindepth", "1", "-type", "d", "-empty", "-delete"], check=True)

    assert verify.main([str(src), str(dst), "--filter", str(FILTER), "--output-dir", str(out)]) == 0
    assert not (dst / verify.REPORT).exists()
    report = (out / verify.REPORT).read_text()
    r = verify.inventory(str(src), str(dst), verify.Rules.from_file(FILTER))
    assert [rel for rel, _ in r["links_excluded"]] == ["ProgramData/Templates"]
    emptied = [d for d, _ in r["emptied_dirs"]]
    assert emptied == ["Downloads", "Projects/tool/bin", "Tools"]
    # only outermost absent folders: Tools/ held nothing but CD/
    assert "Music" not in emptied and "Photos/2019" not in emptied
    assert "`Downloads/`" in report and "ProgramData/Templates" in report


@needs_rsync
def test_checksum_dir_creation_lines_are_not_failures(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    build_drive(src)
    rsync_copy(src, dst)
    subprocess.run(["find", str(dst), "-mindepth", "1", "-type", "d", "-empty", "-delete"], check=True)
    out = tmp_path / "checksum.txt"
    out.write_text("cd+++++++++ Users/bob/Documents/\n")
    assert verify.main([str(src), str(dst), "--filter", str(FILTER), "--checksum-output", str(out)]) == 0
    assert "Users/bob/Documents/" in (dst / verify.REPORT).read_text()
    out.write_text("cd+++++++++ Users/bob/Documents/\n>fcst...... Photos/2019/IMG_0001.JPG\n")
    assert verify.main([str(src), str(dst), "--filter", str(FILTER), "--checksum-output", str(out)]) == 1


PARITY_FILES = ["X/top.cab", "X/sub/deep.cab", "X/sub/more/deeper.cab", "Y/a.cab",
                "Y/X/top.cab", "Z/foo/bar.txt", "Z/q/foo/bar.txt", "foo/bar.txt", "bar.txt"]


@needs_rsync
@pytest.mark.parametrize("pattern", ["/X/**/*.cab", "/X/**.cab", "X/**/*.cab", "**/top.cab",
                                     "foo/bar.txt", "/X/*.cab", "*.cab", "/X/sub/"])
def test_rules_match_what_rsync_excludes(tmp_path, pattern):
    """The verifier's matcher must agree with the installed rsync file by file."""
    src = tmp_path / "src"
    for rel in PARITY_FILES:
        touch(src, rel)
    out = subprocess.run(["rsync", "-rn", "--out-format=%n", f"--exclude={pattern}", f"{src}/", f"{tmp_path}/dst/"],
                         check=True, capture_output=True, text=True).stdout.split("\n")
    copied_by_rsync = {l for l in out if l and not l.endswith("/")}
    rules = verify.Rules([f"- {pattern}\n"])

    def kept(rel):
        parts = rel.split("/")
        dirs = ["/".join(parts[:i]) for i in range(1, len(parts))]
        return not any(rules.dir_rule(d) for d in dirs) and rules.file_rule(rel) is None

    assert {rel for rel in PARITY_FILES if kept(rel)} == copied_by_rsync
