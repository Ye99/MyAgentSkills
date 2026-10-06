---
name: extract-data-from-windows-drive
description: Use when copying, rescuing, or archiving the user data from an old Windows system drive (a C: drive or NTFS disk mounted on Linux) while leaving out Windows, Program Files, executables, DLLs, pagefile/hiberfil and caches, when the copy must lose no data, keep the folder structure, skip empty folders, and come with a skip report another agent can audit.
---

# Extract Data from a Windows Drive

Copy everything a person made or kept off a Windows system drive. Leave out the OS, installed programs, executables and caches. Then prove nothing else was lost.

**Core principle:** exclude only what you have looked at. Every skipped byte is listed with a reason. "No data loss" is checked against the source, not trusted from rsync.

## Rules

- **Treat the source as read-only.** Mount it `-o ro` when you can. Never write, touch or delete there. Write scratch files and reports outside it.
- **Look before you exclude.** Never exclude a folder you have not inspected. User data hides in system-looking places.
- **Ask about executables inside data folders** (project build outputs, vendor driver DLLs, installers). Explain what they are, and that vendor DLLs cannot be rebuilt from source. Record the user's decision in the report.

## Workflow

`scripts/` is relative to this skill directory. SRC is the mounted drive root, DST is the target folder.

1. **Survey** (read-only):
   ```bash
   du -sh --apparent-size SRC/* SRC/'$Recycle.Bin' | sort -h
   du -sh --apparent-size SRC/Users/*/AppData/*/* | sort -h | tail -30
   find SRC -type l -not -path 'SRC/Windows/*' -printf '%p -> %l\n'
   find SRC/Windows SRC/ProgramData SRC/Program* -type f -size +100k \
     \( -iname '*.pst' -o -iname '*.doc*' -o -iname '*.xls*' -o -iname '*.jpg' -o -iname '*.mp4' \)
   ```
   Make an extension histogram of each data folder so you know which `.exe`/`.dll` files the type rules will drop.
2. **Build the filter.** Copy `scripts/windows-data.filter` next to your scratch files. Add root-anchored excludes only for folders you inspected (see the table below). It deliberately does not exclude `/Windows.old/Users/`.
3. **Dry run** and read the totals:
   ```bash
   cd SRC && rsync -rtn --no-links -m --filter='merge FILTER' --stats -h ./ DST/
   ```
4. **Copy** (run in the background; ~55 MB/s from a USB HDD):
   ```bash
   cd SRC && rsync -rt --no-links -m --filter='merge FILTER' --stats -h --log-file=LOG ./ DST/ > OUT 2>&1
   ```
5. **Prune empty folders left by junctions.** `-m` keeps a folder whose only entry was a junction skipped by `--no-links`, such as `Users/*/Documents` holding only `My Music`. This only removes empty directories:
   ```bash
   find DST -mindepth 1 -type d -empty -delete
   ```
6. **Verify content**, then **inventory** (exit 0 = PASS):
   ```bash
   cd SRC && rsync -rcn --no-links -m -i --filter='merge FILTER' --exclude='/_COPY_REPORT*' \
     --exclude='/agentreviewguide.md' --delete ./ DST/ | grep -v '^skipping non-regular' > CHECKSUM
   python3 scripts/verify_copy.py SRC DST --filter FILTER --checksum-output CHECKSUM \
     --rsync-output OUT --notes NOTES.md
   ```
   `verify_copy.py` re-applies the filter itself. It checks every kept file's size and mtime, the exact count and bytes, no extra files, no empty directories and no symlinks. It writes `_COPY_REPORT.md` and `_COPY_REPORT_skipped_files.tsv` (every skipped file with its size and reason) into DST. NOTES.md holds the explanations and the user's decisions.
7. **Hunt skipped user data.** Search the TSV for documents and media outside system and cache trees. Expect only vendor files:
   ```bash
   awk -F'\t' 'NR>1 && tolower($1) ~ /\.(docx?|xlsx?|pptx?|pdf|txt|jpe?g|png|mts|mp4|mov|mp3|pst|zip|rar)$/' DST/_COPY_REPORT_skipped_files.tsv \
     | grep -vE '^(Windows|Program Files( \(x86\))?|ProgramData|MSOCache)/|Temporary Internet Files|INetCache|/AppData/Local/Temp/|Sample (Music|Pictures|Videos|Media)'
   ```
8. **Hand-off for review** (when asked). Put `agentreviewguide.md` in DST with these sections: the user's request and binding decisions; source and DST paths (source read-only); the files in DST; the rsync command and why each exclusion exists; checks already run with their expected outputs; a checklist of commands to re-run; rules (no writes to the source, ask before restoring anything). Copy the filter, the rsync output and `verify_copy.py` into DST as `_COPY_REPORT_*` so it is self-contained.

## Where user data hides (keep)

| Place | What |
|---|---|
| `Users/*/AppData/Local/Microsoft/Outlook/`, `Documents/Outlook Files/` (localized names too) | Outlook `.pst`/`.ost` mailboxes |
| `Users/*/Documents/Tencent Files/`, `WeChat Files/` | QQ / WeChat chat history and received files |
| `Users/*/AppData/Roaming/*` | App profiles: Firefox (`Mozilla`), Thunderbird, IM, input-method dictionaries |
| `Users/*/AppData/Local/Google/Chrome/User Data/` minus caches | Bookmarks, history |
| `Users/Public/` (minus `Sample *`) | Shared documents, media |
| `Windows.old/Users/` | The previous install's profiles |

## Machine-specific excludes (add after inspecting)

| Looks like | Usually |
|---|---|
| Random-hex root folder holding `Windows*-KB*.cab` | Extracted Windows update |
| `/AMD/`, `/ATI/`, `/NVIDIA/`, `/Intel/`, `/Drivers/` | Driver installers |
| `/_SMSTaskSequence/`, corporate `/Resources/` | SCCM / IT deployment leftovers |
| Security-suite sandbox or rescue folders (`/360SANDBOX/` …) | Sandboxed copies, boot images |
| Player cache with opaque chunks (`*.cgd`, `meta.cache`) | Streaming cache, not playable |

## Common mistakes

| Mistake | Fix |
|---|---|
| Rule `/Program Files/` assumed to cover `(x86)` | List both; grep `Program Files( \(x86\))?` |
| Excluding all `AppData` | Exclude only caches/temp; mailboxes and chat history live there |
| Following junctions (`-a` keeps links, `-L` duplicates) | `--no-links`; every Vista+ junction points back inside the tree or to `C:/ProgramData` |
| Unanchored `Windows/` drops a user folder named `Windows` | Anchor system trees with a leading `/` |
| Case-sensitive `*.exe` misses `SETUP.EXE` | Bracket patterns `*.[Ee][Xx][Ee]` (shipped filter) |
| Report counts itself as extra data | Report files are prefixed `_COPY_REPORT`; `--ignore` adds more |
| "error" in the rsync log treated as failure | Filenames like `error.htm` match; trust exit code + `rsync:` lines |
| GB vs GiB mismatch confuses reviewers | Report shows exact bytes; rsync `-h` is decimal |
| `cp` hangs on an "overwrite?" prompt in a background job | `command cp -f` (shells often alias `cp -i`) |
