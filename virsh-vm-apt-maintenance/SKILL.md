---
name: virsh-vm-apt-maintenance
description: Patch one or more libvirt/QEMU/KVM Debian or Ubuntu guest VMs unattended - start them in parallel, resolve each guest's IP from the DHCP leases, SSH in, run apt update/upgrade/autopurge and fstrim, then return each VM to the power state it had before. Use this whenever the user asks to update, upgrade, patch, apt-upgrade, do maintenance on, or "run updates on" one or more VMs, virtual machines, guests, or domains managed by virsh / virt-manager / libvirt on this Linux host - including phrasings like "start VM X and Y and upgrade them", "patch my VMs and shut them down", or "run apt update on the 3D print VM". Also use it when a VM is named but not yet running and the task needs it patched.
---

# Unattended apt maintenance for libvirt guests

## Purpose

Bring one or more Debian/Ubuntu VMs on this KVM host up to date without babysitting
them, then put the host back the way it was found. The whole job per VM is:

start (if off) → resolve IP → wait for sshd → `apt update` / `upgrade` /
`autopurge` / `fstrim -av` → power off (only if it was off to begin with).

Multiple VMs run in parallel, because the slow part is downloading packages and
there is no reason to do that one VM at a time.

## Use the script

`scripts/vm-apt-maintenance.sh` does the whole job and prints a summary table.
Prefer it over hand-rolling the loop — it already handles the timing problems
(lease appears well after `virsh start` returns, sshd appears after that) and the
failure modes that otherwise hang a run for ten minutes before you notice.

```bash
scripts/vm-apt-maintenance.sh process-photo-vm UEFI-Ubuntu-3DPrint
```

Useful flags:

| Flag | Why |
|------|-----|
| `--check` | Prove the VM gets an IP, answers SSH and has passwordless sudo, and report how many upgrades are pending — without touching a package. Good first move on a VM you have not patched before. It does boot a VM that is off (the only way to reach it) and then puts it back. |
| `--user NAME` | SSH user in the guest, if it is not your host username. |
| `--poweroff-all` | Power off every VM at the end, including ones that were already running. Only when the user explicitly asks for that. |
| `--no-poweroff` | Leave everything running. |
| `--log-dir DIR` | Keep the per-VM logs somewhere you can point the user at. |
| `--boot-timeout N` | Raise from 300s for a slow-booting guest. |

Exit status is 0 only when every VM finished its chain. The per-VM log
(`<log-dir>/<vm>.log`) holds the full apt transcript — read it before reporting
anything, and quote real numbers from it rather than saying "updated
successfully".

## Power state is restored, not forced

By default a VM that was already running when you started is **left running**.
Someone may be using it, and shutting it down underneath them is the kind of
surprise that makes an agent untrustworthy. A VM that was shut off gets powered
back off when its maintenance finishes.

If the user says "and power them all off", pass `--poweroff-all` — but say in the
summary which already-running VMs you shut down, so it is visible.

Never touch a VM the user did not name. On a host with a dozen domains it is easy
to "helpfully" patch a neighbour; don't.

## Doing it by hand

If the script does not fit (unusual network, one-off command, guest without
passwordless sudo), the sequence is below. The notes are the parts that actually
bite.

**1. Find the domains.** Plain `virsh` talks to `qemu:///session`, which on most
hosts is an *empty* VM list — the VMs live in the system instance. If
`virsh list --all` shows nothing, that is why:

```bash
virsh -c qemu:///system list --all
```

**2. Record state, then start.** Check `virsh domstate` per VM *before* starting
anything, or you lose the information needed to restore it later.

```bash
virsh -c qemu:///system domstate VM
virsh -c qemu:///system start VM
```

**3. Resolve the IP.** With qemu-guest-agent installed, `domifaddr --source agent`
answers immediately. Otherwise read the NAT network's lease table and match on the
VM's MAC from its XML. The lease shows up roughly 30–60s after `start` returns, so
poll rather than reading it once and concluding the VM has no address:

```bash
mac=$(virsh -c qemu:///system dumpxml VM | grep -oPm1 "(?<=<mac address=')[^']+")
virsh -c qemu:///system net-dhcp-leases default | grep "$mac"
```

**4. Check sudo before running anything long.** A guest that wants a sudo password
will hang a non-interactive SSH session until it times out, in the middle of an
apt run. One cheap probe up front turns that into a clear error:

```bash
ssh -o BatchMode=yes user@IP 'sudo -n true && echo NOPASSWD_OK || echo NEEDS_PASSWORD'
```

**5. Patch.** `DEBIAN_FRONTEND=noninteractive` plus the dpkg conffile options are
what keep an unattended run from stopping on a "keep or replace this config file?"
prompt. `apt-get` rather than `apt` because `apt` warns that its CLI is not stable
for scripts (`apt upgrade` ≡ `apt-get upgrade --with-new-pkgs`, `apt autopurge` ≡
`apt-get autoremove --purge`):

Send the script to an explicit `bash -s` rather than passing a command string:

```bash
ssh -o BatchMode=yes user@IP bash -s <<'EOF'
export DEBIAN_FRONTEND=noninteractive
APTOPTS="-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"
sudo -E apt-get update &&
sudo -E apt-get $APTOPTS --with-new-pkgs upgrade &&
sudo -E apt-get $APTOPTS --purge autoremove &&
sudo fstrim -av
EOF
```

That `bash -s` is not decoration. A plain `ssh host 'command string'` is executed
by the guest user's **login shell**, which on a developer's VM is frequently zsh
— and zsh does not word-split an unquoted `$APTOPTS`, so the whole option string
arrives at apt-get as one argument and the run dies with:

```
E: Command line option ' ' [from -y -o Dpkg::Options::=--force-confdef ...] is not understood
```

This is the most likely reason a hand-written version of this loop fails on the
very first VM, and it is invisible until you look at the log. Naming the
interpreter removes the whole class of problem.

**6. Power off as a separate command.** The SSH connection dies with the guest, so
a nonzero ssh exit here means nothing. Confirm with `domstate` instead of trusting
the exit code:

```bash
ssh -o BatchMode=yes user@IP 'sudo systemctl poweroff'
virsh -c qemu:///system domstate VM   # poll until "shut off"
```

If it will not go down, `virsh shutdown VM` sends ACPI. Reach for `virsh destroy`
only if the user asks — it is the equivalent of pulling the power cord on a
machine that may still be writing to disk.

## Reporting back

Give the user a table, not prose. Per VM: packages upgraded / newly installed /
removed, bytes trimmed, whether `/var/run/reboot-required` exists, and the final
power state. The script prints this; when working by hand pull the numbers from
apt's own `N upgraded, N newly installed, N to remove` lines and fstrim's output.

Two things always worth calling out because the user cannot see them otherwise:

- **reboot-required** on a VM you just powered off is already resolved — the next
  boot uses the new kernel. Say so rather than leaving it as a scary flag.
- **A VM that failed** partway. Name it, give the log path, and leave it running
  so the user can look at it — do not power off a guest whose dpkg state you did
  not verify is clean.
