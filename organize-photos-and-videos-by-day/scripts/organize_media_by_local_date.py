#!/usr/bin/env python3
"""Organize media into %Y/%Y_%m_%d folders using local capture time."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from zoneinfo import ZoneInfo


EXIF_CAPTURE_FIELDS = [
    ("DateTimeOriginal", "OffsetTimeOriginal"),
    ("CreateDate", "OffsetTime"),
    ("MediaCreateDate", None),
    ("TrackCreateDate", None),
]

MAC_METADATA_DIRS = {
    ".Spotlight-V100",
    ".fseventsd",
    ".Trashes",
    ".TemporaryItems",
    ".DocumentRevisions-V100",
}
MAC_METADATA_FILES = {".DS_Store", ".metadata_never_index", "Icon\r", ".VolumeIcon.icns"}
MAC_METADATA_PREFIXES = ("._",)
EXCLUDED_NON_MEDIA_EXTENSIONS = {".url", ".ini", ".bk", ".sav", ".db", ".log", ".txt"}


def signature_key(mime_type: str | None, file_type: str | None, extension: str | None) -> str:
    mime = (mime_type or "").strip().lower() or "unknown"
    ftype = (file_type or "").strip() or "unknown"
    ext = (extension or "").strip().lower() or "unknown"
    return f"{mime}|{ftype}|{ext}"


def classify_media_signature(
    mime_type: str | None,
    file_type: str | None,
    extension: str | None,
    cache: dict[str, str],
) -> tuple[bool, str, bool]:
    key = signature_key(mime_type, file_type, extension)
    mime = (mime_type or "").strip().lower()

    if mime.startswith("image/") or mime.startswith("video/"):
        return True, "mime-prefix", False

    # The all-unknowns key is too broad to trust from cache — many unrelated
    # extensionless files collapse into the same key.  Always re-triage per file.
    all_unknowns = key == "unknown|unknown|unknown"

    cached = cache.get(key)
    if cached == "media" and not all_unknowns:
        return True, "cache:media", False
    if cached == "non_media" and not all_unknowns:
        return False, "cache:non_media", False

    if not mime or mime in {"application/octet-stream", "binary/octet-stream"}:
        return True, "unknown-signature-default-media-candidate", True

    return False, "mime-non-media", False


def auto_triage_unknown_signature(source_path: Path, timeout_sec: int = 10) -> tuple[str | None, str]:
    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        return None, "ffprobe:unavailable"

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=format_name:stream=codec_type,codec_name",
        "-of",
        "json",
        str(source_path),
    ]

    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        return None, "ffprobe:timeout"
    except Exception:
        return None, "ffprobe:error"

    stderr_lower = result.stderr.lower()
    if result.returncode != 0:
        if "invalid data found when processing input" in stderr_lower:
            return "non_media", "ffprobe:invalid-data-non-media"
        return None, "ffprobe:nonzero"

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None, "ffprobe:bad-json"

    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams:
        return "non_media", "ffprobe:no-streams-non-media"

    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codec_type = str(stream.get("codec_type") or "").strip().lower()
        if codec_type in {"video", "audio"}:
            return "media", "ffprobe:auto-media"

    return "non_media", "ffprobe:non-media-streams"


def is_system_metadata_path(path: Path) -> bool:
    parts = path.parts
    if any(part in MAC_METADATA_DIRS for part in parts):
        return True
    name = path.name
    if name in MAC_METADATA_FILES:
        return True
    if any(name.startswith(prefix) for prefix in MAC_METADATA_PREFIXES):
        return True
    return False


def is_explicit_non_media_path(path: Path) -> bool:
    return path.suffix.casefold() in EXCLUDED_NON_MEDIA_EXTENSIONS


def count_media_missing_gps(records: list[dict[str, Any]]) -> int:
    count = 0
    for record in records:
        source_path = Path(str(record.get("SourceFile") or ""))
        if is_system_metadata_path(source_path):
            continue
        if is_explicit_non_media_path(source_path):
            continue
        mime = (record.get("MIMEType") or "").strip().lower()
        if not (mime.startswith("image/") or mime.startswith("video/")):
            continue
        if record.get("GPSLatitude") is None or record.get("GPSLongitude") is None:
            count += 1
    return count


def parse_exif_datetime(value: str | None, offset: str | None = None) -> datetime | None:
    if not value:
        return None

    text = value.strip()
    candidates = [
        "%Y:%m:%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S.%f",
        "%Y:%m:%d %H:%M:%S%z",
        "%Y:%m:%d %H:%M:%S.%f%z",
    ]

    parsed: datetime | None = None
    for fmt in candidates:
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        return None

    if parsed.tzinfo is None and offset:
        try:
            tz_part = datetime.strptime(offset.strip(), "%z").tzinfo
            if tz_part is not None:
                parsed = parsed.replace(tzinfo=tz_part)
        except ValueError:
            pass
    return parsed


def parse_gps_utc_datetime(date_text: str | None, time_text: str | None) -> datetime | None:
    if not date_text or not time_text:
        return None

    date_clean = date_text.strip()
    time_clean = str(time_text).strip()
    if not date_clean or not time_clean:
        return None

    if " " in time_clean:
        time_clean = time_clean.split(" ", 1)[0]
    if "/" in time_clean:
        parts = [segment.strip() for segment in time_clean.split(":")]
        if len(parts) == 3:
            try:
                values: list[float] = []
                for segment in parts:
                    numerator, denominator = segment.split("/")
                    values.append(float(numerator) / float(denominator))
                hours = int(values[0])
                minutes = int(values[1])
                seconds = values[2]
                time_clean = f"{hours:02d}:{minutes:02d}:{seconds:06.3f}".rstrip("0").rstrip(".")
            except (ValueError, ZeroDivisionError):
                return None

    for fmt in ["%Y:%m:%d %H:%M:%S", "%Y:%m:%d %H:%M:%S.%f"]:
        try:
            parsed = datetime.strptime(f"{date_clean} {time_clean}", fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _get_first_exif_datetime(record: dict[str, Any]) -> tuple[datetime | None, str | None]:
    for field, offset_field in EXIF_CAPTURE_FIELDS:
        value = record.get(field)
        offset = record.get(offset_field) if offset_field else None
        parsed = parse_exif_datetime(value, offset)
        if parsed is not None:
            return parsed, field
    return None, None


def _resolve_embedded_capture_datetime(
    record: dict[str, Any],
    timezone_lookup: Callable[[float, float], str | None],
    recording_tz: ZoneInfo | None = None,
) -> tuple[datetime, str, str | None] | None:
    lat = record.get("GPSLatitude")
    lon = record.get("GPSLongitude")
    lat_lon_available = lat is not None and lon is not None

    exif_dt, exif_source = _get_first_exif_datetime(record)
    if exif_dt is None:
        return None

    if exif_dt.tzinfo is not None:
        if lat_lon_available:
            timezone_name = timezone_lookup(float(lat), float(lon))
            if timezone_name:
                local_dt = exif_dt.astimezone(ZoneInfo(timezone_name)).replace(tzinfo=None)
                return local_dt, f"{exif_source}-offset-converted", timezone_name
        if recording_tz is not None:
            local_dt = exif_dt.astimezone(recording_tz).replace(tzinfo=None)
            return local_dt, f"{exif_source}-recording-tz-converted", str(recording_tz)
        return exif_dt.replace(tzinfo=None), f"{exif_source}-offset-kept", None

    gps_utc = parse_gps_utc_datetime(record.get("GPSDateStamp"), record.get("GPSTimeStamp"))
    if gps_utc is not None and lat_lon_available:
        timezone_name = timezone_lookup(float(lat), float(lon))
        if timezone_name:
            local_dt = gps_utc.astimezone(ZoneInfo(timezone_name)).replace(tzinfo=None)
            return local_dt, "gps-utc-converted", timezone_name
        return gps_utc.replace(tzinfo=None), "gps-utc-no-timezone", None

    return exif_dt, f"{exif_source}-naive-local", None


def _parse_sequence_name(path: Path) -> tuple[str, int] | None:
    match = re.match(r"^([A-Za-z]+)(\d+)$", path.stem)
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def build_sequence_capture_overrides(
    records: list[dict[str, Any]],
    timezone_lookup: Callable[[float, float], str | None],
    recording_tz: ZoneInfo | None = None,
    max_sequence_gap: int = 3,
) -> dict[str, tuple[datetime, str, str | None]]:
    anchors_by_group: dict[tuple[str, str, str], list[tuple[int, int, tuple[datetime, str, str | None]]]] = {}
    parsed_records: list[tuple[dict[str, Any], Path, tuple[str, int] | None]] = []

    for index, record in enumerate(records):
        source_path = Path(str(record.get("SourceFile") or ""))
        seq = _parse_sequence_name(source_path)
        parsed_records.append((record, source_path, seq))
        if seq is None:
            continue
        embedded = _resolve_embedded_capture_datetime(record, timezone_lookup, recording_tz)
        if embedded is None:
            continue
        prefix, number = seq
        group = (str(source_path.parent), prefix, source_path.suffix.casefold())
        anchors_by_group.setdefault(group, []).append((number, index, embedded))

    overrides: dict[str, tuple[datetime, str, str | None]] = {}
    for index, (record, source_path, seq) in enumerate(parsed_records):
        if seq is None:
            continue
        source_key = str(source_path)
        if source_key in overrides:
            continue
        if _resolve_embedded_capture_datetime(record, timezone_lookup, recording_tz) is not None:
            continue

        prefix, number = seq
        group = (str(source_path.parent), prefix, source_path.suffix.casefold())
        anchors = anchors_by_group.get(group, [])
        if not anchors:
            continue

        candidate = min(
            anchors,
            key=lambda item: (abs(item[0] - number), abs(item[1] - index), 0 if item[1] >= index else 1),
        )
        if abs(candidate[0] - number) > max_sequence_gap:
            continue

        anchor_dt, anchor_source, anchor_tz = candidate[2]
        overrides[source_key] = (anchor_dt, f"sequence-neighbor-{anchor_source}", anchor_tz)
    return overrides


def resolve_capture_datetime(
    record: dict[str, Any],
    creation_dt: datetime | None,
    mtime_dt: datetime,
    timezone_lookup: Callable[[float, float], str | None],
    recording_tz: ZoneInfo | None = None,
) -> tuple[datetime, str, str | None]:
    embedded = _resolve_embedded_capture_datetime(record, timezone_lookup, recording_tz)
    if embedded is not None:
        return embedded

    if recording_tz is not None:
        if creation_dt is not None:
            return creation_dt, "file-creation-time-recording-local-assumed", str(recording_tz)
        return mtime_dt, "file-mtime-recording-local-assumed", str(recording_tz)

    if creation_dt is not None:
        return creation_dt, "file-creation-time", None

    return mtime_dt, "file-mtime", None


def _files_are_identical(a: Path, b: Path, block_size: int = 1024 * 1024) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                chunk_a = fa.read(block_size)
                chunk_b = fb.read(block_size)
                if chunk_a != chunk_b:
                    return False
                if not chunk_a:
                    return True
    except OSError:
        return False


def next_collision_path(
    path: Path,
    existing_paths: set[Path],
    source_path: Path | None = None,
) -> Path | None:
    """Return the target path, or None if source already exists at the destination."""
    if path not in existing_paths and not path.exists():
        existing_paths.add(path)
        return path

    if source_path is not None and path.exists() and _files_are_identical(source_path, path):
        return None

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    idx = 1
    while True:
        candidate = parent / f"{stem}_col{idx:03d}{suffix}"
        if candidate not in existing_paths and not candidate.exists():
            existing_paths.add(candidate)
            return candidate
        if source_path is not None and candidate.exists() and _files_are_identical(source_path, candidate):
            return None
        idx += 1


def _create_timezone_lookup(cache_precision: int = 3) -> Callable[[float, float], str | None]:
    try:
        from timezonefinder import TimezoneFinder
    except ImportError as exc:
        raise RuntimeError(
            "timezonefinder is required for offline timezone polygon lookup. "
            "Install it before running this script."
        ) from exc

    finder = TimezoneFinder(in_memory=True)
    cache: dict[tuple[float, float], str | None] = {}

    def lookup(lat: float, lon: float) -> str | None:
        key = (round(lat, cache_precision), round(lon, cache_precision))
        if key in cache:
            return cache[key]
        timezone_name = finder.timezone_at(lat=lat, lng=lon)
        if timezone_name is None:
            timezone_name = finder.closest_timezone_at(lat=lat, lng=lon)
        cache[key] = timezone_name
        return timezone_name

    return lookup


def _load_signature_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in data.items():
        if value in {"media", "non_media"}:
            normalized[str(key)] = value
    return normalized


def _save_signature_cache(path: Path, cache: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


def _extract_metadata_records(source_root: Path) -> list[dict[str, Any]]:
    cmd = [
        "exiftool",
        "-r",
        "-j",
        "-n",
        "-api",
        "QuickTimeUTC=1",
        "-SourceFile",
        "-FileName",
        "-MIMEType",
        "-FileType",
        "-FileTypeExtension",
        "-DateTimeOriginal",
        "-CreateDate",
        "-MediaCreateDate",
        "-TrackCreateDate",
        "-OffsetTimeOriginal",
        "-OffsetTime",
        "-GPSDateStamp",
        "-GPSTimeStamp",
        "-GPSLatitude",
        "-GPSLongitude",
        str(source_root),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("exiftool is required but not installed") from exc
    # exit code 1 = minor errors (e.g. non-image files present); stdout still contains valid JSON
    if result.returncode not in (0, 1):
        raise RuntimeError(f"exiftool failed (exit {result.returncode}): {result.stderr.strip()}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Failed to parse exiftool JSON output") from exc
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected exiftool payload format")
    return payload


def _iter_source_files(source_root: Path):
    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames.sort()
        filenames.sort()
        for filename in filenames:
            path = Path(dirpath) / filename
            try:
                if path.is_file():
                    yield path.resolve()
            except OSError:
                continue


def merge_with_source_files(source_root: Path, metadata_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path: dict[Path, dict[str, Any]] = {}
    for record in metadata_records:
        source = record.get("SourceFile")
        if not source:
            continue
        try:
            resolved = Path(str(source)).resolve()
        except OSError:
            continue
        if not resolved.exists() or not resolved.is_file():
            continue
        by_path[resolved] = dict(record)

    merged: list[dict[str, Any]] = []
    for source_path in _iter_source_files(source_root):
        record = by_path.get(source_path)
        if record is None:
            record = {
                "SourceFile": str(source_path),
                "FileName": source_path.name,
                "MIMEType": None,
                "FileType": None,
                "FileTypeExtension": source_path.suffix.lstrip("."),
            }
        else:
            record = dict(record)
            record["SourceFile"] = str(source_path)
        merged.append(record)
    return merged


def _get_file_creation_time(path: Path) -> datetime | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    birth = getattr(stat, "st_birthtime", None)
    if birth is None:
        return None
    return datetime.fromtimestamp(birth)


def _load_find_missing_module(repo_root: Path):
    script_path = repo_root / "find-missing-files" / "scripts" / "check_missing_files_between_two_folders.py"
    if not script_path.exists():
        return None

    module_name = "find_missing_impl"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def verify_with_find_missing(
    media_source_paths: list[Path],
    destination_root: Path,
    workers: int,
    verbose: bool,
    phase_callback: Callable[[str], None] | None = None,
) -> list[str]:
    repo_root = Path(__file__).resolve().parents[2]
    module = _load_find_missing_module(repo_root)
    if module is None:
        return []

    if phase_callback is not None:
        phase_callback("verification started")

    with tempfile.TemporaryDirectory(prefix="media-verify-") as tmp_dir:
        shadow_root = Path(tmp_dir)
        rel_to_source: dict[str, str] = {}

        if phase_callback is not None:
            phase_callback("verification: preparing shadow tree")
        for index, source_path in enumerate(media_source_paths):
            rel_name = f"{index:09d}_{source_path.name}"
            shadow_path = shadow_root / rel_name
            try:
                os.symlink(source_path, shadow_path)
            except OSError:
                shutil.copy2(source_path, shadow_path)
            rel_to_source[rel_name] = str(source_path)

        skip_extensions = module.normalized_extensions(())
        if phase_callback is not None:
            phase_callback("verification: building destination index")
        dest_index = module.build_dest_index(destination_root, (), skip_extensions, verbose)
        if phase_callback is not None:
            phase_callback("verification: hashing destination files")
        dest_hash_sets = module.build_dest_hash_sets(dest_index, 1024 * 1024, workers, verbose)
        if phase_callback is not None:
            phase_callback("verification: comparing source files")
        missing_rel = module.find_missing_files(
            shadow_root,
            dest_hash_sets,
            (),
            skip_extensions,
            1024 * 1024,
            workers,
            verbose,
        )
        if phase_callback is not None:
            phase_callback("verification complete")
        return [rel_to_source.get(rel, rel) for rel in missing_rel]


def build_report(
    source_root: Path,
    destination_root: Path,
    apply_mode: bool,
    copied: list[dict[str, str]],
    failed: list[dict[str, str]],
    non_media: list[str],
    missed_media: list[str],
    unknown_signatures: dict[str, dict[str, str]],
    report_path: Path,
) -> None:
    skipped = [e for e in copied if e.get("status") == "skipped-already-exists"]
    active = [e for e in copied if e.get("status") != "skipped-already-exists"]

    report = {
        "source_root": str(source_root),
        "destination_root": str(destination_root),
        "mode": "apply" if apply_mode else "dry-run",
        "summary": {
            "media_copied_count": len(active),
            "media_skipped_identical_at_destination_count": len(skipped),
            "media_copy_failed_count": len(failed),
            "missed_media_count": len(missed_media),
            "non_media_not_copied_count": len(non_media),
            "unknown_signature_count": len(unknown_signatures),
        },
        "media_copied": active,
        "media_skipped_identical_at_destination": skipped,
        "media_copy_failed": failed,
        "missed_media_files": missed_media,
        "non_media_not_copied": non_media,
        "unknown_signatures_needing_ai_lookup": list(unknown_signatures.values()),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n")


def _emit_progress(done: int, total: int, *, file=sys.stderr) -> None:
    pct = int(done * 100 / total) if total else 100
    print(f"\r[progress] {done}/{total} ({pct}%)", end="", flush=True, file=file)


def _emit_phase(message: str, *, file=sys.stderr) -> None:
    print(f"[phase] {message}", flush=True, file=file)


def _emit_done(report_path: Path, *, file=sys.stdout) -> None:
    print(f"[done] report written: {report_path}", flush=True, file=file)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Organize media by local capture date into %Y/%Y_%m_%d folders"
    )
    parser.add_argument("source_root", help="Source folder tree root")
    parser.add_argument("destination_root", help="Destination root")
    parser.add_argument(
        "--signature-cache",
        default=str(Path(__file__).resolve().parent / "signature_type_cache.json"),
        help="Path to signature classification cache JSON",
    )
    parser.add_argument(
        "--report",
        default="organize_media_report.json",
        help="JSON report output path",
    )
    parser.add_argument(
        "--recording-timezone",
        default=None,
        help="IANA timezone where media was recorded (e.g. Asia/Shanghai, America/Los_Angeles). "
             "Required when any media file lacks GPS coordinates.",
    )
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1, help="Hash workers for verification")
    parser.add_argument("--verbose", action="store_true", help="Verbose verification logs")
    parser.add_argument("--apply", action="store_true", help="Apply copy actions")
    args = parser.parse_args()

    source_root = Path(args.source_root).expanduser().resolve()
    destination_root = Path(args.destination_root).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    cache_path = Path(args.signature_cache).expanduser().resolve()

    recording_tz: ZoneInfo | None = None
    if args.recording_timezone is not None:
        try:
            recording_tz = ZoneInfo(args.recording_timezone)
        except (KeyError, ValueError):
            print(f"Invalid recording timezone: {args.recording_timezone!r}. Use an IANA timezone like 'Asia/Shanghai'.", file=sys.stderr)
            return 2

    if not source_root.is_dir():
        print(f"Source root not found: {source_root}", file=sys.stderr)
        return 2
    destination_root.mkdir(parents=True, exist_ok=True)

    signature_cache = _load_signature_cache(cache_path)
    timezone_lookup = _create_timezone_lookup()
    metadata_records = _extract_metadata_records(source_root)
    records = merge_with_source_files(source_root, metadata_records)
    sequence_capture_overrides = build_sequence_capture_overrides(
        records=records,
        timezone_lookup=timezone_lookup,
        recording_tz=recording_tz,
    )

    missing_gps_count = count_media_missing_gps(records)
    if missing_gps_count > 0 and recording_tz is None:
        print(
            f"Error: {missing_gps_count} media file(s) lack GPS coordinates.\n"
            f"Re-run with --recording-timezone <IANA> (e.g. --recording-timezone Asia/Shanghai) "
            f"to ensure correct date assignment.",
            file=sys.stderr,
        )
        return 2

    copied: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    non_media_not_copied: list[str] = []
    unknown_signatures: dict[str, dict[str, str]] = {}
    media_sources: list[Path] = []
    existing_destinations: set[Path] = set()

    total_records = len(records)
    print(f"[progress] processing {total_records} entries...", file=sys.stderr)
    for record_idx, record in enumerate(records, 1):
        _emit_progress(record_idx, total_records)
        source_path = Path(str(record.get("SourceFile") or "")).resolve()
        if not source_path.exists() or not source_path.is_file():
            continue

        if is_system_metadata_path(source_path):
            non_media_not_copied.append(str(source_path))
            continue

        if is_explicit_non_media_path(source_path):
            non_media_not_copied.append(str(source_path))
            continue

        mime_type = record.get("MIMEType")
        file_type = record.get("FileType")
        extension = record.get("FileTypeExtension") or source_path.suffix.lstrip(".")

        is_media, reason, needs_lookup = classify_media_signature(
            mime_type=mime_type,
            file_type=file_type,
            extension=extension,
            cache=signature_cache,
        )

        key = signature_key(mime_type, file_type, extension)
        if needs_lookup:
            triaged, triage_reason = auto_triage_unknown_signature(source_path)
            if triaged in {"media", "non_media"}:
                signature_cache[key] = triaged
                is_media = triaged == "media"
                reason = triage_reason
                needs_lookup = False

        if needs_lookup and key not in unknown_signatures:
            unknown_signatures[key] = {
                "signature_key": key,
                "mime_type": str(mime_type or ""),
                "file_type": str(file_type or ""),
                "example_source_path": str(source_path),
                "note": "ffprobe auto-triage was unavailable/inconclusive; lookup once via AI/web and cache decision as media/non_media.",
            }

        if not is_media:
            non_media_not_copied.append(str(source_path))
            continue

        media_sources.append(source_path)

        creation_dt = _get_file_creation_time(source_path)
        mtime_dt = datetime.fromtimestamp(source_path.stat().st_mtime)
        sequence_override = sequence_capture_overrides.get(str(source_path))
        if sequence_override is not None:
            capture_dt, timestamp_source, timezone_name = sequence_override
        else:
            capture_dt, timestamp_source, timezone_name = resolve_capture_datetime(
                record=record,
                creation_dt=creation_dt,
                mtime_dt=mtime_dt,
                timezone_lookup=timezone_lookup,
                recording_tz=recording_tz,
            )

        year_folder = capture_dt.strftime("%Y")
        day_folder = capture_dt.strftime("%Y_%m_%d")
        target_dir = destination_root / year_folder / day_folder
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = next_collision_path(
            target_dir / source_path.name,
            existing_destinations,
            source_path=source_path if args.apply else None,
        )

        if target_path is None:
            copied.append(
                {
                    "source_path": str(source_path),
                    "destination_path": str(target_dir / source_path.name),
                    "classification_reason": reason,
                    "timestamp_source": timestamp_source,
                    "timezone": timezone_name or "",
                    "status": "skipped-already-exists",
                }
            )
            continue

        if not args.apply:
            copied.append(
                {
                    "source_path": str(source_path),
                    "destination_path": str(target_path),
                    "classification_reason": reason,
                    "timestamp_source": timestamp_source,
                    "timezone": timezone_name or "",
                    "status": "planned",
                }
            )
            continue

        try:
            shutil.copy2(source_path, target_path)
            copied.append(
                {
                    "source_path": str(source_path),
                    "destination_path": str(target_path),
                    "classification_reason": reason,
                    "timestamp_source": timestamp_source,
                    "timezone": timezone_name or "",
                    "status": "copied",
                }
            )
        except Exception as exc:  # noqa: BLE001
            failed.append(
                {
                    "source_path": str(source_path),
                    "error": str(exc),
                }
            )

    print(file=sys.stderr)  # newline after progress line

    missed_media: list[str] = []

    if args.apply:
        copied_sources = {entry["source_path"] for entry in copied if entry["status"] == "copied"}
        for source_path in media_sources:
            if str(source_path) not in copied_sources:
                missed_media.append(str(source_path))

        missing_by_hash = verify_with_find_missing(
            media_source_paths=media_sources,
            destination_root=destination_root,
            workers=max(1, args.workers),
            verbose=args.verbose,
            phase_callback=_emit_phase,
        )
        for source_path in missing_by_hash:
            if source_path not in missed_media:
                missed_media.append(source_path)

    for entry in failed:
        source_path = entry["source_path"]
        if source_path not in missed_media:
            missed_media.append(source_path)

    _emit_phase("writing report")
    build_report(
        source_root=source_root,
        destination_root=destination_root,
        apply_mode=args.apply,
        copied=copied,
        failed=failed,
        non_media=non_media_not_copied,
        missed_media=sorted(missed_media),
        unknown_signatures=unknown_signatures,
        report_path=report_path,
    )

    _emit_phase("saving signature cache")
    _save_signature_cache(cache_path, signature_cache)

    skipped_count = sum(1 for entry in copied if entry.get("status") == "skipped-already-exists")
    active_count = len(copied) - skipped_count

    print(f"Wrote report: {report_path}")
    print(f"Media planned/copied: {active_count}")
    if skipped_count:
        print(f"Media skipped (identical file already at destination): {skipped_count}")
    print(f"Media copy failed: {len(failed)}")
    print(f"Missed media: {len(missed_media)}")
    print(f"Non-media not copied: {len(non_media_not_copied)}")
    print(f"Unknown signatures still needing AI lookup: {len(unknown_signatures)}")
    _emit_done(report_path)

    if args.apply and missed_media:
        print("Verification failed: missed media detected. See report for details.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
