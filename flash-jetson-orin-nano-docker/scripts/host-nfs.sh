#!/usr/bin/env bash
# Temporarily serve the initrd-flash images over the host's NFS server, then
# put everything back exactly as it was.
#
# usage: sudo bash host-nfs.sh up <L4T_DIR>
#        sudo bash host-nfs.sh down
#
# Why the host and not the container: the Jetson's flashing initrd mounts
# <L4T_DIR>/tools/kernel_flash/images from the host at fc00:1:1:<n>::1. nfsd
# lives in the kernel, so it must be started and exported from the host's own
# namespaces. Starting it from inside a container leaves the target with
# "nfs: server fc00:1:1:0::1 not responding, timed out".
#
# "up" records prior state in $STATE so "down" restores it:
#   /etc/exports contents, masked NFS units, nfs-server active state,
#   and a UFW rule (only if UFW is active) scoped to the USB link fc00:1:1::/48.
set -euo pipefail

STATE=/var/tmp/jetson-flash-nfs.state
NFS_UNITS=(nfs-mountd nfs-idmapd nfsdcld rpc-statd)
# Same options NVIDIA's l4t_kernel_flash_vars.func uses (PERMISSION_STR).
OPTS="rw,nohide,insecure,no_subtree_check,async,no_root_squash"
CLIENTS="fc00:1:1::/48"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

up() {
	local l4t images
	l4t=$(realpath "${1:?usage: host-nfs.sh up <L4T_DIR>}")
	images="$l4t/tools/kernel_flash/images"
	[ -e "$STATE" ] && { echo "ERROR: $STATE exists; run 'down' first" >&2; exit 1; }
	command -v exportfs >/dev/null || { echo "ERROR: install nfs-kernel-server on the host" >&2; exit 1; }
	if [ "$(sysctl -n net.ipv6.conf.all.disable_ipv6)" = 1 ]; then
		echo "ERROR: IPv6 is disabled; the USB link to the Jetson is IPv6-only" >&2; exit 1
	fi

	mkdir -p "$images"; chown root:root "$images"; chmod 755 "$images"

	: > "$STATE"
	if [ -f /etc/exports ]; then
		cp -a /etc/exports /etc/exports.jetson-flash.bak; echo "exports=backup" >> "$STATE"
	else
		echo "exports=absent" >> "$STATE"
	fi
	printf '# jetson-flash (temporary)\n%s %s(%s)\n' "$images" "$CLIENTS" "$OPTS" >> /etc/exports

	local u masked=()
	for u in "${NFS_UNITS[@]}"; do
		[ "$(systemctl is-enabled "$u" 2>/dev/null || true)" = masked ] && masked+=("$u")
	done
	echo "masked=${masked[*]}" >> "$STATE"
	[ ${#masked[@]} -gt 0 ] && systemctl unmask "${masked[@]}"

	echo "nfs_was_active=$(systemctl is-active nfs-server 2>/dev/null || true)" >> "$STATE"
	systemctl restart nfs-server
	exportfs -ra
	showmount -e localhost | grep -qF "$images" || { echo "ERROR: export not visible" >&2; exit 1; }

	if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
		ufw allow from "$CLIENTS" comment jetson-flash-temp >/dev/null
		echo "ufw=added" >> "$STATE"
	fi
	echo "NFS ready: $images -> $CLIENTS"
}

down() {
	[ -f "$STATE" ] || { echo "nothing to undo ($STATE missing)"; exit 0; }
	local exports masked nfs_was_active ufw
	exports=$(sed -n 's/^exports=//p' "$STATE")
	masked=$(sed -n 's/^masked=//p' "$STATE")
	nfs_was_active=$(sed -n 's/^nfs_was_active=//p' "$STATE")
	ufw=$(sed -n 's/^ufw=//p' "$STATE")

	if [ "$ufw" = added ]; then
		local n
		for n in $(ufw status numbered | sed -nE '/jetson-flash-temp/s/^\[ *([0-9]+)\].*/\1/p' | sort -rn); do
			ufw --force delete "$n" >/dev/null
		done
	fi

	if [ "$exports" = backup ]; then
		mv /etc/exports.jetson-flash.bak /etc/exports
	else
		rm -f /etc/exports
	fi
	exportfs -ra || true

	if [ "$nfs_was_active" != active ]; then
		systemctl stop nfs-server
	fi
	if [ -n "$masked" ]; then
		# shellcheck disable=SC2086
		systemctl stop $masked 2>/dev/null || true
		# shellcheck disable=SC2086
		systemctl mask $masked
	fi
	rm -f "$STATE"
	echo "host NFS/firewall restored"
}

case "${1:-}" in
	up) up "${2:-}" ;;
	down) down ;;
	*) echo "usage: sudo bash host-nfs.sh up <L4T_DIR> | down" >&2; exit 2 ;;
esac
