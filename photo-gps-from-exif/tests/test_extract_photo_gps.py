"""extract_photo_gps.sh against a fake exiftool on PATH."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract_photo_gps.sh"

DIR_OUTPUT = """======== {d}/a.jpg
File Name                       : a.jpg
GPS Latitude                    : 47.6062
GPS Longitude                   : -122.3321
======== {d}/b.heic
File Name                       : b.heic
    1 directories scanned
    2 image files read
"""


@pytest.fixture
def fake_exiftool(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    out = tmp_path / "exif.out"
    p = bin_ / "exiftool"
    p.write_text(f'#!/usr/bin/env bash\ncat "{out}"\nexit ${{FAKE_RC:-0}}\n')
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}")
    return out, env


def run(env, target, **extra):
    return subprocess.run(["bash", str(SCRIPT), str(target)], env={**env, **extra},
                          capture_output=True, text=True, timeout=30)


def test_directory_lists_files_with_and_without_gps(fake_exiftool, tmp_path):
    out, env = fake_exiftool
    out.write_text(DIR_OUTPUT.format(d=tmp_path))
    r = run(env, tmp_path)
    assert r.returncode == 0, r.stderr
    with_gps, without = r.stdout.split("Files without GPS:")
    assert f"{tmp_path}/a.jpg: 47.6062, -122.3321" in with_gps
    assert f"{tmp_path}/b.heic" in without
    assert "directories scanned" not in r.stdout


def test_single_file_without_header(fake_exiftool, tmp_path):
    out, env = fake_exiftool
    f = tmp_path / "c.jpg"
    f.write_bytes(b"")
    out.write_text("File Name                       : c.jpg\nGPS Latitude                    : 1.5\n"
                   "GPS Longitude                   : 2.5\n")
    r = run(env, f)
    assert r.returncode == 0, r.stderr
    assert "- c.jpg: 1.5, 2.5" in r.stdout


def test_one_unreadable_file_does_not_lose_the_report(fake_exiftool, tmp_path):
    # exiftool exits 1 when any file errors, but still prints the others.
    out, env = fake_exiftool
    out.write_text(DIR_OUTPUT.format(d=tmp_path))
    r = run(env, tmp_path, FAKE_RC="1")
    assert r.returncode == 0, r.stderr
    assert "a.jpg: 47.6062, -122.3321" in r.stdout
    assert "warning" in r.stderr.lower()


def test_exiftool_failure_with_no_output_fails(fake_exiftool, tmp_path):
    out, env = fake_exiftool
    out.write_text("")
    r = run(env, tmp_path, FAKE_RC="1")
    assert r.returncode != 0


def test_missing_path_fails(fake_exiftool, tmp_path):
    _, env = fake_exiftool
    r = run(env, tmp_path / "nope")
    assert r.returncode == 1
    assert "does not exist" in r.stderr
