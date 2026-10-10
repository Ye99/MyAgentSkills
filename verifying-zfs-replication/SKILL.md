---
name: verifying-zfs-replication
description: Use when checking that a ZFS replication between two hosts (syncoid, zfs send/receive, including a resumed or interrupted transfer or one sent over insecure direct TCP) produced an exact copy — metadata and byte-for-byte content — or when asked to prove there was no data corruption after copying a dataset to a backup or NAS host.
---

# Verifying ZFS Replication

## Overview

Prove that `<dst-host>:<dst-dataset>@<to-snap>` is an exact copy of `<src-host>:<src-dataset>@<to-snap>`, in two layers:

1. **Snapshot GUIDs.** Every target snapshot exists on the source with the same name and GUID. A matching GUID means `zfs receive` committed exactly that snapshot (every record is checksummed in the stream).
2. **Files changed since the previous common snapshot.** For each path `zfs diff` lists between `@<from-snap>` and `@<to-snap>`, both hosts record type, mode, uid, gid, size, mtime (ns), symlink target, xattrs and a SHA-256 of the full content; the two lists must be identical, and every deleted path must be absent on the target.

Both sides are read from the **immutable `.zfs/snapshot/<to-snap>` directories**, never the live filesystems, so nothing can change during the check. SHA-256 equality over every byte is the byte-for-byte comparison, without shipping the data across the network a second time.

## Procedure

```bash
nohup bash scripts/verify.sh <src-host> <src-dataset> <dst-host> <dst-dataset> <from-snap> <to-snap> > verify.log 2>&1 &
```

- `<from-snap>`: the newest snapshot both sides had **before** this transfer; `<to-snap>`: the one just received. To verify a full (non-incremental) send, verify the oldest snapshot with a full-tree manifest instead (see below).
- Needs passwordless ssh and `sudo -n` on both hosts, `python3` on both. `THREADS` (default 4) files are hashed at a time per host.
- Run it only when **no replication, scrub or other heavy I/O** is running on either pool: it reads all the changed data on both hosts and takes as long as the slower pool needs.
- Result: `PASS`, or `FAIL` with one line per problem (`DIFF sha256 <path>`, `DELETED BUT PRESENT <path>`, ...). Work files stay in `~/zfs-verify-<to-snap>/` on both hosts; delete them after reading the result.

The hashing runs detached on each host. If the session running `verify.sh` dies, rerun the same command: finished or running jobs are not restarted, it just resumes polling.

## Quick reference

| Step | Script | Notes |
|---|---|---|
| GUIDs | `verify.sh` step 1 | `zfs list -t snapshot -d 1 -o name,guid`; the target may keep fewer (older pruned) snapshots |
| Change list | `changed_paths.py` | Decodes `zfs diff` escapes; renames count as deleted old path + present new path |
| Manifest | `manifest.py` | Per host, from `.zfs/snapshot/<to-snap>`; directory sizes are skipped (ZAP layout differs legitimately) |
| Compare | `compare.py` | All fields must match; exits non-zero on any difference |

## Full-tree check (optional)

For the whole dataset rather than the change set, build the list from the source snapshot itself and run steps 3–4 by hand:

```bash
cd <src-mount>/.zfs/snapshot/<to-snap> && sudo find . -mindepth 1 -printf '%P\0' > ~/zfs-verify-<to-snap>/present.nul
```

Then copy it to the target with an empty `deleted.nul`. This reads the entire dataset on both hosts.

## Common Mistakes

- **Hashing the live mountpoints.** Files change underneath; compare `.zfs/snapshot/<to-snap>` on both sides.
- **Parsing `zfs diff` paths literally.** Spaces and non-ASCII bytes come out as `\0ooo` (backslash + 4 octal digits): `\0040` is a space, `\0342\0200\0231` is `’`. Undecoded, nearly every lookup fails as "missing".
- **Child datasets.** `zfs diff` does not descend into children; run once per dataset (`zfs list -r`).
- **Nested `ssh` inside `ssh host 'bash -s' <<EOF`** swallows the rest of the script from stdin; use `ssh -n`.
- **zsh login shells** don't word-split `$VAR` lists; the scripts run under `bash`.
- **Treating a partial receive as a failure.** A leftover `receive_resume_token` only means a newer stream is incomplete; the snapshots compared here are complete. Resume the replication first if you also want the newest snapshot.
- **Expecting equal snapshot counts.** Retention can differ per side; compare names and GUIDs of what the target holds.
