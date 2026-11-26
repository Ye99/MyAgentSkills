"""verify-backup.sh and restore-in-docker.sh's guards, with fake zstd, lsusb, sudo and docker.

No Jetson, USB device or container is touched: every external tool is a stub on PATH.
"""
import hashlib
import io
import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _stub(bin_, name, body):
    p = bin_ / name
    p.write_text("#!/usr/bin/env bash\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "calls.log"
    # .tar.zst files in these tests are plain tar: "zstd -dc f" just prints f
    _stub(bin_, "zstd", 'cat "${@: -1}"')
    _stub(bin_, "lsusb", 'echo "Bus 001 Device 009: ID 0955:7523 NVIDIA Corp. APX"')
    _stub(bin_, "sudo", f'echo "sudo $*" >> "{log}"; "$@"')
    _stub(bin_, "docker", f'echo "docker $*" >> "{log}"; exit 0')
    e = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}")
    e["CALLS"] = str(log)
    return e


def _tar(path, release):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        data = f"# {release}, REVISION: 3.0\n".encode()
        info = tarfile.TarInfo("./etc/nv_tegra_release")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    path.write_bytes(buf.getvalue())


def _backup(dirpath, release="R36 (release)", trailing_newline=True):
    dirpath.mkdir(parents=True)
    (dirpath / "nvme0n1p2.img").write_bytes(b"esp image")
    (dirpath / "QSPI0.img").write_bytes(b"qspi")
    _tar(dirpath / "nvme0n1p1.tar.zst", release)
    rows = ["board_spec,jetson-orin-nano-devkit-super"]
    for name, part in (("nvme0n1p1.tar.zst", "APP"), ("nvme0n1p2.img", "esp"), ("QSPI0.img", "QSPI0")):
        digest = hashlib.sha256((dirpath / name).read_bytes()).hexdigest()
        rows.append(f"{name},{part},0,1,raw,{digest}")
    (dirpath / "nvpartitionmap.txt").write_text("\n".join(rows) + ("\n" if trailing_newline else ""))
    return dirpath


def verify(env, d):
    return subprocess.run(["bash", str(SCRIPTS / "verify-backup.sh"), str(d)], env=env,
                          capture_output=True, text=True, timeout=30)


def test_good_backup_passes(env, tmp_path):
    r = verify(env, _backup(tmp_path / "b"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "VERIFY: PASS (3 files)" in r.stdout


def test_last_map_line_without_newline_is_still_checked(env, tmp_path):
    d = _backup(tmp_path / "b", trailing_newline=False)
    (d / "QSPI0.img").write_bytes(b"corrupted")
    r = verify(env, d)
    assert r.returncode != 0
    assert "BAD SUM  QSPI0.img" in r.stdout


def test_bad_checksum_and_missing_file_fail(env, tmp_path):
    d = _backup(tmp_path / "b")
    (d / "nvme0n1p2.img").write_bytes(b"flipped")
    (d / "QSPI0.img").unlink()
    r = verify(env, d)
    assert r.returncode != 0
    assert "BAD SUM  nvme0n1p2.img" in r.stdout and "MISSING  QSPI0.img" in r.stdout


def test_unreadable_app_archive_fails(env, tmp_path):
    d = _backup(tmp_path / "b")
    tarpath = d / "nvme0n1p1.tar.zst"
    tarpath.write_bytes(b"not a tar")
    rows = (d / "nvpartitionmap.txt").read_text().splitlines()
    rows[1] = f"nvme0n1p1.tar.zst,APP,0,1,raw,{hashlib.sha256(b'not a tar').hexdigest()}"
    (d / "nvpartitionmap.txt").write_text("\n".join(rows) + "\n")
    r = verify(env, d)
    assert r.returncode != 0 and "BAD TAR" in r.stdout


def _l4t(tmp_path, release):
    l4t = tmp_path / "Linux_for_Tegra"
    (l4t / "rootfs" / "etc").mkdir(parents=True)
    (l4t / "rootfs" / "etc" / "nv_tegra_release").write_text(f"# {release}, REVISION: 3.0\n")
    return l4t


def restore(env, l4t, backup):
    return subprocess.run(["bash", str(SCRIPTS / "restore-in-docker.sh"), str(l4t), "jetson-orin-nano-devkit-super",
                           str(backup)], env=env, capture_output=True, text=True, timeout=60)


def test_restore_refuses_a_release_mismatch_before_touching_the_board(env, tmp_path):
    l4t = _l4t(tmp_path, "R35 (release)")
    r = restore(env, l4t, _backup(tmp_path / "b", release="R36 (release)"))
    assert r.returncode != 0 and "release mismatch" in r.stderr
    assert "docker" not in Path(env["CALLS"]).read_text() if Path(env["CALLS"]).exists() else True


def test_restore_refuses_a_corrupt_backup(env, tmp_path):
    l4t = _l4t(tmp_path, "R36 (release)")
    d = _backup(tmp_path / "b")
    (d / "nvme0n1p2.img").write_bytes(b"flipped")
    r = restore(env, l4t, d)
    assert r.returncode != 0
    calls = Path(env["CALLS"])
    assert not calls.exists() or "docker" not in calls.read_text()
