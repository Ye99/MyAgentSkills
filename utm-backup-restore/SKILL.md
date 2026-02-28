---
name: utm-backup-restore
description: Back up and restore UTM VM bundles with full macOS bundle metadata
---

# UTM VM Backup/Restore (macOS)

> **Note:** This skill and its scripts are strictly for macOS. They rely on `ditto` to preserve bundle metadata.

## Purpose
Back up and restore UTM `.utm` bundles with full macOS bundle fidelity.

This skill intentionally covers backup/restore only (no crash recovery).

## Safety rules
- Only back up when VM is fully shut down (not running, not suspended).
- Prefer quitting UTM before copying/restoring.
- Always copy entire `.utm` bundle (config + disk images).
- Use `ditto` so macOS bundle metadata is preserved.

## Default UTM VM location
```
~/Library/Containers/com.utmapp.UTM/Data/Documents
```

## Required clarifications

Before executing any backup/restore operation, ask the user to confirm:

1. **VM name** (`vm_name`)
   - Default: `DevVM.utm`
   - Ask: "Which VM do you want to back up or restore? (default: DevVM.utm)"

2. **Backup directory** (`backup_dir`)
   - Default: `/Volumes/T5/UTM`
   - Ask: "Where should the backup be stored? (default: /Volumes/T5/UTM)"

3. **Info tag** (`InfoTag`) - OPTIONAL
   - Default: none (user should provide a short descriptor if needed)
   - Ask: "Add a descriptive tag for this backup? (e.g., 'SetWallPaper', 'BeforeUpgrade') Press Enter to skip."

**Important**: If the user doesn't provide values, use the defaults shown above.

## Back up a VM
One-off timestamped backup:

```bash
backup_dir="/Volumes/T5/UTM"
vm_name="DevVM.utm"
backup_name="DevVM-$(date +%Y%m%d-%H%M%S)-<InfoTag>.utm"
mkdir -p "$backup_dir"

ditto "$HOME/Library/Containers/com.utmapp.UTM/Data/Documents/$vm_name" \
       "$backup_dir/$backup_name"
```

Notes:
- Replace `<InfoTag>` with a short descriptor.
- Renaming the resulting `.utm` is fine as long as the VM is shut down.

## Zip backups
Use `ditto` for zip creation. Use the helper script in this skill when possible:

```bash
zsh scripts/zipnewutm.sh --dir "/Volumes/T5/UTM"
```

## Restore a zip backup
Restores into the default UTM Documents folder:

Use the helper script in this skill when possible:

```bash
zsh scripts/restoreutm.sh \
   "/Volumes/T5/UTM/DevVM-YYYYMMDD-HHMMSS-InfoTag.zip"
```

## Restore a `.utm`
```bash
ditto "/Volumes/T5/UTM/DevVM-YYYYMMDD-HHMMSS-InfoTag.utm" \
       "$HOME/Library/Containers/com.utmapp.UTM/Data/Documents/"
```

## Helper scripts
Run `--help` for options on bundled scripts (`scripts/zipnewutm.sh`, `scripts/restoreutm.sh`).
