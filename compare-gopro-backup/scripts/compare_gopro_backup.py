#!/usr/bin/env python3
"""Compare a GoPro card to a backup by content hash.

Reuses find-missing-files, then drops card database files the camera rewrites.
Reads both trees only. The report is the only file this script writes.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from fnmatch import fnmatch
from pathlib import Path

GOPRO_CARD_FILE_GLOBS = (
    "leinfo.sav",
    "mdb*.db",
    "mdb_h_*.bk",
    "mdb_*.log",
)

CHECKER_PATH = (
    Path(__file__).resolve().parents[2]
    / "find-missing-files"
    / "scripts"
    / "check_missing_files_between_two_folders.py"
)


def is_gopro_card_file(relative_path: str) -> bool:
    name = Path(relative_path).name.lower()
    return any(fnmatch(name, pattern) for pattern in GOPRO_CARD_FILE_GLOBS)


def partition_missing(missing: list[str]) -> tuple[list[str], list[str]]:
    footage: list[str] = []
    ignored: list[str] = []
    for relative_path in missing:
        if is_gopro_card_file(relative_path):
            ignored.append(relative_path)
        else:
            footage.append(relative_path)
    return footage, ignored


def load_checker():
    if not CHECKER_PATH.is_file():
        raise SystemExit(f"find-missing-files checker not found: {CHECKER_PATH}")
    spec = importlib.util.spec_from_file_location("check_missing_files_between_two_folders", CHECKER_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load find-missing-files checker: {CHECKER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def assert_report_outside_trees(source: Path, destination: Path, output: Path) -> None:
    report = output.resolve()
    for tree in (source.resolve(), destination.resolve()):
        if report == tree or report.is_relative_to(tree):
            raise SystemExit(f"report path must be outside both trees: {output}")


def render_report(source: Path, footage: list[str], ignored: list[str], checker) -> str:
    skip_note = ", ".join(checker.DEFAULT_SKIP_EXTENSIONS).lower()
    roots = "; ".join(checker.DEFAULT_SRC_SKIP_ROOT_SUBDIRS) or "nothing"
    header = (
        f".(relative to {source}, skipping {roots}, metadata, {skip_note}; "
        "GoPro card files ignored: leinfo.sav, mdb*.db, mdb_h_*.bk, mdb_*.log)"
    )
    lines = [header]
    if footage:
        lines.extend(checker.build_tree(footage))
    else:
        lines.append("|-- No missing footage (everything matched)")
    if ignored:
        lines.append("")
        lines.append("Ignored GoPro card files (the camera may rewrite these; not a backup miss):")
        lines.extend(checker.build_tree(ignored))
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find GoPro source files whose footage is missing from a backup. "
        "Card database files the camera rewrites are reported separately.",
    )
    parser.add_argument("source", help="GoPro card or other tree that must be fully backed up")
    parser.add_argument("destination", help="Backup tree to check against")
    parser.add_argument("--output", "-o", required=True, help="Report path outside both trees")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--workers", type=int, default=0, help="Hash workers (default: CPU count)")
    parser.add_argument("--chunk-size", type=int, default=1024 * 1024)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    source = Path(args.source).expanduser()
    destination = Path(args.destination).expanduser()
    output = Path(args.output).expanduser()
    if not source.is_dir():
        raise SystemExit(f"Source directory not found: {source}")
    if not destination.is_dir():
        raise SystemExit(f"Destination directory not found: {destination}")
    assert_report_outside_trees(source, destination, output)

    checker = load_checker()
    workers = args.workers or (os.cpu_count() or 1)
    skip_extensions = checker.normalized_extensions(checker.DEFAULT_SKIP_EXTENSIONS)
    src_skip = checker.DEFAULT_SRC_SKIP_ROOT_SUBDIRS
    checker.log("Building destination index...", args.verbose)
    dest_index = checker.build_dest_index(destination, (), skip_extensions, args.verbose)
    checker.log("Hashing destination files...", args.verbose)
    dest_hashes = checker.build_dest_hash_sets(dest_index, args.chunk_size, workers, args.verbose)
    checker.log("Comparing source files...", args.verbose)
    missing = checker.find_missing_files(
        source,
        dest_hashes,
        src_skip,
        skip_extensions,
        args.chunk_size,
        workers,
        args.verbose,
    )
    footage, ignored = partition_missing(missing)
    output.write_text(render_report(source, footage, ignored, checker))
    print(
        f"Footage missing: {len(footage)}. "
        f"Ignored GoPro card files: {len(ignored)}. "
        f"Wrote {output}"
    )


if __name__ == "__main__":
    main()
