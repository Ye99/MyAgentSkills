---
name: virsh-delete-and-flatten-snapshots
description: Use when removing libvirt external snapshots for a QEMU/KVM VM on Linux, either to clean up snapshot metadata or to retire all restore points and reclaim overlay disk space
---

# Delete And Flatten virsh Snapshots (Linux / QEMU/KVM)

## Overview
Use this when a VM has external libvirt snapshots and the user wants one of two outcomes:
- delete selected snapshot metadata only
- delete all restore points and reclaim disk space by flattening the qcow2 chain and deleting obsolete overlay files

This workflow defaults to `qemu:///system`, which matched the successful host workflow used for `process-photo-vm`.

Prefer the bundled helper script for execution:
```bash
python3 scripts/vm_snapshot_helper.py --help
```
It makes parsing, validation, XML rewriting, and path tracking more deterministic than ad hoc shell commands.
It also emits a structured JSON summary at the end of `cleanup` runs.

## Required interaction
Ask these in order before running destructive commands:

1. "What is the VM name?"
2. List snapshots for that VM from `qemu:///system`
3. "Do you want to `remove snapshot records only` or `reclaim disk space`?"
4. Treat `remove snapshot records only` as `metadata-only`: "Which snapshot(s) should be deleted?"
5. Treat `reclaim disk space` as `reclaim-space`: "Type `all` to confirm that all restore points will be removed and only the current VM state will be kept."
6. "Proceed?"

If `snapshot-list` fails with domain not found, ask whether the VM might live under `qemu:///session` and only switch if the user confirms.

## Safety rules
- Treat this as an offline workflow. Confirm the VM is shut off before flattening.
- Deleting libvirt snapshot metadata alone does not reclaim overlay disk space.
- `metadata-only` mode does not reclaim overlay disk space and may leave the VM still running on an overlay.
- `reclaim-space` mode destroys all snapshot restore points and keeps only the VM's current state.
- Deleting all snapshot metadata is only the prerequisite for reclaiming space. Actual reclaim happens later when the overlay chain is committed, the VM is repointed to the base qcow2, and obsolete overlay files are deleted.
- This behavior comes from external snapshot design: overlay qcow2 files can remain structurally required even after libvirt snapshot metadata is gone.
- Do not delete overlay files until the chain has been committed, the VM definition has been updated, and verification shows the base qcow2 has no backing chain.
- Prefer exact snapshot names from `virsh snapshot-list` output instead of retyping from memory.

## Workflow

### 1. List snapshots
Preferred:
```bash
python3 scripts/vm_snapshot_helper.py list-snapshots --vm <vm-name>
```

Fallback:
```bash
virsh -c qemu:///system snapshot-list <vm-name>
```

### 2. Choose mode

#### Mode A: `metadata-only`
Use this when the user wants to remove one or more libvirt snapshot records but does not want to flatten the disk chain.

#### Mode B: `reclaim-space`
Use this when the user is done with all restore points for the VM and wants to keep only the current VM state.

Important:
- Do not use `reclaim-space` for a subset of snapshots.
- External snapshot chains usually do not let you reclaim space for one deleted snapshot in isolation if newer overlays still depend on it.
- The helper script refuses `reclaim-space` unless `dominfo` reports the VM is `shut off`.

### 3A. `metadata-only`: delete requested snapshot metadata
Preferred:
```bash
python3 scripts/vm_snapshot_helper.py cleanup --vm <vm-name> --mode metadata-only --snapshot <snapshot-name>
```

For multiple snapshots, repeat `--snapshot`.

Fallback:
For one snapshot:
```bash
virsh -c qemu:///system snapshot-delete <vm-name> --snapshotname <snapshot-name> --metadata
```

For multiple snapshots, repeat the command once per snapshot name selected by the user.

Then verify:
```bash
virsh -c qemu:///system snapshot-list <vm-name>
virsh -c qemu:///system domblklist <vm-name>
```

If `domblklist` already points to the base qcow2, tell the user there is no active overlay chain from the current VM disk attachment.

Stop here in `metadata-only` mode.

Tell the user explicitly:
- snapshot metadata is removed
- overlay disk files may still exist
- no disk space reclaim is guaranteed
- reclaiming space later requires retiring all restore points and flattening the full active chain

### 3B. `reclaim-space`: delete all snapshot metadata
In `reclaim-space` mode, require `all`. Do not flatten for a subset of snapshots.

Preferred:
```bash
python3 scripts/vm_snapshot_helper.py cleanup --vm <vm-name> --mode reclaim-space --all
```

Fallback:
Delete every snapshot returned by `snapshot-list`:
```bash
virsh -c qemu:///system snapshot-delete <vm-name> --snapshotname <snapshot-name> --metadata
```

Tell the user explicitly:
- all restore points are being retired
- deleting the snapshot metadata does not reclaim space yet
- reclaim happens only after the flatten, redefine, and overlay-file deletion steps complete

