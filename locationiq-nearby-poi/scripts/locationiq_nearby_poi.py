#!/usr/bin/env python3
"""Fetch nearby points of interest from LocationIQ by coordinates."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


def fetch_nearby_poi(
    api_key: str,
    lat: float,
    lon: float,
    tag: str = "all",
    radius: int = 1000,
    region: str = "us1",
) -> list[dict[str, Any]]:
    if region not in {"us1", "eu1"}:
        raise ValueError("region must be 'us1' or 'eu1'")

    params = {
        "key": api_key,
        "lat": lat,
        "lon": lon,
        "tag": tag,
        "radius": radius,
        "format": "json",
    }
    endpoint = f"https://{region}.locationiq.com/v1/nearby?{urlencode(params)}"

    try:
        with urlopen(endpoint, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LocationIQ HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error calling LocationIQ: {exc.reason}") from exc

    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected response: {data}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch nearby POI from LocationIQ")
    parser.add_argument("--lat", type=float, required=True, help="Latitude")
    parser.add_argument("--lon", type=float, required=True, help="Longitude")
    parser.add_argument(
        "--key",
        default=os.getenv("LOCATIONIQ_API_KEY"),
        help="LocationIQ API key (or set LOCATIONIQ_API_KEY)",
    )
    parser.add_argument("--tag", default="all", help="POI tag filter")
    parser.add_argument("--radius", type=int, default=1000, help="Search radius in meters")
    parser.add_argument("--region", default="us1", choices=["us1", "eu1"], help="API region")
    parser.add_argument("--limit", type=int, default=10, help="Maximum rows to print")
    parser.add_argument("--json", action="store_true", help="Print raw JSON response")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    if not args.key:
        print("Missing API key. Use --key or LOCATIONIQ_API_KEY.", file=sys.stderr)
        return 2

    try:
        pois = fetch_nearby_poi(
            api_key=args.key,
            lat=args.lat,
            lon=args.lon,
            tag=args.tag,
            radius=args.radius,
            region=args.region,
        )
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(pois, indent=2 if args.pretty else None))
    else:
        print(f"Found {len(pois)} results. Showing up to {max(args.limit, 0)}:")
        for poi in pois[: max(args.limit, 0)]:
            name = poi.get("name") or "(unnamed)"
            display_name = poi.get("display_name") or ""
            distance = poi.get("distance")
            print(f"- {name} | distance={distance}m | {display_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
