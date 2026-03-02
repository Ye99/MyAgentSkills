#!/usr/bin/env python3
"""Compare two folders and list files whose contents exist only in the source.

The script walks both source and destination trees (skipping macOS metadata,
Backedup, and temporary media files), indexes the destination by size, and
then hashes-on-demand when a potential size match exists. Only files with no
size+hash match in the destination are considered missing, and the final list
is written as a tree. The destination-index phase is CPU-heavy, but the source
comparison stage is largely I/O-bound because hashing requires reading the
entire contents of each candidate file.

Example
-------
    ./check_missing_files_between_two_folders.py \\
        /path/to/source \\
        /path/to/destination \\
        --src-skip-root-subdir Backedup \\
        --skip-extension .THM --skip-extension .LRV \\
        --output ~/missing_files.txt --verbose
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import textwrap
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Sequence, Set, Tuple

MAC_METADATA_DIRS = {
    '.Spotlight-V100', '.fseventsd', '.Trashes', '.TemporaryItems',
    '.DocumentRevisions-V100'
}
MAC_METADATA_FILES = {'.DS_Store', '.metadata_never_index', 'Icon\r', '.VolumeIcon.icns'}
MAC_METADATA_PREFIXES = ('._',)
DEFAULT_SKIP_EXTENSIONS = ('.THM', '.LRV')
DEFAULT_SRC_SKIP_ROOT_SUBDIRS = ('Backedup',)
EXAMPLE_USAGE = textwrap.dedent(
    """\
    Example:
        ./check_missing_files_between_two_folders.py \\
            /path/to/source \\
            /path/to/destination \\
            --src-skip-root-subdir Backedup \\
            --skip-extension .THM --skip-extension .LRV \\
            --output ~/missing_files.txt --verbose
    """
)


@dataclass
class HashJob:
    rel: str | None
    path: Path
    size: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Find source files whose contents are missing on the destination tree.',
        epilog=EXAMPLE_USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('source', help='Path to the source folder (e.g. /path/to/source)')
    parser.add_argument('destination', help='Path to the destination folder (e.g. /path/to/destination)')
    parser.add_argument('--output', '-o', default='missing_files_tree.txt', help='Where to write the tree report (default: %(default)s)')
    parser.add_argument('--src-skip-root-subdir', action='append', default=list(DEFAULT_SRC_SKIP_ROOT_SUBDIRS), help='Root-level subdirectories under source to skip entirely (default: Backedup)')
    parser.add_argument('--dest-skip-root-subdir', action='append', default=[], help='Root-level subdirectories under destination to skip (default: none)')
    parser.add_argument('--skip-extension', action='append', default=list(DEFAULT_SKIP_EXTENSIONS), help='File extensions to ignore (case-insensitive, default: .THM, .LRV)')
    parser.add_argument('--chunk-size', type=int, default=1024 * 1024, help='Chunk size (bytes) used while hashing (default: 1 MiB)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Print progress details to stderr')
    parser.add_argument('--workers', type=int, default=os.cpu_count() or 1, help='Number of worker processes for hashing (default: CPU count)')
    return parser.parse_args()


def log(message: str, verbose: bool) -> None:
    if verbose:
        print(message, file=sys.stderr)


def normalized_extensions(exts: Sequence[str]) -> Tuple[str, ...]:
    return tuple(e.lower() if e.startswith('.') else f'.{e.lower()}' for e in exts)


def should_skip_dir(parts: Tuple[str, ...], src_skip_roots: Sequence[str]) -> bool:
    if parts and parts[0] in src_skip_roots:
        return True
    if any(part in MAC_METADATA_DIRS for part in parts):
        return True
    return False


def should_skip_file(rel_parts: Tuple[str, ...], skip_extensions: Tuple[str, ...]) -> bool:
    name = rel_parts[-1]
    if any(part in MAC_METADATA_DIRS for part in rel_parts[:-1]):
        return True
    if name in MAC_METADATA_FILES:
        return True
    for prefix in MAC_METADATA_PREFIXES:
        if name.startswith(prefix):
            return True
    lowered = name.lower()
    return any(lowered.endswith(ext) for ext in skip_extensions)


def iter_files(base: Path, skip_root_subdirs: Sequence[str], skip_extensions: Tuple[str, ...], verbose: bool) -> Iterator[Tuple[Path, Path]]:
    base = base.resolve()
    for dirpath, dirnames, filenames in os_walk(base):
        current = Path(dirpath)
        if current == base:
            rel_parts: Tuple[str, ...] = ()
        else:
            rel_parts = current.relative_to(base).parts
        # prune directories in-place to avoid descending into skipped trees
        pruned = []
        for dirname in list(dirnames):
            child_parts = rel_parts + (dirname,)
            if should_skip_dir(child_parts, skip_root_subdirs):
                dirnames.remove(dirname)
                pruned.append(dirname)
        if verbose and pruned:
            skipped = ', '.join(pruned)
            log(f'Skipping {skipped} under {current}', verbose)
        for filename in filenames:
            rel = Path(*rel_parts, filename) if rel_parts else Path(filename)
            rel_parts_full = rel.parts
            if should_skip_file(rel_parts_full, skip_extensions):
                continue
            yield rel, current / filename


def os_walk(base: Path):
    """Wrapper for os.walk lazily imported to keep namespace tidy."""
    import os

    yield from os.walk(base)


def hash_file(path: Path, chunk_size: int) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _hash_worker(path_str: str, chunk_size: int) -> str:
    return hash_file(Path(path_str), chunk_size)


def parallel_hash_jobs(jobs: Iterable[HashJob], chunk_size: int, workers: int) -> Iterator[Tuple[HashJob, str | None, BaseException | None]]:
    workers = max(1, workers)
    if workers == 1:
        for job in jobs:
            try:
                digest = hash_file(job.path, chunk_size)
                yield job, digest, None
            except BaseException as exc:
                yield job, None, exc
        return

    max_pending = max(workers * 4, 1)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        pending = {}
        jobs_iter = iter(jobs)
        exhausted = False

        def submit_until_full() -> None:
            nonlocal exhausted
            while len(pending) < max_pending and not exhausted:
                try:
                    job = next(jobs_iter)
                except StopIteration:
                    exhausted = True
                    break
                future = executor.submit(_hash_worker, str(job.path), chunk_size)
                pending[future] = job

        submit_until_full()
        while pending:
            done, _ = wait(set(pending), return_when=FIRST_COMPLETED)
            for future in done:
                job = pending.pop(future)
                digest: str | None = None
                error: BaseException | None = None
                try:
                    digest = future.result()
                except BaseException as exc:
                    error = exc
                yield job, digest, error
            submit_until_full()


def build_dest_index(dest: Path, skip_root_subdirs: Sequence[str], skip_extensions: Tuple[str, ...], verbose: bool):
    index: Dict[int, List[Path]] = defaultdict(list)
    total = 0
    for rel, path in iter_files(dest, skip_root_subdirs, skip_extensions, verbose):
        try:
            size = path.stat().st_size
        except (OSError, PermissionError) as exc:
            log(f'Warning: could not stat destination file {path}: {exc}', True)
            continue
        index[size].append(path)
        total += 1
    log(f'Indexed {total} destination files across {len(index)} unique sizes', verbose)
    return index


def build_dest_hash_sets(dest_index: Dict[int, List[Path]], chunk_size: int, workers: int, verbose: bool) -> Dict[int, Set[str]]:
    jobs = (
        HashJob(rel=None, path=path, size=size)
        for size, paths in dest_index.items()
        for path in paths
    )
    size_hashes: Dict[int, Set[str]] = defaultdict(set)
    hashed = 0
    for job, digest, error in parallel_hash_jobs(jobs, chunk_size, workers):
        if error or digest is None:
            log(f'Warning: could not hash destination file {job.path}: {error}', True)
            continue
        size_hashes[job.size].add(digest)
        hashed += 1
    log(f'Hashed {hashed} destination files across {len(size_hashes)} size buckets', verbose)
    return size_hashes


def find_missing_files(src: Path, dest_hash_sets: Dict[int, Set[str]], skip_root_subdirs: Sequence[str], skip_extensions: Tuple[str, ...], chunk_size: int, workers: int, verbose: bool) -> List[str]:
    missing: List[str] = []
    processed = 0
    dest_sizes = set(dest_hash_sets.keys())

    def job_iter() -> Iterator[HashJob]:
        nonlocal processed
        for rel, path in iter_files(src, skip_root_subdirs, skip_extensions, verbose):
            try:
                size = path.stat().st_size
            except (OSError, PermissionError) as exc:
                log(f'Warning: could not stat source file {path}: {exc}', True)
                continue
            processed += 1
            if size not in dest_sizes:
                missing.append(rel.as_posix())
                continue
            yield HashJob(rel=rel.as_posix(), path=path, size=size)

    for job, digest, error in parallel_hash_jobs(job_iter(), chunk_size, workers):
        if error or digest is None:
            log(f'Warning: could not hash source file {job.path}: {error}', True)
            continue
        hashes = dest_hash_sets.get(job.size)
        if not hashes or digest not in hashes:
            missing.append(job.rel or job.path.as_posix())
    log(f'Processed {processed} source files; {len(missing)} missing by content', verbose)
    return missing


def build_tree(paths: Iterable[str]) -> List[str]:
    class Node(defaultdict):
        def __init__(self):
            super().__init__(Node)

    root = Node()
    for rel in paths:
        cursor = root
        for part in rel.split('/'):
            cursor = cursor[part]

    lines: List[str] = []

    def render(node: Node, prefix: str) -> None:
        items = sorted(node.items())
        total = len(items)
        for idx, (name, child) in enumerate(items):
            connector = '`--' if idx == total - 1 else '|--'
            lines.append(f"{prefix}{connector} {name}")
            new_prefix = prefix + ('    ' if idx == total - 1 else '|   ')
            render(child, new_prefix)

    render(root, '')
    return lines


def main() -> None:
    args = parse_args()
    src = Path(args.source).expanduser()
    dest = Path(args.destination).expanduser()
    output = Path(args.output).expanduser()

    if not src.is_dir():
        raise SystemExit(f'Source directory not found: {src}')
    if not dest.is_dir():
        raise SystemExit(f'Destination directory not found: {dest}')

    skip_extensions = normalized_extensions(args.skip_extension)
    src_skip_roots = tuple(args.src_skip_root_subdir or [])
    dest_skip_roots = tuple(args.dest_skip_root_subdir or [])

    log('Building destination index...', args.verbose)
    dest_index = build_dest_index(dest, dest_skip_roots, skip_extensions, args.verbose)
    log('Hashing destination files...', args.verbose)
    dest_hash_sets = build_dest_hash_sets(dest_index, args.chunk_size, args.workers, args.verbose)
    log('Comparing source files...', args.verbose)
    missing = find_missing_files(src, dest_hash_sets, src_skip_roots, skip_extensions, args.chunk_size, args.workers, args.verbose)

    header = f'.(relative to {src}, skipping {"; ".join(src_skip_roots) or "nothing"}, metadata, {", ".join(skip_extensions)})'
    tree_lines = [header]
    if missing:
        tree_lines.extend(build_tree(missing))
    else:
        tree_lines.append('|-- No missing files (everything matched)')

    output.write_text('\n'.join(tree_lines) + '\n')
    print(f'Wrote tree with {len(missing)} missing files to {output}')


if __name__ == '__main__':
    main()