### 4. Verify metadata is gone
```bash
virsh -c qemu:///system snapshot-list <vm-name>
```

### 5. Inspect current disk source and chain
```bash
virsh -c qemu:///system dominfo <vm-name>
virsh -c qemu:///system domblklist <vm-name>
sudo -n qemu-img info --backing-chain <active-disk-path>
```

Expected pattern for external snapshots:
- `domblklist` points at the top overlay, not the base qcow2
- `qemu-img info --backing-chain` shows overlay -> backing overlay(s) -> base qcow2

Record these before changing anything:
- the active disk path from `domblklist`
- the ordered overlay chain from `qemu-img info --backing-chain`, excluding the final base image entry
- the base qcow2 path
- the directory containing those disk files

### 6. Flatten the chain offline
Commit each overlay into its backing file starting from the topmost active overlay and moving downward.

Example:
```bash
sudo -n qemu-img commit -p /var/lib/libvirt/images/vm.top-overlay
sudo -n qemu-img commit -p /var/lib/libvirt/images/vm.middle-overlay
```

Rules:
- Commit top to bottom, never bottom to top.
- Wait for each `qemu-img commit -p` to finish before starting the next one.
- Commit each overlay entry except the final base image.
- If there are `N` overlay layers above the base image, run `N` commits.
- Do not guess the chain order. Use the order reported by `qemu-img info --backing-chain`.

### 7. Repoint libvirt to the base qcow2
Dump the current XML, replace only the active disk source path reported by `domblklist` with the base qcow2 path, then redefine the domain.

Example:
```bash
virsh -c qemu:///system dumpxml <vm-name> > /tmp/<vm-name>.flatten.before.xml
sed 's#<old-active-overlay>#<base-qcow2>#' /tmp/<vm-name>.flatten.before.xml > /tmp/<vm-name>.flatten.after.xml
virsh -c qemu:///system define /tmp/<vm-name>.flatten.after.xml
```

Rules:
- Only replace the exact active disk path reported by `domblklist`.
- Verify the VM has a single target disk source before using a simple substitution.
- If the VM has multiple disks or ambiguous matches, edit the XML more precisely instead of using a broad replacement.
- Verify the updated XML changes only the intended disk source before running `virsh define`.

### 8. Verify flatten succeeded
```bash
virsh -c qemu:///system domblklist <vm-name>
sudo -n qemu-img info --backing-chain <base-qcow2-path>
```

Success criteria:
- `domblklist` now points directly to the base qcow2
- `qemu-img info --backing-chain` shows no backing file

### 9. Delete obsolete overlay files
Only after the verification above succeeds:
```bash
sudo -n rm -f <old-overlay-1> <old-overlay-2>
```

### 10. Final verification
```bash
sudo -n test -e <base-qcow2-path>
sudo -n test ! -e <old-overlay-1>
sudo -n test ! -e <old-overlay-2>
```

Confirm that:
- the base qcow2 still exists
- the deleted overlay files are gone
- the VM disk source still matches the base qcow2 path

## Decision guide
- If the user wants to keep selected restore points, use `metadata-only` and do not flatten.
- If the user wants to keep the VM exactly as it is now and remove all restore points, use `reclaim-space`.
- If the user wants to discard the snapshot-layer changes and return to an earlier state, do not flatten.
- If the snapshot metadata is removed but `domblklist` still points to an overlay, the disk chain still exists and space has not yet been reclaimed.
- If only one snapshot out of several was deleted, assume no meaningful space reclaim is available through this workflow yet.

## Common mistakes
- Using `reclaim-space` when the user only wanted to remove one snapshot name.
- Deleting snapshot metadata and assuming disk space was reclaimed.
- Assuming that deleting all snapshot metadata is itself the reclaim step.
- Trying to reclaim space for one deleted snapshot while preserving newer dependent overlays.
- Forgetting to check `domblklist` after metadata deletion.
- Running `qemu-img commit` in the wrong order.
- Deleting overlay files before redefining the VM onto the base qcow2.
- Assuming `qemu:///session` when the VM actually lives under `qemu:///system`.

## Helper script
Bundled script:
- `scripts/vm_snapshot_helper.py`

Supported commands:
```bash
python3 scripts/vm_snapshot_helper.py list-snapshots --vm <vm-name>
python3 scripts/vm_snapshot_helper.py cleanup --vm <vm-name> --mode metadata-only --snapshot <snapshot-name>
python3 scripts/vm_snapshot_helper.py cleanup --vm <vm-name> --mode reclaim-space --all
```

Useful options:
- `--uri qemu:///system`
- `--dry-run`

Behavior notes:
- `cleanup` prints a JSON summary describing the mode, deleted snapshots, active disk, base disk, overlays, and whether the run was a dry run.
- `reclaim-space` validates the VM is shut off before planning or executing flatten operations.

Tests:
```bash
pytest tests/test_vm_snapshot_helper.py
```
