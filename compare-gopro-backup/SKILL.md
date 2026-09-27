---
name: compare-gopro-backup
description: Use when checking that a GoPro camera card or GoPro folder was fully backed up, including DCIM/100GOPRO footage such as GX*.MP4. Content differences in card files the camera rewrites (leinfo.sav, mdb12.db, mdb_h_12.bk, and the same mdb*.db / mdb_h_*.bk / mdb_*.log family) are not missing footage.
---

# Compare GoPro Backup

Check that every footage file on a GoPro card is present, by content, in a backup. Reuse `find-missing-files` for the hash comparison. The camera rewrites its own card database; those differences are not a failed backup.

## Required Sub-Skill

Use `find-missing-files` for the content-hash comparison. This skill does not hash files itself. It calls that skill's checker and then sets aside GoPro card database files.

## When to Use

- A GoPro card, or a folder copied from one, needs to be checked against a backup.
- A previous content compare listed `DCIM/leinfo.sav`, `mdb12.db`, or `mdb_h_12.bk` and the question is whether the footage was backed up.

Use `find-missing-files` directly for a general folder compare, or when `.THM` and `.LRV` proxies must be included.

## Read-only

Read both trees. Write the report outside both of them. Leave the card and the backup unchanged.

When one tree is on another machine, mount it read-only (`sshfs -o ro`), compare the mount, then unmount it. For a network mount, pass `--workers 4`.

## GoPro card files to ignore

The camera updates these files when it records or indexes clips. A size or hash difference here is expected. Match by basename, anywhere in the tree, case-insensitive:

| Basename | Role |
|---|---|
| `leinfo.sav` | Camera state. Often at `DCIM/leinfo.sav`. |
| `mdb*.db` | Card media database. Observed as `mdb12.db`. The number can change. |
| `mdb_h_*.bk` | Database companion. Observed as `mdb_h_12.bk`. |
| `mdb_*.log` | Database log. Observed as `mdb_12.log`. |

`.THM` and `.LRV` low-resolution proxies stay excluded by the `find-missing-files` defaults, along with a top-level `Backedup` directory and macOS metadata. Footage is the remaining files, including `GX*.MP4`.

## Run

`scripts/compare_gopro_backup.py` is relative to this skill directory. `source` is the card (the tree that must be fully present). `destination` is the backup.

```bash
python scripts/compare_gopro_backup.py \
    /path/to/gopro-card \
    /path/to/backup \
    --output /tmp/gopro-backup-compare.txt \
    --verbose
```

The script refuses to write the report inside either tree.

## How to read the result

The report has two parts:

1. **Footage.** Paths with no content match in the backup. `|-- No missing footage (everything matched)` means the backup contains every compared footage file, even if a file was renamed or moved.
2. **Ignored GoPro card files.** Card database paths whose bytes differ. Report them as ignored. They do not make the backup incomplete.

A backup is complete when the footage section has no missing files.
