#!/usr/bin/env python3
"""Rename a media folder by itinerary-ordered POI labels."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


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


def _street_from_address(address: dict[str, Any]) -> str | None:
    street_keys = ["road", "pedestrian", "footway", "path", "residential", "suburb"]
    for key in street_keys:
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
        address = poi.get("address") if isinstance(poi.get("address"), dict) else {}
        city = _city_from_address(address)
        if city:
            return city

    for poi in poi_results:
        address = poi.get("address") if isinstance(poi.get("address"), dict) else {}
        street = _street_from_address(address)
        if street:
            return street

    for poi in poi_results:
        name = poi.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()

    return None


def normalize_label(label: str) -> str:
    ascii_label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", ascii_label).strip("_")
    return re.sub(r"_+", "_", cleaned).upper()


def dedupe_labels(labels: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for label in labels:
        normalized = normalize_label(label)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def labels_in_itinerary_order(sets: list[LocationSet]) -> list[str]:
    ordered = sorted(sets, key=lambda location_set: min(p.timestamp for p in location_set.points))
    return [location_set.label for location_set in ordered if location_set.label]


def build_target_name(base_name: str, labels: list[str]) -> str:
    deduped = dedupe_labels(labels)
    if not deduped:
        return base_name
    return f"{base_name}_{'_'.join(deduped)}"


def fetch_nearby_poi(
    api_key: str,
    lat: float,
    lon: float,
    tag: str,
    radius: int,
    region: str,
    retries: int = 3,
) -> list[dict[str, Any]]:
    params = {
        "key": api_key,
        "lat": lat,
        "lon": lon,
        "tag": tag,
        "radius": radius,
        "format": "json",
    }
    endpoint = f"https://{region}.locationiq.com/v1/nearby?{urlencode(params)}"

    attempt = 0
    while True:
        attempt += 1
        try:
            with urlopen(endpoint, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, list):
                return payload
            return []
        except HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                time.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LocationIQ HTTP {exc.code}: {message}") from exc
        except URLError as exc:
            if attempt < retries:
                time.sleep(0.5 * (2 ** (attempt - 1)))
                continue
            raise RuntimeError(f"LocationIQ network error: {exc.reason}") from exc


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
    tag: str,
    radius: int,
    region: str,
) -> None:
    for location_set in sets:
        centroid_lat, centroid_lon = _cluster_centroid(location_set)
        try:
            poi_results = fetch_nearby_poi(
                api_key=api_key,
                lat=centroid_lat,
                lon=centroid_lon,
                tag=tag,
                radius=radius,
                region=region,
            )
        except RuntimeError:
            poi_results = []
        location_set.label = choose_preferred_label(poi_results) or "UNKNOWN_LOCATION"


def main() -> int:
    parser = argparse.ArgumentParser(description="Append itinerary POI labels to a media folder name")
    parser.add_argument("folder", help="Path to media folder")
    parser.add_argument("--key", default=os.getenv("LOCATIONIQ_API_KEY"), help="LocationIQ API key")
    parser.add_argument("--ratio", type=float, default=0.6, help="Sampling ratio of GPS-bearing files")
    parser.add_argument("--threshold-m", type=float, default=300.0, help="Distance threshold for location sets")
    parser.add_argument("--tag", default="all", help="Nearby API tag filter")
    parser.add_argument("--radius", type=int, default=1000, help="Nearby API search radius in meters")
    parser.add_argument("--region", default="us1", choices=["us1", "eu1"], help="LocationIQ region")
    parser.add_argument("--seed", default=None, help="Optional deterministic sampling seed")
    parser.add_argument("--apply", action="store_true", help="Apply folder rename")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        print(f"Folder not found: {folder}", file=sys.stderr)
        return 2

    if not args.key:
        print("Missing API key. Use --key or LOCATIONIQ_API_KEY.", file=sys.stderr)
        return 2

    points = extract_media_points(folder)
    if not points:
        print("No GPS-bearing media found in folder.", file=sys.stderr)
        return 1

    seed = args.seed or folder.name
    sampled = sample_points(points, ratio=args.ratio, seed=seed)
    sets = cluster_points(sampled, threshold_m=args.threshold_m)
    _assign_labels(sets, api_key=args.key, tag=args.tag, radius=args.radius, region=args.region)

    ordered_labels = labels_in_itinerary_order(sets)
    target_name = build_target_name(folder.name, ordered_labels)

    print(f"GPS-bearing files: {len(points)}")
    print(f"Sampled files: {len(sampled)}")
    print(f"Location sets: {len(sets)}")
    print(f"Ordered labels: {', '.join(dedupe_labels(ordered_labels))}")

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
