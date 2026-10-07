#!/usr/bin/env bash
# Run NVIDIA's nvsdkmanager_flash.sh (QSPI firmware + target storage) inside
# the flashing container. The Jetson must already be in Force Recovery Mode and
# host-nfs.sh up must already have run.
#
# usage: bash flash-in-docker.sh <L4T_DIR> [storage] [image]
#   storage: nvme0n1p1 (default), nvme1n1p1, sda1, mmcblk0p1
#   log:     <L4T_DIR>/../flash-<timestamp>.log
set -euo pipefail

l4t=$(realpath "${1:?usage: flash-in-docker.sh <L4T_DIR> [storage] [image]}")
storage=${2:-nvme0n1p1}
image=${3:-l4t-flash:24.04}
log="$(dirname "$l4t")/flash-$(date +%Y%m%d-%H%M%S).log"

# NVIDIA's network_prerequisite() runs "service nfs-kernel-server restart" and
# "rpcbind" unconditionally. Inside a container that restarts the host kernel's
# nfsd from the wrong namespace and the target's NFS mount then times out.
# Replace both with no-ops; the host NFS server (host-nfs.sh) does the serving.
# Not under /tmp: snap-packaged Docker has a private /tmp and cannot see it.
shim=$(mktemp -d "$(dirname "$l4t")/.flash-shim.XXXXXX")
trap 'rm -rf "$shim"' EXIT
printf '#!/bin/sh\necho "[shim] skipped: $0 $*"\nexit 0\n' > "$shim/noop"
chmod 755 "$shim/noop"

# Paths must be identical inside and outside the container: the target mounts
# the host path it is told about, and nfs_check() compares against host exports.
# USER=root: l4t_create_images_for_kernel_flash.sh checks $USER, which Docker
# leaves unset.
docker run --rm --privileged --network host -e USER=root \
	-v /dev:/dev -v /sys:/sys \
	-v "$l4t":"$l4t" -w "$l4t" \
	-v "$shim/noop":/usr/sbin/service:ro \
	-v "$shim/noop":/usr/sbin/rpcbind:ro \
	"$image" bash -c '
mkdir -p /run/nvidia_initrd_flash && touch /run/nvidia_initrd_flash/docker_host_network
./nvsdkmanager_flash.sh --storage "$1"
echo "FLASH_RC=$?"' _ "$storage" 2>&1 | tee "$log"

echo "log: $log"
