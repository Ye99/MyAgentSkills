#!/bin/bash
# Gate before the restart ("pre") and verification after it ("post"). Run on the target:
#   ssh <host> 'bash -s' < postcheck.sh            # post (default)
#   ssh <host> 'MODE=pre bash -s' < postcheck.sh
S(){ for c in /usr/bin/sudo /usr/lib/cargo/bin/sudo /usr/bin/sudo.ws; do [ -x "$c" ] && { "$c" -n "$@"; return; }; done; }
ok(){ echo "OK   $*"; }; fail(){ echo "FAIL $*"; }; info(){ echo "INFO $*"; }
MODE=${MODE:-post}

info "mode $MODE: $(. /etc/os-release; echo "$PRETTY_NAME"), running kernel $(uname -r)"
[ -z "$(S dpkg --audit)" ] && ok "dpkg --audit clean" || fail "dpkg --audit not clean"
n=$(dpkg -l | awk 'NR>5 && $1!="ii" && $1!="rc"' | wc -l); [ "$n" = 0 ] && ok "0 half-installed packages" || fail "$n packages not ii/rc"

newest=$(ls /boot/vmlinuz-* | sed 's#.*/vmlinuz-##' | sort -V | tail -1)
for k in $(ls /boot/vmlinuz-* | sed 's#.*/vmlinuz-##'); do
  [ -f "/boot/initrd.img-$k" ] && ok "initrd for $k" || fail "no initrd for $k"
  if command -v zfs >/dev/null; then
    find "/lib/modules/$k" -name 'zfs.ko*' | grep -q . && ok "zfs module for $k" || fail "no zfs module for $k"
    if [ "$(findmnt -no FSTYPE /)" = zfs ]; then
      ird=$(S lsinitramfs "/boot/initrd.img-$k")
      for want in 'zfs\.ko' 'scripts/zfs$' 'sbin/zpool$' 'sbin/mount\.zfs$'; do
        grep -qE "$want" <<<"$ird" && ok "initrd $k has $want" || fail "initrd $k lacks $want (ZFS root will not import)"
      done
    fi
  fi
done
cfg=$(S grep -m1 -E "^\s*linux\s" /boot/grub/grub.cfg | awk '{print $2}')
echo "$cfg" | grep -q "$newest" && ok "GRUB default boots $newest" || fail "GRUB default entry is '$cfg', newest kernel $newest"

S netplan generate 2>/dev/null && ok "netplan generate" || fail "netplan generate failed"
for u in ssh.socket ssh.service; do info "$u $(systemctl is-enabled $u 2>/dev/null)"; done
systemctl is-enabled ssh.socket 2>/dev/null | grep -q enabled || systemctl is-enabled ssh.service 2>/dev/null | grep -q enabled \
  && ok "ssh will start at boot" || fail "neither ssh.socket nor ssh.service enabled"
for f in /etc/sudoers /etc/sudoers.d/*; do [ "$(S tail -c 1 "$f" | xxd -p)" = 0a ] || fail "sudoers $f lacks trailing newline"; done
S visudo -c >/dev/null 2>&1 && ok "visudo -c" || fail "visudo -c"

if [ "$MODE" = post ]; then
  [ "$(uname -r)" = "$newest" ] && ok "booted newest kernel" || fail "booted $(uname -r), newest is $newest"
  st=$(systemctl is-system-running); [ "$st" = running ] && ok "system running" || fail "system $st: $(systemctl --failed --no-legend | awk '{print $2}' | xargs)"
  [ -e /var/run/reboot-required ] && fail "reboot-required present" || ok "no reboot-required"
  info "pending: $(S apt-get -s dist-upgrade | tail -1)"
  ip r | grep -q '^default' && ok "default route $(ip r | awk '/^default/{print $5, $3; exit}')" || fail "no default route"
  if command -v zpool >/dev/null; then
    info "$(zfs version | xargs)"
    S zpool status -x | grep -q 'all pools are healthy' && ok "pools healthy" || fail "$(S zpool status -x | head -3 | xargs)"
    info "snapshots: $(zfs list -H -t snapshot -o name | wc -l) (compare with backup zfs-snapshots.txt)"
  fi
  shopt -s nullglob
  dis=(/etc/apt/sources.list.d/*.disabled); for f in /etc/apt/sources.list.d/*.sources; do grep -q '^Enabled: no' "$f" && dis+=("$f"); done
  info "disabled sources: ${dis[*]}"
  info "obsolete: $(apt list '?obsolete' 2>/dev/null | tail -n +2 | wc -l) (do not bulk-remove)"
  S journalctl -b -p err --no-pager -q | tail -5 | sed 's/^/INFO boot err: /'
fi
