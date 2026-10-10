#!/bin/bash
# Read-only pre-flight for an Ubuntu LTS release upgrade. Run on the target:
#   ssh <host> 'bash -s' < preflight.sh
# Needs passwordless sudo for some checks. Prints OK/WARN/FAIL/INFO lines.
S(){ for c in /usr/bin/sudo /usr/lib/cargo/bin/sudo /usr/bin/sudo.ws; do [ -x "$c" ] && { "$c" -n "$@"; return; }; done; }
ok(){ echo "OK   $*"; }; warn(){ echo "WARN $*"; }; fail(){ echo "FAIL $*"; }; info(){ echo "INFO $*"; }

info "host $(hostname), $(. /etc/os-release; echo "$PRETTY_NAME"), kernel $(uname -r)"

normal=$(do-release-upgrade -c 2>&1 | grep -o "New release '[^']*'")
proposed=$(do-release-upgrade -c -p 2>&1 | grep -o "New release '[^']*'")
if [ -n "$normal" ]; then ok "channel: normal offers $normal -> no -p"
elif [ -n "$proposed" ]; then warn "channel: only -p offers $proposed -> verify meta-release-lts-proposed Dist/Supported, then use -p (never -d)"
else fail "channel: no newer release offered (already on the newest LTS, or Prompt= in /etc/update-manager/release-upgrades is not lts)"; fi

S apt-get update -qq >/dev/null 2>&1
pending=$(apt list --upgradable 2>/dev/null | tail -n +2)
nonphased=$(echo "$pending" | grep -v '^$' | while read -r l; do p=${l%%/*}; apt-cache policy "$p" | grep -q phased || echo "$p"; done)
[ -z "$nonphased" ] && ok "no blocking pending updates" || fail "pending updates block the upgrader: $(echo $nonphased) -> apt-get dist-upgrade first"
held=$(apt-mark showhold); [ -z "$held" ] && ok "no held packages" || warn "held: $(echo $held)"
S dpkg --audit >/dev/null 2>&1 && [ -z "$(S dpkg --audit)" ] && ok "dpkg --audit clean" || fail "dpkg --audit not clean"

for f in /etc/sudoers /etc/sudoers.d/*; do
  [ -f "$f" ] || continue
  [ "$(S tail -c 1 "$f" | xxd -p)" = 0a ] || fail "sudoers $f lacks trailing newline (sudo-rs rejects it)"
done; ok "sudoers checked"

rootfs=$(findmnt -no FSTYPE /)
[ "$rootfs" = zfs ] && warn "root on ZFS ($(findmnt -no SOURCE /)) -> snapshot root and boot datasets before starting" \
                     || info "root on $rootfs -> no snapshot rollback; backup.sh is the only rollback material"
df -h / /boot /boot/efi 2>/dev/null | awk 'NR>1{print "INFO free " $6 ": " $4}' | sort -u

if command -v zpool >/dev/null; then
  info "zfs $(zfs version 2>/dev/null | head -1)"
  S zpool status -x | grep -q 'all pools are healthy' && ok "pools healthy" || warn "zpool status -x: $(S zpool status -x | head -3 | tr '\n' ' ')"
  S zpool status | grep -E 'scan:.*in progress' && warn "scrub/resilver in progress"
fi
busy=$(pgrep -af 'syncoid|zfs (send|recv|receive)|rsync|rclone|sha256sum' | grep -v pgrep)
[ -z "$busy" ] && ok "no replication/copy/hash jobs running" || warn "busy: $busy"

dm=$(systemctl is-active display-manager 2>/dev/null)
[ "$dm" = active ] && warn "desktop: display manager active -> never launch from a GUI terminal" || info "headless: no active display manager"
command -v VBoxManage virsh >/dev/null 2>&1 && virsh list --state-running --name 2>/dev/null | grep -q . && warn "running VMs: $(virsh list --name | xargs)"

shopt -s nullglob
tp=(); for f in /etc/apt/sources.list.d/*; do b=${f##*/}; [[ $b =~ ^ubuntu(-esm-.*)?\.sources$|\.distUpgrade$|\.save$ ]] || tp+=("$b"); done
info "third-party sources: ${tp[*]}"
info "kernels: $(dpkg -l 'linux-image-[0-9]*' | awk '/^ii/{print $2}' | sed 's/linux-image-//' | xargs)"
info "secure boot: $(mokutil --sb-state 2>/dev/null || echo unknown)"
info "default route: $(ip r | awk '/^default/{print $5, $3; exit}'); netplan: $(cd /etc/netplan && echo *)"
info "ssh: $(systemctl is-enabled ssh.socket 2>/dev/null) socket, $(systemctl is-enabled ssh.service 2>/dev/null) service"
