#!/usr/bin/env bash
# Back up a Jetson's boot drive and QSPI with NVIDIA's l4t_backup_restore.sh,
# run inside the flashing container, then move the result out of the BSP.
# The Jetson must be in Force Recovery Mode, and this must already have run:
#   sudo bash host-nfs.sh up <L4T_DIR> tools/kernel_flash/images tools/backup_restore
#
# usage: bash backup-in-docker.sh <L4T_DIR> <board> <dest_dir> [device] [image]
#   board:  flash.sh board name, e.g. jetson-orin-nano-devkit-super
#   device: nvme0n1 (default). Pass it explicitly: on p3767-0005 (Orin Nano
#           devkit) NVIDIA's script otherwise backs up mmcblk0 (the SD card).
#   result: <dest_dir>/<board>-<timestamp>/ with nvpartitionmap.txt, *.img,
#           APP *.tar.zst, QSPI0.img and backup.log
set -euo pipefail

usage="usage: backup-in-docker.sh <L4T_DIR> <board> <dest_dir> [device] [image]"
l4t=$(realpath "${1:?$usage}")
board=${2:?$usage}
dest=${3:?$usage}
device=${4:-nvme0n1}
image=${5:-l4t-flash:24.04}

images="$l4t/tools/backup_restore/images"
if [ -n "$(ls -A "$images" 2>/dev/null)" ]; then
	echo "ERROR: $images is not empty; move the old backup out first" >&2
	exit 1
fi
lsusb | grep -qE '0955:7[0-9a-f]{3}' || { echo "ERROR: no Jetson on USB" >&2; exit 1; }
lsusb | grep -E '0955:7020' && { echo "ERROR: Jetson is booted normally, not in recovery mode" >&2; exit 1; }

out="$(realpath -m "$dest")/$board-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$out"
log="$out/backup.log"

# NVIDIA's helpers run "service nfs-kernel-server restart", "rpcbind" and
# "exportfs" from inside the container. With --network host those reach the
# host kernel's nfsd from the wrong mount namespace and break the host export
# that host-nfs.sh set up. Replace them with no-ops.
# Not under /tmp: snap-packaged Docker has a private /tmp and cannot see it.
shim=$(mktemp -d "$(dirname "$l4t")/.backup-shim.XXXXXX")
trap 'rm -rf "$shim"' EXIT
printf '#!/bin/sh\necho "[shim] skipped: $0 $*"\nexit 0\n' > "$shim/noop"
chmod 755 "$shim/noop"

# Paths must be identical inside and outside the container: the target mounts
# the host path it is told about.
set +e
docker run --rm --privileged --network host -e USER=root \
	-v /dev:/dev -v /sys:/sys \
	-v "$l4t":"$l4t" -w "$l4t" \
	-v "$shim/noop":/usr/sbin/service:ro \
	-v "$shim/noop":/usr/sbin/rpcbind:ro \
	-v "$shim/noop":/usr/sbin/exportfs:ro \
	"$image" bash -c '
mkdir -p /run/nvidia_initrd_flash && touch /run/nvidia_initrd_flash/docker_host_network
./tools/backup_restore/l4t_backup_restore.sh -e "$1" -b "$2"' _ "$device" "$board" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
set -e
echo "BACKUP_RC=$rc" | tee -a "$log"

if [ "$rc" -ne 0 ] || ! grep -q 'Backup complete' "$log"; then
	echo "ERROR: backup failed; partial files stay in $images, log: $log" >&2
	exit 1
fi
# tmp/tmp is the empty mount point nvbackup_partitions.sh used for APP.
sudo find "$images/tmp" -depth -type d -empty -delete 2>/dev/null || true
sudo mv "$images"/* "$out"/
sudo rmdir "$images"
sudo chown -R "$(id -u):$(id -g)" "$out"
echo "backup: $out"
