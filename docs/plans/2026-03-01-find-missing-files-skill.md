# find-missing-files Skill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move the `check_missing_files` tool from the configs repo into MyAgentSkills as a packaged skill with a `SKILL.md` and co-located scripts.

**Architecture:** Create `find-missing-files/` directory with a `SKILL.md` describing when and how to invoke the tool, and a `scripts/` subfolder containing the Python script, tests, and tox config. Remove the source directory from the configs repo. Update MyAgentSkills README. PII-check all files before pushing.

**Tech Stack:** Python 3.12, pytest, tox, git, gh CLI

---

### Task 1: Create directory structure in MyAgentSkills

**Files:**
- Create: `find-missing-files/scripts/` (directory)

**Step 1: Create the directory**

```bash
mkdir -p ~/p/MyAgentSkills/find-missing-files/scripts
```

**Step 2: Verify structure exists**

```bash
ls ~/p/MyAgentSkills/find-missing-files/
```
Expected: `scripts/`

---

### Task 2: Copy Python files from configs repo to MyAgentSkills

**Files:**
- Create: `find-missing-files/scripts/check_missing_files_between_two_folders.py`
- Create: `find-missing-files/scripts/test_check_missing_files_between_two_folders.py`
- Create: `find-missing-files/scripts/tox.ini`

**Step 1: Copy the files**

```bash
cp ~/p/configs/check_missing_files/check_missing_files_between_two_folders.py \
   ~/p/MyAgentSkills/find-missing-files/scripts/

cp ~/p/configs/check_missing_files/test_check_missing_files_between_two_folders.py \
   ~/p/MyAgentSkills/find-missing-files/scripts/

cp ~/p/configs/check_missing_files/tox.ini \
   ~/p/MyAgentSkills/find-missing-files/scripts/
```

**Step 2: Verify files are present**

```bash
ls ~/p/MyAgentSkills/find-missing-files/scripts/
```
Expected: `check_missing_files_between_two_folders.py  test_check_missing_files_between_two_folders.py  tox.ini`

---

### Task 3: PII check all copied files

**Step 1: Scan for personal info — names, emails, home paths, hostnames**

```bash
grep -rn \
  -e '/home/ye' \
  -e 'ye@' \
  -e 'mr\.ye' \
  -e 'Ye Zhang' \
  -e '192\.168\.' \
  -e 'hddpool\|ssdpool\|5600G\|Z420\|HP800' \
  ~/p/MyAgentSkills/find-missing-files/scripts/
```
Expected: no matches. If any found, remove or generalise them before continuing.

**Step 2: Check the example usage embedded in the script**

```bash
grep -n 'Example\|example\|/media\|/home\|Downloads' \
  ~/p/MyAgentSkills/find-missing-files/scripts/check_missing_files_between_two_folders.py
```
Review any matches — replace personal paths with generic placeholders such as `/path/to/source` and `/path/to/destination`.

---

### Task 4: Write SKILL.md

**Files:**
- Create: `find-missing-files/SKILL.md`

**Step 1: Write the file**

```markdown
---
name: find-missing-files
description: Use when you need to find files present in a source directory that are missing from a destination directory, compared by content hash (not filename). Handles large directories efficiently with multi-core hashing and a size-bucket optimisation.
---

# Find Missing Files

## Overview

Compares two directories by SHA-256 content hash to identify files in the source that have no content match in the destination — even if they have been renamed or reorganised.

The tool is optimised for large directories: it indexes the destination by file size first, and only hashes files where a size match is possible, using all available CPU cores.

## When to Use

Use this skill when:
- Verifying a backup or sync is complete (content-based, not name-based)
- Finding files that exist in one location but not another after a reorganisation
- Checking whether photos, videos, or documents were fully transferred

Do NOT use for:
- Finding duplicate files (different purpose)
- Comparing file metadata or timestamps

## Tool Location

`scripts/check_missing_files_between_two_folders.py` (relative to this skill directory)

## Usage

```bash
python scripts/check_missing_files_between_two_folders.py <source> <destination> [options]
```

### Required arguments

| Argument | Description |
|---|---|
| `source` | Directory whose files you want to check (the "smaller" or "subset" side) |
| `destination` | Reference directory to check against (the "larger" or "superset" side) |

### Key options

| Flag | Default | Description |
|---|---|---|
| `--output`, `-o` | `missing_files_tree.txt` | Output file path for the tree report |
| `--skip-extension` | `.THM`, `.LRV` | Extensions to ignore (repeatable) |
| `--src-skip-root-subdir` | `Backedup` | Top-level subdirs of source to skip (repeatable) |
| `--dest-skip-root-subdir` | _(none)_ | Top-level subdirs of destination to skip (repeatable) |
| `--verbose`, `-v` | off | Print progress to stderr |
| `--workers` | CPU count | Number of parallel hashing workers |

### Example

```bash
python scripts/check_missing_files_between_two_folders.py \
    /path/to/source \
    /path/to/destination \
    --skip-extension .THM --skip-extension .LRV \
    --output ~/missing_files.txt \
    --verbose
