#!/usr/bin/env bash
# Extract the Jetson Linux BSP + sample rootfs and run apply_binaries.sh inside
# the flashing container.
#
# usage: bash prepare-bsp.sh <work_dir> [image]
#   <work_dir> must contain Jetson_Linux_R*_aarch64.tbz2 and
#   Tegra_Linux_Sample-Root-Filesystem_R*_aarch64.tbz2 (already SHA1-verified).
#   Result: <work_dir>/Linux_for_Tegra
#
# apply_binaries.sh chroots into the arm64 rootfs, so this registers a
# qemu-aarch64 binfmt handler in the host kernel (flag F, so it keeps working
# after the container exits). It does not survive a reboot. Remove it after
# flashing with:  echo -1 | sudo tee /proc/sys/fs/binfmt_misc/qemu-aarch64
set -euo pipefail

work_dir=$(realpath "${1:?usage: prepare-bsp.sh <work_dir> [image]}")
image=${2:-l4t-flash:24.04}

bsp=$(ls "$work_dir"/Jetson_Linux_R*_aarch64.tbz2 | head -1)
rootfs=$(ls "$work_dir"/Tegra_Linux_Sample-Root-Filesystem_R*_aarch64.tbz2 | head -1)

if [ -d "$work_dir/Linux_for_Tegra/rootfs/usr" ]; then
	echo "ERROR: $work_dir/Linux_for_Tegra already has a rootfs; use a fresh work_dir" >&2
	exit 1
fi

docker run --rm --privileged -v "$work_dir":/work -w /work "$image" bash -c '
set -euo pipefail
mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc 2>/dev/null || true
if [ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
	cat /usr/lib/binfmt.d/qemu-aarch64.conf > /proc/sys/fs/binfmt_misc/register
fi
grep -q "flags: .*F" /proc/sys/fs/binfmt_misc/qemu-aarch64
tar -I lbzip2 -xpf "$(basename "$1")"
tar -I lbzip2 -xpf "$(basename "$2")" -C Linux_for_Tegra/rootfs/
cd Linux_for_Tegra
./apply_binaries.sh > /work/apply_binaries.log 2>&1
tail -1 /work/apply_binaries.log
' _ "$bsp" "$rootfs"
