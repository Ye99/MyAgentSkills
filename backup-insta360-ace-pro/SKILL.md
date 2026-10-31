---
name: backup-insta360-ace-pro
description: Use when copying photos and videos off an Insta360 Ace Pro series (Ace Pro, Ace Pro 2) microSD card (DCIM/Camera01 with IMG_*.jpg, VID_*.mp4, LRV_*.lrv) into a backup folder such as /run/media/<user>/GoProBackup/AcePro2, adding only new files with rsync and skipping the .lrv low-resolution proxies.
---

# Back up an Insta360 Ace Pro card

## Overview

Use rsync to copy new media from the camera card into a flat backup folder.
Skip the `.lrv` proxies, which the camera writes for previews in the Insta360 app,
because the full-resolution `.mp4` is the footage. The card is only read and
nothing in the backup is deleted, so earlier backups stay even after the card is
wiped.

## Layout

| | Path | Contents |
|---|---|---|
| Card | `<card>/DCIM/Camera01/` | `IMG_YYYYMMDD_HHMMSS_NNN.jpg`, `VID_..._NNN.mp4`, `LRV_..._NNN.lrv` (flat, no subfolders) |
| Backup | `/run/media/<user>/GoProBackup/AcePro2/` | the same files, flat (no `DCIM/` level), without `.lrv` |

`<card>/MISC` holds camera state and is not backed up.

## Procedure

1. **Find the card.** It is an exFAT partition with `DCIM/Camera01` at its root.
   It may be auto-mounted under `/run/media/<user>/` or mounted manually (e.g. `/mnt`).

   ```bash
   lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT
   ls <card>/DCIM/Camera01 | sed 's/.*\.//' | sort | uniq -c
   ```

   Make sure the card and the backup are on different devices. The backup drive
   is labelled `GoProBackup` and has its own `DCIM` from the GoPro, so don't mix the two up.

2. **Check free space**: `df -h <card> <backup>`.

3. **Dry run.** Look at what would be copied:

   ```bash
   rsync -rtn --modify-window=1 --exclude='*.lrv' --itemize-changes --stats \
       <card>/DCIM/Camera01/ /run/media/<user>/GoProBackup/AcePro2/
   ```

   `>f+++++++++` lines are new files. `Number of deleted files: 0` is expected.

4. **Copy** (run in the background if it is many GB; it runs at about 45 MB/s over USB):

   ```bash
   rsync -rt --modify-window=1 --exclude='*.lrv' --info=progress2,stats1 \
       <card>/DCIM/Camera01/ /run/media/<user>/GoProBackup/AcePro2/
   sync
   ```

5. **Verify.**
   - Run the step 3 dry run again. It should list no files.
   - Checksum the files that were just copied, e.g. for one shooting day:

     ```bash
     cd <card>/DCIM/Camera01 && ls *YYYYMMDD* | grep -v '\.lrv$' > "$TMPDIR/new.txt"
     rsync -rcn --itemize-changes --files-from="$TMPDIR/new.txt" \
         <card>/DCIM/Camera01/ /run/media/<user>/GoProBackup/AcePro2/
     ```

     No output means the contents match.
   - Compare counts: the card's non-`.lrv` count should be no more than the backup's.

6. **Report** how many files and GB were copied and what the backup now holds.
   Do not format or delete anything on the card unless the user asks.

## Flags

| Flag | Why |
|---|---|
| `-rt` (not `-a`) | exFAT has no Unix owner, group or permissions, so `-a` only adds errors |
| `--modify-window=1` | tolerates exFAT timestamp rounding, so unchanged files are not re-copied |
| `--exclude='*.lrv'` | skips the low-res preview proxies (about 5% of the card size) |
| no `--delete` | the backup keeps files after the card is cleared |
| trailing `/` on the source | copies the contents of `Camera01`, not the folder itself |

## Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| A `Camera01/` folder appears inside `AcePro2/` | source path given without a trailing `/` | move its files up a level and remove the folder; use `Camera01/` |
| Every file is copied again on each run | `--modify-window` missing, or `-c` used for the copy | use the flags above; `-c` only in the verify step |
| `.lrv` files from earlier runs are in the backup | backed up before the exclude was added | leave them, or delete with `rm <backup>/AcePro2/*.lrv` if the user agrees |
| `chown`/`chmod` errors | `-a` or `-p` on exFAT | use `-rt` |

## Tested with

Insta360 Ace Pro 2, backup to `/run/media/<user>/GoProBackup/AcePro2`. Other Ace Pro
models are expected to use the same `DCIM/Camera01` layout; check with step 1 and
use a backup folder named after the model.
