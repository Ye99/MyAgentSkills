#!/usr/bin/env python3
"""Extract embedded base64 images from a markdown note into a sibling .assets folder.

Inverse of convert-external-images. Takes a markdown file containing reference-style
base64 image definitions like:

    [image1]: <data:image/png;base64,iVBORw0KGgo...>

and produces:

  - Files at <NoteBase>.assets/<refname>.<ext>
  - Inline ![alt][ref] usages rewritten to Obsidian wiki-link ![[<NoteBase>.assets/<refname>.<ext>]]
  - Reference definitions removed from the markdown

Usage:
    python extract_images.py <note.md> [--dry-run] [--keep-defs] [--force]
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import sys
import tempfile
from pathlib import Path

# Matches a reference def: [refname]: <data:image/png;base64,DATA>
# DATA may span lines if the file has wrapped lines, but standard output from
# convert-external-images keeps it on a single line. Be permissive about whitespace
# inside the angle brackets and require closing '>'.
REF_DEF_RE = re.compile(
    r"^\[(?P<ref>[^\]]+)\]:\s*<data:image/(?P<ext>[a-zA-Z0-9+]+);base64,(?P<data>[^>]+)>\s*$",
    re.MULTILINE,
)

# Matches an inline usage: ![alt][refname]
INLINE_USE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\[(?P<ref>[^\]]+)\]")

EXT_NORMALIZE = {"jpeg": "jpg", "svg+xml": "svg"}
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def normalize_ext(ext: str) -> str:
    ext = ext.lower()
    return EXT_NORMALIZE.get(ext, ext)


def is_safe_ref(ref: str) -> bool:
    return bool(SAFE_REF_RE.fullmatch(ref)) and ".." not in ref


def write_text_atomic(path: Path, text: str) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(text)
            temp_path = Path(tmp.name)
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("note", type=Path, help="Markdown file to process")
    ap.add_argument("--dry-run", action="store_true", help="Report what would change without writing")
    ap.add_argument("--keep-defs", action="store_true", help="Keep base64 reference definitions in the note")
    ap.add_argument("--force", action="store_true", help="Overwrite existing extracted asset files")
    ap.add_argument("--assets-dir", type=Path, default=None,
                    help="Override assets directory (default: <NoteBase>.assets next to note)")
    args = ap.parse_args()

    note: Path = args.note.resolve()
    if not note.is_file():
        print(f"error: {note} is not a file", file=sys.stderr)
        return 2

    text = note.read_text(encoding="utf-8")
    base = note.stem
    assets_dir = (args.assets_dir or note.parent / f"{base}.assets").resolve()
    assets_rel = os.path.relpath(assets_dir, note.parent).replace(os.sep, "/")

    defs = list(REF_DEF_RE.finditer(text))
    if not defs:
        print("no embedded base64 image definitions found")
        return 0

    print(f"found {len(defs)} embedded image(s); assets dir: {assets_dir}")

    ref_to_path: dict[str, str] = {}
    planned: list[tuple[str, Path, bytes]] = []
    seen_paths: dict[Path, str] = {}
    errors = False
    for m in defs:
        ref = m.group("ref").strip()
        if ref in ref_to_path:
            print(f"  ! [{ref}] duplicate embedded image definition", file=sys.stderr)
            errors = True
            continue
        if not is_safe_ref(ref):
            print(f"  ! [{ref}] unsafe reference name; use only letters, numbers, dots, dashes, and underscores", file=sys.stderr)
            errors = True
            continue
        ext = normalize_ext(m.group("ext"))
        data = re.sub(r"\s+", "", m.group("data"))
        out_name = f"{ref}.{ext}"
        out_path = assets_dir / out_name
        try:
            out_path.relative_to(assets_dir)
        except ValueError:
            print(f"  ! [{ref}] unsafe output path escapes assets directory: {out_path}", file=sys.stderr)
            errors = True
            continue
        if out_path in seen_paths:
            print(f"  ! [{ref}] output filename collision with [{seen_paths[out_path]}]: {out_path}", file=sys.stderr)
            errors = True
            continue
        seen_paths[out_path] = ref
        link = out_name if assets_rel == "." else f"{assets_rel}/{out_name}"
        ref_to_path[ref] = link
        try:
            decoded = base64.b64decode(data, validate=True)
        except Exception as e:
            print(f"  ! [{ref}] base64 decode failed: {e}", file=sys.stderr)
            errors = True
            continue
        if not decoded:
            print(f"  ! [{ref}] decoded asset is empty", file=sys.stderr)
            errors = True
            continue
        if out_path.exists() and not args.force:
            print(f"  ! [{ref}] asset already exists; use --force to overwrite: {out_path}", file=sys.stderr)
            errors = True
            continue
        planned.append((ref, out_path, decoded))

    if errors:
        print("aborting before writing assets or note", file=sys.stderr)
        return 1

    referenced_refs = {m.group("ref").strip() for m in INLINE_USE_RE.finditer(text)}
    unreferenced = sorted(ref for ref in ref_to_path if ref not in referenced_refs)
    if unreferenced:
        refs = ", ".join(unreferenced)
        print(f"warning: unreferenced embedded definition(s) have no inline image reference and will still be extracted: {refs}", file=sys.stderr)

    if not args.dry_run:
        assets_dir.mkdir(parents=True, exist_ok=True)

    temp_paths: list[Path] = []
    asset_backups: dict[Path, bytes | None] = {}
    written = 0
    try:
        if args.dry_run:
            for _ref, out_path, decoded in planned:
                print(f"  - would write {out_path} ({len(decoded)} bytes)")
        else:
            temp_plans: list[tuple[Path, Path, int]] = []
            for _ref, out_path, decoded in planned:
                asset_backups[out_path] = out_path.read_bytes() if out_path.exists() else None
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=assets_dir,
                    prefix=f".{out_path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as tmp:
                    tmp.write(decoded)
                    tmp_path = Path(tmp.name)
                temp_paths.append(tmp_path)
                temp_plans.append((out_path, tmp_path, len(decoded)))

            for out_path, tmp_path, size in temp_plans:
                tmp_path.replace(out_path)
                temp_paths.remove(tmp_path)
                print(f"  - wrote {out_path} ({size} bytes)")
                written += 1
    finally:
        for tmp_path in temp_paths:
            tmp_path.unlink(missing_ok=True)

    # Rewrite inline usages
    rewritten_refs = 0

    def replace_use(match: re.Match) -> str:
        nonlocal rewritten_refs
        ref = match.group("ref").strip()
        alt = match.group("alt")
        link = ref_to_path.get(ref)
        if not link:
            return match.group(0)  # leave unknown refs alone
        rewritten_refs += 1
        if alt and alt not in ("", "image"):
            # Obsidian supports an alias after a pipe
            return f"![[{link}|{alt}]]"
        return f"![[{link}]]"

    new_text = INLINE_USE_RE.sub(replace_use, text)
    print(f"rewrote {rewritten_refs} inline image reference(s)")

    if not args.keep_defs:
        # Remove the base64 reference definitions and any blank lines they leave behind
        new_text = REF_DEF_RE.sub("", new_text)
        # Collapse trailing whitespace-only lines down to a single trailing newline
        new_text = re.sub(r"\n{3,}\Z", "\n\n", new_text)

    if args.dry_run:
        print("dry run; no changes written to note")
        return 0

    # Verification: every assets file referenced exists and is non-empty
    missing = []
    for _ref, p, _decoded in planned:
        if not p.is_file() or p.stat().st_size == 0:
            missing.append(str(p))
    if missing:
        print("ERROR: expected asset files missing or empty:", file=sys.stderr)
        for p in missing:
            print(f"  - {p}", file=sys.stderr)
        print("aborting before overwriting note", file=sys.stderr)
        return 1

    try:
        write_text_atomic(note, new_text)
    except Exception:
        for p, previous in asset_backups.items():
            if previous is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(previous)
        raise
    print(f"updated {note}: {written} file(s) extracted, {rewritten_refs} reference(s) rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
