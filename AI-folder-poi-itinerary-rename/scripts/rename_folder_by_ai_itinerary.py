#!/usr/bin/env python3
"""Rename a day folder from itinerary-ordered landmark tokens."""

from __future__ import annotations

import argparse
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
import queue
import socket
import threading
import urllib.error
import urllib.request
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from dataclasses import field
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


@dataclass
class OpencodeServerHandle:
    worker_id: int
    port: int
    url: str
    process: subprocess.Popen[str] | None


def _http_json_request(
    *,
    method: str,
    url: str,
    body: dict[str, Any] | None,
    timeout_sec: int,
) -> tuple[int, dict[str, Any]]:
    data = None
    headers: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["content-type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        status = int(response.status)
        raw = response.read().decode("utf-8", "ignore")
    if not raw:
        return status, {}
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        return status, parsed
    return status, {}


def _parse_http_model(opencode_model: str | None) -> dict[str, str] | None:
    if not opencode_model:
        return None
    if "/" not in opencode_model:
        return None
    provider_id, model_id = opencode_model.split("/", 1)
    provider_id = provider_id.strip()
    model_id = model_id.strip()
    if not provider_id or not model_id:
        return None
    return {"providerID": provider_id, "modelID": model_id}


def _create_http_session(server_url: str, timeout_sec: int) -> str:
    _status, payload = _http_json_request(
        method="POST",
        url=f"{server_url}/session",
        body={},
        timeout_sec=timeout_sec,
    )
    session_id = payload.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("session create returned no id")
    return session_id


def _run_opencode_http_with_retry(
    *,
    server_url: str,
    prompt: str,
    timeout_sec: int,
    retries: int,
    backoff_sec: float,
    session_id: str | None,
    opencode_model: str | None,
    attempt_report: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    attempts = max(1, retries)
    last_error = "opencode http failed"
    attempt_failures: list[dict[str, Any]] = []
    current_session_id = session_id
    parsed_model = _parse_http_model(opencode_model)

    for attempt in range(1, attempts + 1):
        try:
            if not current_session_id:
                current_session_id = _create_http_session(server_url, timeout_sec=timeout_sec)

            request_payload: dict[str, Any] = {
                "variant": "medium",
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "landmark_name": {"type": "string"},
                            "country_name": {"type": "string"},
                        },
                        "required": ["landmark_name", "country_name"],
                        "additionalProperties": False,
                    },
                },
                "parts": [
                    {
                        "type": "text",
                        "text": prompt,
                    }
                ],
            }
            if parsed_model is not None:
                request_payload["model"] = parsed_model

            _status, response_payload = _http_json_request(
                method="POST",
                url=f"{server_url}/session/{current_session_id}/message",
                body=request_payload,
                timeout_sec=timeout_sec,
            )
            info = response_payload.get("info")
            if isinstance(info, dict):
                structured = info.get("structured")
                if isinstance(structured, dict):
                    if attempt_report is not None:
                        attempt_report["attempt_count"] = attempt
                        attempt_report["attempt_failures"] = list(attempt_failures)
                    return structured, current_session_id

            parts = response_payload.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict) or part.get("type") != "text":
                        continue
                    text = part.get("text")
                    if not isinstance(text, str):
                        continue
                    payload = parse_json_payload(text)
                    if payload is not None:
                        if attempt_report is not None:
                            attempt_report["attempt_count"] = attempt
                            attempt_report["attempt_failures"] = list(attempt_failures)
                        return payload, current_session_id

            last_error = "opencode http returned non-JSON payload"
            attempt_failures.append(
                {
                    "attempt": attempt,
                    "failure_type": "invalid-json",
                    "detail": last_error,
                }
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore").strip()
            last_error = f"opencode http status={exc.code}: {detail}" if detail else f"opencode http status={exc.code}"
            attempt_failures.append(
                {
                    "attempt": attempt,
                    "failure_type": "http-error",
                    "detail": last_error,
                }
            )
            if exc.code in {404, 410}:
                current_session_id = None
        except TimeoutError:
            last_error = f"opencode http timeout after {timeout_sec}s"
            attempt_failures.append(
                {
                    "attempt": attempt,
                    "failure_type": "timeout",
                    "detail": last_error,
                }
            )
        except urllib.error.URLError as exc:
            last_error = f"opencode http error: {exc}"
            attempt_failures.append(
                {
                    "attempt": attempt,
                    "failure_type": "network",
                    "detail": last_error,
                }
            )
        except Exception as exc:  # noqa: BLE001
            last_error = f"opencode http error: {exc}"
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


@dataclass(frozen=True)
class MediaPoint:
    source_file: str
    lat: float
    lon: float
    timestamp: datetime


@dataclass
class LocationCluster:
    points: list[MediaPoint]
    lat_sum: float = field(init=False, repr=False)
    lon_sum: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.lat_sum = sum(point.lat for point in self.points)
        self.lon_sum = sum(point.lon for point in self.points)

    def add_point(self, point: MediaPoint) -> None:
        self.points.append(point)
        self.lat_sum += point.lat
        self.lon_sum += point.lon

    @property
    def centroid(self) -> tuple[float, float]:
        lat = self.lat_sum / len(self.points)
        lon = self.lon_sum / len(self.points)
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
        raw_source = record.get("SourceFile")
        src = raw_source.strip() if isinstance(raw_source, str) else ""
        if not src:
            continue
        lat = record.get("GPSLatitude")
        lon = record.get("GPSLongitude")
        timestamp = _extract_timestamp(record)
        if not isinstance(lat, (float, int)) or not isinstance(lon, (float, int)):
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


def build_input_fingerprint(points: list[MediaPoint], without_gps: list[str]) -> str:
    digest = hashlib.sha256()
    sortable_points = sorted(points, key=lambda p: (p.source_file, p.timestamp.isoformat(), p.lat, p.lon))
    for point in sortable_points:
        digest.update(point.source_file.encode("utf-8"))
        digest.update(b"\n")
        digest.update(f"{point.lat:.6f},{point.lon:.6f}".encode("ascii"))
        digest.update(b"\n")
        digest.update(point.timestamp.isoformat().encode("ascii"))
        digest.update(b"\n")
    for source in sorted(without_gps):
        digest.update(source.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def cluster_media_points(
    points: list[MediaPoint],
    cluster_distance_m: float,
    already_sorted: bool = False,
) -> list[LocationCluster]:
    if not points:
        return []

    ordered = points if already_sorted else sorted(points, key=lambda p: p.timestamp)

    clusters: list[LocationCluster] = [LocationCluster(points=[ordered[0]])]
    for point in ordered[1:]:
        current = clusters[-1]
        centroid_lat, centroid_lon = current.centroid
        distance = haversine_m(point.lat, point.lon, centroid_lat, centroid_lon)
        if distance <= cluster_distance_m:
            current.add_point(point)
            continue
        clusters.append(LocationCluster(points=[point]))

    return clusters


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


_OPENCODE_AVAILABLE: bool | None = None


def has_opencode_command() -> bool:
    global _OPENCODE_AVAILABLE
    if _OPENCODE_AVAILABLE is None:
        _OPENCODE_AVAILABLE = shutil.which("opencode") is not None
    return _OPENCODE_AVAILABLE


def _run_opencode_with_retry(
    command: list[str],
    timeout_sec: int,
    retries: int,
    backoff_sec: float,
    attempt_report: dict[str, Any] | None = None,
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
                    if attempt_report is not None:
                        attempt_report["attempt_count"] = attempt
                        attempt_report["attempt_failures"] = list(attempt_failures)
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
    opencode_attach_url: str | None = None,
    opencode_session_id: str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, str]:
    rounded = (round(lat, 4), round(lon, 4))
    if cache is not None and rounded in cache:
        if diagnostics is not None:
            diagnostics["source"] = "cache"
            diagnostics["opencode_attempt_count"] = 0
            diagnostics["opencode_retry_count"] = 0
        return cache[rounded]

    result: dict[str, str] = {"landmark": UNKNOWN_LANDMARK, "country": "UnknownCountry"}
    if has_opencode_command():
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
        attempt_report: dict[str, Any] = {}
        try:
            if opencode_attach_url:
                payload, returned_session_id = _run_opencode_http_with_retry(
                    server_url=opencode_attach_url,
                    prompt=prompt,
                    timeout_sec=opencode_timeout_sec,
                    retries=opencode_retries,
                    backoff_sec=opencode_backoff_sec,
                    session_id=opencode_session_id,
                    opencode_model=opencode_model,
                    attempt_report=attempt_report,
                )
                if diagnostics is not None:
                    diagnostics["opencode_session_id"] = returned_session_id
            else:
                command = ["opencode", "run"]
                if opencode_model:
                    command.extend(["-m", opencode_model])
                command.extend(["--variant", "medium", prompt])
                payload = _run_opencode_with_retry(
                    command=command,
                    timeout_sec=opencode_timeout_sec,
                    retries=opencode_retries,
                    backoff_sec=opencode_backoff_sec,
                    attempt_report=attempt_report,
                )
        except InferenceExhaustedError:
            if strict:
                raise
            payload = None

        if payload is not None:
            if diagnostics is not None:
                attempt_count = int(attempt_report.get("attempt_count", 1))
                diagnostics["source"] = "opencode"
                diagnostics["opencode_attempt_count"] = attempt_count
                diagnostics["opencode_retry_count"] = max(0, attempt_count - 1)
                diagnostics["opencode_attempt_failures"] = list(attempt_report.get("attempt_failures", []))
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


def should_split_country_groups(country_groups: list[dict[str, Any]]) -> bool:
    known_countries = {
        str(group.get("country") or "UnknownCountry")
        for group in country_groups
        if str(group.get("country") or "UnknownCountry") != "UnknownCountry"
    }
    return len(known_countries) >= 2


def _cluster_inference_key(cluster: LocationCluster) -> tuple[float, float]:
    c_lat, c_lon = cluster.centroid
    return round(c_lat, 4), round(c_lon, 4)


def _find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _append_worker_log(logs: list[dict[str, Any]], entry: dict[str, Any], max_entries: int = 400) -> None:
    if len(logs) >= max_entries:
        return
    logs.append(entry)


def start_opencode_server(worker_id: int, startup_timeout_sec: float = 30.0) -> OpencodeServerHandle:
    port = _find_free_local_port()
    process = subprocess.Popen(
        ["opencode", "serve", "--port", str(port), "--hostname", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + startup_timeout_sec
    health_url = f"{url}/global/health"
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"worker-{worker_id} opencode serve exited early code={process.returncode}")
        try:
            with urllib.request.urlopen(health_url, timeout=1.0) as response:
                if response.status == 200:
                    return OpencodeServerHandle(worker_id=worker_id, port=port, url=url, process=process)
        except Exception:
            time.sleep(0.2)

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    raise RuntimeError(f"worker-{worker_id} timed out waiting for opencode serve health")


def stop_opencode_server(handle: OpencodeServerHandle) -> None:
    if handle.process is None:
        return
    if handle.process.poll() is not None:
        return
    handle.process.terminate()
    try:
        handle.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        handle.process.kill()


def start_opencode_server_pool(worker_count: int) -> list[OpencodeServerHandle]:
    servers: list[OpencodeServerHandle] = []
    for worker_id in range(max(1, worker_count)):
        try:
            servers.append(start_opencode_server(worker_id=worker_id))
        except Exception:
            for started in servers:
                stop_opencode_server(started)
            raise
    return servers


def stop_opencode_server_pool(servers: list[OpencodeServerHandle]) -> None:
    for handle in servers:
        stop_opencode_server(handle)


def _has_rate_limit_hint(detail: str) -> bool:
    lowered = detail.casefold()
    return (
        "rate limit" in lowered
        or "too many requests" in lowered
        or "throttle" in lowered
        or "quota" in lowered
        or "429" in lowered
    )


@dataclass
class SchedulerTaskResult:
    info: dict[str, str]
    diagnostics: dict[str, Any]


class SharedInferenceScheduler:
    def __init__(
        self,
        servers: list[OpencodeServerHandle],
        *,
        opencode_timeout_sec: int,
        opencode_retries: int,
        opencode_backoff_sec: float,
        opencode_model: str | None,
    ) -> None:
        self._servers = list(servers)
        self._timeout_sec = opencode_timeout_sec
        self._retries = opencode_retries
        self._backoff_sec = opencode_backoff_sec
        self._model = opencode_model
        self._queue: queue.Queue[tuple[tuple[float, float, str], LocationCluster] | None] = queue.Queue()
        self._futures: dict[tuple[float, float, str], Future[SchedulerTaskResult]] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._metrics: dict[str, int] = {
            "submit_total": 0,
            "dedupe_hit_total": 0,
            "queued_total": 0,
            "executed_total": 0,
        }
        self._threads: list[threading.Thread] = []
        for handle in self._servers:
            thread = threading.Thread(target=self._worker_loop, args=(handle,), daemon=True)
            thread.start()
            self._threads.append(thread)

    def submit(self, key: tuple[float, float], cluster: LocationCluster) -> Future[SchedulerTaskResult]:
        task_key = (key[0], key[1], self._model or "")
        with self._lock:
            self._metrics["submit_total"] += 1
            if self._closed:
                future: Future[SchedulerTaskResult] = Future()
                future.set_exception(InferenceExhaustedError("scheduler closed", attempt_count=0))
                return future
            existing = self._futures.get(task_key)
            if existing is not None:
                self._metrics["dedupe_hit_total"] += 1
                return existing
            future = Future()
            self._futures[task_key] = future
            self._metrics["queued_total"] += 1
            self._queue.put((task_key, cluster))
            return future

    def _worker_loop(self, handle: OpencodeServerHandle) -> None:
        session_id: str | None = None
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                break
            task_key, cluster = item
            with self._lock:
                future = self._futures.get(task_key)
            if future is None or future.done():
                self._queue.task_done()
                continue
            c_lat, c_lon = cluster.centroid
            diagnostics: dict[str, Any] = {}
            try:
                info = infer_landmark_info(
                    c_lat,
                    c_lon,
                    start_time=cluster.start_time,
                    end_time=cluster.end_time,
                    sample_count=len(cluster.points),
                    opencode_timeout_sec=self._timeout_sec,
                    opencode_retries=self._retries,
                    opencode_backoff_sec=self._backoff_sec,
                    opencode_model=self._model,
                    cache=None,
                    strict=True,
                    opencode_attach_url=handle.url,
                    opencode_session_id=session_id,
                    diagnostics=diagnostics,
                )
                session_value = diagnostics.get("opencode_session_id")
                if isinstance(session_value, str) and session_value:
                    session_id = session_value
                future.set_result(SchedulerTaskResult(info=info, diagnostics=diagnostics))
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, InferenceExhaustedError):
                    future.set_exception(exc)
                else:
                    future.set_exception(
                        InferenceExhaustedError(
                            f"opencode error: {exc}",
                            attempt_count=0,
                            attempt_failures=[
                                {
                                    "attempt": 0,
                                    "failure_type": "exception",
                                    "detail": f"opencode error: {exc}",
                                }
                            ],
                        )
                    )
            finally:
                with self._lock:
                    self._metrics["executed_total"] += 1
                self._queue.task_done()

    def snapshot_metrics(self) -> dict[str, int]:
        with self._lock:
            return dict(self._metrics)

    def shutdown(self, cancel_pending: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if cancel_pending:
                for future in self._futures.values():
                    if not future.done():
                        future.set_exception(
                            InferenceExhaustedError("processing cancelled by user", attempt_count=0)
                        )
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=5)


def infer_pending_cluster_infos(
    pending_clusters: list[tuple[int, LocationCluster]],
    *,
    opencode_timeout_sec: int,
    opencode_retries: int,
    opencode_backoff_sec: float,
    opencode_model: str | None,
    inference_workers: int,
    server_pool: list[OpencodeServerHandle] | None = None,
    inference_scheduler: SharedInferenceScheduler | None = None,
) -> tuple[
    list[tuple[int, LocationCluster, dict[str, str]]],
    tuple[int, LocationCluster, InferenceExhaustedError] | None,
    dict[str, Any],
]:
    completed: list[tuple[int, LocationCluster, dict[str, str]]] = []
    worker_report: dict[str, Any] = {
        "workers_requested": inference_workers,
        "workers_started": 0,
        "servers_started": 0,
        "servers_stopped": 0,
        "tasks_total": len(pending_clusters),
        "tasks_succeeded": 0,
        "tasks_failed": 0,
        "retry_attempts_total": 0,
        "rate_limit_hint_count": 0,
        "cancelled": False,
        "worker_logs": [],
    }

    if not pending_clusters:
        return completed, None, worker_report

    if inference_scheduler is not None:
        ordered_pending = sorted(pending_clusters, key=lambda item: item[0])
        key_members: dict[tuple[float, float], list[tuple[int, LocationCluster]]] = {}
        key_representative: dict[tuple[float, float], LocationCluster] = {}
        for idx, cluster in ordered_pending:
            key = _cluster_inference_key(cluster)
            key_members.setdefault(key, []).append((idx, cluster))
            if key not in key_representative:
                key_representative[key] = cluster

        worker_report["unique_inference_requests"] = len(key_representative)
        worker_report["duplicate_inference_skipped"] = len(ordered_pending) - len(key_representative)
        worker_report["workers_started"] = max(1, inference_workers)
        worker_report["servers_started"] = max(1, inference_workers)

        future_by_key: dict[tuple[float, float], Future[SchedulerTaskResult]] = {}
        for key, cluster in key_representative.items():
            future_by_key[key] = inference_scheduler.submit(key, cluster)

        seen_keys: set[tuple[float, float]] = set()
        for idx, cluster in ordered_pending:
            key = _cluster_inference_key(cluster)
            future = future_by_key[key]
            try:
                task_result = future.result()
            except InferenceExhaustedError as exc:
                worker_report["tasks_succeeded"] = len(completed)
                worker_report["tasks_failed"] = 1
                worker_report["scheduler_metrics"] = inference_scheduler.snapshot_metrics()
                return completed, (idx, cluster, exc), worker_report

            info = task_result.info
            if key not in seen_keys:
                seen_keys.add(key)
                diagnostics = task_result.diagnostics
                worker_report["retry_attempts_total"] = int(worker_report["retry_attempts_total"]) + int(
                    diagnostics.get("opencode_retry_count", 0)
                )
                attempt_failures = diagnostics.get("opencode_attempt_failures", [])
                if isinstance(attempt_failures, list):
                    for failure in attempt_failures:
                        if not isinstance(failure, dict):
                            continue
                        detail = str(failure.get("detail", ""))
                        if _has_rate_limit_hint(detail):
                            worker_report["rate_limit_hint_count"] = int(worker_report["rate_limit_hint_count"]) + 1
            completed.append((idx, cluster, info))

        worker_report["tasks_succeeded"] = len(completed)
        worker_report["tasks_failed"] = 0
        worker_report["servers_stopped"] = 0
        worker_report["scheduler_metrics"] = inference_scheduler.snapshot_metrics()
        return completed, None, worker_report

    if inference_workers <= 1:
        info_cache: dict[tuple[float, float], dict[str, str]] = {}
        for idx, cluster in pending_clusters:
            c_lat, c_lon = cluster.centroid
            diagnostics: dict[str, Any] = {}
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
                    diagnostics=diagnostics,
                )
            except InferenceExhaustedError as exc:
                worker_report["tasks_failed"] = int(worker_report["tasks_failed"]) + 1
                worker_report["retry_attempts_total"] = int(worker_report["retry_attempts_total"]) + max(
                    0,
                    exc.attempt_count - 1,
                )
                return completed, (idx, cluster, exc), worker_report
            worker_report["tasks_succeeded"] = int(worker_report["tasks_succeeded"]) + 1
            worker_report["retry_attempts_total"] = int(worker_report["retry_attempts_total"]) + int(
                diagnostics.get("opencode_retry_count", 0)
            )
            completed.append((idx, cluster, info))
        return completed, None, worker_report

    ordered_pending = sorted(pending_clusters, key=lambda item: item[0])
    key_members: dict[tuple[float, float], list[tuple[int, LocationCluster]]] = {}
    key_representative: dict[tuple[float, float], LocationCluster] = {}
    unique_keys_in_order: list[tuple[float, float]] = []
    for idx, cluster in ordered_pending:
        key = _cluster_inference_key(cluster)
        if key not in key_members:
            key_members[key] = []
            key_representative[key] = cluster
            unique_keys_in_order.append(key)
        key_members[key].append((idx, cluster))

    unique_tasks: list[tuple[tuple[float, float], LocationCluster]] = [
        (key, key_representative[key]) for key in unique_keys_in_order
    ]

    worker_report["unique_inference_requests"] = len(unique_tasks)
    worker_report["duplicate_inference_skipped"] = len(ordered_pending) - len(unique_tasks)

    task_queue: queue.Queue[tuple[tuple[float, float], LocationCluster]] = queue.Queue()
    for item in unique_tasks:
        task_queue.put(item)

    worker_count = max(1, inference_workers)
    lock = threading.Lock()
    stop_event = threading.Event()
    results_by_key: dict[tuple[float, float], dict[str, str]] = {}
    failures_by_key: dict[tuple[float, float], InferenceExhaustedError] = {}
    owns_servers = server_pool is None
    if server_pool is None:
        try:
            servers = start_opencode_server_pool(worker_count)
        except Exception as exc:  # noqa: BLE001
            error = InferenceExhaustedError(
                f"server pool start failed: {exc}",
                attempt_count=0,
                attempt_failures=[
                    {
                        "attempt": 0,
                        "failure_type": "server-start",
                        "detail": str(exc),
                    }
                ],
            )
            first_idx, first_cluster = ordered_pending[0]
            worker_report["tasks_failed"] = 1
            _append_worker_log(
                worker_report["worker_logs"],
                {
                    "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "worker_id": -1,
                    "event": "server_start_failed",
                    "detail": str(exc),
                },
            )
            return completed, (first_idx, first_cluster, error), worker_report
    else:
        servers = list(server_pool)

    if not servers:
        error = InferenceExhaustedError("no opencode servers available", attempt_count=0)
        first_idx, first_cluster = ordered_pending[0]
        worker_report["tasks_failed"] = 1
        return completed, (first_idx, first_cluster, error), worker_report

    if len(servers) > worker_count:
        servers = servers[:worker_count]

    worker_report["workers_started"] = len(servers)
    worker_report["servers_started"] = len(servers)
    for handle in servers:
        _append_worker_log(
            worker_report["worker_logs"],
            {
                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "worker_id": handle.worker_id,
                "event": "server_attached",
                "attach_url": handle.url,
                "reuse_pool": not owns_servers,
            },
        )

    def worker_loop(handle: OpencodeServerHandle) -> None:
        worker_id = handle.worker_id
        while not stop_event.is_set():
            try:
                key, cluster = task_queue.get_nowait()
            except queue.Empty:
                break

            task_members = key_members[key]
            first_idx = task_members[0][0]

            c_lat, c_lon = cluster.centroid
            diagnostics: dict[str, Any] = {}
            task_started_at = time.perf_counter()
            _append_worker_log(
                worker_report["worker_logs"],
                {
                    "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "worker_id": worker_id,
                    "event": "task_start",
                    "cluster_index": first_idx,
                    "duplicate_cluster_count": len(task_members),
                },
            )
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
                    cache=None,
                    strict=True,
                    opencode_attach_url=handle.url,
                    diagnostics=diagnostics,
                )
                with lock:
                    results_by_key[key] = info
                    worker_report["retry_attempts_total"] = int(worker_report["retry_attempts_total"]) + int(
                        diagnostics.get("opencode_retry_count", 0)
                    )
                    attempt_failures = diagnostics.get("opencode_attempt_failures", [])
                    if isinstance(attempt_failures, list):
                        for failure in attempt_failures:
                            if not isinstance(failure, dict):
                                continue
                            detail = str(failure.get("detail", ""))
                            if _has_rate_limit_hint(detail):
                                worker_report["rate_limit_hint_count"] = int(worker_report["rate_limit_hint_count"]) + 1
                _append_worker_log(
                    worker_report["worker_logs"],
                    {
                        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "worker_id": worker_id,
                        "event": "task_success",
                        "cluster_index": first_idx,
                        "retry_count": int(diagnostics.get("opencode_retry_count", 0)),
                        "duration_sec": round(time.perf_counter() - task_started_at, 3),
                    },
                )
            except InferenceExhaustedError as exc:
                with lock:
                    failures_by_key[key] = exc
                    worker_report["retry_attempts_total"] = int(worker_report["retry_attempts_total"]) + max(
                        0,
                        exc.attempt_count - 1,
                    )
                _append_worker_log(
                    worker_report["worker_logs"],
                    {
                        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "worker_id": worker_id,
                        "event": "task_failed",
                        "cluster_index": first_idx,
                        "detail": str(exc),
                        "duration_sec": round(time.perf_counter() - task_started_at, 3),
                    },
                )
                stop_event.set()
            finally:
                task_queue.task_done()

    threads: list[threading.Thread] = []
    cancelled_error: InferenceExhaustedError | None = None
    try:
        for handle in servers:
            thread = threading.Thread(target=worker_loop, args=(handle,), daemon=True)
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        worker_report["cancelled"] = True
        stop_event.set()
        cancelled_error = InferenceExhaustedError(
            "processing cancelled by user",
            attempt_count=0,
            attempt_failures=[
                {
                    "attempt": 0,
                    "failure_type": "cancelled",
                    "detail": "processing cancelled by user",
                }
            ],
        )
    finally:
        if owns_servers:
            for handle in servers:
                stop_opencode_server(handle)
                worker_report["servers_stopped"] = int(worker_report["servers_stopped"]) + 1
                _append_worker_log(
                    worker_report["worker_logs"],
                    {
                        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "worker_id": handle.worker_id,
                        "event": "server_stopped",
                    },
                )
        else:
            worker_report["servers_stopped"] = 0

    if cancelled_error is not None:
        first_idx, first_cluster = ordered_pending[0]
        worker_report["tasks_failed"] = int(worker_report["tasks_failed"]) + 1
        return completed, (first_idx, first_cluster, cancelled_error), worker_report

    if failures_by_key:
        failed_key = min(
            failures_by_key,
            key=lambda key: key_members[key][0][0],
        )
        failed_idx = key_members[failed_key][0][0]
        failure_exc = failures_by_key[failed_key]
        for idx, cluster in ordered_pending:
            if idx >= failed_idx:
                break
            key = _cluster_inference_key(cluster)
            info = results_by_key.get(key)
            if info is None:
                break
            completed.append((idx, cluster, info))
        failed_cluster = key_members[failed_key][0][1]
        worker_report["tasks_succeeded"] = len(completed)
        worker_report["tasks_failed"] = 1
        return completed, (failed_idx, failed_cluster, failure_exc), worker_report

    for idx, cluster in ordered_pending:
        key = _cluster_inference_key(cluster)
        info = results_by_key.get(key)
        if info is None:
            continue
        completed.append((idx, cluster, info))

    worker_report["tasks_succeeded"] = len(completed)
    worker_report["tasks_failed"] = 0

    return completed, None, worker_report


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


