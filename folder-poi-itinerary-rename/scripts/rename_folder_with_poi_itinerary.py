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
YEAR_NAME_RE = re.compile(r"^\d{4}$")
DATE_NAME_RE = re.compile(r"^(\d{4})_(\d{2})_(\d{2})$")
APP_USER_AGENT = "Lookup_POI_withlocalcache"


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
    match = DATE_NAME_RE.match(folder.name)
    if not match:
        return False

    parent_name = folder.parent.name
    return bool(YEAR_NAME_RE.match(parent_name) and parent_name == match.group(1))


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
        command_attempts = [["opencode", "--prompt", prompt], ["opencode", "-p", prompt]]
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

            if exc.code == 429 and attempt < retries:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                retry_after_sec = float(retry_after) if retry_after and retry_after.isdigit() else 0.0
                jitter = random.uniform(0.05, 0.25)
                time.sleep(max(retry_after_sec, 0.5 * (2 ** (attempt - 1)) + jitter))
                continue
            if exc.code in {404, 429}:
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

    if location_set_members and len(deduped) > max_landmark_names:
        stats: dict[str, tuple[int, int]] = {}
        for entry in location_set_members:
            raw_name = entry.get("landmark_name")
            if not isinstance(raw_name, str):
                continue
            normalized_name = normalize_label(raw_name)
            if not normalized_name:
                continue
            key = normalized_name.casefold()
            member_count = int(entry.get("set_member_count") or 0)
            itinerary_order = int(entry.get("itinerary_order") or 0)
            previous = stats.get(key)
            if previous is None:
                stats[key] = (member_count, itinerary_order)
            else:
                stats[key] = (max(previous[0], member_count), min(previous[1], itinerary_order))

        ranked = sorted(
            deduped,
            key=lambda label: (
                -stats.get(label.casefold(), (0, 10**9))[0],
                stats.get(label.casefold(), (0, 10**9))[1],
            ),
        )
        kept = {name.casefold() for name in ranked[:max_landmark_names]}
        fallback = [name for name in deduped if name.casefold() in kept][:max_landmark_names]
    else:
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

    valid_by_key = {label.casefold(): label for label in deduped}
    selected_keys: set[str] = set()
    for landmark_name in requested:
        match = valid_by_key.get(landmark_name.casefold())
        if match:
            selected_keys.add(match.casefold())
        if len(selected_keys) >= max_landmark_names:
            break

    selected = [label for label in deduped if label.casefold() in selected_keys][:max_landmark_names]
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
        location_set_members.append(
            {
                "landmark_name": normalize_label(location_set.label),
                "set_member_count": len(location_set.points),
                "itinerary_order": idx,
            }
        )
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
    opencode_timeout_sec: int,
    opencode_model: str | None,
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
    fallback = _fallback_best_label(normalized_candidates)
    if shutil.which("opencode") is None:
        return fallback

    prompt = (
        "Return exactly one best-matching travel destination name. Give preference to widely recognizable landmarks or natural attractions. "
        "Prefer specific names over generic names: 'Statue of Liberty Information Center' is good, plain 'Information Center' is too generic. "
        "Avoid generic admin/road/timezone-like names. "
        "Use normalized metrics to compare candidates across sources. "
        "Return ONLY JSON with schema {\"label\": string} and the label MUST be one from the provided candidates. "
        f"Candidates with complete metadata: {json.dumps(normalized_candidates, ensure_ascii=True)}"
    )

    command = ["opencode"]
    if opencode_model:
        command.extend(["-m", opencode_model])
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
    if not payload:
        return fallback

    raw_label = payload.get("label")
    if not isinstance(raw_label, str):
        return fallback

    picked_key = raw_label.strip().casefold()
    for candidate in normalized_candidates:
        label = candidate.get("label")
        if isinstance(label, str) and label.casefold() == picked_key:
            if not normalize_label(label):
                return fallback
            return label

    return fallback


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
    opencode_timeout_sec: int,
    opencode_model: str | None,
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
            except RuntimeError:
                reverse_payload = {}

            try:
                nearby_results = nearby_future.result()
            except RuntimeError:
                nearby_results = []

        combined_candidates = build_locationiq_candidates(nearby_results, centroid_lat, centroid_lon)
        combined_candidates.extend(build_nominatim_candidates(reverse_payload, centroid_lat, centroid_lon))

        best_label = choose_best_label_from_candidates(
            combined_candidates,
            opencode_timeout_sec=opencode_timeout_sec,
            opencode_model=opencode_model,
        )
        location_set.label = best_label or "UNKNOWN_LOCATION"


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        print(f"Folder not found: {folder}", file=sys.stderr)
        return 2

    if not is_supported_date_folder_path(folder):
        print("Skipping folder: expected path format YYYY/YYYY_MM_DD.")
        return 0

    if not args.key:
        print("Missing API key. Use --key or LOCATIONIQ_API_KEY.", file=sys.stderr)
        return 2

    if args.nominatim_requests_per_second > 1.0:
        print("Nominatim policy requires <= 1 request per second.", file=sys.stderr)
        return 2

    points = extract_media_points(folder)
    if not points:
        print("No GPS-bearing media found in folder.", file=sys.stderr)
        return 1

    seed = args.seed or folder.name
    sampled = sample_points(points, ratio=args.ratio, seed=seed)
    sets = cluster_points(sampled, threshold_m=args.event_distance_m)
    api_cache = LocalApiCache(Path(args.cache_file).expanduser().resolve())
    _assign_labels(
        sets,
        api_key=args.key,
        landmark_filter=args.landmark_filter,
        radius=args.radius,
        region=args.region,
        opencode_timeout_sec=args.opencode_timeout_sec,
        opencode_model=args.opencode_model,
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
    base_name = extract_base_date_name(folder.name)
    target_name = build_target_name(
        base_name,
        selected_labels,
        use_local_agent_compaction=args.use_local_agent_compaction,
    )

    print(f"GPS-bearing files: {len(points)}")
    print(f"Sampled files: {len(sampled)}")
    print(f"Location sets: {len(sets)}")
    print(f"Ordered landmark names: {', '.join(dedupe_labels(selected_labels))}")

    if target_name == folder.name:
        print("No rename needed.")
        return 0

    destination = _unique_destination(folder.with_name(target_name))
    print(f"Proposed name: {destination.name}")

    if not args.apply:
        print("Dry run only. Use --apply to rename.")
        return 0

    folder.rename(destination)
    print(f"Renamed to: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
