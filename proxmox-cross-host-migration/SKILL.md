---
name: proxmox-cross-host-migration
description: Use when moving, copying, migrating, restoring, or planning a move/copy of QEMU VMs, CTs, or LXC containers between two standalone non-cluster Proxmox VE/PVE node hosts over SSH with vzdump/qmrestore/pct restore, especially when VMIDs collide, MAC addresses must survive for DHCP reservations, or unprivileged LXCs, linked clones, or templates are involved.
---

# Proxmox Cross-Host VM/LXC Migration (no cluster)

## Purpose
Move QEMU VMs and LXC containers between two standalone Proxmox VE hosts using `vzdump` → `qmrestore` / `pct restore` over SSH, with **byte-for-byte preservation** of MAC addresses, memory, cores, BIOS, machine type, boot order, and unprivileged/mountoptions flags.

This skill exists because several aspects of the workflow are **not obvious** and have bitten real migrations on PVE 9.1.x.

## When this skill applies
- Two PVE hosts, neither in a cluster (or in different clusters), reachable over SSH from a workstation.
- Full downtime is acceptable for the guests being moved (use `vzdump --mode stop`).
- You need original VMIDs preserved on the destination, MAC addresses preserved (DHCP reservations), and full guest config (memory, cores, BIOS, machine, etc.) preserved.

## When NOT to use
- Hosts in the same cluster — use `qm migrate` / `pct migrate`.
- Need zero-downtime / live migration — set up shared storage and a cluster.
- Want incremental/repeatable replication — use ZFS send/recv or PBS instead.

## Iron rules (the non-obvious ones)

### 1. Never use `pct restore -` (stdin) on PVE 9.1.x for unprivileged LXCs
Streaming `vzdump --stdout | ssh dest 'pct restore <id> - --storage X'` **silently drops** `unprivileged: 1` and `mountoptions=discard` from the restored container's config. The cause is in `/usr/share/perl5/PVE/API2/LXC.pm`: the path that reads the embedded `pct.conf` from the archive only runs when `$archive ne '-'`. Stdin restores get the default privileged container with no mountoptions.

**Fix:** Always stream the dump into a **file** on the destination first, then `pct restore` from that file:

```sh
ssh src 'vzdump <id> --stdout --compress zstd --mode stop' \
  | ssh dest 'cat > /var/lib/vz/dump/lxc-<id>-migration.tar.zst'
ssh dest 'pct restore <id> /var/lib/vz/dump/lxc-<id>-migration.tar.zst --storage local-zfs'
```

The file path correctly propagates `unprivileged`, `mountoptions=discard`, and any other LXC-only fields.

### 2. `qmrestore` requires standard vzdump filename pattern
For VMs, `qmrestore <file> <vmid>` errors with `couldn't determine archive info` if the file is not named like `vzdump-qemu-<vmid>-YYYY_MM_DD-HH_MM_SS.vma.zst`. If you saved the stream under a custom name, rename it first:

```sh
ssh dest 'mv /var/lib/vz/dump/vm-<id>-migration.vma.zst \
  /var/lib/vz/dump/vzdump-qemu-<id>-YYYY_MM_DD-HH_MM_SS.vma.zst'
```

`pct restore` does not have this restriction — any filename works for LXCs.

### 3. Templates cannot be boot-tested
A guest with `template: 1` will refuse `qm start` ("you can't start a vm if it's a template"). When renumbering or restoring a template, the boot-test gate is **N/A by definition**. Skip that step and rely on the config diff + MAC check.

Detect before booting:

```sh
ssh dest 'qm config <vmid> | grep -E "^template:"'
```

If `template: 1`, do not run `qm start`.

### 4. Linked clones block destruction of their parent
If VM 101 was created as a **linked clone** of VM 100 (`base-100-disk-X@__base__`), then `qm destroy 100` fails with `base volume '...' is still in use by linked cloned VM 101` even after VM 100 has been renumbered/repurposed.

Order of operations when both source VMIDs collide:

1. Renumber the parent (100 → 110) via vzdump+qmrestore (the new VMID 110 has its own copy of the base disks).
2. Renumber the clone (101 → 111). A **full** `vzdump` of 101 captures real disk content (not the clone reference), and the restore as 111 creates independent volumes.
3. `qm destroy 101` first.
4. **Then** `qm destroy 100`.

Sanity check before step 4:

```sh
ssh dest 'zfs get -H -o name,property,value origin <pool>/data \
  | grep -v "origin\t-$" || echo "no clones remain"'
```

### 5. Preservation is the default — verification is the gate
`vzdump` + `qmrestore` / `pct restore` preserve memory, cores, sockets, CPU type, BIOS, machine, boot order, NIC settings (including MAC), `agent`, `onboot`, and disk sizes by default. Don't trust the default — diff against a baseline before booting.

### 6. Never pass `--unique` when DHCP/MAC preservation matters
Both `qmrestore` and `pct restore` support `--unique`, which assigns a random Ethernet address. That is useful for cloned lab guests, but wrong for migrations where DHCP reservations, firewall rules, monitoring, or ARP expectations depend on the original MAC. Leave `--unique` out and make the restored MAC comparison part of the pre-boot gate.

## Workflow

### Phase 0: Inventory and capture baselines (read-only, on workstation)
Inventory each requested VMID first so you only run QEMU commands against VMs and LXC commands against containers:

```sh
ssh src  'qm list && pct list'
ssh dest 'qm list && pct list'
```

For every source guest being migrated, capture the matching source config and MAC baseline:

```sh
mkdir -p ~/migration-artifacts
ssh src 'qm config <vmid>'  > ~/migration-artifacts/baseline-vm-<vmid>.txt      # QEMU VM only
ssh src 'pct config <vmid>' > ~/migration-artifacts/baseline-lxc-<vmid>.txt     # LXC only
ssh src 'qm config <vmid>  | grep -E "^net[0-9]+:"'  > ~/migration-artifacts/mac-baseline-<vmid>.txt   # QEMU VM only
ssh src 'pct config <vmid> | grep -E "^net[0-9]+:"'  > ~/migration-artifacts/mac-baseline-<vmid>.txt   # LXC only
```

Save the MAC values explicitly — they are the contract for the verification gate.

For every destination VMID collision, capture the existing destination guest before renumbering it:

```sh
ssh dest 'qm config <old-id>'  > ~/migration-artifacts/dest-baseline-vm-<old-id>.txt    # QEMU VM collision only
ssh dest 'pct config <old-id>' > ~/migration-artifacts/dest-baseline-lxc-<old-id>.txt   # LXC collision only
```

### Phase 1: Free up colliding VMIDs on destination (if any)
For each colliding existing guest on dest:

**QEMU VM collision:**

```sh
ssh dest 'vzdump <old-id> --dumpdir /var/lib/vz/dump --compress zstd --mode stop'
DUMP=$(ssh dest "ls -1t /var/lib/vz/dump/vzdump-qemu-<old-id>-*.vma.zst | head -1")
ssh dest "qmrestore $DUMP <new-id> --storage local-zfs"
ssh dest 'qm config <new-id>' | diff - ~/migration-artifacts/dest-baseline-vm-<old-id>.txt
# If template, skip boot test. Otherwise:
ssh dest 'qm start <new-id> && sleep 20 && qm status <new-id> && qm stop <new-id>'
ssh dest 'qm destroy <old-id>'                   # may fail if linked-clone parent
ssh dest "rm -f $DUMP ${DUMP}.* ${DUMP%.vma.zst}.log"
```

**LXC collision:**

```sh
ssh dest 'vzdump <old-id> --stdout --compress zstd --mode stop' \
  | ssh dest 'cat > /var/lib/vz/dump/lxc-<old-id>-renumber.tar.zst'
ssh dest 'pct restore <new-id> /var/lib/vz/dump/lxc-<old-id>-renumber.tar.zst --storage local-zfs'
ssh dest 'pct config <new-id>' | diff - ~/migration-artifacts/dest-baseline-lxc-<old-id>.txt
ssh dest 'pct start <new-id> && sleep 20 && pct status <new-id> && pct stop <new-id>'
ssh dest 'pct destroy <old-id>'
ssh dest 'rm -f /var/lib/vz/dump/lxc-<old-id>-renumber.tar.zst'
```

Any unexpected diff stops the workflow. Review and fix the restored guest before boot-testing or destroying the old colliding VMID.

### Phase 2: Stop running source guests
```sh
ssh src 'qm shutdown <vmid> --timeout 120 || qm stop <vmid>'
ssh src 'pct shutdown <vmid> --timeout 60  || pct stop <vmid>'
```

### Phase 3: Migrate (file-based, workstation as relay)

