#!/usr/bin/env python3
"""
Deduplicated copy: parse jdupes output, pick the best file from each
duplicate set (preferring shorter/cleaner filenames), copy unique files
to a target directory preserving directory structure, and write a JSON
log of all duplicate sets.

Usage:
    python dedup_and_copy.py SOURCE DEST --jdupes-output FILE [options]
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


def score_filepath(filepath: str, source_dir: str) -> float:
    """Score a filepath -- lower is better (more readable/succinct)."""
    rel = os.path.relpath(filepath, source_dir)
    name = os.path.basename(filepath)
    penalty = 0.0

    # Heavy penalty for copy/duplicate markers in filename
    if re.search(r"\(\d+\)", name):
        penalty += 100
    if re.search(r"[-_ ]copy", name, re.IGNORECASE):
        penalty += 100
    if re.search(r"duplicate", name, re.IGNORECASE):
        penalty += 100
    # Penalize "edited", "modified" etc.
    if re.search(r"[-_ ](edited|modified|backup)", name, re.IGNORECASE):
        penalty += 50

    # Prefer shorter filenames
    penalty += len(name) * 1.0

    # Prefer fewer directory levels (shorter path depth)
    penalty += rel.count(os.sep) * 5.0

    # Prefer shorter total relative path (mild tiebreaker)
    penalty += len(rel) * 0.1

    # Penalize hidden/system dirs like .Trashes, .fseventsd
    for part in Path(rel).parts:
        if part.startswith("."):
            penalty += 50

    return penalty


def parse_jdupes_output(filepath: str):
    """Parse jdupes output into list of duplicate sets (list of lists)."""
    duplicate_sets = []
    current_set = []

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line == "":
                if current_set:
                    duplicate_sets.append(current_set)
                    current_set = []
            else:
                current_set.append(line)

    if current_set:
        duplicate_sets.append(current_set)

    return duplicate_sets


def should_exclude(
    filepath: str,
    exclude_names: set,
    exclude_extensions: set,
) -> bool:
    """Check if a file should be excluded from copying."""
    basename = os.path.basename(filepath)
    _, ext = os.path.splitext(basename)

    if basename in exclude_names:
        return True
    if ext.lower() in exclude_extensions:
        return True
    return False


def sha256_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_inside(path: str, parent: str) -> bool:
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def build_args():
    parser = argparse.ArgumentParser(
        description="Copy files from SOURCE to DEST, skipping jdupes-detected duplicates."
    )
    parser.add_argument("source", help="Source directory")
    parser.add_argument("destination", help="Destination directory (must be empty unless --resume)")
    parser.add_argument(
        "--jdupes-output",
        required=True,
        help="Path to jdupes raw output file",
    )
    parser.add_argument(
        "--log",
        default="dedup_log.json",
        help="Path for the JSON duplicate log (default: dedup_log.json)",
    )
    parser.add_argument(
        "--exclude-ext",
        nargs="*",
        default=[],
        help="File extensions to skip, e.g. .bak .tmp",
    )
    parser.add_argument(
        "--exclude-name",
        nargs="*",
        default=[],
        help="Exact basenames to skip, e.g. Thumbs.db .DS_Store",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be copied without actually copying",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue into a non-empty DEST left by an interrupted run: files already there must be "
             "identical to the source; a different file is reported, never overwritten",
    )
    return parser.parse_args()


def main():
    args = build_args()
    source = os.path.abspath(args.source)
    destination = os.path.abspath(args.destination)
    exclude_names = set(args.exclude_name)
    exclude_extensions = {
        e if e.startswith(".") else f".{e}" for e in args.exclude_ext
    }

    if is_inside(destination, source):
        sys.exit(f"refusing: DEST {destination} is inside SOURCE {source}")
    if is_inside(source, destination):
        sys.exit(f"refusing: SOURCE {source} is inside DEST {destination}")
    if os.path.isdir(destination) and os.listdir(destination) and not args.resume:
        sys.exit(f"refusing: DEST {destination} is not empty; pass --resume to continue an interrupted run")

    print("=== Dedup and Copy ===")
    print(f"Source:      {source}")
    print(f"Destination: {destination}")
    if args.dry_run:
        print("*** DRY RUN -- no files will be copied ***")

    # --- Step 1: build full file list ---
    print("\n[1/5] Building full file list...")
    all_files = []
    excluded_files = []
    for root, _dirs, files in os.walk(source):
        for fname in files:
            fpath = os.path.join(root, fname)
            if should_exclude(fpath, exclude_names, exclude_extensions):
                excluded_files.append(fpath)
            else:
                all_files.append(fpath)
    eligible = set(all_files)
    print(f"  Total files:                 {len(all_files)}")
    print(f"  Excluded (ext/name filters): {len(excluded_files)}")

    # --- Step 2: parse jdupes output ---
    print("\n[2/5] Parsing jdupes output...")
    duplicate_sets = parse_jdupes_output(args.jdupes_output)
    print(f"  Found {len(duplicate_sets)} duplicate sets")

    # --- Step 3: score, select keepers, and re-check every skip against its keeper ---
    # jdupes ran earlier: a file edited since then is no longer a duplicate and must be copied.
    print("\n[3/5] Selecting keepers and re-hashing duplicates...")
    skip_set: set[str] = set()
    log_sets = []
    changed_since_jdupes = []
    for dset in duplicate_sets:
        members = [m for m in (os.path.abspath(f) for f in dset) if m in eligible]
        if len(members) < 2:
            continue
        scored = sorted((score_filepath(f, source), f) for f in members)
        keeper = scored[0][1]
        keeper_hash = sha256_file(keeper)
        skipped = []
        for sc, f in scored[1:]:
            if sha256_file(f) == keeper_hash:
                skip_set.add(f)
                skipped.append({"path": os.path.relpath(f, source), "score": sc})
            else:
                changed_since_jdupes.append(os.path.relpath(f, source))
        if skipped:
            log_sets.append(
                {
                    "set_id": len(log_sets) + 1,
                    "kept": os.path.relpath(keeper, source),
                    "kept_score": scored[0][0],
                    "kept_sha256": keeper_hash,
                    "skipped": skipped,
                }
            )
    for rel in changed_since_jdupes:
        print(f"  no longer a duplicate, will be copied: {rel}")
    print(f"  Files to skip as duplicates: {len(skip_set)}")

    copy_list = [f for f in all_files if f not in skip_set]
    print(f"  Files to copy:               {len(copy_list)}")

    # --- Step 4: copy ---
    print(f"\n[4/5] Copying {len(copy_list)} files...")
    copied_count = 0
    errors = []

    for fpath in copy_list:
        rel = os.path.relpath(fpath, source)
        dest = os.path.join(destination, rel)
        dest_dir = os.path.dirname(dest)

        if args.dry_run:
            copied_count += 1
            continue

        try:
            if os.path.lexists(dest):
                if not os.path.islink(dest) and os.path.isfile(dest) and sha256_file(dest) == sha256_file(fpath):
                    copied_count += 1  # finished on an earlier, interrupted run
                    continue
                raise FileExistsError("a different file is already at the destination; not overwritten")
            os.makedirs(dest_dir, exist_ok=True)
            # A fresh, exclusively created temp name, so no existing file is overwritten.
            fd, part = tempfile.mkstemp(dir=dest_dir, prefix=f".{os.path.basename(dest)}.", suffix=".dedup-copy-part")
            os.close(fd)
            try:
                shutil.copy2(fpath, part)
                os.replace(part, dest)
            except BaseException:
                if os.path.lexists(part):
                    os.unlink(part)
                raise
            copied_count += 1
            if copied_count % 500 == 0:
                print(f"  Copied {copied_count}/{len(copy_list)}...")
        except Exception as e:
            errors.append({"file": rel, "error": str(e)})
            print(f"  ERROR copying {rel}: {e}", file=sys.stderr)

    print(f"  Done. {'Would copy' if args.dry_run else 'Copied'} {copied_count} files. Errors: {len(errors)}")

    # --- Step 5: write JSON log ---
    print(f"\n[5/5] Writing {args.log}...")
    log_data = {
        "duplicate_sets": log_sets,
        "summary": {
            "total_source_files_scanned": len(all_files),
            "excluded_files_count": len(excluded_files),
            "duplicate_sets_found": len(log_sets),
            "files_skipped_as_duplicates": len(skip_set),
            "files_copied": copied_count,
            "copy_errors": len(errors),
            "expected_target_files": len(copy_list),
        },
        "no_longer_duplicates": changed_since_jdupes,
        "excluded_files": [os.path.relpath(f, source) for f in excluded_files],
        "copy_errors": errors,
    }

    with open(args.log, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)

    print("\n=== Summary ===")
    print(f"  Source files (after exclusions): {len(all_files)}")
    print(f"  Duplicate sets found:            {len(log_sets)}")
    print(f"  Files skipped (duplicates):      {len(skip_set)}")
    print(f"  Files copied to target:          {copied_count}")
    print(f"  Copy errors:                     {len(errors)}")
    print(f"  Log written to:                  {args.log}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
