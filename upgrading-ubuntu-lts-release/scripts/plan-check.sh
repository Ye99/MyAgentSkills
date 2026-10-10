#!/bin/bash
# Show the upgrader's authoritative plan from main.log, at the "Do you want to start the upgrade?" prompt.
#   ssh <host> 'bash -s' < plan-check.sh
# Prints the four list sizes, the full Remove list, and flags critical packages found in Remove.
S(){ for c in /usr/bin/sudo /usr/lib/cargo/bin/sudo /usr/bin/sudo.ws; do [ -x "$c" ] && { "$c" -n "$@"; return; }; done; }
LOG=/var/log/dist-upgrade/main.log
list(){ S grep "DEBUG $1: " "$LOG" | tail -1 | sed "s/.*DEBUG $1: //"; }
for k in Remove Install Upgrade "Keep at same version"; do printf '%-22s %s\n' "$k:" "$(list "$k" | wc -w)"; done
echo "REMOVE: $(list Remove)"
crit='^(zfsutils-linux|zfs-zed|zfs-initramfs|initramfs-tools|grub-.*|shim-signed|openssh-server|network-manager|netplan.io|systemd-networkd|systemd|tmux|sudo|sudo-rs|linux-generic.*|cryptsetup.*)$'
hits=$(list Remove | tr ' ' '\n' | grep -E "$crit")
[ -z "$hits" ] && echo "OK   no critical package in Remove" || echo "FAIL critical package(s) in Remove: $(echo $hits) -> answer N and investigate"
echo "KEEP: $(list 'Keep at same version')"
