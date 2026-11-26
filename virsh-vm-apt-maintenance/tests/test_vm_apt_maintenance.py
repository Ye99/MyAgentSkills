"""vm-apt-maintenance.sh against fake virsh and ssh on PATH. No real VM is touched."""
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vm-apt-maintenance.sh"

FAKE_VIRSH = r"""#!/usr/bin/env bash
# fake virsh: state in $FAKE/state, calls logged to $FAKE/virsh.log
shift 2   # -c URI
echo "$*" >> "$FAKE/virsh.log"
case "$1" in
  domstate) cat "$FAKE/state" ;;
  start) echo running > "$FAKE/state" ;;
  shutdown|destroy) echo "shut off" > "$FAKE/state" ;;
  dumpxml) echo "<mac address='52:54:00:aa:bb:cc'/><source network='default'/>" ;;
  domifaddr) exit 1 ;;
  net-dhcp-leases) cat "$FAKE/leases" ;;
  list) echo " - vm1 shut off" ;;
esac
"""

FAKE_SSH = r"""#!/usr/bin/env bash
# fake ssh: the host is the argument after the options; fails while $FAKE/ssh_down exists
host=""
for a in "$@"; do case "$a" in *@*) host="${a#*@}";; esac; done
echo "$host" >> "$FAKE/ssh_hosts"
[ -e "$FAKE/ssh_down" ] && exit 255
last="${@: -1}"
if [ "$last" = "bash -s" ]; then cat >> "$FAKE/remote_scripts"; exit 0; fi
case "$last" in *poweroff*) echo "shut off" > "$FAKE/state";; esac
exit 0
"""

FAKE_SLEEP = "#!/usr/bin/env bash\n/bin/sleep 0.05\n"

LEASES = """ Expiry Time           MAC address         Protocol   IP address          Hostname   Client ID or DUID
-------------------------------------------------------------------------------------------------------------
 2026-11-25 08:00:00   52:54:00:aa:bb:cc   ipv4       192.168.122.10/24   vm1        -
 2026-11-26 09:00:00   52:54:00:aa:bb:cc   ipv4       192.168.122.20/24   vm1        -
"""


def _env(tmp_path, state="shut off", ssh_down=False):
    fake = tmp_path / "fake"
    bin_ = tmp_path / "bin"
    fake.mkdir()
    bin_.mkdir()
    for name, body in (("virsh", FAKE_VIRSH), ("ssh", FAKE_SSH), ("sleep", FAKE_SLEEP)):
        p = bin_ / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    (fake / "state").write_text(state + "\n")
    (fake / "leases").write_text(LEASES)
    if ssh_down:
        (fake / "ssh_down").touch()
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", FAKE=str(fake), USER="tester")
    return fake, env


def _run(tmp_path, env, *args):
    return subprocess.run(["bash", str(SCRIPT), "--log-dir", str(tmp_path / "logs"), "--boot-timeout", "1",
                           "--off-timeout", "1", *args, "vm1"], env=env, capture_output=True, text=True, timeout=60)


def test_vm_it_started_is_shut_down_when_ssh_never_comes_up(tmp_path):
    fake, env = _env(tmp_path, ssh_down=True)
    r = _run(tmp_path, env)
    assert r.returncode != 0
    assert (fake / "state").read_text().strip() == "shut off"
    assert "shutdown vm1" in (fake / "virsh.log").read_text()


def test_vm_that_was_running_is_left_running_after_an_early_failure(tmp_path):
    fake, env = _env(tmp_path, state="running", ssh_down=True)
    r = _run(tmp_path, env)
    assert r.returncode != 0
    assert (fake / "state").read_text().strip() == "running"


def test_no_poweroff_leaves_it_running_after_an_early_failure(tmp_path):
    fake, env = _env(tmp_path, ssh_down=True)
    r = _run(tmp_path, env, "--no-poweroff")
    assert r.returncode != 0
    assert (fake / "state").read_text().strip() == "running"


def test_newest_dhcp_lease_wins(tmp_path):
    fake, env = _env(tmp_path)
    r = _run(tmp_path, env, "--check")
    assert r.returncode == 0, r.stdout + r.stderr
    hosts = set((fake / "ssh_hosts").read_text().split())
    assert hosts == {"192.168.122.20"}


def test_apt_chain_does_not_need_sudo_to_preserve_the_environment(tmp_path):
    fake, env = _env(tmp_path)
    r = _run(tmp_path, env)
    assert r.returncode == 0, r.stdout + r.stderr
    remote = (fake / "remote_scripts").read_text()
    assert "sudo -E" not in remote
    assert "DEBIAN_FRONTEND=noninteractive apt-get" in remote
    assert (fake / "state").read_text().strip() == "shut off"
