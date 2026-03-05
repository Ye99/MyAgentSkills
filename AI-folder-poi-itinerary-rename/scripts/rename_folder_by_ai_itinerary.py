#!/usr/bin/env python3
"""Rename a day folder from itinerary-ordered landmark tokens."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Callable


DATE_PREFIX_RE = re.compile(r"^(\d{4}_\d{2}_\d{2})")
DAY_FOLDER_EXACT_RE = re.compile(r"^\d{4}_\d{2}_\d{2}$")
UNKNOWN_LANDMARK = "UnknownLandmark"


class InferenceExhaustedError(RuntimeError):
    """Raised when landmark inference retries are exhausted."""

    def __init__(self, message: str, attempt_count: int, attempt_failures: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.attempt_count = attempt_count
        self.attempt_failures = attempt_failures or []


@dataclass(frozen=True)
class MediaPoint:
    source_file: str
    lat: float
    lon: float
    timestamp: datetime


@dataclass
class LocationCluster:
    points: list[MediaPoint]

    @property
    def centroid(self) -> tuple[float, float]:
        lat = sum(point.lat for point in self.points) / len(self.points)
        lon = sum(point.lon for point in self.points) / len(self.points)
        return lat, lon

    @property
    def start_time(self) -> datetime:
        return self.points[0].timestamp

    @property
    def end_time(self) -> datetime:
        return self.points[-1].timestamp


InferFunc = Callable[[float, float, datetime | None, datetime | None, int], str]


def _points_centroid(points: list[MediaPoint]) -> tuple[float, float]:
    lat = sum(point.lat for point in points) / len(points)
    lon = sum(point.lon for point in points) / len(points)
    return lat, lon


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_m * c


def parse_exif_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in (
        "%Y:%m:%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S%z",
        "%Y:%m:%d %H:%M:%S.%f",
        "%Y:%m:%d %H:%M:%S.%f%z",
    ):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    return None


def _extract_timestamp(record: dict[str, object]) -> datetime | None:
    for field in ("DateTimeOriginal", "CreateDate", "MediaCreateDate", "TrackCreateDate", "FileModifyDate"):
        value = record.get(field)
        parsed = parse_exif_datetime(value if isinstance(value, str) else None)
        if parsed is not None:
            return parsed
    return None


def extract_media_points(folder: Path) -> tuple[list[MediaPoint], list[str]]:
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
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    records = json.loads(proc.stdout)

    points: list[MediaPoint] = []
    without_gps: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        src = str(record.get("SourceFile", ""))
        lat = record.get("GPSLatitude")
        lon = record.get("GPSLongitude")
        timestamp = _extract_timestamp(record)
        if not isinstance(lat, (float, int)) or not isinstance(lon, (float, int)):
            if src:
                without_gps.append(src)
            continue
        if timestamp is None:
            timestamp = datetime.min
        points.append(MediaPoint(source_file=src, lat=float(lat), lon=float(lon), timestamp=timestamp))

    points.sort(key=lambda p: p.timestamp)
    return points, without_gps


def sample_points(points: list[MediaPoint], ratio: float) -> list[MediaPoint]:
    if not points:
        return []
    if ratio >= 1.0:
        return points
    if ratio <= 0.0:
        return []

    sample_count = max(1, int(len(points) * ratio))
    if sample_count >= len(points):
        return points
    if sample_count == 1:
        return [points[0]]

    step = (len(points) - 1) / (sample_count - 1)
    indexes: list[int] = []
    for i in range(sample_count):
        idx = round(i * step)
        if not indexes or idx != indexes[-1]:
            indexes.append(idx)
    return [points[idx] for idx in indexes]


def cluster_media_points(
    points: list[MediaPoint],
    cluster_distance_m: float,
) -> list[LocationCluster]:
    if not points:
        return []

    ordered = sorted(points, key=lambda p: p.timestamp)

    clusters: list[LocationCluster] = [LocationCluster(points=[ordered[0]])]
    for point in ordered[1:]:
        current = clusters[-1]
        centroid_lat, centroid_lon = current.centroid
        distance = haversine_m(point.lat, point.lon, centroid_lat, centroid_lon)
        if distance <= cluster_distance_m:
            current.points.append(point)
            continue
        clusters.append(LocationCluster(points=[point]))

    return clusters


def group_points_by_distance(points: list[MediaPoint], split_distance_m: float) -> list[list[MediaPoint]]:
    if not points:
        return []

    ordered = sorted(points, key=lambda p: p.timestamp)
    groups: list[list[MediaPoint]] = []

    for point in ordered:
        best_group_idx: int | None = None
        best_distance = float("inf")
        for idx, group in enumerate(groups):
            c_lat, c_lon = _points_centroid(group)
            distance = haversine_m(point.lat, point.lon, c_lat, c_lon)
            if distance < best_distance:
                best_distance = distance
                best_group_idx = idx

        if best_group_idx is None or best_distance > split_distance_m:
            groups.append([point])
            continue

        groups[best_group_idx].append(point)

    groups.sort(key=lambda group: min(point.timestamp for point in group))
    return groups


def assign_points_to_group_centroids(
    points: list[MediaPoint],
    group_centroids: list[tuple[float, float]],
) -> list[list[MediaPoint]]:
    assigned: list[list[MediaPoint]] = [[] for _ in group_centroids]
    if not group_centroids:
        return assigned

    for point in points:
        best_idx = 0
        best_distance = float("inf")
        for idx, (c_lat, c_lon) in enumerate(group_centroids):
            distance = haversine_m(point.lat, point.lon, c_lat, c_lon)
            if distance < best_distance:
                best_distance = distance
                best_idx = idx
        assigned[best_idx].append(point)
    return assigned


def parse_json_payload(stdout: str) -> dict[str, Any] | None:
    content = stdout.strip()
    if not content:
        return None

    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(content):
        if ch != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(content[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(loaded, dict):
        return loaded
    return None


def default_state_file(folder: Path) -> Path:
    return folder.parent / f".{folder.name}.ai-itinerary-state.json"


def default_report_file(folder: Path) -> Path:
    return folder.parent / f".{folder.name}.ai-itinerary-report.json"


def default_tree_state_file(root: Path) -> Path:
    return root / ".ai-itinerary-tree-state.json"


def default_tree_report_file(root: Path) -> Path:
    return root / ".ai-itinerary-tree-report.json"


def _run_opencode_with_retry(
    command: list[str],
    timeout_sec: int,
    retries: int,
    backoff_sec: float,
) -> dict[str, Any]:
    attempts = max(1, retries)
    last_error = "opencode failed"
    attempt_failures: list[dict[str, Any]] = []
    for attempt in range(1, attempts + 1):
        try:
            run_result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            if run_result.returncode == 0:
                payload = parse_json_payload(run_result.stdout)
                if payload is not None:
                    return payload
                last_error = "opencode returned non-JSON payload"
                attempt_failures.append(
                    {
                        "attempt": attempt,
                        "failure_type": "invalid-json",
                        "detail": last_error,
                    }
                )
            else:
                stderr = (run_result.stderr or "").strip()
                last_error = f"opencode exit={run_result.returncode}: {stderr}" if stderr else f"opencode exit={run_result.returncode}"
                attempt_failures.append(
                    {
                        "attempt": attempt,
                        "failure_type": "non-zero-exit",
                        "detail": last_error,
                    }
                )
        except subprocess.TimeoutExpired:
            last_error = f"opencode timeout after {timeout_sec}s"
            attempt_failures.append(
                {
                    "attempt": attempt,
                    "failure_type": "timeout",
                    "detail": last_error,
                }
            )
        except Exception as exc:  # noqa: BLE001
            last_error = f"opencode error: {exc}"
            attempt_failures.append(
                {
                    "attempt": attempt,
                    "failure_type": "exception",
                    "detail": last_error,
                }
            )

        if attempt < attempts:
            wait_sec = backoff_sec * (2 ** (attempt - 1))
            attempt_failures[-1]["wait_before_next_sec"] = wait_sec
            time.sleep(wait_sec)

    raise InferenceExhaustedError(last_error, attempt_count=attempts, attempt_failures=attempt_failures)


def normalize_landmark_token(raw_name: str) -> str:
    normalized = unicodedata.normalize("NFKD", raw_name)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[A-Za-z0-9]+", ascii_text)
    if not words:
        return UNKNOWN_LANDMARK
    token = "".join(word[:1].upper() + word[1:] for word in words)
    return token if token else UNKNOWN_LANDMARK


def normalize_country_name(raw_name: str) -> str:
    normalized = unicodedata.normalize("NFKD", raw_name)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[A-Za-z0-9]+", ascii_text)
    if not words:
        return "UnknownCountry"
    return " ".join(word[:1].upper() + word[1:].lower() for word in words)


def infer_landmark_info(
    lat: float,
    lon: float,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    sample_count: int = 0,
    opencode_timeout_sec: int = 180,
    opencode_retries: int = 5,
    opencode_backoff_sec: float = 3.0,
    opencode_model: str | None = None,
    cache: dict[tuple[float, float], dict[str, str]] | None = None,
    strict: bool = False,
) -> dict[str, str]:
    rounded = (round(lat, 4), round(lon, 4))
    if cache is not None and rounded in cache:
        return cache[rounded]

    result: dict[str, str] = {"landmark": UNKNOWN_LANDMARK, "country": "UnknownCountry"}
    if shutil.which("opencode") is not None:
        prompt = (
            "Infer one well-known landmark and country near the provided coordinates using general geographic knowledge only. "
            "Do not call any reverse geocoding service. "
            "Prefer a specific landmark over settlement/region/country names. "
            "If uncertain for landmark, return UnknownLandmark. "
            "If uncertain for country, return UnknownCountry. "
            "Return only JSON object with schema "
            '{"landmark_name":"PascalCaseTokenOrUnknownLandmark","country_name":"CountryOrUnknownCountry"}. '
            f"Coordinate: lat={lat:.6f}, lon={lon:.6f}. "
            f"Cluster sample count: {sample_count}. "
            f"Cluster start: {start_time.isoformat() if start_time else 'unknown'}. "
            f"Cluster end: {end_time.isoformat() if end_time else 'unknown'}."
        )
        command = ["opencode"]
        if opencode_model:
            command.extend(["-m", opencode_model])
        command.extend(["--variant", "medium", "run", prompt])
        try:
            payload = _run_opencode_with_retry(
                command=command,
                timeout_sec=opencode_timeout_sec,
                retries=opencode_retries,
                backoff_sec=opencode_backoff_sec,
            )
        except InferenceExhaustedError:
            if strict:
                raise
            payload = None

        if payload is not None:
            raw_landmark = payload.get("landmark_name")
            raw_country = payload.get("country_name")
            if isinstance(raw_landmark, str) and raw_landmark.strip():
                result["landmark"] = normalize_landmark_token(raw_landmark.strip())
            if isinstance(raw_country, str) and raw_country.strip():
                result["country"] = normalize_country_name(raw_country.strip())
    elif strict:
        raise InferenceExhaustedError("opencode command not found", attempt_count=0)

    if result["landmark"].casefold() in {"unknown", "unknownlandmark"}:
        result["landmark"] = UNKNOWN_LANDMARK
    if result["country"].casefold() in {"unknown", "unknowncountry"}:
        result["country"] = "UnknownCountry"

    if cache is not None:
        cache[rounded] = dict(result)
    return result


def infer_landmark_token(
    lat: float,
    lon: float,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    sample_count: int = 0,
    opencode_timeout_sec: int = 180,
    opencode_retries: int = 5,
    opencode_backoff_sec: float = 3.0,
    opencode_model: str | None = None,
    cache: dict[tuple[float, float], str] | None = None,
) -> str:
    rounded = (round(lat, 4), round(lon, 4))
    if cache is not None and rounded in cache:
        return cache[rounded]

    info_cache: dict[tuple[float, float], dict[str, str]] | None = None
    if cache is not None:
        info_cache = {}
        for key, value in cache.items():
            info_cache[key] = {"landmark": value, "country": "UnknownCountry"}

    info = infer_landmark_info(
        lat,
        lon,
        start_time=start_time,
        end_time=end_time,
        sample_count=sample_count,
        opencode_timeout_sec=opencode_timeout_sec,
        opencode_retries=opencode_retries,
        opencode_backoff_sec=opencode_backoff_sec,
        opencode_model=opencode_model,
        cache=info_cache,
        strict=False,
    )
    token = info.get("landmark", UNKNOWN_LANDMARK)

    if token.casefold() in {"unknown", "unknownlandmark"}:
        token = UNKNOWN_LANDMARK

    if cache is not None:
        cache[rounded] = token
    return token


def build_itinerary_landmarks(
    points: list[MediaPoint],
    infer_func: InferFunc | None = None,
    cluster_distance_m: float = 2_000.0,
    opencode_timeout_sec: int = 180,
    opencode_retries: int = 5,
    opencode_backoff_sec: float = 3.0,
    opencode_model: str | None = None,
) -> list[str]:
    clusters = cluster_media_points(points, cluster_distance_m=cluster_distance_m)
    if infer_func is None:
        cache: dict[tuple[float, float], str] = {}

        def _default_infer(
            lat: float,
            lon: float,
            start_time: datetime | None,
            end_time: datetime | None,
            sample_count: int,
        ) -> str:
            return infer_landmark_token(
                lat,
                lon,
                start_time=start_time,
                end_time=end_time,
                sample_count=sample_count,
                opencode_timeout_sec=opencode_timeout_sec,
                opencode_retries=opencode_retries,
                opencode_backoff_sec=opencode_backoff_sec,
                opencode_model=opencode_model,
                cache=cache,
            )

        infer_func = _default_infer

    tokens: list[str] = []
    seen: set[str] = set()
    for cluster in clusters:
        centroid_lat, centroid_lon = cluster.centroid
        token = infer_func(
            centroid_lat,
            centroid_lon,
            cluster.start_time,
            cluster.end_time,
            len(cluster.points),
        )
        if not token or token == UNKNOWN_LANDMARK:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def group_clusters_by_country(
    cluster_infos: list[tuple[LocationCluster, dict[str, str]]],
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    index_by_country: dict[str, int] = {}

    for cluster, info in cluster_infos:
        country = info.get("country") or "UnknownCountry"
        idx = index_by_country.get(country)
        if idx is None:
            idx = len(groups)
            index_by_country[country] = idx
            groups.append({"country": country, "clusters": []})
        groups[idx]["clusters"].append((cluster, info))

    return groups


def select_top_landmarks_by_count(
    landmark_counts: dict[str, int],
    first_seen_order: dict[str, int],
    max_landmarks: int,
) -> list[str]:
    if max_landmarks <= 0:
        return []
    ranked = sorted(
        landmark_counts.items(),
        key=lambda item: (-item[1], first_seen_order.get(item[0], 10**9)),
    )
    return [name for name, _count in ranked[:max_landmarks]]


def rank_landmarks_by_location_set_size(
    reference_clusters: list[tuple[LocationCluster, dict[str, str]]],
    full_clusters: list[LocationCluster],
    max_landmarks: int,
) -> list[str]:
    if not reference_clusters:
        return []

    references: list[tuple[float, float, str, int, int]] = []
    first_seen_order: dict[str, int] = {}
    for idx, (cluster, info) in enumerate(reference_clusters):
        landmark = info.get("landmark", UNKNOWN_LANDMARK)
        if not landmark or landmark == UNKNOWN_LANDMARK:
            continue
        c_lat, c_lon = cluster.centroid
        references.append((c_lat, c_lon, landmark, idx, len(cluster.points)))
        if landmark not in first_seen_order:
            first_seen_order[landmark] = idx

    if not references:
        return []

    counts: dict[str, int] = {}
    if full_clusters:
        for cluster in full_clusters:
            full_lat, full_lon = cluster.centroid
            best_ref = min(
                references,
                key=lambda ref: haversine_m(full_lat, full_lon, ref[0], ref[1]),
            )
            landmark = best_ref[2]
            counts[landmark] = counts.get(landmark, 0) + len(cluster.points)
    else:
        for _lat, _lon, landmark, _idx, sampled_size in references:
            counts[landmark] = counts.get(landmark, 0) + sampled_size

    return select_top_landmarks_by_count(counts, first_seen_order, max_landmarks)


def build_target_folder_name(date_prefix: str, landmarks: list[str]) -> str:
    if not landmarks:
        return date_prefix
    return f"{date_prefix}_{','.join(landmarks)}"


def find_available_target(source_folder: Path, target_name: str) -> Path:
    candidate = source_folder.parent / target_name
    if not candidate.exists() or candidate == source_folder:
        return candidate

    suffix = 2
    while True:
        numbered = source_folder.parent / f"{target_name}_{suffix}"
        if not numbered.exists():
            return numbered
        suffix += 1


def _unique_target_name(target_name: str, used_names: set[str]) -> str:
    if target_name not in used_names:
        used_names.add(target_name)
        return target_name

    suffix = 2
    while True:
        candidate = f"{target_name}_{suffix}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        suffix += 1


def _safe_move_media_file(source_file: Path, source_root: Path, target_root: Path) -> Path:
    try:
        relative = source_file.relative_to(source_root)
    except ValueError:
        relative = Path(source_file.name)

    destination = target_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        source_file.rename(destination)
        return destination

    stem = destination.stem
    suffix = destination.suffix
    counter = 2
    while True:
        candidate = destination.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            source_file.rename(candidate)
            return candidate
        counter += 1


def _date_prefix_from_folder(folder: Path) -> str:
    match = DATE_PREFIX_RE.match(folder.name)
    if not match:
        raise ValueError("Folder name must start with YYYY_MM_DD")
    return match.group(1)


def discover_day_folders(root: Path) -> list[Path]:
    discovered: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_dir():
            continue
        if DAY_FOLDER_EXACT_RE.match(path.name):
            discovered.append(path)
    return discovered


def _status_category(status: str) -> str:
    if status in {"renamed", "split-renamed"}:
        return "renamed"
    if status in {"planned-rename", "planned-split"}:
        return "planned"
    if status.startswith("failed"):
        return "failed"
    if status.startswith("skipped"):
        return "skipped"
    return "other"


def verify_tree_integrity(folder_results: list[dict[str, Any]], apply: bool) -> dict[str, Any]:
    total_folder_count = len(folder_results)
    category_counts: dict[str, int] = {"renamed": 0, "planned": 0, "failed": 0, "skipped": 0, "other": 0}

    for result in folder_results:
        status = str(result.get("status") or "")
        category = _status_category(status)
        category_counts[category] = category_counts.get(category, 0) + 1

    accounted_folder_count = sum(category_counts.values())
    math_logic_ok = accounted_folder_count == total_folder_count

    expected_target_folder_count = 0
    observed_target_folder_count = 0
    if apply:
        for result in folder_results:
            status = str(result.get("status") or "")
            folder_path_raw = result.get("folder_path")
            if not isinstance(folder_path_raw, str):
                continue
            folder_path = Path(folder_path_raw)

            if status == "renamed":
                target_name = result.get("target_name")
                if isinstance(target_name, str) and target_name:
                    expected_target_folder_count += 1
                    if (folder_path.parent / target_name).exists():
                        observed_target_folder_count += 1
                continue

            if status == "split-renamed":
                split_folders = result.get("split_folders")
                if not isinstance(split_folders, list):
                    continue
                for entry in split_folders:
                    if not isinstance(entry, dict):
                        continue
                    target_name = entry.get("target_name")
                    if not isinstance(target_name, str) or not target_name:
                        continue
                    expected_target_folder_count += 1
                    if (folder_path.parent / target_name).exists():
                        observed_target_folder_count += 1

    target_folder_count_ok = (not apply) or (expected_target_folder_count == observed_target_folder_count)
    passed = math_logic_ok and target_folder_count_ok

    return {
        "passed": passed,
        "math_logic_ok": math_logic_ok,
        "target_folder_count_ok": target_folder_count_ok,
        "total_folder_count": total_folder_count,
        "accounted_folder_count": accounted_folder_count,
        "renamed_folder_count": category_counts["renamed"],
        "planned_folder_count": category_counts["planned"],
        "failed_folder_count": category_counts["failed"],
        "skipped_folder_count": category_counts["skipped"],
        "other_folder_count": category_counts["other"],
        "expected_target_folder_count": expected_target_folder_count,
        "observed_target_folder_count": observed_target_folder_count,
    }


def process_folder_tree(
    root: Path,
    apply: bool,
    ratio: float = 1.0,
    cluster_distance_m: float = 2_000.0,
    max_landmarks: int = 8,
    opencode_timeout_sec: int = 180,
    opencode_retries: int = 5,
    opencode_backoff_sec: float = 3.0,
    opencode_model: str | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    day_folders = discover_day_folders(root)
    folder_results: list[dict[str, Any]] = []

    for folder in day_folders:
        result = rename_folder_from_itinerary(
            folder=folder,
            apply=apply,
            ratio=ratio,
            cluster_distance_m=cluster_distance_m,
            max_landmarks=max_landmarks,
            opencode_timeout_sec=opencode_timeout_sec,
            opencode_retries=opencode_retries,
            opencode_backoff_sec=opencode_backoff_sec,
            opencode_model=opencode_model,
            state_file=default_state_file(folder),
            report_file=default_report_file(folder),
            resume=resume,
        )
        if isinstance(result, dict):
            folder_results.append(result)

    integrity_check = verify_tree_integrity(folder_results, apply=apply)

    summary_status = "completed"
    if not integrity_check["passed"]:
        summary_status = "failed-integrity"
    elif integrity_check["failed_folder_count"] > 0:
        summary_status = "completed-with-failures"

    failed_folders: list[dict[str, Any]] = []
    for result in folder_results:
        status = str(result.get("status") or "")
        if not status.startswith("failed"):
            continue
        failed_folders.append(
            {
                "folder_path": result.get("folder_path"),
                "status": status,
                "state_file": result.get("state_file"),
                "report_file": result.get("report_file"),
            }
        )

    summary: dict[str, Any] = {
        "root_path": str(root),
        "status": summary_status,
        "total_folder_count": integrity_check["total_folder_count"],
        "renamed_folder_count": integrity_check["renamed_folder_count"],
        "planned_folder_count": integrity_check["planned_folder_count"],
        "failed_folder_count": integrity_check["failed_folder_count"],
        "skipped_folder_count": integrity_check["skipped_folder_count"],
        "other_folder_count": integrity_check["other_folder_count"],
        "integrity_check": integrity_check,
        "failed_folders": failed_folders,
        "tree_state_file": str(default_tree_state_file(root)),
        "tree_report_file": str(default_tree_report_file(root)),
    }

    tree_state_payload: dict[str, Any] = {
        "root_path": str(root),
        "status": summary_status,
        "config": {
            "ratio": ratio,
            "cluster_distance_m": cluster_distance_m,
            "max_landmarks": max_landmarks,
            "opencode_timeout_sec": opencode_timeout_sec,
            "opencode_max_attempts": opencode_retries,
            "opencode_initial_backoff_sec": opencode_backoff_sec,
        },
        "total_folder_count": integrity_check["total_folder_count"],
        "integrity_check": integrity_check,
        "folder_results": folder_results,
    }
    write_json_file(default_tree_state_file(root), tree_state_payload)

    tree_report_payload: dict[str, Any] = {
        "root_path": str(root),
        "status": summary_status,
        "total_folder_count": integrity_check["total_folder_count"],
        "renamed_folder_count": integrity_check["renamed_folder_count"],
        "planned_folder_count": integrity_check["planned_folder_count"],
        "failed_folder_count": integrity_check["failed_folder_count"],
        "skipped_folder_count": integrity_check["skipped_folder_count"],
        "integrity_check": {
            "passed": integrity_check["passed"],
            "math_logic_ok": integrity_check["math_logic_ok"],
            "target_folder_count_ok": integrity_check["target_folder_count_ok"],
        },
        "failed_folders": failed_folders,
        "tree_state_file": str(default_tree_state_file(root)),
    }
    write_json_file(default_tree_report_file(root), tree_report_payload)

    return summary


def rename_folder_from_itinerary(
    folder: Path,
    apply: bool,
    ratio: float = 1.0,
    cluster_distance_m: float = 2_000.0,
    max_landmarks: int = 8,
    opencode_timeout_sec: int = 180,
    opencode_retries: int = 5,
    opencode_backoff_sec: float = 3.0,
    opencode_model: str | None = None,
    state_file: Path | None = None,
    report_file: Path | None = None,
    resume: bool = True,
) -> dict[str, object]:
    date_prefix = _date_prefix_from_folder(folder)
    if state_file is None:
        state_file = default_state_file(folder)
    if report_file is None:
        report_file = default_report_file(folder)

    current_config: dict[str, Any] = {
        "ratio": ratio,
        "cluster_distance_m": cluster_distance_m,
        "max_landmarks": max_landmarks,
    }

    points, without_gps = extract_media_points(folder)
    sampled_points = sample_points(points, ratio=ratio)

    sampled_clusters = cluster_media_points(sampled_points, cluster_distance_m=cluster_distance_m)
    previous_state = read_json_file(state_file) if resume else None
    completed_infos: list[dict[str, str]] = []
    persistent_failure_log: list[dict[str, Any]] = []
    if previous_state is not None:
        if (
            previous_state.get("folder_path") == str(folder)
            and previous_state.get("config") == current_config
        ):
            raw_infos = previous_state.get("completed_cluster_infos")
            if isinstance(raw_infos, list):
                for item in raw_infos:
                    if isinstance(item, dict):
                        landmark = item.get("landmark")
                        country = item.get("country")
                        if isinstance(landmark, str) and isinstance(country, str):
                            completed_infos.append({"landmark": landmark, "country": country})
            raw_persistent_log = previous_state.get("persistent_failure_log")
            if isinstance(raw_persistent_log, list):
                for item in raw_persistent_log:
                    if isinstance(item, dict):
                        persistent_failure_log.append(item)

    info_cache: dict[tuple[float, float], dict[str, str]] = {}
    cluster_infos: list[tuple[LocationCluster, dict[str, str]]] = []
    for idx, cluster in enumerate(sampled_clusters):
        if idx < len(completed_infos):
            cluster_infos.append((cluster, completed_infos[idx]))
            continue

        c_lat, c_lon = cluster.centroid
        try:
            info = infer_landmark_info(
                c_lat,
                c_lon,
                start_time=cluster.start_time,
                end_time=cluster.end_time,
                sample_count=len(cluster.points),
                opencode_timeout_sec=opencode_timeout_sec,
                opencode_retries=opencode_retries,
                opencode_backoff_sec=opencode_backoff_sec,
                opencode_model=opencode_model,
                cache=info_cache,
                strict=True,
            )
        except InferenceExhaustedError as exc:
            failure_entry: dict[str, Any] = {
                "failed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "cluster_index": idx,
                "cluster_centroid": {"lat": c_lat, "lon": c_lon},
                "cluster_start": cluster.start_time.isoformat(),
                "cluster_end": cluster.end_time.isoformat(),
                "cluster_sample_count": len(cluster.points),
                "error_message": str(exc),
                "attempt_count": exc.attempt_count,
                "attempt_failures": exc.attempt_failures,
            }
            persistent_failure_log.append(failure_entry)

            state_payload: dict[str, Any] = {
                "folder_path": str(folder),
                "status": "failed-inference",
                "config": current_config,
                "next_cluster_index": idx,
                "completed_cluster_infos": [info for _cluster, info in cluster_infos],
                "persistent_failure_count": len(persistent_failure_log),
                "persistent_failure_log": persistent_failure_log,
                "error": {
                    "message": str(exc),
                    "attempt_count": exc.attempt_count,
                },
            }
            write_json_file(state_file, state_payload)

            persistent_failure_summary: dict[str, Any] = {
                "persistent_failure_count": len(persistent_failure_log),
                "last_failed_cluster_index": idx,
                "last_error_message": str(exc),
                "last_error_attempt_count": exc.attempt_count,
            }
            report_payload: dict[str, Any] = {
                "status": "failed-inference",
                "folder_path": str(folder),
                "state_file": str(state_file),
                "next_cluster_index": idx,
                "error": state_payload["error"],
                "persistent_failure_summary": persistent_failure_summary,
            }
            write_json_file(report_file, report_payload)
            return {
                "folder_path": str(folder),
                "status": "failed-inference",
                "state_file": str(state_file),
                "report_file": str(report_file),
                "next_cluster_index": idx,
                "persistent_failure_count": len(persistent_failure_log),
                "media_with_gps_count": len(points),
                "media_with_gps_sampled": len(sampled_points),
                "sample_ratio_requested": ratio,
                "media_without_gps_count": len(without_gps),
                "used_reverse_geocoding": False,
            }

        cluster_infos.append((cluster, info))
        state_payload = {
            "folder_path": str(folder),
            "status": "in-progress",
            "config": current_config,
            "next_cluster_index": idx + 1,
            "completed_cluster_infos": [info for _cluster, info in cluster_infos],
            "persistent_failure_count": len(persistent_failure_log),
            "persistent_failure_log": persistent_failure_log,
        }
        write_json_file(state_file, state_payload)

    country_groups = group_clusters_by_country(cluster_infos)
    if len(country_groups) > 1:
        full_clusters = cluster_media_points(points, cluster_distance_m=cluster_distance_m)
        country_centroids: list[tuple[float, float]] = []
        for group in country_groups:
            group_points: list[MediaPoint] = []
            for sampled_cluster, _info in group["clusters"]:
                group_points.extend(sampled_cluster.points)
            country_centroids.append(_points_centroid(group_points))

        assigned_full_clusters: list[list[LocationCluster]] = [[] for _ in country_groups]
        for full_cluster in full_clusters:
            full_lat, full_lon = full_cluster.centroid
            best_idx = 0
            best_distance = float("inf")
            for idx, (c_lat, c_lon) in enumerate(country_centroids):
                distance = haversine_m(full_lat, full_lon, c_lat, c_lon)
                if distance < best_distance:
                    best_distance = distance
                    best_idx = idx
            assigned_full_clusters[best_idx].append(full_cluster)

        used_target_names: set[str] = set()
        split_folders: list[dict[str, object]] = []
        moved_file_count = 0

        for idx, group in enumerate(country_groups, start=1):
            landmarks = rank_landmarks_by_location_set_size(
                reference_clusters=group["clusters"],
                full_clusters=assigned_full_clusters[idx - 1],
                max_landmarks=max_landmarks,
            )
            if not landmarks:
                landmarks = [f"UnknownSegment{idx}"]

            target_name = build_target_folder_name(date_prefix, landmarks)
            target_name = _unique_target_name(target_name, used_target_names)
            target_path = find_available_target(folder, target_name)

            media_with_gps_count = sum(len(cluster.points) for cluster in assigned_full_clusters[idx - 1])
            sampled_gps_count = sum(len(cluster.points) for cluster, _info in group["clusters"])

            entry: dict[str, object] = {
                "target_name": target_path.name,
                "country": group["country"],
                "landmarks": landmarks,
                "sampled_gps_count": sampled_gps_count,
                "media_with_gps_count": media_with_gps_count,
            }

            if apply:
                target_path.mkdir(parents=True, exist_ok=True)
                for full_cluster in assigned_full_clusters[idx - 1]:
                    for point in full_cluster.points:
                        source_file = Path(point.source_file)
                        if not source_file.exists():
                            continue
                        _safe_move_media_file(source_file, folder, target_path)
                        moved_file_count += 1

            split_folders.append(entry)

        status = "planned-split"
        if apply:
            status = "split-renamed"
            try:
                if folder.exists() and not any(folder.iterdir()):
                    folder.rmdir()
            except OSError:
                pass

        completion_state: dict[str, Any] = {
            "folder_path": str(folder),
            "status": "completed",
            "config": current_config,
            "completed_cluster_infos": [info for _cluster, info in cluster_infos],
            "persistent_failure_count": len(persistent_failure_log),
            "persistent_failure_log": persistent_failure_log,
            "result_status": status,
        }
        write_json_file(state_file, completion_state)
        write_json_file(
            report_file,
            {
                "status": status,
                "folder_path": str(folder),
                "state_file": str(state_file),
                "split_folder_count": len(split_folders),
                "persistent_failure_summary": {
                    "persistent_failure_count": len(persistent_failure_log),
                },
            },
        )

        return {
            "folder_path": str(folder),
            "status": status,
            "split_folders": split_folders,
            "media_with_gps_count": len(points),
            "media_with_gps_sampled": len(sampled_points),
            "sample_ratio_requested": ratio,
            "media_without_gps_count": len(without_gps),
            "moved_media_with_gps_count": moved_file_count,
            "used_reverse_geocoding": False,
        }

    full_clusters = cluster_media_points(points, cluster_distance_m=cluster_distance_m)
    landmarks = rank_landmarks_by_location_set_size(
        reference_clusters=cluster_infos,
        full_clusters=full_clusters,
        max_landmarks=max_landmarks,
    )
    target_name = build_target_folder_name(date_prefix, landmarks)
    target_path = find_available_target(folder, target_name)

    status = "planned-rename"
    if not landmarks:
        status = "skipped-no-landmark"
    elif apply and target_path != folder:
        folder.rename(target_path)
        status = "renamed"

    completion_state = {
        "folder_path": str(folder),
        "status": "completed",
        "config": current_config,
        "completed_cluster_infos": [info for _cluster, info in cluster_infos],
        "persistent_failure_count": len(persistent_failure_log),
        "persistent_failure_log": persistent_failure_log,
        "result_status": status,
        "target_name": target_path.name,
    }
    write_json_file(state_file, completion_state)
    write_json_file(
        report_file,
        {
            "status": status,
            "folder_path": str(folder),
            "state_file": str(state_file),
            "target_name": target_path.name,
            "persistent_failure_summary": {
                "persistent_failure_count": len(persistent_failure_log),
            },
        },
    )

    return {
        "folder_path": str(folder),
        "status": status,
        "target_name": target_path.name,
        "landmarks": landmarks,
        "media_with_gps_count": len(points),
        "media_with_gps_sampled": len(sampled_points),
        "sample_ratio_requested": ratio,
        "media_without_gps_count": len(without_gps),
        "used_reverse_geocoding": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rename folder using AI-inferred itinerary landmarks")
    parser.add_argument("folder", help="Path to day folder (YYYY_MM_DD...) or tree root containing day folders")
    parser.add_argument("--apply", action="store_true", help="Apply rename instead of dry-run")
    parser.add_argument("--ratio", type=float, default=1.0, help="Sampling ratio for GPS media (0-1], default 1.0")
    parser.add_argument(
        "--cluster-distance-m",
        type=float,
        default=2_000.0,
        help="Distance threshold in meters for itinerary clustering",
    )
    parser.add_argument(
        "--opencode-timeout-sec",
        type=int,
        default=180,
        help="Timeout in seconds for each opencode landmark inference call",
    )
    parser.add_argument(
        "--opencode-max-attempts",
        type=int,
        default=5,
        help="Retry attempts for opencode landmark inference",
    )
    parser.add_argument(
        "--opencode-initial-backoff-sec",
        type=float,
        default=3.0,
        help="Initial backoff seconds (exponential) between opencode retries",
    )
    parser.add_argument(
        "--max-landmarks",
        type=int,
        default=8,
        help="Maximum number of landmarks kept in final day folder name",
    )
    parser.add_argument(
        "--state-file",
        help="Optional path to processing state JSON file",
    )
    parser.add_argument(
        "--report-file",
        help="Optional path to processing report JSON file",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Do not resume from existing state file",
    )
    parser.add_argument(
        "--opencode-model",
        default=os.getenv("OPENCODE_MODEL"),
        help="Model passed to opencode -m (defaults to OPENCODE_MODEL)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if DATE_PREFIX_RE.match(folder.name):
        result = rename_folder_from_itinerary(
            folder,
            apply=args.apply,
            ratio=args.ratio,
            cluster_distance_m=args.cluster_distance_m,
            max_landmarks=args.max_landmarks,
            opencode_timeout_sec=args.opencode_timeout_sec,
            opencode_retries=args.opencode_max_attempts,
            opencode_backoff_sec=args.opencode_initial_backoff_sec,
            opencode_model=args.opencode_model,
            state_file=Path(args.state_file).expanduser().resolve() if args.state_file else None,
            report_file=Path(args.report_file).expanduser().resolve() if args.report_file else None,
            resume=not args.no_resume,
        )
    else:
        result = process_folder_tree(
            root=folder,
            apply=args.apply,
            ratio=args.ratio,
            cluster_distance_m=args.cluster_distance_m,
            max_landmarks=args.max_landmarks,
            opencode_timeout_sec=args.opencode_timeout_sec,
            opencode_retries=args.opencode_max_attempts,
            opencode_backoff_sec=args.opencode_initial_backoff_sec,
            opencode_model=args.opencode_model,
            resume=not args.no_resume,
        )
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
