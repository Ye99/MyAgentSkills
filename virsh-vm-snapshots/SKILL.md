---
name: virsh-vm-snapshots
description: Manage QEMU/KVM VM external snapshots (backups) using virsh on Linux
---

# virsh VM Snapshot Management (Linux / QEMU/KVM)

## Purpose
Create, list, inspect, and delete external virsh snapshots for QEMU/KVM VMs.

## Safety rules
- Only snapshot when VM is shut off, or use `--live` deliberately for running VMs.
- Use external snapshots only (`--disk-only`) due to UEFI compatibility requirements.
- External snapshots are created near-instantly (overlay file) and do not block the disk image.
- Always verify the snapshot was created with `virsh snapshot-info` before considering the backup complete.

## Key commands

### List all snapshots for a VM
```bash
virsh snapshot-list <vm-name>
```

### Show snapshot details (location, parent, current)
```bash
virsh snapshot-info <vm-name> <snapshot-name>
```

### Show VM's disk(s) — useful after external snapshot to see new overlay path
```bash
virsh domblklist <vm-name>
```

### Create an external snapshot (fast overlay — preferred for large disks)
```bash
virsh snapshot-create-as <vm-name> <name> "<description>" --disk-only
```
- Creates a new overlay qcow2 near the base image (exact filename/path depends on libvirt/diskspec settings).
- The original image becomes the read-only backing file (the actual backup point).

### Delete snapshot metadata only (recommended for external-only workflow)
```bash
virsh snapshot-delete <vm-name> --snapshotname <snapshot-name> --metadata
```
- Removes libvirt snapshot metadata without forcing data merge semantics.

### Flatten/remove overlay data in an external chain (optional maintenance)
```bash
virsh blockcommit <vm-name> <disk-path> --active --pivot --delete --wait
```
- Commits overlay data into backing image and deletes committed overlay files.

## Inspecting VMs

### List all VMs and their states
```bash
virsh list --all
```

### Get full VM info (memory, CPU, state, UUID)
```bash
virsh dominfo <vm-name>
```

## External snapshot summary

| Property | External |
|----------|----------|
| Storage | New overlay file on disk |
| Create speed | Instant |
| Metadata delete speed | Fast (`snapshot-delete --metadata`) |
| Data merge/removal speed | Depends on image size and I/O (`blockcommit`) |
| Required flag | `--disk-only` |
| Location field | `external` |
