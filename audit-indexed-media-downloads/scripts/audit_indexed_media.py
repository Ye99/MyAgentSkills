#!/usr/bin/env python3
"""Audit and safely normalize indexed MP4/SRT download sets."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


INDEX_RE = re.compile(r"^(?P<index>\d+)\.\s+(?P<title>.+)$")
COPY_RE = re.compile(r"\s+\((?P<number>\d+)\)$")
LANG_RE = re.compile(r"\.(?P<language>[A-Za-z]{2,3}(?:[-_ ][A-Za-z0-9]{2,4})?)$")
VIDEO_SUFFIXES = {".mp4"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".m4b"}
MEDIA_SUFFIXES = VIDEO_SUFFIXES | AUDIO_SUFFIXES
SUBTITLE_SUFFIXES = {".srt"}
PARTIAL_SUFFIXES = {".part", ".crdownload", ".download", ".tmp"}
DASHES = str.maketrans({"–": "-", "—": "-", "−": "-"})


@dataclass(frozen=True)
class MediaFile:
    path: Path
    kind: str
    index: str | None
    index_number: int | None
    title: str
    title_key: str
    language: str | None
    copy_number: int | None
    digest: str


@dataclass(frozen=True)
class Action:
    kind: str
    source: Path
    target: Path | None = None
    keeper: Path | None = None


def is_media_kind(kind: str) -> bool:
    """True for a lesson asset (video or audio), false for a subtitle.

    Courses ship as video or as audiobook; both pair with subtitles the same
    way, so every pairing rule keys off this rather than the mp4 extension.
    """
    return f".{kind}" in MEDIA_SUFFIXES


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).translate(DASHES).casefold()
    normalized = re.sub(r"\s*[-]\s*", " - ", normalized)
    return " ".join(normalized.split())


def strip_copy_suffix(stem: str) -> tuple[str, int | None]:
    match = COPY_RE.search(stem)
    if not match:
        return stem, None
    return stem[: match.start()], int(match.group("number"))


def strip_copy_suffix_if_sibling(
    stem: str, rebuild: "Callable[[str], str]", siblings: set[str]
) -> tuple[str, int | None]:
    """Strip a trailing "(n)" only when the un-suffixed filename exists in the set.

    Download managers create a copy by appending "(n)" to a name that is already
    on disk, so the base sibling is what proves the suffix is a copy marker.
    Without that proof the parentheses belong to the title itself - a release
    year ("AlexNet (2012)"), a lesson number ("Lesson 3.2 - FAQ (3)") or any
    other trailing parenthetical.
    """
    match = COPY_RE.search(stem)
    if not match:
        return stem, None
    base = stem[: match.start()]
    if rebuild(base).casefold() not in siblings:
        return stem, None
    return base, int(match.group("number"))


def parse_media(path: Path, siblings: set[str]) -> MediaFile:
    kind = path.suffix.lower().lstrip(".")
    extension = path.suffix
    stem = path.name[: -len(path.suffix)]

    stem, copy_number = strip_copy_suffix_if_sibling(
        stem, lambda base: f"{base}{extension}", siblings
    )
    language = None
    if kind == "srt":
        language_match = LANG_RE.search(stem)
        language_text = ""
        if language_match:
            language = language_match.group("language").lower()
            language_text = language_match.group(0)
            stem = stem[: language_match.start()]
        stem, inner_copy = strip_copy_suffix_if_sibling(
            stem, lambda base: f"{base}{language_text}{extension}", siblings
        )
        copy_number = copy_number if copy_number is not None else inner_copy

    index_match = INDEX_RE.match(stem)
    if index_match:
        index = index_match.group("index")
        title = index_match.group("title")
    else:
        index = None
        title = stem

    return MediaFile(
        path=path,
        kind=kind,
        index=index,
        index_number=int(index) if index is not None else None,
        title=title,
        title_key=normalize_title(title),
        language=language,
        copy_number=copy_number,
        digest=sha256(path),
    )


def inventory(directory: Path) -> list[MediaFile]:
    paths = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in (MEDIA_SUFFIXES | SUBTITLE_SUFFIXES)
        ),
        key=lambda path: path.name.casefold(),
    )
    siblings = {path.name.casefold() for path in paths}
    return [parse_media(path, siblings) for path in paths]


def keeper_rank(item: MediaFile) -> tuple[int, int, int, str]:
    return (
        0 if item.index is not None else 1,
        0 if item.copy_number is None else 1,
        len(item.path.name),
        item.path.name.casefold(),
    )


def plan_changes(files: list[MediaFile]) -> tuple[list[Action], list[str]]:
    actions: dict[Path, Action] = {}
    conflicts: list[str] = []

    by_content: dict[tuple[str, str], list[MediaFile]] = {}
    for item in files:
        by_content.setdefault((item.kind, item.digest), []).append(item)

    for group in by_content.values():
        if len(group) < 2:
            continue
        title_keys = {item.title_key for item in group}
        if len(title_keys) != 1:
            names = ", ".join(sorted(item.path.name for item in group))
            conflicts.append(f"identical content has different titles: {names}")
            continue
        keeper = min(group, key=keeper_rank)
        for item in group:
            if item.path != keeper.path:
                actions[item.path] = Action("delete", item.path, keeper=keeper.path)

    indexed_videos: dict[str, list[MediaFile]] = {}
    for item in files:
        if (
            is_media_kind(item.kind)
            and item.index is not None
            and item.copy_number is None
            and item.path not in actions
        ):
            indexed_videos.setdefault(item.title_key, []).append(item)

    path_map = {item.path: item for item in files}
    reserved_targets: set[Path] = set()
    for subtitle in files:
        if subtitle.kind != "srt" or subtitle.index is not None or subtitle.path in actions:
            continue
        candidates = indexed_videos.get(subtitle.title_key, [])
        if len(candidates) != 1:
            reason = "no indexed video" if not candidates else "ambiguous title"
            conflicts.append(f"{reason} for subtitle: {subtitle.path.name}")
            continue

        video = candidates[0]
        language = f".{subtitle.language}" if subtitle.language else ""
        target = subtitle.path.with_name(
            f"{video.index}. {video.title}{language}.srt"
        )
        existing = path_map.get(target)
        if existing:
            if existing.digest == subtitle.digest:
                actions[subtitle.path] = Action(
                    "delete", subtitle.path, keeper=existing.path
                )
            else:
                conflicts.append(
                    f"destination differs for {subtitle.path.name}: {target.name}"
                )
        elif target in reserved_targets:
            conflicts.append(f"multiple subtitles target: {target.name}")
        else:
            reserved_targets.add(target)
            actions[subtitle.path] = Action("rename", subtitle.path, target=target)

    for item in files:
        if item.copy_number is not None and item.path not in actions:
            conflicts.append(
                f"copy-suffixed file is not a hash duplicate: {item.path.name}"
            )

    return sorted(actions.values(), key=lambda action: action.source.name.casefold()), sorted(
        set(conflicts)
    )


def projected_files(files: list[MediaFile], actions: list[Action]) -> list[MediaFile]:
    by_source = {action.source: action for action in actions}
    survivors: list[Path] = []
    for item in files:
        action = by_source.get(item.path)
        if action is None:
            survivors.append(item.path)
        elif action.kind == "rename" and action.target is not None:
            survivors.append(action.target)
    siblings = {path.name.casefold() for path in survivors}
    projected: list[MediaFile] = []
    for item in files:
        action = by_source.get(item.path)
        if action is None:
            projected.append(item)
        elif action.kind == "rename" and action.target is not None:
            projected.append(
                parse_media_projection(action.target, item.digest, siblings)
            )
    return projected


def parse_media_projection(path: Path, digest: str, siblings: set[str]) -> MediaFile:
    kind = path.suffix.lower().lstrip(".")
    extension = path.suffix
    stem = path.name[: -len(path.suffix)]
    stem, copy_number = strip_copy_suffix_if_sibling(
        stem, lambda base: f"{base}{extension}", siblings
    )
    language = None
    if kind == "srt":
        language_match = LANG_RE.search(stem)
        language_text = ""
        if language_match:
            language = language_match.group("language").lower()
            language_text = language_match.group(0)
            stem = stem[: language_match.start()]
        stem, inner_copy = strip_copy_suffix_if_sibling(
            stem, lambda base: f"{base}{language_text}{extension}", siblings
        )
        copy_number = copy_number if copy_number is not None else inner_copy
    match = INDEX_RE.match(stem)
    index = match.group("index") if match else None
    title = match.group("title") if match else stem
    return MediaFile(
        path,
        kind,
        index,
        int(index) if index is not None else None,
        title,
        normalize_title(title),
        language,
        copy_number,
        digest,
    )


def apply_changes(actions: list[Action]) -> None:
    for action in actions:
        if action.kind == "delete":
            action.source.unlink()
    for action in actions:
        if action.kind == "rename" and action.target is not None:
            if action.target.exists():
                raise FileExistsError(f"refusing to overwrite {action.target}")
            action.source.rename(action.target)


def find_partial_artifacts(directory: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in PARTIAL_SUFFIXES
        ),
        key=lambda path: path.name.casefold(),
    )


def pair_report(files: Iterable[MediaFile]) -> tuple[list[str], list[str], list[str], int]:
    videos = {
        (item.index_number, item.title_key): item
        for item in files
        if is_media_kind(item.kind) and item.index_number is not None
    }
    subtitles = {
        (item.index_number, item.title_key): item
        for item in files
        if item.kind == "srt" and item.index_number is not None
    }
    video_keys = set(videos)
    subtitle_keys = set(subtitles)
    missing_srt = sorted(
        f"{videos[(index, title)].index}. {videos[(index, title)].title}"
        for index, title in video_keys - subtitle_keys
        if index is not None
    )
    missing_mp4 = sorted(
        f"{subtitles[(index, title)].index}. {subtitles[(index, title)].title}"
        for index, title in subtitle_keys - video_keys
        if index is not None
    )

    numbers = sorted({index for index, _ in video_keys if index is not None})
    width = max(
        (len(item.index) for item in files if item.index is not None),
        default=3,
    )
    missing_indexes: list[str] = []
    if numbers:
        present = set(numbers)
        missing_indexes = [
            f"{number:0{width}d}"
            for number in range(numbers[0], numbers[-1] + 1)
            if number not in present
        ]
    return missing_srt, missing_mp4, missing_indexes, len(video_keys & subtitle_keys)


def run_media_checks(files: Iterable[MediaFile], mode: str) -> list[str]:
    if mode == "none":
        return []
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return ["ffprobe is unavailable"]
    errors: list[str] = []
    videos = [item for item in files if is_media_kind(item.kind)]
    for item in videos:
        probe = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "default=noprint_wrappers=1",
                str(item.path),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if probe.returncode or "duration=" not in probe.stdout:
            errors.append(f"ffprobe failed: {item.path.name}")
            continue
        if "codec_type=audio" not in probe.stdout:
            errors.append(f"missing audio stream: {item.path.name}")

    if mode == "scan":
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            errors.append("ffmpeg is unavailable")
            return errors
        for item in videos:
            scan = subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(item.path),
                    "-map",
                    "0",
                    "-c",
                    "copy",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                check=False,
            )
            if scan.returncode:
                errors.append(f"full packet scan failed: {item.path.name}")
    return errors


def print_report(
    files: list[MediaFile], actions: list[Action], partials: list[Path], check_errors: list[str]
) -> bool:
    for action in actions:
        if action.kind == "delete":
            print(
                f"DELETE duplicate: {action.source.name} "
                f"(same SHA-256 as {action.keeper.name if action.keeper else 'keeper'})"
            )
        else:
            print(f"RENAME subtitle: {action.source.name} -> {action.target.name}")
    print(f"ACTIONS: {len(actions)}")

    missing_srt, missing_mp4, missing_indexes, complete = pair_report(files)

    # A title that ships no subtitles at all is not a per-item gap: audiobooks
    # and many video editions are published without captions. Listing every
    # asset as "missing SRT" buries real findings, so say it once instead.
    # Partial coverage still reports each missing item.
    has_subtitles = any(item.kind == "srt" for item in files)
    if not has_subtitles:
        missing_srt = []
    print(f"SUBTITLES: {'present' if has_subtitles else 'none present'}")
    print(f"MISSING_INDEXES: {' '.join(missing_indexes) if missing_indexes else 'none'}")
    print(f"MP4_WITHOUT_SRT: {'; '.join(missing_srt) if missing_srt else 'none'}")
    print(f"SRT_WITHOUT_MP4: {'; '.join(missing_mp4) if missing_mp4 else 'none'}")
    print(
        f"PAIRS: {complete} complete, {len(missing_srt)} missing SRT, "
        f"{len(missing_mp4)} missing MP4"
    )
    print(
        "PARTIAL_DOWNLOADS: "
        + (", ".join(path.name for path in partials) if partials else "none")
    )
    print("MEDIA_CHECK_ERRORS: " + ("; ".join(check_errors) if check_errors else "none"))

    unindexed = [item.path.name for item in files if item.index is None]
    numbered = [item.path.name for item in files if item.copy_number is not None]
    print("UNINDEXED_MEDIA: " + (", ".join(unindexed) if unindexed else "none"))
    print("NUMBERED_COPIES: " + (", ".join(numbered) if numbered else "none"))
    return bool(
        missing_srt
        or missing_mp4
        or missing_indexes
        or partials
        or check_errors
        or unindexed
        or numbered
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit indexed MP4/SRT downloads; dry-run unless --apply is given."
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply collision-free renames and permanently delete hash-proven duplicates",
    )
    parser.add_argument(
        "--media-check",
        choices=("none", "probe", "scan"),
        default="probe",
        help="MP4 validation level (default: probe)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    directory = args.directory.expanduser().resolve()
    if not directory.is_dir():
        print(f"ERROR: not a directory: {directory}", file=sys.stderr)
        return 2

    files = inventory(directory)
    actions, conflicts = plan_changes(files)
    for conflict in conflicts:
        print(f"CONFLICT: {conflict}")
    if conflicts:
        print("APPLY_BLOCKED: resolve every conflict before changing files")
        return 2

    if args.apply:
        try:
            apply_changes(actions)
        except OSError as error:
            print(f"ERROR: apply failed: {error}", file=sys.stderr)
            return 2
        files = inventory(directory)
        report_actions = actions
    else:
        files = projected_files(files, actions)
        report_actions = actions

    partials = find_partial_artifacts(directory)
    check_errors = run_media_checks(files, args.media_check)
    incomplete = print_report(files, report_actions, partials, check_errors)
    return 1 if incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
