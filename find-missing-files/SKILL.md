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
