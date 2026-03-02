#!/usr/bin/env python3
"""
Independent verification of a deduplicated copy operation.

Performs five checks:
  1. Arithmetic consistency (scanned - skipped == copied, matches actual count)
  2. Every source file is accounted for (in target OR in a skip list)
  3. Every "kept" file from each duplicate set exists in the target
  4. SHA-256 spot-check of random target files against source originals
  5. No extra files in the target that don't originate from the source

Usage:
    python verify_dedup_copy.py SOURCE DEST --log dedup_log.json [--spot-check 10]
"""

import argparse
import hashlib
import json
import os
import random
import sys


def sha256_file(filepath: str) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def enumerate_files(directory: str) -> dict[str, str]:
    """Return {relative_path: absolute_path} for every file under directory."""
    result = {}
    for root, _dirs, files in os.walk(directory):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, directory)
            result[rel] = fpath
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Verify a deduplicated copy against its JSON log."
    )
    parser.add_argument("source", help="Original source directory")
    parser.add_argument("destination", help="Copy destination directory")
    parser.add_argument("--log", required=True, help="Path to dedup_log.json")
    parser.add_argument(
        "--spot-check",
        type=int,
        default=10,
        help="Number of random files to SHA-256 verify (default: 10)",
    )
    args = parser.parse_args()

    source = os.path.abspath(args.source)
    destination = os.path.abspath(args.destination)

    with open(args.log, "r", encoding="utf-8") as f:
        log = json.load(f)

    summary = log["summary"]
    dup_sets = log["duplicate_sets"]
    excluded_rel = set(log.get("excluded_files", []))

    # Build skip set from log
    skip_rel: set[str] = set()
    kept_rel: set[str] = set()
    for ds in dup_sets:
        kept_rel.add(ds["kept"])
        for sk in ds["skipped"]:
            skip_rel.add(sk["path"])

    print("=== Dedup Copy Verification ===\n")

    results = {}

    # ---- Check 1: Arithmetic ----
    print("[Check 1] Arithmetic consistency")
    expected_copied = summary["total_source_files_scanned"] - summary["files_skipped_as_duplicates"]
    logged_copied = summary["files_copied"]
    target_files = enumerate_files(destination)
    actual_count = len(target_files)

    arith_ok = expected_copied == logged_copied
    count_ok = actual_count == logged_copied

    print(f"  scanned - skipped = {expected_copied}, logged copied = {logged_copied} -> {'OK' if arith_ok else 'MISMATCH'}")
    print(f"  actual target files = {actual_count}, expected = {logged_copied} -> {'OK' if count_ok else 'MISMATCH'}")

    if not count_ok:
        diff = actual_count - logged_copied
        print(f"  (difference: {diff:+d} files)")

    results["arithmetic"] = "PASS" if (arith_ok and count_ok) else "FAIL"

    # ---- Check 2: Every source file accounted for ----
    print("\n[Check 2] Every source file accounted for")
    source_files = enumerate_files(source)

    # Eligible source files = all source files minus excluded ones
    eligible = {
        rel for rel in source_files
        if rel not in excluded_rel
    }

    missed = []
    for rel in eligible:
        in_target = rel in target_files
        in_skip = rel in skip_rel
        if not in_target and not in_skip:
            missed.append(rel)

    if missed:
        print(f"  MISSED {len(missed)} files:")
        for m in missed[:20]:
            print(f"    {m}")
        if len(missed) > 20:
            print(f"    ... and {len(missed) - 20} more")
    else:
        print(f"  All {len(eligible)} eligible source files accounted for.")

    results["accounting"] = "PASS" if not missed else "FAIL"

    # ---- Check 3: Every keeper exists in target ----
    print("\n[Check 3] Every keeper file exists in target")
    missing_keepers = [k for k in kept_rel if k not in target_files]

    if missing_keepers:
        print(f"  MISSING {len(missing_keepers)} keeper files:")
        for m in missing_keepers[:20]:
            print(f"    {m}")
    else:
        print(f"  All {len(kept_rel)} keepers present in target.")

    results["keepers_present"] = "PASS" if not missing_keepers else "FAIL"

    # ---- Check 4: SHA-256 spot-check ----
    print(f"\n[Check 4] SHA-256 spot-check ({args.spot_check} files)")
    target_rels = list(target_files.keys())
    sample_size = min(args.spot_check, len(target_rels))
    sample = random.sample(target_rels, sample_size)

    mismatches = []
    for rel in sample:
        src_path = os.path.join(source, rel)
        dst_path = target_files[rel]
        if not os.path.exists(src_path):
            # File might not exist at same relative path in source if it was
            # only present under a different relative path (unlikely but check)
            print(f"  SKIP (no source match): {rel}")
            continue
        src_hash = sha256_file(src_path)
        dst_hash = sha256_file(dst_path)
        status = "OK" if src_hash == dst_hash else "MISMATCH"
        if status == "MISMATCH":
            mismatches.append(rel)
        print(f"  {status}: {rel}")

    results["content_integrity"] = "PASS" if not mismatches else "FAIL"

    # ---- Check 5: No extra files in target ----
    print("\n[Check 5] No extra files in target")
    extras = [rel for rel in target_files if rel not in source_files]

    if extras:
        print(f"  {len(extras)} extra files in target:")
        for e in extras[:20]:
            print(f"    {e}")
        if len(extras) > 20:
            print(f"    ... and {len(extras) - 20} more")
    else:
        print("  No extra files.")

    results["no_extras"] = "PASS" if not extras else "FAIL"

    # ---- Final verdict ----
    all_pass = all(v == "PASS" for v in results.values())
    print("\n=== Verification Summary ===")
    for check, result in results.items():
        print(f"  {check:25s} {result}")
    print(f"\n  Final verdict: {'PASS' if all_pass else 'FAIL'}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
