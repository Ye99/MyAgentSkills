---
name: flash-jetson-orin-nano-docker
description: Use when installing or reinstalling JetPack / Jetson Linux (L4T) on a Jetson Orin Nano or Orin NX developer kit from an x86_64 Linux host whose Ubuntu release the NVIDIA flash tools do not support, when moving between JetPack generations (6.x to 7.x) that need a QSPI firmware update, or when l4t_initrd_flash fails inside Docker with "nfs: server fc00:1:1:0::1 not responding", "requires root privilege", or "No such file or directory: 'cpp'".
---

# Flash a Jetson Orin Nano with NVIDIA's tools in Docker

## Overview

Flash QSPI firmware and the boot SSD together over USB-C in Force Recovery Mode,
running NVIDIA's own `nvsdkmanager_flash.sh` (what SDK Manager runs) inside an
Ubuntu 24.04 container. The container supplies a supported host userland; the
**host** still serves NFS, because the Jetson's flashing initrd pulls its images
from the host kernel's nfsd.

This erases the target storage and replaces the module's QSPI firmware.

## When to use

- New JetPack release, or a JetPack generation jump: JetPack 7.2+ (L4T r39) on
  Orin needs r39 QSPI firmware, which cannot be written by imaging the SSD alone.
- Host runs an Ubuntu release (or distro) outside NVIDIA's 22.04/24.04 support.
- Reflashing a board whose boot drive is gone or unbootable.

## When not to use

- Same generation, firmware already matches (e.g. 36.x firmware, JetPack 6.x
  image): you can image the SSD in a USB enclosure on the host with
  `l4t_initrd_flash.sh --direct sdX ... external` and skip recovery mode.
- No USB-C data cable to the host or no way to short the recovery pins: use
  NVIDIA's Jetson ISO installer (JetPack 7.2+) from a USB stick instead.

## Firmware gate (decide before downloading)

| Current QSPI firmware | Target JetPack | This skill |
|---|---|---|
| any | 7.2+ (L4T r39) | yes, writes r39 firmware + SSD |
| 36.x | 6.x (L4T r36) | yes, or `--direct` SSD-only is enough |

Read the current version on a running board with `sudo nvbootctrl dump-slots-info`
or in the UEFI setup screen (press Esc at the NVIDIA logo). After flashing r39
firmware the board no longer boots JetPack 6 drives until reflashed.

## Procedure

Set `WORK=<dir with ~25 GB free, not under /tmp>`, `SKILL=<this skill dir>`, `L4T=$WORK/Linux_for_Tegra`.

1. **Back up** anything needed from the target storage. It will be erased.
2. **Download** from https://developer.nvidia.com/embedded/jetson-linux (the
   current release page) the *Driver Package (BSP)*, *Sample Root Filesystem*,
   and `release_sha_hashes.txt` into `$WORK`. Verify:
   `cd $WORK && sha1sum Jetson_Linux_R*.tbz2 Tegra_Linux_Sample-Root-Filesystem_R*.tbz2`
   and compare with `release_sha_hashes.txt`.
3. **Build the image**: `docker build -t l4t-flash:24.04 -f $SKILL/scripts/Dockerfile $SKILL/scripts`.
   On a new L4T release, diff the package list against
   `$L4T/tools/l4t_flash_prerequisites.sh` and update the Dockerfile.
4. **Prepare the BSP**: `bash $SKILL/scripts/prepare-bsp.sh $WORK`
   (extracts, registers qemu-aarch64 binfmt, runs `apply_binaries.sh`; expect `Success!`).
5. **Hardware** (ask the user to do this; they confirm with "done"):
   - NVMe SSD in the M.2 slot (2280 slot = `nvme0n1` when only one drive is fitted).
   - USB-C port on the carrier board to the host, preferably a direct motherboard port.
   - Jumper J14 pin 9 (FC REC) to pin 10 (GND), then connect power.
6. **Confirm recovery mode**: `lsusb | grep 0955`. Expect `7523` (Orin Nano 8GB),
   `7623` (Orin Nano 4GB), `7323`/`7423` (Orin NX 16/8GB).
