#!/usr/bin/env python3
"""Rename a media folder by itinerary-ordered POI labels."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
import random
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


MAX_FOLDER_NAME_LEN = 120
DATE_FOLDER_RE = re.compile(r"^\d{4}_\d{2}_\d{2}(?:_|$)")
DATE_NAME_RE = re.compile(r"^(\d{4})_(\d{2})_(\d{2})$")
APP_USER_AGENT = "Lookup_POI_withlocalcache"
LOW_BALANCE_THRESHOLD = 100
UNKNOWN_429_BALANCE_RECHECK_SEC = 2.0


_ACTIVE_SHUTDOWN_CONTROLLER: "ShutdownController | None" = None


class LocationIQGracefulStop(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ShutdownController:
    def __init__(self) -> None:
        self.interrupt_count = 0
        self.shutdown_requested = False
        self.force_exit = False
        self.interrupt_source: str | None = None

    def request_shutdown(self, source: str) -> bool:
        self.interrupt_count += 1
        if self.interrupt_count == 1:
            self.shutdown_requested = True
            if self.interrupt_source is None:
                self.interrupt_source = source
            return False

        self.force_exit = True
        if self.interrupt_source is None:
            self.interrupt_source = source
        return True


def install_signal_handlers(controller: ShutdownController) -> tuple[Any, Any]:
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _handler(_signum: int, _frame: Any) -> None:
        if controller.request_shutdown("signal"):
            raise KeyboardInterrupt
        print("Interrupt received. Graceful stop requested; finishing current folder.", file=sys.stderr)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    return previous_sigint, previous_sigterm


def restore_signal_handlers(previous_handlers: tuple[Any, Any]) -> None:
    previous_sigint, previous_sigterm = previous_handlers
    signal.signal(signal.SIGINT, previous_sigint)
    signal.signal(signal.SIGTERM, previous_sigterm)


def default_cache_path() -> Path:
    return Path(__file__).resolve().parent / "cache" / "geo_api_cache.json"


@dataclass(frozen=True)
class MediaPoint:
    source_file: str
    lat: float
    lon: float
    timestamp: datetime


@dataclass
class LocationSet:
    points: list[MediaPoint]
    label: str | None = None
    top_candidates: list[dict[str, Any]] | None = None


class LocationIQRateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.requests_per_second = max(requests_per_second, 0.1)
        self._min_interval_sec = 1.0 / self.requests_per_second
        self._lock = threading.Lock()
        self._next_allowed_time = 0.0

    def wait_for_slot(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed_time:
                time.sleep(self._next_allowed_time - now)
                now = time.monotonic()
            self._next_allowed_time = now + self._min_interval_sec


class LocalApiCache:
    def __init__(self, cache_path: Path) -> None:
        self.cache_path = cache_path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {"version": 1, "entries": {}}
        self._load()

    def _load(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            loaded = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        if isinstance(loaded, dict) and isinstance(loaded.get("entries"), dict):
            self._data = loaded

    def _write(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(self._data, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
        temp_path.replace(self.cache_path)

    def _key(self, service: str, params: dict[str, Any]) -> str:
        payload = {"service": service, "params": params}
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def get(self, service: str, params: dict[str, Any]) -> Any | None:
        key = self._key(service, params)
        with self._lock:
            entry = self._data["entries"].get(key)
            if not isinstance(entry, dict):
                return None
            return entry.get("payload")

    def set(self, service: str, params: dict[str, Any], payload: Any) -> None:
        key = self._key(service, params)
        with self._lock:
            self._data["entries"][key] = {
                "service": service,
                "params": params,
                "payload": payload,
                "updated_at": int(time.time()),
            }
            self._write()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_m = 6_371_000.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return earth_radius_m * c


def parse_exif_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    candidates = [
        "%Y:%m:%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S%z",
        "%Y:%m:%d %H:%M:%S.%f",
        "%Y:%m:%d %H:%M:%S.%f%z",
    ]
    for fmt in candidates:
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    return None


def _extract_timestamp(record: dict[str, Any]) -> datetime | None:
    fields = [
        "DateTimeOriginal",
        "CreateDate",
        "MediaCreateDate",
        "TrackCreateDate",
        "FileModifyDate",
    ]
    for field in fields:
        parsed = parse_exif_datetime(record.get(field))
        if parsed:
            return parsed
    return None


def extract_media_points(folder: Path) -> list[MediaPoint]:
    cmd = [
        "exiftool",
        "-r",
        "-ext",
        "jpg",
        "-ext",
        "jpeg",
        "-ext",
        "heic",
        "-ext",
        "mov",
        "-ext",
        "mp4",
        "-ext",
        "m4v",
        "-j",
        "-n",
        "-GPSLatitude",
        "-GPSLongitude",
        "-DateTimeOriginal",
        "-CreateDate",
        "-MediaCreateDate",
        "-TrackCreateDate",
        "-FileModifyDate",
        str(folder),
    ]

    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("exiftool is required but not installed") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"exiftool failed: {exc.stderr.strip()}") from exc

    try:
        records = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Failed to parse exiftool JSON output") from exc

    points: list[MediaPoint] = []
    for rec in records:
        lat = rec.get("GPSLatitude")
        lon = rec.get("GPSLongitude")
        source = rec.get("SourceFile") or rec.get("FileName") or "(unknown)"

        if lat is None or lon is None:
            continue

        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            continue

        timestamp = _extract_timestamp(rec)
        if timestamp is None:
            try:
                timestamp = datetime.fromtimestamp(Path(source).stat().st_mtime)
            except OSError:
                continue

        points.append(MediaPoint(source_file=str(source), lat=lat_f, lon=lon_f, timestamp=timestamp))

    return points


def sample_points(points: list[MediaPoint], ratio: float, seed: str) -> list[MediaPoint]:
    if not points:
        return []
    if ratio <= 0 or ratio > 1:
        raise ValueError("ratio must be in (0, 1]")

    sample_size = max(1, math.ceil(len(points) * ratio))
    if sample_size >= len(points):
        return list(points)

    rng = random.Random(seed)
    indices = sorted(rng.sample(range(len(points)), sample_size))
    return [points[idx] for idx in indices]


def _cluster_centroid(location_set: LocationSet) -> tuple[float, float]:
    lat = sum(point.lat for point in location_set.points) / len(location_set.points)
    lon = sum(point.lon for point in location_set.points) / len(location_set.points)
    return lat, lon


def cluster_points(points: list[MediaPoint], threshold_m: float = 300.0) -> list[LocationSet]:
    if threshold_m <= 0:
        raise ValueError("threshold_m must be > 0")
    if not points:
        return []

    ordered = sorted(points, key=lambda point: point.timestamp)
    sets: list[LocationSet] = []

    for point in ordered:
        best_index: int | None = None
        best_distance = float("inf")

        for idx, location_set in enumerate(sets):
            centroid_lat, centroid_lon = _cluster_centroid(location_set)
            distance = haversine_m(point.lat, point.lon, centroid_lat, centroid_lon)
            if distance <= threshold_m and distance < best_distance:
                best_index = idx
                best_distance = distance

        if best_index is None:
            sets.append(LocationSet(points=[point]))
        else:
            sets[best_index].points.append(point)

    return sets


def _is_landmark(poi: dict[str, Any]) -> bool:
    poi_class = str(poi.get("class") or "").lower()
    poi_type = str(poi.get("type") or poi.get("tag_type") or "").lower()

    landmark_classes = {"tourism", "historic", "leisure"}
    landmark_types = {
        "attraction",
        "museum",
        "monument",
        "memorial",
        "theme_park",
        "viewpoint",
        "zoo",
        "castle",
        "artwork",
    }
    return poi_class in landmark_classes or poi_type in landmark_types


def _city_from_address(address: dict[str, Any]) -> str | None:
    city_keys = ["city", "town", "village", "municipality", "county", "state"]
    for key in city_keys:
        value = address.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def choose_preferred_label(poi_results: list[dict[str, Any]]) -> str | None:
    for poi in poi_results:
        name = poi.get("name")
        if _is_landmark(poi) and isinstance(name, str) and name.strip():
            return name.strip()

    for poi in poi_results:
        raw_address = poi.get("address")
        address: dict[str, Any] = raw_address if isinstance(raw_address, dict) else {}
        city = _city_from_address(address)
        if city:
            return city

    for poi in poi_results:
        name = poi.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()

    return None


def _is_rejected_label(label: str, category: str | None = None, candidate_type: str | None = None) -> bool:
    lower_label = label.strip().lower()
    lower_category = (category or "").strip().lower()
    lower_type = (candidate_type or "").strip().lower()

    timezone_tokens = ["timezone", "america/", "asia/", "africa/", "europe/", "pacific/"]
    if any(token in lower_label for token in timezone_tokens):
        return True

    if lower_type == "timezone":
        return True

    if lower_category == "boundary" and lower_type in {
        "timezone",
        "political",
        "census",
        "administrative",
        "cadastral",
    }:
        return True

    return False


def choose_nominatim_label(payload: dict[str, Any]) -> str | None:
    category = str(payload.get("category") or payload.get("class") or "")
    candidate_type = str(payload.get("type") or "")

    name = payload.get("name")
    if isinstance(name, str) and name.strip():
        picked = name.strip()
        if not _is_rejected_label(picked, category=category, candidate_type=candidate_type):
            return picked

    address = payload.get("address")
    if isinstance(address, dict):
        for key in ["tourism", "attraction", "natural", "leisure", "historic", "waterway"]:
            value = address.get(key)
            if isinstance(value, str) and value.strip():
                picked = value.strip()
                if not _is_rejected_label(picked, category=category, candidate_type=candidate_type):
                    return picked

    display_name = payload.get("display_name")
    if isinstance(display_name, str) and display_name.strip():
        first_segment = display_name.split(",", 1)[0].strip()
        if first_segment:
            if not _is_rejected_label(first_segment, category=category, candidate_type=candidate_type):
                return first_segment

    return None


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_numbers(values: list[float | None], higher_is_better: bool) -> list[float | None]:
    available = [value for value in values if value is not None]
    if not available:
        return [None for _ in values]

    min_value = min(available)
    max_value = max(available)
    if max_value == min_value:
        base = [1.0 if value is not None else None for value in values]
    else:
        base = [
            ((value - min_value) / (max_value - min_value)) if value is not None else None
            for value in values
        ]

    if higher_is_better:
        return base
    return [(1.0 - value) if value is not None else None for value in base]


def normalize_candidate_metrics(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [dict(candidate) for candidate in candidates]

    importance_values = [_as_float(candidate.get("importance_raw")) for candidate in normalized]
    place_rank_values = [_as_float(candidate.get("place_rank_raw")) for candidate in normalized]
    distance_values = [_as_float(candidate.get("distance_m")) for candidate in normalized]

    importance_norm = _normalize_numbers(importance_values, higher_is_better=True)
    place_rank_norm = _normalize_numbers(place_rank_values, higher_is_better=True)
    proximity_norm = _normalize_numbers(distance_values, higher_is_better=False)

    for idx, candidate in enumerate(normalized):
        candidate["importance_norm"] = importance_norm[idx]
        candidate["place_rank_norm"] = place_rank_norm[idx]
        candidate["proximity_norm"] = proximity_norm[idx]

    return normalized


def _candidate_distance_m(candidate_lat: Any, candidate_lon: Any, centroid_lat: float, centroid_lon: float) -> float | None:
    lat = _as_float(candidate_lat)
    lon = _as_float(candidate_lon)
    if lat is None or lon is None:
        return None
    return haversine_m(centroid_lat, centroid_lon, lat, lon)


def build_locationiq_candidates(
    poi_results: list[dict[str, Any]],
    centroid_lat: float,
    centroid_lon: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    for poi in poi_results:
        name = poi.get("name")
        address_raw = poi.get("address")
        address: dict[str, Any] = address_raw if isinstance(address_raw, dict) else {}

        label = name.strip() if isinstance(name, str) and name.strip() else None
        if label is None:
            label = _city_from_address(address)
        if label is None:
            road = address.get("road")
            if isinstance(road, str) and road.strip():
                label = road.strip()
        if label is None:
            continue

        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)

        candidates.append(
            {
                "label": label,
                "source": "locationiq",
                "category": str(poi.get("class") or poi.get("category") or ""),
                "type": str(poi.get("type") or poi.get("tag_type") or ""),
                "importance_raw": _as_float(poi.get("importance")),
                "place_rank_raw": _as_float(poi.get("place_rank")),
                "distance_m": _candidate_distance_m(poi.get("lat"), poi.get("lon"), centroid_lat, centroid_lon),
            }
        )

    filtered: list[dict[str, Any]] = []
    for candidate in candidates:
        label = str(candidate.get("label") or "")
        category = str(candidate.get("category") or "")
        candidate_type = str(candidate.get("type") or "")
        if _is_rejected_label(label, category=category, candidate_type=candidate_type):
            continue
        filtered.append(candidate)

    return filtered


def build_nominatim_candidates(payload: dict[str, Any], centroid_lat: float, centroid_lon: float) -> list[dict[str, Any]]:
    label = choose_nominatim_label(payload)
    if not label:
        return []

    category = str(payload.get("category") or payload.get("class") or "")
    candidate_type = str(payload.get("type") or "")

    if category.casefold() == "boundary" and candidate_type.casefold() == "protected_area" and "/" in label:
        split_candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_part in label.split("/"):
            part = raw_part.strip()
            if not part:
                continue
            key = part.casefold()
            if key in seen:
                continue
            seen.add(key)
            split_candidates.append(
                {
                    "label": part,
                    "source": "nominatim",
                    "category": "natural",
                    "type": "protected_area_fragment",
                    "importance_raw": _as_float(payload.get("importance")),
                    "place_rank_raw": _as_float(payload.get("place_rank")),
                    "distance_m": _candidate_distance_m(payload.get("lat"), payload.get("lon"), centroid_lat, centroid_lon),
                }
            )
        return split_candidates

    if _is_rejected_label(label, category=category, candidate_type=candidate_type):
        return []

    return [
        {
            "label": label,
            "source": "nominatim",
            "category": str(payload.get("category") or payload.get("class") or ""),
            "type": str(payload.get("type") or ""),
            "importance_raw": _as_float(payload.get("importance")),
            "place_rank_raw": _as_float(payload.get("place_rank")),
            "distance_m": _candidate_distance_m(payload.get("lat"), payload.get("lon"), centroid_lat, centroid_lon),
        }
    ]


def normalize_label(label: str) -> str:
    ascii_label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    parts = re.findall(r"[A-Za-z0-9]+", ascii_label)
    if not parts:
        return ""

    normalized_parts: list[str] = []
    for part in parts:
        has_upper = any(ch.isupper() for ch in part)
        has_lower = any(ch.islower() for ch in part)
        if has_upper and has_lower:
            normalized_parts.append(part[:1].upper() + part[1:])
        else:
            normalized_parts.append(part[:1].upper() + part[1:].lower())

    return "".join(normalized_parts)


def dedupe_labels(labels: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for label in labels:
        normalized = normalize_label(label)
        if normalized.casefold() == "unknownlocation":
            continue
        if is_low_signal_label(normalized):
            continue
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def is_low_signal_label(label: str) -> bool:
    lower = label.casefold()
    if bool(re.search(r"\d", label)):
        return True
    if "timezone" in lower:
        return True
    return False


def labels_in_itinerary_order(sets: list[LocationSet]) -> list[str]:
    ordered = sorted(sets, key=lambda location_set: min(p.timestamp for p in location_set.points))
    return [location_set.label for location_set in ordered if location_set.label]


def build_target_name(base_name: str, labels: list[str], use_local_agent_compaction: bool = False) -> str:
    deduped = dedupe_labels(labels)
    if not deduped:
        return base_name

    candidate = f"{base_name}_{','.join(deduped)}"
    if len(candidate) <= MAX_FOLDER_NAME_LEN:
        return candidate

    if use_local_agent_compaction:
        compacted = compact_folder_name_with_local_agent(base_name, deduped, MAX_FOLDER_NAME_LEN)
        if compacted and len(compacted) <= MAX_FOLDER_NAME_LEN:
            return compacted

    return candidate


def extract_base_date_name(folder_name: str) -> str:
    if DATE_FOLDER_RE.match(folder_name):
        return folder_name[:10]
    return folder_name


def is_supported_date_folder_path(folder: Path) -> bool:
    return bool(DATE_NAME_RE.match(folder.name))


def _is_already_landmark_named_folder_name(folder_name: str) -> bool:
    return bool(DATE_FOLDER_RE.match(folder_name) and not DATE_NAME_RE.match(folder_name))


def discover_day_folders(root_path: Path) -> list[dict[str, str]]:
    discovered: list[dict[str, str]] = []

    for current_root, dirnames, _ in os.walk(root_path, topdown=True):
        kept: list[str] = []
        for dirname in dirnames:
            child = Path(current_root) / dirname
            if DATE_NAME_RE.match(dirname):
                discovered.append({"folder_path": str(child), "status": "eligible-date-folder"})
                continue
            if _is_already_landmark_named_folder_name(dirname):
                discovered.append({"folder_path": str(child), "status": "already-landmark-named"})
                continue
            kept.append(dirname)
        dirnames[:] = kept

    discovered.sort(key=lambda entry: entry["folder_path"])
    return discovered


def canonical_folder_id(folder_path: Path) -> str:
    base_name = extract_base_date_name(folder_path.name)
    return str(folder_path.with_name(base_name))


def _default_resume_state() -> dict[str, Any]:
    return {"version": 1, "folders": {}}


def load_resume_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return _default_resume_state()
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to parse state file {state_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid state file {state_path}: expected JSON object")
    folders = payload.get("folders")
    if not isinstance(folders, dict):
        raise RuntimeError(f"Invalid state file {state_path}: missing 'folders' object")
    if "version" not in payload:
        payload["version"] = 1
    return payload


def save_resume_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_name(f"{state_path.name}.tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(state_path)


def should_process_folder(state_entry: dict[str, Any] | None, retry_cfg: dict[str, int]) -> bool:
    if not state_entry:
        return True

    latest_status = str(state_entry.get("latest_status") or "")
    if latest_status in {"renamed", "error-retry-exhausted", "no-landmark-retry-exhausted"}:
        return False

    if latest_status == "error":
        error_attempt_count = int(state_entry.get("error_attempt_count") or 0)
        return error_attempt_count < retry_cfg["error_retry_max"] + 1

    if latest_status == "skipped-no-landmark-name-proposed":
        no_landmark_attempt_count = int(state_entry.get("no_landmark_attempt_count") or 0)
        return no_landmark_attempt_count < retry_cfg["no_landmark_retry_max"] + 1

    return True


def compute_coverage_check(
    discovered_ids: set[str],
    folder_results: list[dict[str, Any]],
    pending_folder_ids: list[str],
) -> dict[str, Any]:
    terminal_ids = {
        str(entry.get("folder_id"))
        for entry in folder_results
        if isinstance(entry.get("folder_id"), str)
    }
    pending_ids = {str(folder_id) for folder_id in pending_folder_ids}
    accounted_ids = terminal_ids | pending_ids

    missing_folder_ids = sorted(discovered_ids - accounted_ids)
    unexpected_folder_ids = sorted(accounted_ids - discovered_ids)
    return {
        "coverage_check_failed": bool(missing_folder_ids or unexpected_folder_ids),
        "missing_folder_ids": missing_folder_ids,
        "unexpected_folder_ids": unexpected_folder_ids,
    }


def find_available_local_agent() -> str | None:
    for name in ["opencode", "claude", "codex"]:
        if shutil.which(name):
            return name
    return None


def compact_folder_name_with_local_agent(base_name: str, labels: list[str], max_len: int) -> str | None:
    agent = find_available_local_agent()
    if not agent:
        return None

    prompt = (
        "Compact this folder name while preserving POI order and readability. "
        "Return plain text only in format DATE_POE1,POE2 with PascalCase POE tokens, "
        "no delimiters inside a POE token. "
        f"Date prefix: {base_name}. "
        f"POE labels: {', '.join(labels)}. "
        f"Max length: {max_len}."
    )

    command_attempts: list[list[str]]
    if agent == "opencode":
        command_attempts = [
            ["opencode", "--variant", "medium", "--prompt", prompt],
            ["opencode", "--variant", "medium", "-p", prompt],
        ]
    elif agent == "claude":
        command_attempts = [["claude", "-p", prompt], ["claude", "--print", prompt]]
    else:
        command_attempts = [["codex", "-p", prompt], ["codex", "--prompt", prompt]]

    for cmd in command_attempts:
        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=20)
            if result.returncode != 0:
                continue
            content = result.stdout.strip()
            if not content:
                continue
            compacted = sanitize_compacted_name(content.splitlines()[0], base_name)
            if compacted.startswith(f"{base_name}_") and len(compacted) <= max_len:
                return compacted
        except Exception:  # noqa: BLE001
            continue
    return None


def sanitize_compacted_name(name: str, base_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_,]+", "", name)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned.startswith(base_name):
        return f"{base_name}_{cleaned}" if cleaned else base_name
    return cleaned


def classify_locationiq_429_bucket(payload_text: str) -> str:
    lowered = payload_text.lower()
    if "rate limited day" in lowered:
        return "day"
    if "rate limited minute" in lowered:
        return "minute"
    if "rate limited second" in lowered:
        return "second"
    return "unknown"


def parse_locationiq_balance_day(payload: Any) -> int | None:
    if isinstance(payload, dict):
        nested = payload.get("balance")
        day_value: Any = None
        if isinstance(nested, dict):
            day_value = nested.get("day")
        if day_value is not None:
            try:
                return int(day_value)
            except (TypeError, ValueError):
                return None

        day_value = payload.get("day")
        if day_value is not None:
            try:
                return int(day_value)
            except (TypeError, ValueError):
                return None
    return None


def fetch_locationiq_balance_day(api_key: str, region: str = "us1") -> int | None:
    params = {"key": api_key, "format": "json"}
    endpoint = f"https://{region}.locationiq.com/v1/balance?{urlencode(params)}"
    try:
        with urlopen(endpoint, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return parse_locationiq_balance_day(payload)


def evaluate_unknown_429_balance_stop(api_key: str, region: str) -> tuple[str | None, str | None]:
    first_balance = fetch_locationiq_balance_day(api_key, region=region)
    if first_balance is not None and first_balance < LOW_BALANCE_THRESHOLD:
        return (
            "locationiq-balance-low-threshold",
            f"LocationIQ balance.day {first_balance} is below threshold {LOW_BALANCE_THRESHOLD}.",
        )

    if first_balance is not None:
        time.sleep(UNKNOWN_429_BALANCE_RECHECK_SEC)
        second_balance = fetch_locationiq_balance_day(api_key, region=region)
        if second_balance is not None and second_balance < LOW_BALANCE_THRESHOLD:
            return (
                "locationiq-balance-low-threshold-confirmed",
                f"LocationIQ balance.day {second_balance} is below threshold {LOW_BALANCE_THRESHOLD} on recheck.",
            )

    return None, None


def fetch_nearby_poi(
    api_key: str,
    lat: float,
    lon: float,
    landmark_filter: str,
    radius: int,
    region: str,
    retries: int = 3,
    locationiq_rate_limiter: LocationIQRateLimiter | None = None,
    api_cache: LocalApiCache | None = None,
) -> list[dict[str, Any]]:
    cache_params = {
        "lat": f"{lat:.7f}",
        "lon": f"{lon:.7f}",
        "landmark_filter": landmark_filter,
        "radius": int(radius),
        "region": region,
        "format": "json",
    }
    if api_cache is not None:
        cached_payload = api_cache.get("locationiq-nearby", cache_params)
        if isinstance(cached_payload, list):
            return cached_payload
        if cached_payload is not None:
            return []

    params = {
        "key": api_key,
        "lat": lat,
        "lon": lon,
        "tag": landmark_filter,
        "radius": radius,
        "format": "json",
    }
    endpoint = f"https://{region}.locationiq.com/v1/nearby?{urlencode(params)}"

    attempt = 0
    while True:
        attempt += 1
        try:
            if locationiq_rate_limiter is not None:
                locationiq_rate_limiter.wait_for_slot()
            with urlopen(endpoint, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if api_cache is not None:
                api_cache.set("locationiq-nearby", cache_params, payload)
            if isinstance(payload, list):
                return payload
            return []
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                error_payload: Any = json.loads(body)
            except json.JSONDecodeError:
                error_payload = {"error_body": body}

            if api_cache is not None:
                api_cache.set("locationiq-nearby", cache_params, error_payload)

            if exc.code == 429:
                bucket = classify_locationiq_429_bucket(body)
                if bucket == "day":
                    raise LocationIQGracefulStop(
                        "locationiq-rate-limited-day",
                        "LocationIQ returned HTTP 429 Rate Limited Day.",
                    ) from exc

                if bucket == "unknown" and attempt == 1:
                    stop_reason, stop_message = evaluate_unknown_429_balance_stop(api_key, region=region)
                    if stop_reason and stop_message:
                        raise LocationIQGracefulStop(stop_reason, stop_message) from exc

                if attempt < retries:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    retry_after_sec = 0.0
                    if retry_after:
                        try:
                            retry_after_sec = float(retry_after)
                        except ValueError:
                            retry_after_sec = 0.0
                    jitter = random.uniform(0.05, 0.25)
                    time.sleep(max(retry_after_sec, 0.5 * (2 ** (attempt - 1)) + jitter))
                    continue

                if bucket in {"second", "minute"}:
                    raise LocationIQGracefulStop(
                        "locationiq-rate-limit-retry-exhausted",
                        f"LocationIQ HTTP 429 Rate Limited {bucket.title()} retries exhausted.",
                    ) from exc

                raise LocationIQGracefulStop(
                    "locationiq-rate-limit-unknown",
                    "LocationIQ HTTP 429 with unknown rate-limit scope; retries exhausted.",
                ) from exc

            if exc.code == 404:
                return []
            raise RuntimeError(f"LocationIQ HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            if attempt < retries:
                time.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            raise RuntimeError(f"LocationIQ network error: {exc.reason}") from exc


def fetch_nominatim_reverse(
    lat: float,
    lon: float,
    zoom: int,
    layer: str,
    retries: int = 2,
    nominatim_rate_limiter: LocationIQRateLimiter | None = None,
    api_cache: LocalApiCache | None = None,
) -> dict[str, Any]:
    cache_params = {
        "lat": f"{lat:.7f}",
        "lon": f"{lon:.7f}",
        "zoom": int(zoom),
        "layer": layer,
        "format": "jsonv2",
        "addressdetails": 1,
        "namedetails": 1,
        "extratags": 1,
        "accept-language": "en",
    }
    if api_cache is not None:
        cached_payload = api_cache.get("nominatim-reverse", cache_params)
        if isinstance(cached_payload, dict):
            return cached_payload
        if cached_payload is not None:
            return {}

    params = {
        "format": "jsonv2",
        "lat": lat,
        "lon": lon,
        "addressdetails": 1,
        "namedetails": 1,
        "extratags": 1,
        "accept-language": "en",
        "zoom": zoom,
        "layer": layer,
    }
    endpoint = f"https://nominatim.openstreetmap.org/reverse?{urlencode(params)}"
    request = Request(endpoint, headers={"User-Agent": APP_USER_AGENT})

    attempt = 0
    while True:
        attempt += 1
        try:
            if nominatim_rate_limiter is not None:
                nominatim_rate_limiter.wait_for_slot()
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if api_cache is not None:
                api_cache.set("nominatim-reverse", cache_params, payload)
            if isinstance(payload, dict):
                return payload
            return {}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                error_payload: Any = json.loads(body)
            except json.JSONDecodeError:
                error_payload = {"error_body": body}

            if api_cache is not None:
                api_cache.set("nominatim-reverse", cache_params, error_payload)

            if attempt < retries:
                time.sleep(0.4 * (2 ** (attempt - 1)))
                continue
            return {}
        except URLError:
            if attempt < retries:
                time.sleep(0.4 * (2 ** (attempt - 1)))
                continue
            return {}


def _parse_opencode_json(stdout: str) -> dict[str, Any] | None:
    content = stdout.strip()
    if not content:
        return None

    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
        return None
    except json.JSONDecodeError:
        return None


def _extract_string_values(payload: dict[str, Any], keys: list[str]) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                values.append(stripped)
            continue
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, str) and entry.strip():
                    values.append(entry.strip())
    return values


def _extract_final_landmark_names(payload: dict[str, Any]) -> list[str]:
    return _extract_string_values(payload, ["final_landmark_names", "final_landmark_name"])


def consolidate_itinerary_landmark_names(
    labels: list[str],
    max_landmark_names: int,
    opencode_timeout_sec: int,
    opencode_model: str | None,
    location_set_members: list[dict[str, Any]] | None = None,
) -> list[str]:
    if max_landmark_names <= 0:
        return []

    deduped = dedupe_labels(labels)
    if not deduped:
        return []

    fallback = deduped[:max_landmark_names]
    if len(fallback) <= 1:
        return fallback

    if shutil.which("opencode") is None:
        return fallback

    prompt = (
        "You are refining itinerary landmark names visited during a trip. "
        "Goal: reflect visited places without redundant parent-child landmark names (example: StatueOfLiberty + NewYork -> keep StatueOfLiberty), "
        "remove redundant parent-city/admin names when a specific POI is present; Choose only from candidates, no invented landmark names. "
        "Prefer specific names over generic names: 'Statue of Liberty Information Center' is good, plain 'Information Center' is too generic. "
        "You MAY use tools/web lookup to check place parent-child relationships if uncertain. "
        "Preserve itinerary order from the original list. "
        "If two labels are from different countries or distant regions, do not drop one as redundant. "
        "When location set count is greater than max total landmark names, trim from the smallest location sets first. "
        "Each location set may include runner_up_candidates; if you drop a set's primary label as redundant, you MAY substitute one of its runner-ups instead of dropping the set entirely. "
        "Return ONLY JSON with schema "
        '{"final_landmark_names":[string]}. '
        f"Max total landmark names: {max_landmark_names}. "
        f"Candidate landmark names in itinerary order: {json.dumps(deduped)}"
    )
    if location_set_members:
        prompt += f" Location set members metadata: {json.dumps(location_set_members, ensure_ascii=True)}"

    command = ["opencode"]
    if opencode_model:
        command.extend(["-m", opencode_model])
    command.extend(["--variant", "medium"])
    command.extend(["run", prompt])

    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=opencode_timeout_sec,
        )
    except Exception:  # noqa: BLE001
        return fallback

    if result.returncode != 0:
        return fallback

    payload = _parse_opencode_json(result.stdout)
    if payload is None:
        return fallback

    requested = dedupe_labels(_extract_final_landmark_names(payload))
    if not requested:
        return fallback

    valid_by_key: dict[str, str] = {label.casefold(): label for label in deduped}
    label_order: dict[str, int] = {label.casefold(): idx for idx, label in enumerate(deduped)}
    runner_up_alias_to_primary: dict[str, str] = {}

    if location_set_members:
        for member in location_set_members:
            primary = normalize_label(str(member.get("landmark_name") or ""))
            primary_key = primary.casefold()
            if primary_key == "unknownlocation":
                continue
            primary_idx = label_order.get(primary_key)
            if primary_idx is None:
                continue
            raw_primary_count = member.get("primary_candidate_count", 0)
            try:
                primary_candidate_count = int(raw_primary_count)
            except (TypeError, ValueError):
                primary_candidate_count = 0
            primary_locked = primary_candidate_count >= 2
            for runner_up_raw in member.get("runner_up_candidates", []):
                normalized = normalize_label(runner_up_raw)
                if not normalized or is_low_signal_label(normalized):
                    continue
                key = normalized.casefold()
                if primary_locked:
                    runner_up_alias_to_primary[key] = primary_key
                    continue
                if key not in valid_by_key:
                    valid_by_key[key] = normalized
                    label_order[key] = primary_idx

    selected_keys: set[str] = set()
    for landmark_name in requested:
        key = landmark_name.casefold()
        key = runner_up_alias_to_primary.get(key, key)
        if key in valid_by_key and key not in selected_keys:
            selected_keys.add(key)
        if len(selected_keys) >= max_landmark_names:
            break

    ordered_keys = sorted(selected_keys, key=lambda k: label_order.get(k, len(deduped)))
    selected = [valid_by_key[k] for k in ordered_keys][:max_landmark_names]
    if not selected:
        return fallback

    minimum_keep = max(2, math.ceil(len(fallback) * 0.5))
    if len(selected) < minimum_keep:
        return fallback

    return selected


def consolidate_itinerary_labels(
    labels: list[str],
    max_landmark_names: int,
    opencode_timeout_sec: int,
    opencode_model: str | None,
    location_set_members: list[dict[str, Any]] | None = None,
) -> list[str]:
    # Backward-compatible alias kept for external callers.
    return consolidate_itinerary_landmark_names(
        labels=labels,
        max_landmark_names=max_landmark_names,
        opencode_timeout_sec=opencode_timeout_sec,
        opencode_model=opencode_model,
        location_set_members=location_set_members,
    )


def finalize_landmark_names(
    sets: list[LocationSet],
    max_landmark_names: int,
    opencode_timeout_sec: int,
    opencode_model: str | None,
) -> list[str]:
    ordered = sorted(sets, key=lambda location_set: min(p.timestamp for p in location_set.points))
    ordered_labels = [location_set.label for location_set in ordered if location_set.label]
    location_set_members: list[dict[str, Any]] = []
    for idx, location_set in enumerate(ordered, start=1):
        if not location_set.label:
            continue
        member: dict[str, Any] = {
            "landmark_name": normalize_label(location_set.label),
            "set_member_count": len(location_set.points),
            "itinerary_order": idx,
        }
        if location_set.top_candidates:
            primary_key = normalize_label(location_set.label).casefold()
            runner_ups: list[str] = []
            primary_candidate_count = 0
            for candidate in location_set.top_candidates:
                c_label = candidate.get("label")
                if not isinstance(c_label, str):
                    continue
                normalized = normalize_label(c_label)
                if normalized and normalized.casefold() == primary_key:
                    primary_candidate_count += 1
                    continue
                if normalized and not is_low_signal_label(normalized):
                    runner_ups.append(c_label)
            if runner_ups:
                member["runner_up_candidates"] = runner_ups[:2]
            if primary_candidate_count:
                member["primary_candidate_count"] = primary_candidate_count
        location_set_members.append(member)
    return consolidate_itinerary_landmark_names(
        ordered_labels,
        max_landmark_names=max_landmark_names,
        opencode_timeout_sec=opencode_timeout_sec,
        opencode_model=opencode_model,
        location_set_members=location_set_members,
    )


def _looks_generic_label(candidate: dict[str, Any]) -> bool:
    label = str(candidate.get("label") or "").lower()
    category = str(candidate.get("category") or "").lower()
    candidate_type = str(candidate.get("type") or "").lower()

    bad_terms = ["timezone", "america/", "road", "street", "highway", "county", "borough", "region"]
    if any(term in label for term in bad_terms):
        return True

    admin_types = {"administrative", "boundary", "postcode"}
    if category in admin_types or candidate_type in admin_types:
        return True

    return False


def _fallback_best_label(normalized_candidates: list[dict[str, Any]]) -> str | None:
    viable = [
        candidate
        for candidate in normalized_candidates
        if normalize_label(str(candidate.get("label") or ""))
    ]
    if not viable:
        return None

    def score(candidate: dict[str, Any]) -> float:
        importance = float(candidate.get("importance_norm") or 0.0)
        place_rank = float(candidate.get("place_rank_norm") or 0.0)
        proximity = float(candidate.get("proximity_norm") or 0.0)
        generic_penalty = 0.4 if _looks_generic_label(candidate) else 0.0
        return (0.50 * importance) + (0.30 * place_rank) + (0.20 * proximity) - generic_penalty

    best = max(viable, key=score)
    label = best.get("label")
    return label if isinstance(label, str) and label.strip() else None


def choose_best_label_from_candidates(
    candidates: list[dict[str, Any]],
) -> str | None:
    if not candidates:
        return None

    cleaned: list[dict[str, Any]] = []
    for candidate in candidates:
        label = candidate.get("label")
        if isinstance(label, str) and label.strip():
            cleaned.append(dict(candidate))

    if not cleaned:
        return None
    if len(cleaned) == 1:
        only_candidate = cleaned[0]
        only = only_candidate.get("label")
        if not isinstance(only, str):
            return None
        if _looks_generic_label(only_candidate):
            return None
        if _is_rejected_label(
            only,
            category=str(only_candidate.get("category") or ""),
            candidate_type=str(only_candidate.get("type") or ""),
        ):
            return None
        return only

    normalized_candidates = normalize_candidate_metrics(cleaned)
    return _fallback_best_label(normalized_candidates)


def select_top_candidates(
    candidates: list[dict[str, Any]],
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """Return top N candidates by deterministic scoring."""
    if not candidates:
        return []

    cleaned: list[dict[str, Any]] = []
    for candidate in candidates:
        label = candidate.get("label")
        if isinstance(label, str) and label.strip():
            cleaned.append(dict(candidate))

    if not cleaned:
        return []

    normalized = normalize_candidate_metrics(cleaned)

    def score(c: dict[str, Any]) -> float:
        importance = float(c.get("importance_norm") or 0.0)
        place_rank = float(c.get("place_rank_norm") or 0.0)
        proximity = float(c.get("proximity_norm") or 0.0)
        generic_penalty = 0.4 if _looks_generic_label(c) else 0.0
        return (0.50 * importance) + (0.30 * place_rank) + (0.20 * proximity) - generic_penalty

    ranked = sorted(normalized, key=score, reverse=True)
    return ranked[:top_n]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append itinerary landmark names to a media folder name")
    parser.add_argument("folder", help="Path to media folder")
    parser.add_argument("--key", default=os.getenv("LOCATIONIQ_API_KEY"), help="LocationIQ API key")
    parser.add_argument("--ratio", type=float, default=1.0, help="Sampling ratio of GPS-bearing files")
    parser.add_argument(
        "--event-distance-m",
        "--threshold-m",
        dest="event_distance_m",
        type=float,
        default=2000.0,
        help="Distance threshold for location sets in meters",
    )
    parser.add_argument("--landmark-filter", default="all", help="Nearby API landmark filter")
    parser.add_argument("--radius", type=int, default=1000, help="Nearby API search radius in meters")
    parser.add_argument("--region", default="us1", choices=["us1", "eu1"], help="LocationIQ region")
    parser.add_argument("--seed", default=None, help="Optional deterministic sampling seed")
    parser.add_argument(
        "--max-landmark-names",
        dest="max_landmark_names",
        type=int,
        default=8,
        help="Maximum number of highlight landmark names to keep",
    )
    parser.add_argument(
        "--opencode-timeout-sec",
        type=int,
        default=60,
        help="Timeout in seconds for local opencode highlight selection",
    )
    parser.add_argument(
        "--opencode-model",
        default=os.getenv("OPENCODE_MODEL"),
        help="Model name passed to opencode -m (defaults to OPENCODE_MODEL)",
    )
    parser.add_argument("--nominatim-zoom", type=int, default=18, help="Nominatim reverse zoom level (0-18)")
    parser.add_argument(
        "--nominatim-layer",
        default="poi,natural,manmade",
        help="Nominatim reverse layer restriction",
    )
    parser.add_argument(
        "--nominatim-requests-per-second",
        type=float,
        default=1.0,
        help="Nominatim request rate; policy maximum is 1.0",
    )
    parser.add_argument(
        "--locationiq-requests-per-second",
        type=float,
        default=float(os.getenv("LOCATIONIQ_REQUESTS_PER_SECOND", "1.0")),
        help="Throttle for LocationIQ requests per second",
    )
    parser.add_argument(
        "--cache-file",
        default=str(default_cache_path()),
        help="Local API cache file path for Nominatim and LocationIQ responses",
    )
    parser.add_argument(
        "--use-local-agent-compaction",
        action="store_true",
        help="Use local coding agent CLI (opencode/claude/codex) to compact long folder names",
    )
    parser.add_argument(
        "--report-json",
        default="folder_poi_itinerary_rename_report.json",
        help="JSON report output path",
    )
    parser.add_argument(
        "--state-json",
        default="folder_poi_itinerary_rename_state.json",
        help="State ledger path used for resumable multi-day runs",
    )
    parser.add_argument(
        "--error-retry-max",
        type=int,
        default=2,
        help="Maximum retries for folders ending in error (excludes first attempt)",
    )
    parser.add_argument(
        "--no-landmark-retry-max",
        type=int,
        default=1,
        help="Maximum retries for folders ending in no-landmark-proposed (excludes first attempt)",
    )
    parser.add_argument("--apply", action="store_true", help="Apply folder rename")
    return parser


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    suffix = 2
    while True:
        candidate = path.with_name(f"{path.name}_{suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1


def _assign_labels(
    sets: list[LocationSet],
    api_key: str,
    landmark_filter: str,
    radius: int,
    region: str,
    locationiq_requests_per_second: float,
    nominatim_zoom: int,
    nominatim_layer: str,
    nominatim_requests_per_second: float = 1.0,
    api_cache: LocalApiCache | None = None,
) -> None:
    locationiq_rate_limiter = LocationIQRateLimiter(locationiq_requests_per_second)
    nominatim_rate_limiter = LocationIQRateLimiter(min(max(nominatim_requests_per_second, 0.1), 1.0))

    for location_set in sets:
        centroid_lat, centroid_lon = _cluster_centroid(location_set)
        with ThreadPoolExecutor(max_workers=2) as executor:
            nominatim_future = executor.submit(
                fetch_nominatim_reverse,
                centroid_lat,
                centroid_lon,
                nominatim_zoom,
                nominatim_layer,
                2,
                nominatim_rate_limiter,
                api_cache,
            )
            nearby_future = executor.submit(
                fetch_nearby_poi,
                api_key,
                centroid_lat,
                centroid_lon,
                landmark_filter,
                radius,
                region,
                3,
                locationiq_rate_limiter,
                api_cache,
            )
            try:
                reverse_payload = nominatim_future.result()
            except LocationIQGracefulStop:
                raise
            except RuntimeError:
                reverse_payload = {}

            try:
                nearby_results = nearby_future.result()
            except LocationIQGracefulStop:
                raise
            except RuntimeError:
                nearby_results = []

        combined_candidates = build_locationiq_candidates(nearby_results, centroid_lat, centroid_lon)
        combined_candidates.extend(build_nominatim_candidates(reverse_payload, centroid_lat, centroid_lon))

        best_label = choose_best_label_from_candidates(combined_candidates)
        location_set.label = best_label or "UNKNOWN_LOCATION"
        location_set.top_candidates = select_top_candidates(combined_candidates)


def process_single_folder(
    folder: Path,
    args: argparse.Namespace,
    api_cache: LocalApiCache | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "folder_path": str(folder),
        "status": "error",
    }

    try:
        points = extract_media_points(folder)
        result["gps_bearing_files"] = len(points)
        if not points:
            result["status"] = "skipped-no-gps-media"
            return result

        seed = args.seed or folder.name
        sampled = sample_points(points, ratio=args.ratio, seed=seed)
        sets = cluster_points(sampled, threshold_m=args.event_distance_m)
        _assign_labels(
            sets,
            api_key=args.key,
            landmark_filter=args.landmark_filter,
            radius=args.radius,
            region=args.region,
            locationiq_requests_per_second=args.locationiq_requests_per_second,
            nominatim_zoom=args.nominatim_zoom,
            nominatim_layer=args.nominatim_layer,
            nominatim_requests_per_second=args.nominatim_requests_per_second,
            api_cache=api_cache,
        )

        selected_labels = finalize_landmark_names(
            sets,
            max_landmark_names=args.max_landmark_names,
            opencode_timeout_sec=args.opencode_timeout_sec,
            opencode_model=args.opencode_model,
        )
        deduped_landmarks = dedupe_labels(selected_labels)
        base_name = extract_base_date_name(folder.name)
        target_name = build_target_name(
            base_name,
            selected_labels,
            use_local_agent_compaction=args.use_local_agent_compaction,
        )

        result.update(
            {
                "sampled_files": len(sampled),
                "location_sets": len(sets),
                "ordered_landmark_names": deduped_landmarks,
                "base_name": base_name,
                "target_name": target_name,
            }
        )

        if target_name == folder.name:
            result["status"] = "skipped-no-landmark-name-proposed"
            return result

        destination = _unique_destination(folder.with_name(target_name))
        result["proposed_destination"] = str(destination)
        result["collision_suffix_used"] = destination.name != target_name

        if not args.apply:
            result["status"] = "planned-rename"
            return result

        folder.rename(destination)
        result["status"] = "renamed"
        result["applied_destination"] = str(destination)
        return result
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, LocationIQGracefulStop):
            raise
        result["status"] = "error"
        result["error"] = str(exc)
        return result


def build_rename_report(
    root_path: Path,
    apply_mode: bool,
    folder_results: list[dict[str, Any]],
    discovered_folders: list[dict[str, str]],
    started_at: datetime,
    finished_at: datetime,
    run_stats: dict[str, int] | None = None,
    run_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def _iso_utc(value: datetime) -> str:
        utc_value = value.astimezone(timezone.utc).replace(microsecond=0)
        return utc_value.isoformat().replace("+00:00", "Z")

    def _count(entries: list[dict[str, Any]], status: str) -> int:
        return sum(1 for entry in entries if entry.get("status") == status)

    def _count_many(entries: list[dict[str, Any]], statuses: set[str]) -> int:
        return sum(1 for entry in entries if entry.get("status") in statuses)

    stats = run_stats or {}
    meta = run_meta or {}

    no_landmark_paths = sorted(
        str(entry["folder_path"])
        for entry in folder_results
        if entry.get("status") == "skipped-no-landmark-name-proposed"
    )
    already_named_paths = sorted(
        str(entry["folder_path"])
        for entry in folder_results
        if entry.get("status") == "skipped-already-landmark-named"
    )
    error_paths = sorted(
        str(entry["folder_path"])
        for entry in folder_results
        if entry.get("status") == "error"
    )
    error_retry_exhausted_paths = sorted(
        str(entry["folder_path"])
        for entry in folder_results
        if entry.get("status") == "error-retry-exhausted"
    )
    no_landmark_retry_exhausted_paths = sorted(
        str(entry["folder_path"])
        for entry in folder_results
        if entry.get("status") == "no-landmark-retry-exhausted"
    )

    summary = {
        "candidate_folder_count": len(discovered_folders),
        "eligible_date_folder_count": _count(discovered_folders, "eligible-date-folder"),
        "processed_folder_count": len(folder_results),
        "planned_rename_count": _count(folder_results, "planned-rename"),
        "renamed_count": _count(folder_results, "renamed"),
        "already_landmark_named_count": _count(folder_results, "skipped-already-landmark-named"),
        "no_landmark_name_proposed_count": _count(folder_results, "skipped-no-landmark-name-proposed"),
        "no_gps_media_count": _count(folder_results, "skipped-no-gps-media"),
        "rename_failed_count": _count_many(folder_results, {"error", "error-retry-exhausted"}),
        "processed_this_run_count": int(stats.get("processed_this_run_count", 0)),
        "skipped_frozen_applied_count": int(stats.get("skipped_frozen_applied_count", 0)),
        "retried_error_count": int(stats.get("retried_error_count", 0)),
        "retried_no_landmark_count": int(stats.get("retried_no_landmark_count", 0)),
        "pending_folder_count": len(meta.get("pending_folder_ids") or []),
    }

    report = {
        "root_path": str(root_path),
        "mode": "apply" if apply_mode else "dry-run",
        "started_at": _iso_utc(started_at),
        "finished_at": _iso_utc(finished_at),
        "duration_seconds": round(max(0.0, (finished_at - started_at).total_seconds()), 3),
        "interrupted": bool(meta.get("interrupted", False)),
        "interrupt_source": meta.get("interrupt_source"),
        "last_completed_folder_id": meta.get("last_completed_folder_id"),
        "pending_folder_ids": sorted(str(folder_id) for folder_id in (meta.get("pending_folder_ids") or [])),
        "coverage_check_failed": bool(meta.get("coverage_check_failed", False)),
        "missing_folder_ids": sorted(str(folder_id) for folder_id in (meta.get("missing_folder_ids") or [])),
        "unexpected_folder_ids": sorted(str(folder_id) for folder_id in (meta.get("unexpected_folder_ids") or [])),
        "summary": summary,
        "no_landmark_name_proposed_paths": no_landmark_paths,
        "already_landmark_named_paths": already_named_paths,
        "error_paths": error_paths,
        "error_retry_exhausted_paths": error_retry_exhausted_paths,
        "no_landmark_retry_exhausted_paths": no_landmark_retry_exhausted_paths,
        "folders": sorted(folder_results, key=lambda entry: str(entry.get("folder_path") or "")),
    }
    return report


def _write_report(report_path: Path, report: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.folder).expanduser().resolve()
    report_path = Path(args.report_json).expanduser().resolve()
    state_path = Path(args.state_json).expanduser().resolve()
    started_at = datetime.now(timezone.utc)
    shutdown_controller = ShutdownController()
    previous_handlers = install_signal_handlers(shutdown_controller)
    global _ACTIVE_SHUTDOWN_CONTROLLER
    _ACTIVE_SHUTDOWN_CONTROLLER = shutdown_controller

    retry_cfg = {
        "error_retry_max": max(int(args.error_retry_max), 0),
        "no_landmark_retry_max": max(int(args.no_landmark_retry_max), 0),
    }
    run_stats = {
        "processed_this_run_count": 0,
        "skipped_frozen_applied_count": 0,
        "retried_error_count": 0,
        "retried_no_landmark_count": 0,
    }
    run_meta: dict[str, Any] = {
        "interrupted": False,
        "interrupt_source": None,
        "last_completed_folder_id": None,
        "pending_folder_ids": [],
        "coverage_check_failed": False,
        "missing_folder_ids": [],
        "unexpected_folder_ids": [],
    }

    try:
        if not input_path.exists() or not input_path.is_dir():
            print(f"Folder not found: {input_path}", file=sys.stderr)
            return 2

        if args.nominatim_requests_per_second > 1.0:
            print("Nominatim policy requires <= 1 request per second.", file=sys.stderr)
            return 2

        try:
            resume_state = load_resume_state(state_path)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        folders_state = resume_state.setdefault("folders", {})

        def _entry(folder_id: str) -> dict[str, Any]:
            existing = folders_state.get(folder_id)
            if isinstance(existing, dict):
                return existing
            created: dict[str, Any] = {"first_seen_at": started_at.isoformat().replace("+00:00", "Z")}
            folders_state[folder_id] = created
            return created

        def _persist_state() -> None:
            save_resume_state(state_path, resume_state)

        single_folder_input = is_supported_date_folder_path(input_path)
        if single_folder_input:
            discovered_folders = [{"folder_path": str(input_path), "status": "eligible-date-folder"}]
        elif _is_already_landmark_named_folder_name(input_path.name):
            discovered_folders = [{"folder_path": str(input_path), "status": "already-landmark-named"}]
        else:
            discovered_folders = discover_day_folders(input_path)

        discovered_ids = {canonical_folder_id(Path(entry["folder_path"])) for entry in discovered_folders}

        folder_results: list[dict[str, Any]] = []
        eligible_folders: list[tuple[Path, str]] = []

        for entry in discovered_folders:
            folder_path = Path(entry["folder_path"])
            folder_id = canonical_folder_id(folder_path)
            state_entry = _entry(folder_id)
            status = entry["status"]
            if status == "already-landmark-named":
                result_status = "skipped-already-landmark-named"
                if state_entry.get("latest_status") == "renamed":
                    result_status = "skipped-frozen-applied"
                    run_stats["skipped_frozen_applied_count"] += 1
                folder_results.append(
                    {
                        "folder_path": str(folder_path),
                        "folder_id": folder_id,
                        "status": result_status,
                        "base_name": extract_base_date_name(folder_path.name),
                        "target_name": folder_path.name,
                        "processed_this_run": False,
                    }
                )
                if result_status == "skipped-already-landmark-named":
                    state_entry["latest_status"] = "skipped-already-landmark-named"
                state_entry["last_seen_path"] = str(folder_path)
                state_entry["last_attempt_at"] = started_at.isoformat().replace("+00:00", "Z")
                _persist_state()
                continue

            if status == "eligible-date-folder":
                if should_process_folder(state_entry, retry_cfg):
                    eligible_folders.append((folder_path, folder_id))
                    continue

                latest_status = str(state_entry.get("latest_status") or "")
                skipped_status = "skipped-frozen-applied"
                if latest_status in {"error", "error-retry-exhausted"}:
                    skipped_status = "error-retry-exhausted"
                    state_entry["latest_status"] = "error-retry-exhausted"
                elif latest_status in {"skipped-no-landmark-name-proposed", "no-landmark-retry-exhausted"}:
                    skipped_status = "no-landmark-retry-exhausted"
                    state_entry["latest_status"] = "no-landmark-retry-exhausted"

                if skipped_status == "skipped-frozen-applied":
                    run_stats["skipped_frozen_applied_count"] += 1

                folder_results.append(
                    {
                        "folder_path": str(folder_path),
                        "folder_id": folder_id,
                        "status": skipped_status,
                        "base_name": extract_base_date_name(folder_path.name),
                        "target_name": folder_path.name,
                        "processed_this_run": False,
                    }
                )
                state_entry["last_seen_path"] = str(folder_path)
                state_entry["last_attempt_at"] = started_at.isoformat().replace("+00:00", "Z")
                _persist_state()

        if eligible_folders and not args.key:
            for folder, folder_id in eligible_folders:
                folder_results.append(
                    {
                        "folder_path": str(folder),
                        "folder_id": folder_id,
                        "status": "error",
                        "error": "Missing API key. Use --key or LOCATIONIQ_API_KEY.",
                        "processed_this_run": False,
                    }
                )

            finished_at = datetime.now(timezone.utc)
            coverage_check = compute_coverage_check(discovered_ids, folder_results, pending_folder_ids=[])
            run_meta.update(coverage_check)
            report = build_rename_report(
                root_path=input_path,
                apply_mode=args.apply,
                folder_results=folder_results,
                discovered_folders=discovered_folders,
                started_at=started_at,
                finished_at=finished_at,
                run_stats=run_stats,
                run_meta=run_meta,
            )
            _write_report(report_path, report)
            print(f"Wrote report: {report_path}")
            print("Missing API key. Use --key or LOCATIONIQ_API_KEY.", file=sys.stderr)
            return 2

        api_cache = LocalApiCache(Path(args.cache_file).expanduser().resolve()) if eligible_folders else None
        pending_folder_ids: list[str] = []

        quota_stop_reason: str | None = None

        for index, (folder, folder_id) in enumerate(eligible_folders):
            if shutdown_controller.shutdown_requested:
                pending_folder_ids.extend(remaining_folder_id for _, remaining_folder_id in eligible_folders[index:])
                break

            try:
                result = process_single_folder(folder, args, api_cache)
            except LocationIQGracefulStop as exc:
                quota_stop_reason = exc.reason
                pending_folder_ids.extend(remaining_folder_id for _, remaining_folder_id in eligible_folders[index:])
                break

            result["folder_id"] = folder_id
            result["processed_this_run"] = True
            run_stats["processed_this_run_count"] += 1

            state_entry = _entry(folder_id)
            state_entry["attempt_count"] = int(state_entry.get("attempt_count") or 0) + 1
            state_entry["last_seen_path"] = str(folder)
            state_entry["last_attempt_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

            outcome_status = str(result.get("status") or "")
            if outcome_status == "renamed":
                state_entry["latest_status"] = "renamed"
                state_entry["applied_destination"] = str(result.get("applied_destination") or "")
                state_entry["last_success_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            elif outcome_status == "error":
                error_attempt_count = int(state_entry.get("error_attempt_count") or 0) + 1
                state_entry["error_attempt_count"] = error_attempt_count
                state_entry["last_error"] = str(result.get("error") or "")
                if error_attempt_count > 1:
                    run_stats["retried_error_count"] += 1
                if error_attempt_count >= retry_cfg["error_retry_max"] + 1:
                    result["status"] = "error-retry-exhausted"
                    state_entry["latest_status"] = "error-retry-exhausted"
                else:
                    state_entry["latest_status"] = "error"
            elif outcome_status == "skipped-no-landmark-name-proposed":
                no_landmark_attempt_count = int(state_entry.get("no_landmark_attempt_count") or 0) + 1
                state_entry["no_landmark_attempt_count"] = no_landmark_attempt_count
                if no_landmark_attempt_count > 1:
                    run_stats["retried_no_landmark_count"] += 1
                if no_landmark_attempt_count >= retry_cfg["no_landmark_retry_max"] + 1:
                    result["status"] = "no-landmark-retry-exhausted"
                    state_entry["latest_status"] = "no-landmark-retry-exhausted"
                else:
                    state_entry["latest_status"] = "skipped-no-landmark-name-proposed"
            else:
                state_entry["latest_status"] = outcome_status

            state_entry["base_name"] = extract_base_date_name(folder.name)
            state_entry["last_target_name"] = str(result.get("target_name") or "")
            _persist_state()
            folder_results.append(result)
            run_meta["last_completed_folder_id"] = folder_id

        run_meta["interrupted"] = shutdown_controller.shutdown_requested or quota_stop_reason is not None
        run_meta["interrupt_source"] = quota_stop_reason or shutdown_controller.interrupt_source
        run_meta["pending_folder_ids"] = sorted(pending_folder_ids)
        coverage_check = compute_coverage_check(discovered_ids, folder_results, pending_folder_ids=pending_folder_ids)
        run_meta.update(coverage_check)

        finished_at = datetime.now(timezone.utc)
        report = build_rename_report(
            root_path=input_path,
            apply_mode=args.apply,
            folder_results=folder_results,
            discovered_folders=discovered_folders,
            started_at=started_at,
            finished_at=finished_at,
            run_stats=run_stats,
            run_meta=run_meta,
        )
        _write_report(report_path, report)

        summary = report["summary"]
        print(f"Wrote report: {report_path}")
        print(f"Candidate folders: {summary['candidate_folder_count']}")
        print(f"Planned renames: {summary['planned_rename_count']}")
        print(f"Renamed folders: {summary['renamed_count']}")
        print(f"Already landmark named: {summary['already_landmark_named_count']}")
        print(f"No landmark proposed: {summary['no_landmark_name_proposed_count']}")
        print(f"Folders with no GPS media: {summary['no_gps_media_count']}")
        print(f"Rename failures: {summary['rename_failed_count']}")
        print(f"Processed this run: {summary['processed_this_run_count']}")
        print(f"Skipped frozen applied: {summary['skipped_frozen_applied_count']}")
        print(f"Retried errors: {summary['retried_error_count']}")
        print(f"Retried no-landmark: {summary['retried_no_landmark_count']}")

        if quota_stop_reason:
            print(f"Graceful stop: {quota_stop_reason}", file=sys.stderr)

        if report.get("coverage_check_failed"):
            print("Coverage check failed: discovered folders did not reconcile with outcomes.", file=sys.stderr)
            return 1
        if report.get("interrupted"):
            interrupt_source = str(report.get("interrupt_source") or "")
            if interrupt_source.startswith("locationiq-"):
                return 1
            return 130
        if single_folder_input and len(folder_results) == 1 and folder_results[0].get("status") == "skipped-no-gps-media":
            return 1
        if summary["rename_failed_count"] > 0:
            return 1
        return 0
    except KeyboardInterrupt:
        print("Forced interruption received; exiting immediately.", file=sys.stderr)
        return 130
    finally:
        restore_signal_handlers(previous_handlers)
        _ACTIVE_SHUTDOWN_CONTROLLER = None


if __name__ == "__main__":
    raise SystemExit(main())
