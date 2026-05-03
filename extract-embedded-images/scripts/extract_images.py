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
    r"^\[(?P<ref>[^\]\r\n]+)\]:[ \t]*<data:image/(?P<ext>[a-zA-Z0-9+]+);base64,(?P<data>[^>\r\n]+)>[ \t]*(?:\r?\n|$)",
    re.MULTILINE,
)

# Matches reference-style image usages:
#   full:      ![alt][ref]
#   collapsed: ![ref][]
#   shortcut:  ![ref]
IMAGE_USE_RE = re.compile(
    r"!\[(?P<full_alt>[^\]\n]*)\]\[(?P<full_ref>[^\]\n]+)\]"
    r"|!\[(?!\[)(?P<collapsed_label>[^\]\n]+)\]\[\]"
    r"|!\[(?!\[)(?P<shortcut_label>[^\]\n]+)\](?![\[(])"
)
FENCE_OPEN_RE = re.compile(r" {0,3}(?P<fence>`{3,}|~{3,})")

EXT_NORMALIZE = {"jpeg": "jpg", "svg+xml": "svg"}
GENERIC_ALT_TEXTS = {"image"}
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
WIKILINK_UNSAFE_RE = re.compile(r"[\]\[|#\r\n]")


def normalize_ext(ext: str) -> str:
    ext = ext.lower()
    return EXT_NORMALIZE.get(ext, ext)


def is_safe_ref(ref: str) -> bool:
    return bool(SAFE_REF_RE.fullmatch(ref)) and ".." not in ref


def canonical_ref(ref: str) -> str:
    return ref.strip().casefold()


def is_meaningful_alt(alt: str) -> bool:
    stripped = alt.strip()
    return bool(stripped) and stripped.casefold() not in GENERIC_ALT_TEXTS


def is_safe_wikilink_part(value: str) -> bool:
    return not WIKILINK_UNSAFE_RE.search(value)


def image_use_ref(match: re.Match) -> str:
    return match.group("full_ref") or match.group("collapsed_label") or match.group("shortcut_label")


def image_use_alt(match: re.Match) -> str:
    if match.group("full_ref") is not None:
        return match.group("full_alt")
    return match.group("collapsed_label") or match.group("shortcut_label")


def default_file_mode() -> int:
    # This temporary umask read is safe here because the script is single-threaded.
    current_umask = os.umask(0)
    os.umask(current_umask)
    return 0o666 & ~current_umask


def fenced_code_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    start = 0
    pos = 0

    for line in text.splitlines(keepends=True):
        line_text = line.rstrip("\r\n")
        if not in_fence:
            m = FENCE_OPEN_RE.match(line_text)
            if m:
                fence = m.group("fence")
                in_fence = True
                fence_char = fence[0]
                fence_len = len(fence)
                start = pos
        else:
            stripped = line_text.lstrip(" ")
            indent = len(line_text) - len(stripped)
            if indent <= 3:
                close_re = re.compile(rf"{re.escape(fence_char)}{{{fence_len},}}[ \t]*$")
                if close_re.fullmatch(stripped):
                    in_fence = False
                    spans.append((start, pos + len(line)))
        pos += len(line)

    if in_fence:
        spans.append((start, len(text)))
    return spans


