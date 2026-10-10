---
name: upgrading-ubuntu-lts-release
description: Use when upgrading an Ubuntu machine to the next LTS release (e.g. 24.04 to 26.04) with do-release-upgrade, especially over SSH or on a headless server, a ZFS host, or a desktop whose session the upgrade will kill; or when an upgrade shows "There is no development version of an LTS available", "Please install all available updates for your release before upgrading", "Could not download the release announcement", `/usr/bin/sudo` missing mid-upgrade, hundreds of `iU` packages, SSH refusing connections, or "Remove obsolete packages?".
---

# Upgrading Ubuntu to the Next LTS

## Overview

`do-release-upgrade` is safe to run remotely **if** it runs in a detached root tmux session, is watched with probes that survive the sudo → sudo-rs swap, and every prompt is answered from evidence (the plan in `main.log`, a conffile diff) rather than from the screen text. The upgrade is done only after the post-reboot check passes.

## Procedure

Scripts live in `scripts/`; run host-side ones with `ssh <host> 'bash -s' < scripts/<name>.sh` (the login shell may be zsh — see Common Mistakes).

1. **Pre-flight (read-only):** `preflight.sh`. Stop and fix any `FAIL`. It reports:
   - **Channel.** `-c` offers the release → plain upgrade. Only `-c -p` offers it → use `-p` after confirming `meta-release-lts-proposed` says `Dist: <codename>`, `Supported: 1`. **Never `-d`** — it targets the next *development* release, not the LTS.
   - **Pending updates.** Any non-phased update blocks the upgrader: `apt-get dist-upgrade` first. A phased leftover does not block.
   - Sudoers files without a trailing newline (sudo-rs rejects them), held packages, root filesystem type, busy ZFS/backup jobs, third-party sources, desktop vs headless.
2. **Rollback point.** ZFS root: `zfs snapshot -r` of the root and boot datasets, named `pre-upgrade-<from>-to-<to>-<YYYYMMDD-HHMM>`, VMs shut off. Always: `backup.sh` (`/etc` tarball, package lists, network and ZFS state) under `/root/upgrade-<to>-<host>-<ts>`. On ext4 the backup is the only rollback material — say so.
3. **Launch** detached, display variables unset, sleep/shutdown inhibited (add `handle-lid-switch` on laptops):
   ```bash
   sudo env -u DISPLAY -u WAYLAND_DISPLAY tmux new-session -d -s upgrade \
     'systemd-inhibit --what=sleep:idle:shutdown --why=dist-upgrade do-release-upgrade -f DistUpgradeViewText; exec bash'
   ```
   Desktop: never launch from a GUI terminal — the display manager restart kills it. Reattach from a TTY with `sudo tmux attach -t upgrade`.
4. **Watch** from the workstation: `bash scripts/watch.sh <host>` (run in background). It exits when the pane sits on a prompt or the upgrader exits. Answer with `sudo tmux send-keys -t upgrade <answer> Enter`.
5. **Answer prompts** with the table below.
6. **Pre-reboot gate:** `ssh <host> 'MODE=pre bash -s' < scripts/postcheck.sh`. Reboot only when every line passes; a remote-only machine with no console must pass the network and SSH lines.
7. **Reboot** by answering `y` at `Restart required`; wait for SSH; run `postcheck.sh` (default mode is post).
8. **After:** re-enable disabled third-party repos (check for rotated signing keys); leave the obsolete-package cleanup until the new kernel has run for a while.

## Prompts

| Prompt | Answer | Why |
|---|---|---|
| Release notes `Continue [yN]` | y | |
| "Unofficial packages … Installed from: UbuntuESM…" | y | ESM carries them forward to the new release |
| `Do you want to start the upgrade?` | y **after** `plan-check.sh` | The counts must match; `Remove` must not contain the ZFS userspace, `initramfs-tools`, GRUB/shim, `openssh-server`, the network stack or `tmux` |
| Conffile `Y/I/N/O/D/Z` | Diff first (`D`). Keep local (N) when it carries real settings (Samba shares, libvirt socket perms, a vendor block in `/etc/services`); take maintainer (Y) when local is stock or only cosmetic | An unanswered prompt stalls the run silently |
| `Remove obsolete packages?` | **N** | Lists fallback kernels before the new one has booted, and third-party `.deb`s that are only "obsolete" because they are not in the archive |
| `Restart required` | y after `postcheck.sh pre` passes | |

## Common Mistakes

- **Monitoring with `sudo …` and declaring the upgrade dead.** Mid-unpack `/usr/bin/sudo` disappears; probe `/usr/bin/sudo`, `/usr/lib/cargo/bin/sudo` and `/usr/bin/sudo.ws` in turn (the scripts do). Ground truth is `pgrep -f do-release-upgrade`, not package states.
- **Treating hundreds of `iU` packages or an idle-looking machine as damage.** dpkg unpacks everything first, then configures. Sample the `iU` count twice to tell slow from hung.
- **Rebooting because SSH refuses connections.** `openssh-server` is replaced mid-run; it comes back in seconds to minutes. The tmux session keeps running.
- **Reading `apt.log` `MarkDelete` lines as the removal list.** They are resolver steps. The plan is the `DEBUG Remove/Install/Upgrade/Keep at same version:` lines in `main.log` (`plan-check.sh`).
- **Detecting prompts by counting matches in tmux history.** History holds ~2,000 lines; old prompts scroll out. Use the last visible non-blank line, unchanged for ~10 s.
- **`Could not download the release announcement` while curl fetches the same URL.** Transient; relaunch.
- **zsh login shell:** `ssh host 'P=$(…); apt-get purge $P'` passes the whole list as one word. Wrap in `bash -s <<'EOF' … EOF`.
- **`ssh host 'sudo apt purge …'` without `-t`** waits at `Continue? [Y/n]` holding the dpkg lock. Use `-t`, or `apt-get -y` after a `-s` simulation.
- **`zpool upgrade` to silence "Some supported and requested features are not enabled".** One-way; can make the pool unimportable from older rescue media and break sends to older hosts. Leave it.
- **Bulk-removing `apt list '?obsolete'`.** On hosts with third-party software, "obsolete" mostly means "not from the Ubuntu archive". Read it; keep one fallback kernel.

## Cleanup leftovers

`depmod` output (`/lib/modules/<old>/modules.*`, incl. `modules.weakdep`) always outlives a removed kernel, so `rmdir … Directory not empty` after a kernel purge is expected. Purging the `rc` kernel entries removes most of them; delete any remaining directory once `dpkg -S` shows it unowned and `/boot` has no matching kernel.