def is_safe_source_for_move(source_file: Path, source_root: Path, resolved_source_root: Path | None = None) -> bool:
    try:
        resolved_source = source_file.resolve(strict=True)
    except OSError:
        return False
    if not resolved_source.is_file():
        return False
    resolved_root = resolved_source_root or source_root.resolve()
    try:
        resolved_source.relative_to(resolved_root)
    except ValueError:
        return False
    return True


def _date_prefix_from_folder(folder: Path) -> str:
    match = DATE_PREFIX_RE.match(folder.name)
    if not match:
        raise ValueError("Folder name must start with YYYY_MM_DD")
    return match.group(1)


def discover_day_folders(root: Path) -> list[Path]:
    discovered: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(root):
        parent = Path(dirpath)
        for dirname in dirnames:
            if DAY_FOLDER_EXACT_RE.match(dirname):
                discovered.append(parent / dirname)
    discovered.sort()
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
    inference_workers: int = 3,
    resume: bool = True,
) -> dict[str, Any]:
    day_folders = discover_day_folders(root)
    folder_results: list[dict[str, Any] | None] = [None] * len(day_folders)
    shared_server_pool: list[OpencodeServerHandle] | None = None
    inference_scheduler: SharedInferenceScheduler | None = None

    if inference_workers > 1:
        try:
            shared_server_pool = start_opencode_server_pool(inference_workers)
            inference_scheduler = SharedInferenceScheduler(
                shared_server_pool,
                opencode_timeout_sec=opencode_timeout_sec,
                opencode_retries=opencode_retries,
                opencode_backoff_sec=opencode_backoff_sec,
                opencode_model=opencode_model,
            )
        except Exception as exc:  # noqa: BLE001
            summary: dict[str, Any] = {
                "root_path": str(root),
                "status": "failed-server-start",
                "total_folder_count": len(day_folders),
                "failed_folder_count": len(day_folders),
                "error": {"message": str(exc)},
                "tree_state_file": str(default_tree_state_file(root)),
                "tree_report_file": str(default_tree_report_file(root)),
            }
            write_json_file(default_tree_state_file(root), summary)
            write_json_file(default_tree_report_file(root), summary)
            return summary

    def run_folder(index: int, folder: Path) -> tuple[int, dict[str, Any]]:
        try:
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
                inference_workers=inference_workers,
                server_pool=shared_server_pool,
                inference_scheduler=inference_scheduler,
                state_file=default_state_file(folder),
                report_file=default_report_file(folder),
                resume=resume,
            )
        except Exception as exc:  # noqa: BLE001
            write_json_file(
                default_state_file(folder),
                {
                    "folder_path": str(folder),
                    "status": "failed-exception",
                    "error": {"message": str(exc)},
                },
            )
            write_json_file(
                default_report_file(folder),
                {
                    "folder_path": str(folder),
                    "status": "failed-exception",
                    "state_file": str(default_state_file(folder)),
                    "error": {"message": str(exc)},
                    "used_reverse_geocoding": False,
                },
            )
            result = {
                "folder_path": str(folder),
                "status": "failed-exception",
                "state_file": str(default_state_file(folder)),
                "report_file": str(default_report_file(folder)),
                "error": {"message": str(exc)},
                "used_reverse_geocoding": False,
            }
        return index, result

    cancelled = False
    try:
        if inference_scheduler is not None and len(day_folders) > 1:
            max_folder_workers = min(len(day_folders), max(4, inference_workers * 3))
            with ThreadPoolExecutor(max_workers=max_folder_workers) as executor:
                future_map = {
                    executor.submit(run_folder, idx, folder): idx
                    for idx, folder in enumerate(day_folders)
                }
                for future in as_completed(future_map):
                    idx, result = future.result()
                    folder_results[idx] = result
        else:
            for idx, folder in enumerate(day_folders):
                _idx, result = run_folder(idx, folder)
                folder_results[_idx] = result
    except KeyboardInterrupt:
        cancelled = True
        raise
    finally:
        if inference_scheduler is not None:
            inference_scheduler.shutdown(cancel_pending=cancelled)
        if shared_server_pool is not None:
            stop_opencode_server_pool(shared_server_pool)

    materialized_results: list[dict[str, Any]] = [result for result in folder_results if isinstance(result, dict)]

    integrity_check = verify_tree_integrity(materialized_results, apply=apply)

    summary_status = "completed"
    if not integrity_check["passed"]:
        summary_status = "failed-integrity"
    elif integrity_check["failed_folder_count"] > 0:
        summary_status = "completed-with-failures"

    failed_folders: list[dict[str, Any]] = []
    for result in materialized_results:
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
            "inference_workers": inference_workers,
        },
        "total_folder_count": integrity_check["total_folder_count"],
        "integrity_check": integrity_check,
        "folder_results": materialized_results,
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
    inference_workers: int = 3,
    server_pool: list[OpencodeServerHandle] | None = None,
    inference_scheduler: SharedInferenceScheduler | None = None,
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
        "opencode_timeout_sec": opencode_timeout_sec,
        "opencode_max_attempts": opencode_retries,
        "opencode_initial_backoff_sec": opencode_backoff_sec,
        "opencode_model": opencode_model,
        "inference_workers": inference_workers,
    }

    try:
        points, without_gps = extract_media_points(folder)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        error_payload: dict[str, Any] = {
            "message": str(exc),
            "failure_type": "extract-media",
        }
        failure_state: dict[str, Any] = {
            "folder_path": str(folder),
            "status": "failed-extract",
            "config": current_config,
            "error": error_payload,
            "persistent_failure_count": 0,
            "persistent_failure_log": [],
        }
        write_json_file(state_file, failure_state)
        write_json_file(
            report_file,
            {
                "status": "failed-extract",
                "folder_path": str(folder),
                "state_file": str(state_file),
                "error": error_payload,
                "persistent_failure_summary": {
                    "persistent_failure_count": 0,
                },
                "used_reverse_geocoding": False,
            },
        )
        return {
            "folder_path": str(folder),
            "status": "failed-extract",
            "state_file": str(state_file),
            "report_file": str(report_file),
            "error": error_payload,
            "media_with_gps_count": 0,
            "media_with_gps_sampled": 0,
            "sample_ratio_requested": ratio,
            "media_without_gps_count": 0,
            "used_reverse_geocoding": False,
        }
    input_fingerprint = build_input_fingerprint(points, without_gps)
    sampled_points = sample_points(points, ratio=ratio)

    sampled_clusters = cluster_media_points(sampled_points, cluster_distance_m=cluster_distance_m, already_sorted=True)
    previous_state = read_json_file(state_file) if resume else None
    completed_infos: list[dict[str, str]] = []
    persistent_failure_log: list[dict[str, Any]] = []
    if previous_state is not None:
        if (
            previous_state.get("folder_path") == str(folder)
            and previous_state.get("config") == current_config
            and previous_state.get("input_fingerprint") == input_fingerprint
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

    cluster_infos: list[tuple[LocationCluster, dict[str, str]]] = []
    pending_clusters: list[tuple[int, LocationCluster]] = []
    for idx, cluster in enumerate(sampled_clusters):
        if idx < len(completed_infos):
            cluster_infos.append((cluster, completed_infos[idx]))
            continue

        pending_clusters.append((idx, cluster))

    completed_pending_infos, pending_failure, inference_worker_report = infer_pending_cluster_infos(
        pending_clusters,
        opencode_timeout_sec=opencode_timeout_sec,
        opencode_retries=opencode_retries,
        opencode_backoff_sec=opencode_backoff_sec,
        opencode_model=opencode_model,
        inference_workers=inference_workers,
        server_pool=server_pool,
        inference_scheduler=inference_scheduler,
    )

    for idx, cluster, info in completed_pending_infos:
        cluster_infos.append((cluster, info))
        state_payload = {
            "folder_path": str(folder),
            "status": "in-progress",
            "config": current_config,
            "input_fingerprint": input_fingerprint,
            "next_cluster_index": idx + 1,
            "completed_cluster_infos": [info for _cluster, info in cluster_infos],
            "persistent_failure_count": len(persistent_failure_log),
            "persistent_failure_log": persistent_failure_log,
        }
        write_json_file(state_file, state_payload)

    if pending_failure is not None:
        idx, cluster, exc = pending_failure
        c_lat, c_lon = cluster.centroid
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

        state_payload = {
            "folder_path": str(folder),
            "status": "failed-inference",
            "config": current_config,
            "input_fingerprint": input_fingerprint,
            "next_cluster_index": idx,
            "completed_cluster_infos": [info for _cluster, info in cluster_infos],
            "persistent_failure_count": len(persistent_failure_log),
            "persistent_failure_log": persistent_failure_log,
            "error": {
                "message": str(exc),
                "attempt_count": exc.attempt_count,
            },
            "inference_worker_report": inference_worker_report,
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
            "inference_worker_report": inference_worker_report,
            "used_reverse_geocoding": False,
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
            "inference_worker_report": inference_worker_report,
            "used_reverse_geocoding": False,
        }

    leftover_media_examples = list(without_gps[:20])
    invalid_source_media: list[str] = []

    country_groups = group_clusters_by_country(cluster_infos)
    if should_split_country_groups(country_groups):
        full_clusters = cluster_media_points(points, cluster_distance_m=cluster_distance_m, already_sorted=True)
        resolved_folder = folder.resolve()
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
                created_target = False
                for full_cluster in assigned_full_clusters[idx - 1]:
                    for point in full_cluster.points:
                        source_file = Path(point.source_file)
                        if not is_safe_source_for_move(source_file, folder, resolved_source_root=resolved_folder):
                            invalid_source_media.append(str(source_file))
                            continue
                        if not created_target:
                            target_path.mkdir(parents=True, exist_ok=True)
                            created_target = True
                        try:
                            _safe_move_media_file(source_file, folder, target_path)
                        except OSError as exc:
                            error_payload = {
                                "message": str(exc),
                                "failed_source_file": str(source_file),
                            }
                            failure_state: dict[str, Any] = {
                                "folder_path": str(folder),
                                "status": "failed-apply",
                                "config": current_config,
                                "input_fingerprint": input_fingerprint,
                                "completed_cluster_infos": [info for _cluster, info in cluster_infos],
                                "persistent_failure_count": len(persistent_failure_log),
                                "persistent_failure_log": persistent_failure_log,
                                "error": error_payload,
                                "moved_media_with_gps_count": moved_file_count,
                                "invalid_source_media_count": len(invalid_source_media),
                                "inference_worker_report": inference_worker_report,
                            }
                            write_json_file(state_file, failure_state)
                            combined_leftover = leftover_media_examples + invalid_source_media
                            write_json_file(
                                report_file,
                                {
                                    "status": "failed-apply",
                                    "folder_path": str(folder),
                                    "state_file": str(state_file),
                                    "error": error_payload,
                                    "moved_media_with_gps_count": moved_file_count,
                                    "leftover_media_count": len(without_gps) + len(invalid_source_media),
                                    "leftover_media_examples": combined_leftover[:20],
                                    "invalid_source_media_count": len(invalid_source_media),
                                    "inference_worker_report": inference_worker_report,
                                    "persistent_failure_summary": {
                                        "persistent_failure_count": len(persistent_failure_log),
                                    },
                                    "used_reverse_geocoding": False,
                                },
                            )
                            return {
                                "folder_path": str(folder),
                                "status": "failed-apply",
                                "state_file": str(state_file),
                                "report_file": str(report_file),
                                "error": error_payload,
                                "media_with_gps_count": len(points),
                                "media_with_gps_sampled": len(sampled_points),
                                "sample_ratio_requested": ratio,
                                "media_without_gps_count": len(without_gps),
                                "leftover_media_count": len(without_gps) + len(invalid_source_media),
                                "leftover_media_examples": combined_leftover[:20],
                                "invalid_source_media_count": len(invalid_source_media),
                                "inference_worker_report": inference_worker_report,
                                "moved_media_with_gps_count": moved_file_count,
                                "used_reverse_geocoding": False,
                            }
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
            "input_fingerprint": input_fingerprint,
            "completed_cluster_infos": [info for _cluster, info in cluster_infos],
            "persistent_failure_count": len(persistent_failure_log),
            "persistent_failure_log": persistent_failure_log,
            "result_status": status,
            "invalid_source_media_count": len(invalid_source_media),
            "inference_worker_report": inference_worker_report,
        }
        combined_leftover = leftover_media_examples + invalid_source_media
        write_json_file(state_file, completion_state)
        write_json_file(
            report_file,
            {
                "status": status,
                "folder_path": str(folder),
                "state_file": str(state_file),
                "split_folder_count": len(split_folders),
                "leftover_media_count": len(without_gps) + len(invalid_source_media),
                "leftover_media_examples": combined_leftover[:20],
                "invalid_source_media_count": len(invalid_source_media),
                "inference_worker_report": inference_worker_report,
                "persistent_failure_summary": {
                    "persistent_failure_count": len(persistent_failure_log),
                },
                "used_reverse_geocoding": False,
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
            "leftover_media_count": len(without_gps) + len(invalid_source_media),
            "leftover_media_examples": combined_leftover[:20],
            "invalid_source_media_count": len(invalid_source_media),
            "invalid_source_media_examples": invalid_source_media[:20],
            "inference_worker_report": inference_worker_report,
            "moved_media_with_gps_count": moved_file_count,
            "used_reverse_geocoding": False,
        }

    full_clusters = cluster_media_points(points, cluster_distance_m=cluster_distance_m, already_sorted=True)
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
        try:
            folder.rename(target_path)
            status = "renamed"
        except OSError as exc:
            error_payload = {
                "message": str(exc),
                "failure_type": "apply-rename",
                "target_name": target_path.name,
            }
            failure_state: dict[str, Any] = {
                "folder_path": str(folder),
                "status": "failed-rename",
                "config": current_config,
                "input_fingerprint": input_fingerprint,
                "completed_cluster_infos": [info for _cluster, info in cluster_infos],
                "persistent_failure_count": len(persistent_failure_log),
                "persistent_failure_log": persistent_failure_log,
                "error": error_payload,
                "target_name": target_path.name,
                "inference_worker_report": inference_worker_report,
            }
            write_json_file(state_file, failure_state)
            write_json_file(
                report_file,
                {
                    "status": "failed-rename",
                    "folder_path": str(folder),
                    "state_file": str(state_file),
                    "target_name": target_path.name,
                    "error": error_payload,
                    "leftover_media_count": len(without_gps),
                    "leftover_media_examples": leftover_media_examples,
                    "inference_worker_report": inference_worker_report,
                    "persistent_failure_summary": {
                        "persistent_failure_count": len(persistent_failure_log),
                    },
                    "used_reverse_geocoding": False,
                },
            )
            return {
                "folder_path": str(folder),
                "status": "failed-rename",
                "state_file": str(state_file),
                "report_file": str(report_file),
                "target_name": target_path.name,
                "error": error_payload,
                "media_with_gps_count": len(points),
                "media_with_gps_sampled": len(sampled_points),
                "sample_ratio_requested": ratio,
                "media_without_gps_count": len(without_gps),
                "leftover_media_count": len(without_gps),
                "leftover_media_examples": leftover_media_examples,
                "inference_worker_report": inference_worker_report,
                "used_reverse_geocoding": False,
            }

    completion_state = {
        "folder_path": str(folder),
        "status": "completed",
        "config": current_config,
        "input_fingerprint": input_fingerprint,
        "completed_cluster_infos": [info for _cluster, info in cluster_infos],
        "persistent_failure_count": len(persistent_failure_log),
        "persistent_failure_log": persistent_failure_log,
        "result_status": status,
        "target_name": target_path.name,
        "leftover_media_count": len(without_gps),
        "inference_worker_report": inference_worker_report,
    }
    write_json_file(state_file, completion_state)
    write_json_file(
        report_file,
        {
            "status": status,
            "folder_path": str(folder),
            "state_file": str(state_file),
            "target_name": target_path.name,
            "leftover_media_count": len(without_gps),
            "leftover_media_examples": leftover_media_examples,
            "inference_worker_report": inference_worker_report,
            "persistent_failure_summary": {
                "persistent_failure_count": len(persistent_failure_log),
            },
            "used_reverse_geocoding": False,
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
        "leftover_media_count": len(without_gps),
        "leftover_media_examples": leftover_media_examples,
        "inference_worker_report": inference_worker_report,
        "used_reverse_geocoding": False,
    }


def parse_ratio_arg(raw_value: str) -> float:
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ratio must be a float in (0, 1]") from exc
    if value <= 0.0 or value > 1.0:
        raise argparse.ArgumentTypeError("ratio must be in (0, 1]")
    return value


def parse_positive_float_arg(raw_value: str, *, name: str) -> float:
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be a number") from exc
    if value <= 0.0:
        raise argparse.ArgumentTypeError(f"{name} must be > 0")
    return value


def parse_non_negative_float_arg(raw_value: str, *, name: str) -> float:
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be a number") from exc
    if value < 0.0:
        raise argparse.ArgumentTypeError(f"{name} must be >= 0")
    return value


def parse_positive_int_arg(raw_value: str, *, name: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError(f"{name} must be >= 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rename folder using AI-inferred itinerary landmarks")
    parser.add_argument("folder", help="Path to day folder (YYYY_MM_DD...) or tree root containing day folders")
    parser.add_argument("--apply", action="store_true", help="Apply rename instead of dry-run")
    parser.add_argument(
        "--ratio",
        type=parse_ratio_arg,
        default=1.0,
        help="Sampling ratio for GPS media (0-1], default 1.0",
    )
    parser.add_argument(
        "--cluster-distance-m",
        type=lambda raw: parse_positive_float_arg(raw, name="cluster-distance-m"),
        default=2_000.0,
        help="Distance threshold in meters for itinerary clustering",
    )
    parser.add_argument(
        "--opencode-timeout-sec",
        type=lambda raw: parse_positive_int_arg(raw, name="opencode-timeout-sec"),
        default=180,
        help="Timeout in seconds for each opencode landmark inference call",
    )
    parser.add_argument(
        "--opencode-max-attempts",
        type=lambda raw: parse_positive_int_arg(raw, name="opencode-max-attempts"),
        default=5,
        help="Retry attempts for opencode landmark inference",
    )
    parser.add_argument(
        "--opencode-initial-backoff-sec",
        type=lambda raw: parse_non_negative_float_arg(raw, name="opencode-initial-backoff-sec"),
        default=3.0,
        help="Initial backoff seconds (exponential) between opencode retries",
    )
    parser.add_argument(
        "--max-landmarks",
        type=lambda raw: parse_positive_int_arg(raw, name="max-landmarks"),
        default=8,
        help="Maximum number of landmarks kept in final day folder name",
    )
    parser.add_argument(
        "--inference-workers",
        type=lambda raw: parse_positive_int_arg(raw, name="inference-workers"),
        default=3,
        help="Parallel workers for independent landmark inference",
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
    if not folder.exists() or not folder.is_dir():
        parser.error("folder must exist and be a directory")

    has_day_children = any(
        child.is_dir() and DAY_FOLDER_EXACT_RE.match(child.name)
        for child in folder.iterdir()
    )
    if DATE_PREFIX_RE.match(folder.name) and not has_day_children:
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
            inference_workers=args.inference_workers,
            state_file=Path(args.state_file).expanduser().resolve() if args.state_file else None,
            report_file=Path(args.report_file).expanduser().resolve() if args.report_file else None,
            resume=not args.no_resume,
        )
    else:
        if args.state_file or args.report_file:
            parser.error("--state-file and --report-file are only supported for single day folder input")
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
            inference_workers=args.inference_workers,
            resume=not args.no_resume,
        )
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
