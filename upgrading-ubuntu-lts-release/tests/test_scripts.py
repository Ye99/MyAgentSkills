"""postcheck.sh and watch.sh helpers, sourced in isolation. Nothing runs on a real host."""
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def bash(code, **env):
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", **env}, timeout=30)


def test_postcheck_empty_sudoers_d_is_not_a_failure(tmp_path):
    sudoers = tmp_path / "sudoers"
    sudoers.write_text("root ALL=(ALL) ALL\n")
    d = tmp_path / "sudoers.d"
    d.mkdir()
    r = bash(f'POSTCHECK_FUNCS_ONLY=1 . "{SCRIPTS}/postcheck.sh"; S(){{ "$@"; }}; '
             f'check_sudoers_newlines "{sudoers}" "{d}"')
    assert r.returncode == 0, r.stderr
    assert "FAIL" not in r.stdout, r.stdout


def test_postcheck_flags_a_sudoers_file_without_trailing_newline(tmp_path):
    sudoers = tmp_path / "sudoers"
    sudoers.write_text("root ALL=(ALL) ALL\n")
    d = tmp_path / "sudoers.d"
    d.mkdir()
    (d / "extra").write_text("ye ALL=(ALL) NOPASSWD: ALL")
    r = bash(f'POSTCHECK_FUNCS_ONLY=1 . "{SCRIPTS}/postcheck.sh"; S(){{ "$@"; }}; '
             f'check_sudoers_newlines "{sudoers}" "{d}"')
    assert f"FAIL sudoers {d}/extra lacks trailing newline" in r.stdout


@pytest.mark.parametrize("line, prompt", [
    ("Continue [yN]  Details [d]", True),
    ("Remove obsolete packages? [yN]", True),
    ("*** sshd_config (Y/I/N/O/D/Z) [default=N] ?", True),
    ("Restart services during package upgrades without asking?", True),
    ("Progress: [ 45%] [###########.................]", False),
    ("Get:1234 http://archive.ubuntu.com/ubuntu noble/main amd64 libfoo [120 kB]", False),
    ("Unpacking linux-firmware (20240318) over (20220329) ...", False),
    ("Setting up systemd (255.4-1ubuntu8) ...", False),
])
def test_watch_prompt_heuristic(line, prompt):
    r = bash(f'WATCH_FUNCS_ONLY=1 . "{SCRIPTS}/watch.sh"; looks_like_prompt "$LINE" && echo yes || echo no',
             LINE=line)
    assert r.stdout.strip() == ("yes" if prompt else "no"), r.stderr
