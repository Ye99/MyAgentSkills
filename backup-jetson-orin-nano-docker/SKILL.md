---
name: backup-jetson-orin-nano-docker
description: Use when backing up or cloning a Jetson Orin Nano or Orin NX developer kit's boot drive (NVMe) and QSPI firmware before an upgrade, reflash or risky change, when a consistent full image is needed rather than a copy of a running system, or when restoring a Jetson from such a backup, from an x86_64 Linux host whose Ubuntu release NVIDIA's tools do not support.
---

# Back up a Jetson Orin Nano with NVIDIA's tools in Docker

## Overview

Boot the Jetson into NVIDIA's recovery initrd over USB-C, so its drive is
**not mounted**, and run NVIDIA's `l4t_backup_restore.sh -b` (from the BSP) in
an Ubuntu 24.04 container. The target writes the backup over NFS to the host:
GPT, raw images of the small partitions, the root (APP) partition as a
`tar.zst`, and the QSPI firmware. Every file is SHA-256 checked.

Nothing on the Jetson is changed. Size is roughly the used space on the root
partition (zstd-compressed) plus ~200 MB.

This skill reuses the container and host-NFS scripts from the
**flash-jetson-orin-nano-docker** skill (same repository). Set
`FLASH=<that skill>/scripts` and `SKILL=<this skill>/scripts`.

## When to use / not

| Need | Use |
|---|---|
| Exact, consistent, restorable image of drive + firmware | this skill |
| Quick copy while the board keeps running | `rsync -aHAX` of `/` over ssh (files being written may be inconsistent) |
| Only your data (home, models, configs) | plain `rsync` of those directories |

A running system cannot be frozen: `dd` of a mounted root gives a damaged
filesystem image, and `rsync` can catch files mid-write. Only an unmounted drive
gives a consistent image, which is why this skill uses recovery mode.

## First-time setup (once per host)

Skip a step if it is already done (the flash skill does the same steps).

1. Download the Jetson Linux *Driver Package (BSP)* and *Sample Root Filesystem*
   for the release **the board currently runs** (`head -1 /etc/nv_tegra_release`
   on the board) from https://developer.nvidia.com/embedded/jetson-linux into
   `WORK=<dir with ~25 GB free plus room for the backup, not under /tmp>`.
2. `docker build -t l4t-flash:24.04 -f $FLASH/Dockerfile $FLASH`
3. `bash $FLASH/prepare-bsp.sh $WORK`, then remove the qemu handler it leaves:
   `echo -1 | sudo tee /proc/sys/fs/binfmt_misc/qemu-aarch64`.
   `L4T=$WORK/Linux_for_Tegra`.
4. Find the board name: the flash log line `Identified '<board>' target board`,
   or `jetson-orin-nano-devkit-super` for an Orin Nano devkit on JetPack 6.2+/7
   (`jetson-orin-nano-devkit` on older releases). See `ls $L4T/*.conf`.

## Backup procedure

Explain each step to the user before doing it; step 1 (and the jumper, if used) is theirs.

1. **Connect** a USB-C data cable from the carrier board to the host (a direct
   motherboard port works best). Ethernet can stay connected.
2. **Enter recovery mode**. On a board you can ssh to, no jumper is needed:
   `ssh <board> 'sudo systemctl reboot --reboot-argument=forced-recovery'`
   (not `systemctl reboot forced-recovery`, which fails with "Too many arguments").
   Otherwise: power off, jumper J14 pin 9 (FC REC) to pin 10 (GND), power on,
   then remove the jumper (it is only read at power-up).
3. **Confirm**: `lsusb | grep 0955` shows `7523` (Orin Nano 8GB), `7623` (Orin
   Nano 4GB), `7323`/`7423` (Orin NX 16/8GB). `7020` means it booted normally.
4. **Host NFS up**:
   `sudo bash $FLASH/host-nfs.sh up $L4T tools/kernel_flash/images tools/backup_restore`
5. **Back up** (5-15 min; run in the background):
   `bash $SKILL/backup-in-docker.sh $L4T <board> <dest_dir> nvme0n1`.
   Success: `Backup complete`, `BACKUP_RC=0`, then `backup: <dest_dir>/<board>-<timestamp>`.
6. **Reboot the board** out of the recovery initrd (no jumper fitted, so it
   boots normally; power-cycling also works):
   `docker run --rm --network host l4t-flash:24.04 sshpass -p root ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@fc00:1:1::2 'sync; (sleep 1; reboot -f) >/dev/null 2>&1 &'`
7. **Host NFS down**: `sudo bash $FLASH/host-nfs.sh down`.
8. **Verify**: `bash $SKILL/verify-backup.sh <dest_dir>/<board>-<timestamp>`
   must end in `VERIFY: PASS` and list `QSPI0.img`.

With a USB-C cable left connected permanently, steps 2-8 need nobody at the
board: backups (and restores, reflashes) become fully remote.

