---
name: utm-backup-restore
description: Back up and restore UTM VM bundles with full macOS bundle metadata
license: MIT
compatibility: opencode
---

# UTM VM Backup/Restore (macOS)

## Purpose
Back up and restore UTM `.utm` bundles with full macOS bundle fidelity.

This skill intentionally covers backup/restore only (no crash recovery).

## Safety rules (must follow)
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
   - Ask: "Which VM do you want to backup/restore? (default: DevVM.utm)"

2. **Backup directory** (`backup_dir`)
   - Default: `/Volumes/T5/UTM`
   - Ask: "Where should the backup be stored? (default: /Volumes/T5/UTM)"

3. **Info tag** (`InfoTag`) - OPTIONAL
   - Default: none (user should provide a short descriptor if needed)
   - Ask: "Add a descriptive tag for this backup? (e.g., 'SetWallPaper', 'BeforeUpgrade') Press Enter to skip."

**Important**: If the user doesn't provide values, use the defaults shown above.

## Back up a VM (recommended)
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

## Zip backups (preserve bundle metadata)
Use `ditto` for zip creation. Prefer to use the helper script in this skill:

```bash
zsh .claude/skills/utm-backup-restore/scripts/zipnewutm.sh --dir "/Volumes/T5/UTM"
```

Raw `ditto` form:

```bash
ditto -c -k --sequesterRsrc --keepParent \
   "/Volumes/T5/UTM/DevVM-YYYYMMDD-HHMMSS-InfoTag.utm" \
   "/Volumes/T5/UTM/DevVM-YYYYMMDD-HHMMSS-InfoTag.zip"
```

## Restore a zip backup
Restores into the default UTM Documents folder:

Prefer to use the helper script in this skill:

```bash
zsh .claude/skills/utm-backup-restore/scripts/restoreutm.sh \
   "/Volumes/T5/UTM/DevVM-YYYYMMDD-HHMMSS-InfoTag.zip"
```

Raw `ditto` form:

```bash
restore_dir="$HOME/Library/Containers/com.utmapp.UTM/Data/Documents"
mkdir -p "$restore_dir"

ditto -x -k "/Volumes/T5/UTM/DevVM-YYYYMMDD-HHMMSS-InfoTag.zip" \
   "$restore_dir"
```

## Restore a `.utm` (no zip)
```bash
ditto "/Volumes/T5/UTM/DevVM-YYYYMMDD-HHMMSS-InfoTag.utm" \
       "$HOME/Library/Containers/com.utmapp.UTM/Data/Documents/"
```

## Safe clone workflow (manual)
1. Shut down the VM.
2. Quit UTM.
3. Copy the `.utm` bundle to your backup location.
4. Open UTM and use Open to test the copy.

## Helper scripts (optional)
This skill ships helper scripts:

- `.claude/skills/utm-backup-restore/scripts/zipnewutm.sh` creates `.zip` backups with `ditto`.
- `.claude/skills/utm-backup-restore/scripts/restoreutm.sh` restores a `.zip` into the default UTM folder.

Run `--help` for options:

```bash
zsh .claude/skills/utm-backup-restore/scripts/zipnewutm.sh --help
zsh .claude/skills/utm-backup-restore/scripts/restoreutm.sh --help
```