**LXC** (use `.tar.zst`):
```sh
ssh src 'vzdump <id> --stdout --compress zstd --mode stop' \
  | ssh dest 'cat > /var/lib/vz/dump/lxc-<id>-migration.tar.zst'
ssh dest 'pct restore <id> /var/lib/vz/dump/lxc-<id>-migration.tar.zst --storage local-zfs'
ssh dest 'rm -f /var/lib/vz/dump/lxc-<id>-migration.tar.zst'
```

**VM** (use `.vma.zst`, name it the standard way):
```sh
DUMP=/var/lib/vz/dump/vzdump-qemu-<id>-$(date +%Y_%m_%d-%H_%M_%S).vma.zst
ssh src 'vzdump <id> --stdout --compress zstd --mode stop' \
  | ssh dest "cat > $DUMP"
ssh dest "qmrestore $DUMP <id> --storage local-zfs"
ssh dest "rm -f $DUMP ${DUMP}.* ${DUMP%.vma.zst}.log"
```

The workstation only relays the byte stream; nothing is written there.

### Phase 4: Verification gate (per guest, before boot)

All of these must pass. Any failure = fix on dest before booting.

| Check | Command | Acceptable diff |
|-------|---------|-----------------|
| Config diff | `diff baseline-*.txt <(ssh dest 'qm/pct config <id>')` | Only VMID-bearing volume names, regenerated `smbios1`/`vmgenid`, `rootfs` storage-path normalization. **All other fields must match — especially memory, cores, MAC, `unprivileged`, `mountoptions`.** |
| MAC | `grep -E '^net[0-9]+:' post.txt` | Every `macaddr=` / `hwaddr=` / `virtio=` byte-for-byte equal to baseline. |
| ZFS volume | `ssh dest 'zfs list \| grep -E "(vm\|subvol)-<id>-disk"'` | Exists at the expected size. |

Then boot once, capture IPv4 (DHCP reservation must hand out the same IP for the preserved MAC), and leave the guest in the end state that matches its source state (running stays running, stopped stays stopped).

### Phase 5: Source quiescence
Source guests on the source host are already stopped from Phase 2. **Leave them stopped, do not destroy** — they are the rollback. Only destroy after a soak period and explicit operator approval.

## Common mistakes

| Mistake | What happens | Fix |
|--------|--------------|-----|
| `pct restore <id> -` (stdin) | LXC ends up privileged + no `mountoptions=discard`; UID mapping wrong on first boot | Stream to file first, then restore from file |
| `qmrestore /var/lib/vz/dump/custom-name.vma.zst` | `couldn't determine archive info` | Rename to `vzdump-qemu-<id>-YYYY_MM_DD-HH_MM_SS.vma.zst` first |
| `qm destroy <parent-id>` while a linked clone exists | `base volume ... still in use by linked cloned VM` | Renumber/destroy the clone first; verify no `origin` pointers remain |
| Booting a template | `you can't start a vm if it's a template` | Skip boot test for templates; rely on config diff + MAC check |
| Skipping config diff | Silent loss of `unprivileged`/`mountoptions`/CPU type/etc. boots into a subtly broken state | Diff against baseline before every `qm start` / `pct start` |
| Pinging an LXC without first running `pct exec ... ip -4 addr` | DHCP lease may not yet be visible from outside | Read the IP from inside the container after a 30s settle, then ping from workstation |

## Required clarifications (ask before running)
1. **Source host** and **destination host** SSH aliases (e.g. `pve-src`, `pve-dest`).
2. **VMIDs to migrate** and any **VMID collisions** on destination (collisions block phase 0).
3. **End state** desired on destination per guest (running vs stopped).
4. **Storage name on destination** (default `local-zfs`).
5. **Whether the source is destroyed** after verification (default: NO — keep stopped for rollback).

## What this skill explicitly does NOT cover
- Renaming guests (only VMIDs are renumbered when needed).
- Cluster join/migrate, live migration, shared storage setup.
- Proxmox Backup Server (PBS) workflows.
- ZFS send/recv replication.
- Decommissioning the source host after migration.

## Provenance
Validated end-to-end on PVE 9.1.9 → 9.1.9: one ~120 GB sparse VM and two unprivileged LXCs migrated, with prior-renumber of two destination-side VMs (one template, one linked clone). Each of the iron rules above corresponds to a real failure mode hit during that run.
