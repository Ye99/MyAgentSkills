#!/usr/bin/env bash
# Restore a backup made by backup-in-docker.sh with NVIDIA's
# l4t_backup_restore.sh -r, run inside the flashing container.
# ERASES the target drive (blkdiscard) and rewrites the QSPI firmware.
# The Jetson must be in Force Recovery Mode, and this must already have run:
#   sudo bash host-nfs.sh up <L4T_DIR> tools/kernel_flash/images tools/backup_restore
#
# usage: bash restore-in-docker.sh <L4T_DIR> <board> <backup_dir> [device] [image]
#
# Checks first, because NVIDIA's restore does not: every file's SHA-256 (it
# never checks the APP tar.zst, and on a bad image checksum it skips that
# partition yet still reports success), and that the backup's L4T release
# matches this BSP (the restore boots this BSP's initrd and writes the
# backup's QSPI firmware; a mismatch can leave a board that will not boot).
set -euo pipefail

usage="usage: restore-in-docker.sh <L4T_DIR> <board> <backup_dir> [device] [image]"
l4t=$(realpath "${1:?$usage}")
board=${2:?$usage}
src=$(realpath "${3:?$usage}")
device=${4:-nvme0n1}
image=${5:-l4t-flash:24.04}
here=$(cd "$(dirname "$0")" && pwd)
images="$l4t/tools/backup_restore/images"

bash "$here/verify-backup.sh" "$src"

app=$(ls "$src"/"$device"p*.tar.zst | head -1)
backup_rel=$(zstd -dc "$app" | tar -xOf - ./etc/nv_tegra_release | head -1 | cut -d, -f1-2)
bsp_rel=$(head -1 "$l4t/rootfs/etc/nv_tegra_release" | cut -d, -f1-2)
echo "backup release: $backup_rel"
echo "BSP release:    $bsp_rel"
[ "$backup_rel" = "$bsp_rel" ] || { echo "ERROR: release mismatch; use the BSP the backup was taken with" >&2; exit 1; }

if [ -n "$(sudo ls -A "$images" 2>/dev/null)" ]; then
	echo "ERROR: $images is not empty; move it out first" >&2
	exit 1
fi
lsusb | grep -qE '0955:7[0-9a-f]{3}' || { echo "ERROR: no Jetson on USB" >&2; exit 1; }
lsusb | grep -E '0955:7020' && { echo "ERROR: Jetson is booted normally, not in recovery mode" >&2; exit 1; }

log="$src/restore-$(date +%Y%m%d-%H%M%S).log"
sudo mkdir -p "$images"
# Copy (not move) so the backup stays intact whatever happens.
sudo cp "$src"/nvpartitionmap.txt "$src"/*.img "$src"/*.tar.zst "$images"/
trap 'sudo rm -rf "$images"' EXIT

# Same shims as backup-in-docker.sh (see there).
shim=$(mktemp -d "$(dirname "$l4t")/.restore-shim.XXXXXX")
trap 'sudo rm -rf "$images"; rm -rf "$shim"' EXIT
printf '#!/bin/sh\necho "[shim] skipped: $0 $*"\nexit 0\n' > "$shim/noop"
chmod 755 "$shim/noop"

set +e
docker run --rm --privileged --network host -e USER=root \
	-v /dev:/dev -v /sys:/sys \
	-v "$l4t":"$l4t" -w "$l4t" \
	-v "$shim/noop":/usr/sbin/service:ro \
	-v "$shim/noop":/usr/sbin/rpcbind:ro \
	-v "$shim/noop":/usr/sbin/exportfs:ro \
	"$image" bash -c '
mkdir -p /run/nvidia_initrd_flash && touch /run/nvidia_initrd_flash/docker_host_network
./tools/backup_restore/l4t_backup_restore.sh -e "$1" -r "$2"' _ "$device" "$board" 2>&1 | sudo tee "$log"
rc=${PIPESTATUS[0]}
set -e
echo "RESTORE_RC=$rc" | sudo tee -a "$log"
sudo chown "$(id -u):$(id -g)" "$log"

if [ "$rc" -ne 0 ] || ! grep -q 'Successful restore of partitions' "$log" \
	|| grep -q 'does not match the checksum' "$log"; then
	echo "ERROR: restore failed; target stays in its initrd. log: $log" >&2
	exit 1
fi
echo "restore OK. log: $log"
