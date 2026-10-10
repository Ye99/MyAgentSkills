#!/bin/bash
# Pre-upgrade config backup. Run on the target:
#   ssh <host> 'sudo bash -s' < backup.sh            # target release defaults to "next"
#   ssh <host> 'sudo TO=26.04 bash -s' < backup.sh
# Contains /etc (including any secrets in it): it stays root-only and is deleted once the upgrade is proven.
set -e
B=/root/upgrade-${TO:-next}-$(hostname)-$(date +%Y%m%dT%H%M%S)
mkdir -m 700 "$B"; cd "$B"
tar czf etc.tgz -C / etc 2>/dev/null
dpkg -l > dpkg-l.txt; apt-mark showmanual > manual-packages.txt
systemctl list-units --type=service --state=running --no-legend > running-services.txt
cp /etc/os-release os-release; ls -la /boot > boot-listing.txt
ip a > ip-a.txt; ip r > ip-r.txt
command -v nmcli >/dev/null && nmcli -t con show > nm-connections.txt || true
command -v snap >/dev/null && snap list > snap-list.txt 2>&1 || true
command -v pro >/dev/null && pro status > pro-status.txt 2>&1 || true
if command -v zfs >/dev/null; then
  zfs list -o name,used,avail,mountpoint > zfs-list.txt
  zfs list -H -t snapshot -o name,guid > zfs-snapshots.txt
  for p in $(zpool list -H -o name); do zpool get all "$p" > "zpool-$p-props.txt"; done
fi
command -v virsh >/dev/null && { mkdir libvirt-xml; for d in $(virsh list --all --name); do virsh dumpxml --inactive "$d" > "libvirt-xml/$d.xml"; done; } || true
echo "$B"; ls -la