```

## Output

A tree-formatted text file listing all source files with no content match in the destination:

```
.(relative to /path/to/source, skipping Backedup, metadata, .thm, .lrv)
|-- folder_a
|   |-- photo1.jpg
|   `-- video1.mp4
`-- folder_b
    `-- document.pdf
```

If nothing is missing, the file contains: `|-- No missing files (everything matched)`

## Running Tests

```bash
cd scripts
pip install tox
tox
```
```

**Step 2: Verify the file was written**

```bash
head -5 ~/p/MyAgentSkills/find-missing-files/SKILL.md
```
Expected: frontmatter block starting with `---`

---

### Task 5: Update MyAgentSkills README.md

**Files:**
- Modify: `~/p/MyAgentSkills/README.md`

**Step 1: Add the new skill entry to the Available Skills list, in alphabetical order**

Insert the following line in the `## Available Skills` bullet list (between `format-markdownfile-code-block` and `jetson-ollama-upgrade`):

```markdown
- **find-missing-files** ([`find-missing-files/SKILL.md`](find-missing-files/SKILL.md)): Find files present in a source directory that are missing from a destination directory, compared by content hash rather than filename.
```

**Step 2: Verify placement**

```bash
grep -n 'find-missing-files\|format-markdown\|jetson' ~/p/MyAgentSkills/README.md
```
Expected: `find-missing-files` appears between `format-markdownfile-code-block` and `jetson-ollama-upgrade`.

---

### Task 6: Commit and push MyAgentSkills

**Step 1: Stage files**

```bash
cd ~/p/MyAgentSkills
git add find-missing-files/ docs/plans/2026-03-01-find-missing-files-skill.md README.md
git status
```
Expected: new files listed under "Changes to be committed", no untracked personal files.

**Step 2: Pull with rebase (linear history policy)**

```bash
git pull --rebase
```

**Step 3: Commit with co-author trailers from template**

```bash
git commit -m "$(cat <<'EOF'
Add find-missing-files skill with co-located scripts

Moves check_missing_files_between_two_folders.py from the configs
repo into a self-contained skill directory. Includes SKILL.md
describing usage, scripts/ subfolder with the tool and tests, and
README entry.

Co-authored-by: OpenCode <265697+opencode@users.noreply.github.com>
Co-authored-by: Claude <81847+claude@users.noreply.github.com>
Co-authored-by: Codex <208188539+codex-cli@users.noreply.github.com>
EOF
)"
```

**Step 4: Verify trailers parsed correctly**

```bash
git show -s --format=%B HEAD | git interpret-trailers --parse
```
Expected: three `Co-authored-by` entries.

**Step 5: Push**

```bash
git push
```

---

### Task 7: Remove check_missing_files from configs repo and commit

**Files:**
- Delete: `~/p/configs/check_missing_files/` (entire directory)

**Step 1: Remove the directory**

```bash
cd ~/p/configs
git rm -r check_missing_files/
```

**Step 2: Verify removal**

```bash
git status
```
Expected: `deleted: check_missing_files/check_missing_files_between_two_folders.py` etc. listed.

**Step 3: Pull with rebase**

```bash
git pull --rebase
```

**Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
Move check_missing_files tool to MyAgentSkills repo

The tool is now maintained as a skill in Ye99/MyAgentSkills under
find-missing-files/scripts/.
EOF
)"
```

**Step 5: Push**

```bash
git push
```