7. **Host NFS up**: `sudo bash $SKILL/scripts/host-nfs.sh up $L4T`.
8. **Flash**: `bash $SKILL/scripts/flash-in-docker.sh $L4T nvme0n1p1` (run in the
   background; 10-20 min). The board type is auto-detected from EEPROM
   (e.g. `jetson-orin-nano-devkit-super`). Success ends with
   `Successfully flashed the QSPI.`, `Flash is successful`, and `FLASH_RC=0`.
   On success the script reboots the target; with the jumper still fitted it
   may come back up in recovery mode, which is harmless.
9. **Clean up the host**: `sudo bash $SKILL/scripts/host-nfs.sh down` and
   `echo -1 | sudo tee /proc/sys/fs/binfmt_misc/qemu-aarch64`.
10. **Boot**: user removes the J14 jumper and power-cycles. First boot runs
    oem-config (EULA, user, network) on a DisplayPort monitor + keyboard, or
    headless over the USB-C serial console (`/dev/ttyACM0` on the host).
11. **Verify** on the booted board: `head -1 /etc/nv_tegra_release` shows the
    new release, `sudo nvbootctrl dump-slots-info` shows the new firmware
    version, and `df -h /` shows the root partition filling the drive.

## Resume a failed step 3 without re-entering recovery

If the log shows `Step 3: Start the flashing process` and then
`Error: Flash failure ... cannot mount the NFS server`, the target is still in
its flashing initrd with images already generated. Fix NFS on the host, then:

```bash
# on the host: target link needs the host-side address back
sudo ip addr add fc00:1:1:0::1/64 dev <enx... interface of the Jetson>
docker run --rm --network host l4t-flash:24.04 sshpass -p root ssh \
  -o StrictHostKeyChecking=no root@fc00:1:1:0::2 \
  'NFS_IMAGES_DIR=<L4T>/tools/kernel_flash/images /bin/nv_flash_from_network.sh'
```

Before that, kill any `mount`/`mount.nfs` left over from the failed attempt
on the target (`ps`, then `kill -9 <pid>`); a hung one blocks new mounts.

`Flash is successful` there means both SSD and QSPI are written. The target
stays in the initrd (this path skips the reboot), so you can inspect it there:
`mkdir -p /r && mount -o ro /dev/nvme0n1p1 /r && head -1 /r/etc/nv_tegra_release && df -h /r; umount /r`.
Then run steps 9-11.

## Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `FileNotFoundError: ... 'cpp'`, `Parsing boardid failed` | container lacks NVIDIA's prerequisites | use this Dockerfile (mirrors `l4t_flash_prerequisites.sh`) |
| `l4t_create_images_for_kernel_flash.sh requires root privilege` | Docker leaves `$USER` unset | `-e USER=root` (in `flash-in-docker.sh`) |
| target dmesg `nfs: server fc00:1:1:0::1 not responding, timed out` | NVIDIA's script ran `service nfs-kernel-server restart` inside the container | `flash-in-docker.sh` shims `service`/`rpcbind`; serve NFS from the host (`host-nfs.sh`) |
| `Failed to start nfs-server.service: Unit nfs-mountd.service is masked` | host deliberately masked NFS units | `host-nfs.sh up` unmasks and `down` re-masks them |
| NFS mount hangs although the server is fine | a stale `mount.nfs` from the failed attempt blocks new mounts on the target | `kill -9` its PID on the target before retrying |
| NFS timeouts with UFW active | UFW drops NFS from the USB link | `host-nfs.sh up` adds a rule scoped to `fc00:1:1::/48` |
| `Not all of the space available ... appears to be used` during flash | layout is written at 64 GB then expanded | expected; APP is grown to fill the drive |
| `mounting ... not a directory` or empty mounts in `docker run` | snap-packaged Docker cannot see host `/tmp` | keep `$WORK` and helper files outside `/tmp` |
| `WARNING: failed to import T264 module` | Thor-only Python module | harmless on Orin |
| board boots straight back to recovery | J14 jumper still fitted | remove it and power-cycle |
| `pkill -f <pattern>` kills your own shell | the pattern matches the command line running it | target processes by PID |

## Tested with

Jetson Linux r39.2.1 (JetPack 7.2.1) on an Orin Nano 8GB Super devkit
(p3767-0005), upgrading from 36.4.7 firmware; host Ubuntu 26.04, Docker 29.
