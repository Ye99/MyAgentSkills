"""flash-in-docker.sh and host-nfs.sh with every system tool stubbed on PATH.

No Jetson, container, NFS server, firewall or /etc file is touched: host-nfs.sh is
pointed at temp files through its JETSON_NFS_* overrides.
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _stub(bin_, name, body):
    p = bin_ / name
    p.write_text("#!/usr/bin/env bash\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def tools(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    calls = tmp_path / "calls.log"
    rec = f'echo "$(basename "$0") $*" >> "{calls}"'
    _stub(bin_, "lsusb", 'echo "Bus 001 Device 009: ID ${FAKE_USB:-0955:7523} NVIDIA Corp. APX"')
    _stub(bin_, "docker", rec + '\necho "flash output"; exit ${FAKE_RC:-0}')
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}")
    return bin_, calls, env


def flash(env, l4t, **extra):
    return subprocess.run(["bash", str(SCRIPTS / "flash-in-docker.sh"), str(l4t)], env={**env, **extra},
                          capture_output=True, text=True, timeout=30)


def test_failed_flash_exits_nonzero(tools, tmp_path):
    _, calls, env = tools
    l4t = tmp_path / "Linux_for_Tegra"
    l4t.mkdir()
    r = flash(env, l4t, FAKE_RC="3")
    assert r.returncode != 0
    assert "FAILED" in r.stderr


def test_successful_flash_exits_zero(tools, tmp_path):
    _, calls, env = tools
    l4t = tmp_path / "Linux_for_Tegra"
    l4t.mkdir()
    r = flash(env, l4t)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("usb", ["1d6b:0002", "0955:7020"])
def test_flash_refuses_without_a_jetson_in_recovery_mode(tools, tmp_path, usb):
    _, calls, env = tools
    l4t = tmp_path / "Linux_for_Tegra"
    l4t.mkdir()
    r = flash(env, l4t, FAKE_USB=usb)
    assert r.returncode != 0
    assert not calls.exists() or "docker" not in calls.read_text()


@pytest.fixture
def nfs(tools, tmp_path):
    bin_, calls, env = tools
    rec = f'echo "$(basename "$0") $*" >> "{calls}"'
    units = tmp_path / "units"
    units.mkdir()
    (units / "rpc-statd").write_text("masked")
    _stub(bin_, "id", 'echo 0')
    _stub(bin_, "sysctl", 'echo 0')
    _stub(bin_, "exportfs", rec)
    _stub(bin_, "showmount", 'echo "Export list"; grep -v "^#" "$JETSON_NFS_EXPORTS" | cut -d" " -f1')
    _stub(bin_, "nmcli", rec)
    _stub(bin_, "ufw", rec + '\n[ "$1" = status ] && echo "Status: inactive"; true')
    _stub(bin_, "systemctl", rec + f'''
case "$1" in
  is-enabled) cat "{units}/$2" 2>/dev/null || echo enabled ;;
  is-active) [ "$2" = "-q" ] && exit 1; echo inactive ;;
  unmask) shift; for u in "$@"; do echo enabled > "{units}/$u"; done ;;
  mask) shift; for u in "$@"; do echo masked > "{units}/$u"; done ;;
esac
true''')
    exports = tmp_path / "exports"
    exports.write_text("/srv/share 192.168.1.0/24(ro)\n")
    env = dict(env, JETSON_NFS_EXPORTS=str(exports), JETSON_NFS_STATE=str(tmp_path / "state"),
               JETSON_NFS_NM_CONF=str(tmp_path / "nm.conf"))
    l4t = tmp_path / "Linux_for_Tegra"
    l4t.mkdir()
    return env, exports, units, l4t


def nfs_cmd(env, *args):
    return subprocess.run(["bash", str(SCRIPTS / "host-nfs.sh"), *args], env=env, capture_output=True,
                          text=True, timeout=30)


def test_host_nfs_up_then_down_restores_everything(nfs):
    env, exports, units, l4t = nfs
    before = exports.read_text()
    r = nfs_cmd(env, "up", str(l4t))
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"{l4t}/tools/kernel_flash/images fc00:1:1::/48(" in exports.read_text()
    assert (units / "rpc-statd").read_text().strip() == "enabled"
    r = nfs_cmd(env, "down")
    assert r.returncode == 0, r.stdout + r.stderr
    assert exports.read_text() == before
    assert (units / "rpc-statd").read_text().strip() == "masked"
    assert not Path(env["JETSON_NFS_STATE"]).exists()


def test_host_nfs_up_refuses_a_second_up(nfs):
    env, exports, units, l4t = nfs
    assert nfs_cmd(env, "up", str(l4t)).returncode == 0
    after_first = exports.read_text()
    r = nfs_cmd(env, "up", str(l4t))
    assert r.returncode != 0
    assert exports.read_text() == after_first