Record the board, JetPack release and BSP version with the backup: a restore
needs the same BSP release.

## Resume after a dropped link

If the log stops with `BACKUP_RC=255` mid-`tar`, the ssh session to the target
died. The target stays in its initrd (`lsusb` shows `0955:7035`). Check the
host still has `fc00:1:1::1` on the `cdc_ncm` interface (`ip -br addr`); if not,
`sudo ip link set <if> up && sudo ip addr replace fc00:1:1::1/64 dev <if>`.
The target's NFS mount is `hard`, so a running backup simply continues once the
link is back; do not delete files under `images/` while it runs. To restart
instead: on the target kill `nvbackup_partitions.sh`, `tar`, `zstd`, then
`umount -f /mnt`; on the host empty `images/`; then on the target run
`mount -o nolock [fc00:1:1::1]:<L4T>/tools/backup_restore /mnt && /mnt/nvbackup_partitions.sh -e nvme0n1 -n`,
and move `images/*` to the destination yourself.

## Restore (erases the target drive and QSPI)

`restore-in-docker.sh` wraps NVIDIA's `l4t_backup_restore.sh -r` and first
checks what NVIDIA's restore does not: every file's SHA-256 (NVIDIA never checks
the APP `tar.zst`, and on a bad image checksum skips that partition yet still
reports success) and that the backup's L4T release equals the BSP's.

1. Tell the user this erases the target drive and rewrites its QSPI firmware,
   and get a yes. Then recovery mode (steps 1-3 above). A board that no longer
   boots needs the J14 jumper.
2. Host NFS up (step 4).
3. `bash $SKILL/restore-in-docker.sh $L4T <board> <backup_dir> nvme0n1`
   (~10 min). Success: `Successful restore of partitions`, `RESTORE_RC=0`,
   `restore OK`. The backup folder is only read; its files are copied into the
   BSP and removed afterwards. The log is saved in `<backup_dir>`.
4. Reboot the board (step 6) and host NFS down (step 7).
5. Check on the booted board: `systemctl is-system-running` says `running`,
   `head -1 /etc/nv_tegra_release` and `sudo nvbootctrl dump-slots-info` show
   the backup's release, and ssh connects without a host-key warning.

The restore writes the backup's GPT verbatim, so the target drive must be at
least as large as the source; a smaller one is not supported. On a larger one
the extra space stays unused until you move the backup GPT and grow APP
(`sgdisk -e`, `growpart`, `resize2fs`). APP gets a new ext4 UUID, which is
harmless: the board boots by PARTUUID, which the GPT keeps.

## Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `BACKUP_RC=255` about a minute in; host lost `fc00:1:1::1` | NetworkManager tried DHCP on the Jetson's USB link and flushed it after 45 s | `host-nfs.sh up` marks `driver:cdc_ncm` unmanaged (current version); see Resume |
| backup of `mmcblk0` or "no such device" on an Orin Nano | NVIDIA's script defaults to `mmcblk0` for p3767-0005 | always pass the device (`-e nvme0n1`) |
| target `mount ... /mnt` hangs or times out | NVIDIA's helpers restarted/re-exported nfsd from inside the container | `backup-in-docker.sh` shims `service`, `rpcbind`, `exportfs`; NFS is served by `host-nfs.sh` |
| `ERROR: ... images is not empty` | previous backup still in the BSP | move it out; the script refuses to mix backups |
| `Failed to start nfs-server.service: Unit nfs-mountd.service is masked` | host masked NFS units | `host-nfs.sh up`/`down` unmasks and re-masks |
| `mounting ... not a directory` in `docker run` | snap-packaged Docker cannot see host `/tmp` | keep `$WORK` and the destination outside `/tmp` |
| `lsusb` shows `7020` | board booted normally | step 2 again |
| `ERROR: release mismatch` | BSP release differs from the backup | use the BSP release the backup was taken with |
| `You are trying to flash images from a board model that does not match` | backup is from another module/carrier (board_spec) | restore only onto the same board type |
| `ping: ... missing cap_net_raw` after restore | not the restore: NVIDIA's sample rootfs ships with no file capabilities, so the board never had it | `sudo setcap cap_net_raw+p /usr/bin/ping` |

## Tested with

Orin Nano 8GB Super devkit (p3767-0005), 500 GB NVMe with 7.9 GB used, Jetson
Linux r39.2.1 (JetPack 7.2.1); host Ubuntu 26.04, Docker 29. Recovery entered
with the jumper-free `systemctl` command, left with `reboot -f`. Backup: 2.9 GB,
18 files incl. `QSPI0.img`, ~3 min, `VERIFY: PASS`. Restore of that backup
onto the same board (~10 min), checked before booting: all 198,850 root entries
match the backup (content SHA-256, mode, owner, symlink targets, file
capabilities), the 14 raw partitions and the 64 MB QSPI match byte for byte, and
marker files written after the backup were gone. It then booted with no failed
units, same SSH host key, firmware 39.2.1.
