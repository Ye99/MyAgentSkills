"""zipnewutm.sh and restoreutm.sh under zsh, with uname and ditto stubbed (no macOS needed)."""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
pytestmark = pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")

# -c ... SRC DST writes "bundle=<name>" to DST; -x -k ZIP DIR recreates the bundle in DIR.
DITTO = r'''#!/usr/bin/env bash
echo "ditto $*" >> "$CALLS"
if [ "$1" = "-c" ]; then
  src="${@: -2:1}"; dst="${@: -1}"
  echo "bundle=$(basename "$src")" > "$dst"
  if [ -n "${FAKE_DITTO_FAIL:-}" ]; then exit 1; fi
  echo "complete" >> "$dst"
elif [ "$1" = "-x" ]; then
  zip="$3"; dir="$4"; name=$(sed -n 's/^bundle=//p' "$zip")
  mkdir -p "$dir/$name"; echo restored > "$dir/$name/config.plist"
fi
'''


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in {"uname": "#!/usr/bin/env bash\necho Darwin\n", "ditto": DITTO}.items():
        p = bin_ / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    calls = tmp_path / "calls.log"
    calls.write_text("")
    return dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", CALLS=str(calls), HOME=str(tmp_path))


def zsh(script, *args, env, **extra):
    return subprocess.run(["zsh", str(SCRIPTS / script), *map(str, args)], env={**env, **extra},
                          capture_output=True, text=True, timeout=30)


@pytest.fixture
def vms(tmp_path):
    d = tmp_path / "vms"
    for n in ("A", "B"):
        (d / f"{n}.utm").mkdir(parents=True)
    return d


def test_zips_each_bundle_and_skips_existing(env, vms):
    (vms / "B.zip").write_text("old")
    r = zsh("zipnewutm.sh", "--dir", vms, env=env)
    assert r.returncode == 0, r.stderr
    assert (vms / "A.zip").read_text() == "bundle=A.utm\ncomplete\n"
    assert (vms / "B.zip").read_text() == "old"
    assert "Skipping" in r.stdout


def test_force_replaces_and_dry_run_writes_nothing(env, vms):
    (vms / "B.zip").write_text("old")
    r = zsh("zipnewutm.sh", "--dir", vms, "--dry-run", "--force", env=env)
    assert r.returncode == 0 and not (vms / "A.zip").exists() and (vms / "B.zip").read_text() == "old"
    r = zsh("zipnewutm.sh", "--dir", vms, "--force", env=env)
    assert r.returncode == 0, r.stderr
    assert (vms / "B.zip").read_text() == "bundle=B.utm\ncomplete\n"


def test_failed_zip_leaves_no_zip_that_a_rerun_would_skip(env, vms):
    r = zsh("zipnewutm.sh", "--dir", vms, env=env, FAKE_DITTO_FAIL="1")
    assert r.returncode != 0
    assert not list(vms.glob("*.zip")), "a partial zip would be skipped as done next time"
    r = zsh("zipnewutm.sh", "--dir", vms, env=env)
    assert r.returncode == 0, r.stderr
    assert (vms / "A.zip").read_text().endswith("complete\n")


def test_non_mac_refused(env, vms, tmp_path):
    (tmp_path / "bin" / "uname").write_text("#!/usr/bin/env bash\necho Linux\n")
    r = zsh("zipnewutm.sh", "--dir", vms, env=env)
    assert r.returncode == 1 and "macOS" in r.stderr


def test_restore_into_directory(env, tmp_path):
    z = tmp_path / "A.zip"
    z.write_text("bundle=A.utm\ncomplete\n")
    dest = tmp_path / "docs"
    r = zsh("restoreutm.sh", z, "--restore-dir", dest, env=env)
    assert r.returncode == 0, r.stderr
    assert (dest / "A.utm" / "config.plist").read_text() == "restored\n"
    assert [p.name for p in dest.iterdir()] == ["A.utm"]


def test_restore_refuses_to_overwrite_an_existing_bundle(env, tmp_path):
    z = tmp_path / "A.zip"
    z.write_text("bundle=A.utm\ncomplete\n")
    dest = tmp_path / "docs"
    (dest / "A.utm").mkdir(parents=True)
    (dest / "A.utm" / "config.plist").write_text("live VM")
    r = zsh("restoreutm.sh", z, "--restore-dir", dest, env=env)
    assert r.returncode != 0
    assert "already exists" in r.stderr
    assert (dest / "A.utm" / "config.plist").read_text() == "live VM"
    assert [p.name for p in dest.iterdir()] == ["A.utm"], "no temp dir left behind"


def test_restore_dry_run_and_missing_zip(env, tmp_path):
    z = tmp_path / "A.zip"
    z.write_text("x")
    r = zsh("restoreutm.sh", z, "--dry-run", env=env)
    assert r.returncode == 0 and "Would restore" in r.stdout
    assert "ditto" not in (tmp_path / "calls.log").read_text()
    r = zsh("restoreutm.sh", tmp_path / "nope.zip", env=env)
    assert r.returncode == 2
