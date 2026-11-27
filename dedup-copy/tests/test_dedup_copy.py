import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def run(script, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                          capture_output=True, text=True)


@pytest.fixture
def tree(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.jpg").write_bytes(b"photo")
    (src / "sub" / "a (1).jpg").write_bytes(b"photo")
    (src / "b.jpg").write_bytes(b"other")
    jd = tmp_path / "jdupes.txt"
    jd.write_text(f"{src / 'a.jpg'}\n{src / 'sub' / 'a (1).jpg'}\n\n")
    return src, tmp_path / "dest", jd, tmp_path / "log.json"


def copy(src, dest, jd, log, *extra):
    return run("dedup_and_copy.py", src, dest, "--jdupes-output", jd, "--log", log, *extra)


def verify(src, dest, log, *extra):
    return run("verify_dedup_copy.py", src, dest, "--log", log, *extra)


def test_duplicates_are_skipped_and_verification_passes(tree):
    src, dest, jd, log = tree
    r = copy(src, dest, jd, log)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()) == ["a.jpg", "b.jpg"]
    v = verify(src, dest, log)
    assert v.returncode == 0, v.stdout


def test_a_file_edited_after_jdupes_ran_is_copied_not_lost(tree):
    src, dest, jd, log = tree
    (src / "sub" / "a (1).jpg").write_bytes(b"photo, then cropped")
    r = copy(src, dest, jd, log)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dest / "sub" / "a (1).jpg").read_bytes() == b"photo, then cropped"
    assert verify(src, dest, log).returncode == 0


def test_verifier_fails_when_a_skipped_file_differs_from_its_keeper(tree):
    src, dest, jd, log = tree
    assert copy(src, dest, jd, log).returncode == 0
    (src / "sub" / "a (1).jpg").write_bytes(b"changed after the copy")  # the skip is no longer safe
    v = verify(src, dest, log)
    assert v.returncode == 1
    assert "a (1).jpg" in v.stdout


def test_verifier_hashes_every_target_file(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    src.mkdir()
    for i in range(300):
        (src / f"f{i:03}.bin").write_bytes(f"content {i}".encode())
    jd = tmp_path / "jd.txt"
    jd.write_text("")
    log = tmp_path / "log.json"
    assert copy(src, dest, jd, log).returncode == 0
    (dest / "f137.bin").write_bytes(b"bit rot")
    v = verify(src, dest, log)
    assert v.returncode == 1
    assert "f137.bin" in v.stdout


def test_refuses_a_non_empty_destination(tree):
    src, dest, jd, log = tree
    dest.mkdir()
    (dest / "b.jpg").write_bytes(b"someone else's b")
    r = copy(src, dest, jd, log)
    assert r.returncode != 0
    assert (dest / "b.jpg").read_bytes() == b"someone else's b"


def test_resume_never_overwrites_a_different_file(tree):
    src, dest, jd, log = tree
    dest.mkdir()
    (dest / "a.jpg").write_bytes(b"photo")            # finished on the interrupted run
    (dest / "b.jpg").write_bytes(b"someone else's b")
    r = copy(src, dest, jd, log, "--resume")
    assert r.returncode != 0
    assert (dest / "b.jpg").read_bytes() == b"someone else's b"
    assert "b.jpg" in r.stdout + r.stderr


def test_resume_finishes_an_interrupted_copy(tree):
    src, dest, jd, log = tree
    dest.mkdir()
    (dest / "a.jpg").write_bytes(b"photo")
    r = copy(src, dest, jd, log, "--resume")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dest / "b.jpg").read_bytes() == b"other"
    assert verify(src, dest, log).returncode == 0


def test_refuses_a_destination_inside_the_source(tree):
    src, _, jd, log = tree
    r = copy(src, src / "deduped", jd, log)
    assert r.returncode != 0
    assert not (src / "deduped").exists()


def test_keeper_is_never_an_excluded_file(tree):
    src, dest, jd, log = tree
    # the better-scored name is excluded by filter: the other member must be copied instead
    r = copy(src, dest, jd, log, "--exclude-name", "a.jpg")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dest / "sub" / "a (1).jpg").read_bytes() == b"photo"
    assert verify(src, dest, log).returncode == 0


def test_a_source_file_named_like_a_temp_file_is_not_overwritten(tree):
    src, dest, jd, log = tree
    (src / ".b.jpg.dedup-copy-part").write_bytes(b"a real file with an odd name")
    r = copy(src, dest, jd, log)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dest / ".b.jpg.dedup-copy-part").read_bytes() == b"a real file with an odd name"
    assert (dest / "b.jpg").read_bytes() == b"other"
    assert verify(src, dest, log).returncode == 0


def test_resume_leaves_an_existing_destination_file_with_a_temp_like_name(tree):
    src, dest, jd, log = tree
    dest.mkdir()
    (dest / ".b.jpg.dedup-copy-part").write_bytes(b"someone else's file")
    r = copy(src, dest, jd, log, "--resume")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dest / ".b.jpg.dedup-copy-part").read_bytes() == b"someone else's file"
    assert (dest / "b.jpg").read_bytes() == b"other"