def inline_code_spans(text: str, excluded_spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    segments: list[tuple[int, int]] = []
    last = 0
    for start, end in excluded_spans:
        if last < start:
            segments.append((last, start))
        last = end
    if last < len(text):
        segments.append((last, len(text)))

    for segment_start, segment_end in segments:
        pos = segment_start
        while pos < segment_end:
            if text[pos] != "`":
                pos += 1
                continue

            run_end = pos + 1
            while run_end < segment_end and text[run_end] == "`":
                run_end += 1
            run_len = run_end - pos
            close = text.find("`" * run_len, run_end, segment_end)
            if close == -1:
                pos = run_end
                continue
            spans.append((pos, close + run_len))
            pos = close + run_len
    return spans


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    sorted_spans = sorted(spans)
    merged = [sorted_spans[0]]
    for start, end in sorted_spans[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def markdown_code_spans(text: str) -> list[tuple[int, int]]:
    fenced = fenced_code_spans(text)
    return merge_spans(fenced + inline_code_spans(text, fenced))


def overlaps_spans(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def matches_outside_spans(pattern: re.Pattern, text: str, spans: list[tuple[int, int]]) -> list[re.Match]:
    return [m for m in pattern.finditer(text) if not overlaps_spans(m.start(), m.end(), spans)]


def sub_outside_spans(pattern: re.Pattern, repl, text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return pattern.sub(repl, text)

    pieces: list[str] = []
    last = 0
    for start, end in spans:
        pieces.append(pattern.sub(repl, text[last:start]))
        pieces.append(text[start:end])
        last = end
    pieces.append(pattern.sub(repl, text[last:]))
    return "".join(pieces)


def write_text_atomic(path: Path, text: str, mode: int) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(text)
            temp_path = Path(tmp.name)
        temp_path.chmod(mode)
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def rollback_assets(asset_backups: dict[Path, Path | None]) -> set[Path]:
    failed_backups: set[Path] = set()
    for path, backup in asset_backups.items():
        try:
            if backup is None:
                path.unlink(missing_ok=True)
            elif backup.exists():
                backup.replace(path)
        except Exception as e:
            if backup is not None:
                failed_backups.add(backup)
            print(f"ERROR: failed to roll back {path}: {e}", file=sys.stderr)
    return failed_backups


def cleanup_backups(asset_backups: dict[Path, Path | None], keep: set[Path] | None = None) -> None:
    keep = keep or set()
    for backup in asset_backups.values():
        if backup is not None and backup not in keep:
            backup.unlink(missing_ok=True)


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

    note_mode = note.stat().st_mode & 0o777
    try:
        text = note.read_bytes().decode("utf-8")
    except UnicodeDecodeError as e:
        print(f"error: {note} is not valid UTF-8: {e}", file=sys.stderr)
        return 1
    base = note.stem
    assets_dir = (args.assets_dir or note.parent / f"{base}.assets").resolve()
    assets_rel = os.path.relpath(assets_dir, note.parent).replace(os.sep, "/")
    if ".." in assets_rel.split("/"):
        print(
            f"error: --assets-dir must stay inside the note directory for Obsidian wikilinks; got outside the note directory: {assets_dir}",
            file=sys.stderr,
        )
        return 1

    code_spans = markdown_code_spans(text)
    defs = matches_outside_spans(REF_DEF_RE, text, code_spans)
    if not defs:
        print("no embedded base64 image definitions found")
        return 0

    print(f"found {len(defs)} embedded image(s); assets dir: {assets_dir}")

    ref_to_path: dict[str, str] = {}
    ref_labels: dict[str, str] = {}
    planned: list[tuple[str, Path, bytes, int]] = []
    seen_refs: dict[str, str] = {}
    seen_paths: dict[Path, str] = {}
    errors = False
    for m in defs:
        ref = m.group("ref").strip()
        ref_key = canonical_ref(ref)
        if ref_key in seen_refs:
            print(f"  ! [{ref}] duplicate embedded image definition", file=sys.stderr)
            errors = True
            continue
        seen_refs[ref_key] = ref
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
        link = out_name if assets_rel == "." else f"{assets_rel}/{out_name}"
        if not is_safe_wikilink_part(link):
            print(f"  ! [{ref}] link target cannot be represented safely as an Obsidian wikilink: {link}", file=sys.stderr)
            errors = True
            continue
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
        mode = out_path.stat().st_mode & 0o777 if out_path.exists() else default_file_mode()
        seen_paths[out_path] = ref
        ref_to_path[ref_key] = link
        ref_labels[ref_key] = ref
        planned.append((ref, out_path, decoded, mode))

    inline_uses = matches_outside_spans(IMAGE_USE_RE, text, code_spans)
    for m in inline_uses:
        ref_key = canonical_ref(image_use_ref(m))
        if ref_key not in ref_to_path:
            continue
        alt = image_use_alt(m)
        if is_meaningful_alt(alt) and not is_safe_wikilink_part(alt):
            print(f"  ! [{image_use_ref(m).strip()}] alt text cannot be represented safely as an Obsidian wikilink alias: {alt}", file=sys.stderr)
            errors = True

    if errors:
        print("aborting before writing assets or note", file=sys.stderr)
        return 1

    referenced_refs = {canonical_ref(image_use_ref(m)) for m in inline_uses}
    unreferenced = sorted(ref_labels[ref] for ref in ref_to_path if ref not in referenced_refs)
    if unreferenced:
        refs = ", ".join(unreferenced)
        print(f"warning: unreferenced embedded definition(s) have no inline image reference and will still be extracted: {refs}", file=sys.stderr)

    if not args.dry_run:
        assets_dir.mkdir(parents=True, exist_ok=True)

    temp_paths: set[Path] = set()
    asset_backups: dict[Path, Path | None] = {}
    wrote_messages: list[str] = []
    written = 0
    try:
        if args.dry_run:
            for _ref, out_path, decoded, _mode in planned:
                print(f"  - would write {out_path} ({len(decoded)} bytes)")
        else:
            temp_plans: list[tuple[Path, Path, int]] = []
            for _ref, out_path, decoded, mode in planned:
                if out_path.exists():
                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        dir=assets_dir,
                        prefix=f".{out_path.name}.backup.",
                        suffix=".tmp",
                        delete=False,
                    ) as backup:
                        backup_path = Path(backup.name)
                    backup_path.unlink()
                    out_path.replace(backup_path)
                    asset_backups[out_path] = backup_path
                else:
                    asset_backups[out_path] = None
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=assets_dir,
                    prefix=f".{out_path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as tmp:
                    tmp.write(decoded)
                    tmp_path = Path(tmp.name)
                temp_paths.add(tmp_path)
                tmp_path.chmod(mode)
                temp_plans.append((out_path, tmp_path, len(decoded)))

            for out_path, tmp_path, size in temp_plans:
                tmp_path.replace(out_path)
                temp_paths.discard(tmp_path)
                wrote_messages.append(f"  - wrote {out_path} ({size} bytes)")
                written += 1
    except Exception as e:
        failed_backups = rollback_assets(asset_backups)
        cleanup_backups(asset_backups, failed_backups)
        print(f"ERROR: asset write failed; rolled back extracted assets: {e}", file=sys.stderr)
        return 1
    finally:
        for tmp_path in temp_paths:
            tmp_path.unlink(missing_ok=True)

    # Rewrite inline usages
    rewritten_refs = 0

    def replace_use(match: re.Match) -> str:
        nonlocal rewritten_refs
        ref = canonical_ref(image_use_ref(match))
        alt = image_use_alt(match)
        link = ref_to_path.get(ref)
        if not link:
            return match.group(0)  # leave unknown refs alone
        rewritten_refs += 1
        if is_meaningful_alt(alt):
            # Obsidian supports an alias after a pipe
            return f"![[{link}|{alt.strip()}]]"
        return f"![[{link}]]"

    new_text = sub_outside_spans(IMAGE_USE_RE, replace_use, text, code_spans)
    print(f"rewrote {rewritten_refs} inline image reference(s)")

    if not args.keep_defs:
        # Remove the base64 reference definitions and any blank lines they leave behind
        updated_code_spans = markdown_code_spans(new_text)
        new_text = sub_outside_spans(REF_DEF_RE, "", new_text, updated_code_spans)
        # Collapse trailing whitespace-only lines down to a single trailing newline
        new_text = re.sub(r"\n{3,}\Z", "\n\n", new_text)

    if args.dry_run:
        print("dry run; no changes written to note")
        return 0

    # Verification: every assets file referenced exists and is non-empty
    missing = []
    for _ref, p, _decoded, _mode in planned:
        if not p.is_file() or p.stat().st_size == 0:
            missing.append(str(p))
    if missing:
        print("ERROR: expected asset files missing or empty:", file=sys.stderr)
        for p in missing:
            print(f"  - {p}", file=sys.stderr)
        print("aborting before overwriting note", file=sys.stderr)
        failed_backups = rollback_assets(asset_backups)
        cleanup_backups(asset_backups, failed_backups)
        return 1

    try:
        write_text_atomic(note, new_text, note_mode)
    except Exception:
        failed_backups = rollback_assets(asset_backups)
        cleanup_backups(asset_backups, failed_backups)
        raise
    cleanup_backups(asset_backups)
    for message in wrote_messages:
        print(message)
    print(f"updated {note}: {written} file(s) extracted, {rewritten_refs} reference(s) rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
