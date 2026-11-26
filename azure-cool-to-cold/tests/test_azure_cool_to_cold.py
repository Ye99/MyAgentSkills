"""azure_cool_to_cold.py against a fake rclone on PATH. No Azure account is touched."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "azure_cool_to_cold.py"

FAKE_RCLONE = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE"]
args = sys.argv[1:]
with open(os.path.join(d, "calls.log"), "a") as f:
    f.write(" ".join(args) + "\n")
objs_path = os.path.join(d, "objects.json")
if args[:2] == ["backend", "features"]:
    print(open(os.path.join(d, "features.json")).read())
elif args[0] == "lsjson":
    print(open(objs_path).read())
elif args[0] == "settier":
    if "--dry-run" not in args:
        objs = json.load(open(objs_path))
        for o in objs:
            o["Tier"] = args[1]
        json.dump(objs, open(objs_path, "w"))
else:
    sys.exit(1)
'''

AZURE = {"String": "Azure container media", "Features": {"SetTier": True}}
CRYPT = {"String": "Encrypted drive 'secret:'", "Features": {"SetTier": True}}


@pytest.fixture
def fake(tmp_path):
    d = tmp_path / "fake"
    d.mkdir()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    rc = bin_ / "rclone"
    rc.write_text(FAKE_RCLONE)
    rc.chmod(rc.stat().st_mode | stat.S_IEXEC)
    (d / "features.json").write_text(json.dumps(AZURE))
    set_objects(d, [("a.jpg", 10, "Cool"), ("b/c.jpg", 20, "Cool")])
    return SimpleNamespace(dir=d, env=dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", FAKE=str(d)),
                           state=tmp_path / "state.json")


def set_objects(d, items):
    d = getattr(d, "dir", d)
    (d / "objects.json").write_text(json.dumps(
        [{"Path": p, "Size": s, "Tier": t, "IsDir": False} for p, s, t in items]))


def tiers(d):
    return sorted(o["Tier"] for o in json.loads((d.dir / "objects.json").read_text()))


def run(d, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=d.env, capture_output=True, text=True)


def settier_calls(d):
    log = d.dir / "calls.log"
    return [line for line in log.read_text().splitlines() if line.startswith("settier")] if log.exists() else []


@pytest.mark.parametrize("target", ["remote:", "remote", "remote:box/sub", "remote:..", "a:b:c"])
def test_targets_other_than_one_whole_container_are_refused(fake, target):
    r = run(fake, "preflight", target, "--state", str(fake.state))
    assert r.returncode != 0
    assert not fake.state.exists()


def test_crypt_overlay_is_refused(fake):
    (fake.dir / "features.json").write_text(json.dumps(CRYPT))
    r = run(fake, "preflight", "remote:box", "--state", str(fake.state))
    assert r.returncode != 0 and "crypt" in r.stderr


def test_preflight_refuses_files_not_all_cool(fake):
    set_objects(fake, [("a.jpg", 10, "Cool"), ("b.jpg", 20, "Hot")])
    r = run(fake, "preflight", "remote:box", "--state", str(fake.state))
    assert r.returncode != 0 and "Cool" in r.stderr


def test_full_flow_converts_and_verifies(fake):
    assert run(fake, "preflight", "remote:box", "--state", str(fake.state)).returncode == 0
    r = run(fake, "dry-run", "remote:box", "--state", str(fake.state))
    assert r.returncode == 0, r.stderr
    assert tiers(fake) == ["Cool", "Cool"]
    assert all("--dry-run" in c for c in settier_calls(fake))
    r = run(fake, "apply", "remote:box", "--state", str(fake.state), "--confirm-target", "remote:box",
            "--confirm-count", "2")
    assert r.returncode == 0, r.stderr
    assert tiers(fake) == ["Cold", "Cold"]
    assert run(fake, "verify", "remote:box", "--state", str(fake.state)).returncode == 0
    assert "post_change" in json.loads(fake.state.read_text())


@pytest.mark.parametrize("confirm_target, confirm_count", [("remote:other", "2"), ("remote:box", "3")])
def test_apply_refuses_a_wrong_confirmation(fake, confirm_target, confirm_count):
    run(fake, "preflight", "remote:box", "--state", str(fake.state))
    r = run(fake, "apply", "remote:box", "--state", str(fake.state), "--confirm-target", confirm_target,
            "--confirm-count", confirm_count)
    assert r.returncode != 0
    assert settier_calls(fake) == []


def test_apply_refuses_drift_since_preflight(fake):
    run(fake, "preflight", "remote:box", "--state", str(fake.state))
    set_objects(fake, [("a.jpg", 10, "Cool"), ("b/c.jpg", 21, "Cool")])
    r = run(fake, "apply", "remote:box", "--state", str(fake.state), "--confirm-target", "remote:box",
            "--confirm-count", "2")
    assert r.returncode != 0 and "drifted" in r.stderr
    assert settier_calls(fake) == []


def test_state_for_another_container_is_refused(fake):
    run(fake, "preflight", "remote:box", "--state", str(fake.state))
    r = run(fake, "apply", "remote:other", "--state", str(fake.state), "--confirm-target", "remote:other",
            "--confirm-count", "2")
    assert r.returncode != 0
    assert settier_calls(fake) == []
