---
name: dedup-copy
description: Use when copying files from a source directory to a destination while eliminating content-identical duplicates detected by jdupes. Covers the full workflow from duplicate detection through scored keeper selection, structured copy, JSON logging, and independent verification.
---

# Deduplicated Copy

## Overview

Copy files from a source directory to a destination, skipping content-identical duplicates found by `jdupes`. From each duplicate set, the file with the most readable/succinct name is kept. A JSON log records every duplicate set and a separate verification pass confirms no files were missed.

## When to Use

- Consolidating photos/videos from multiple sources that may overlap
- Migrating a media library while removing redundant copies
- Preparing files for processing where duplicates waste time/space

Do NOT use for:
- Just listing duplicates without copying (use `jdupes` directly)
- Comparing two already-separate directories (use `find-missing-files` skill instead)

## Prerequisites

- `jdupes` installed (`apt install jdupes` / `brew install jdupes`)
- Python 3.8+

## Workflow

```
1. jdupes -r -o name <source>    → raw duplicate sets
2. dedup_and_copy.py             → score, copy, log
3. verify_dedup_copy.py          → independent verification
```

## Tool Location

- `scripts/dedup_and_copy.py` — main copy script
- `scripts/verify_dedup_copy.py` — independent verification script

## Usage

### Step 1: Run jdupes

```bash
jdupes -r -o name /path/to/source > /tmp/jdupes_raw_output.txt
```

### Step 2: Deduplicated copy

```bash
python scripts/dedup_and_copy.py \
    /path/to/source \
    /path/to/destination \
    --jdupes-output /tmp/jdupes_raw_output.txt \
    --log /path/to/dedup_log.json \
    --exclude-ext .bak .tmp \
    --exclude-name jdupes_raw_output.txt dedup_log.json
```

### Step 3: Verify

```bash
python scripts/verify_dedup_copy.py \
    /path/to/source \
    /path/to/destination \
    --log /path/to/dedup_log.json \
    --spot-check 10
```

## Key Options

### dedup_and_copy.py

| Flag | Default | Description |
|---|---|---|
| `source` | *(required)* | Source directory |
| `destination` | *(required)* | Target directory |
| `--jdupes-output` | *(required)* | Path to jdupes raw output file |
| `--log` | `dedup_log.json` | Path for the JSON duplicate log |
| `--exclude-ext` | *(none)* | File extensions to skip (repeatable) |
| `--exclude-name` | *(none)* | Exact basenames to skip (repeatable) |
| `--dry-run` | off | Print what would be copied without copying |

### verify_dedup_copy.py

| Flag | Default | Description |
|---|---|---|
| `source` | *(required)* | Source directory |
| `destination` | *(required)* | Target directory |
| `--log` | *(required)* | Path to the dedup_log.json |
| `--spot-check` | `10` | Number of random files to SHA-256 verify |

## Duplicate Selection (Keeper Scoring)

From each duplicate set the file with the **lowest penalty score** is kept:

| Criterion | Penalty |
|---|---|
| Copy markers in name (`(1)`, `- Copy`, `_copy`) | +100 |
| Edit markers (`edited`, `modified`, `backup`) | +50 |
| Hidden/dot directories in path | +50 per segment |
| Filename length | +1 per character |
| Path depth | +5 per directory level |
| Total path length | +0.1 per character |

Ties broken alphabetically for determinism.

## JSON Log Format

```json
{
  "duplicate_sets": [
    {
      "set_id": 1,
      "kept": "relative/path/to/kept_file.jpg",
      "kept_score": 12.5,
      "skipped": [
        { "path": "relative/path/to/dup.jpg", "score": 45.2 }
      ]
    }
  ],
  "summary": {
    "total_source_files_scanned": 28663,
    "excluded_files_count": 6,
    "duplicate_sets_found": 322,
    "files_skipped_as_duplicates": 322,
    "files_copied": 28341,
    "copy_errors": 0
  }
}
```

## Verification Report

The verification script performs five independent checks:

| Check | Description |
|---|---|
| Arithmetic | `scanned - skipped == copied` and matches actual target count |
| Accounting | Every source file is either in target or in a skip list |
| Keepers present | Every "kept" file from the log exists in the target |
| Content integrity | SHA-256 spot-check of N random target files against source |
| No extras | Target contains no files absent from source |

## Common Mistakes

| Mistake | Fix |
|---|---|
| Forgetting to exclude script artifacts from the copy | Use `--exclude-name` to list artifact filenames |
| Running verification before copy finishes | Always run sequentially |
| Source contains symlinks | `jdupes` follows symlinks by default; use `-L` to skip them if needed |
