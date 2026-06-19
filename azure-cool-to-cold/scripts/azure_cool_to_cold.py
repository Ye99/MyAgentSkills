#!/usr/bin/env python3
"""Guarded rclone workflow for one Azure Cool-to-Cold container conversion."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def fail(message: str, code: int = 2) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def run_json(command: list[str]) -> Any:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        fail(f"{' '.join(command[:3])}: {detail}", result.returncode)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON from {' '.join(command[:3])}: {exc}")


def validate_target(target: str) -> tuple[str, str]:
    if target.count(":") != 1:
        fail("target must be an rclone remote and one container: REMOTE:container")
    remote, container = target.split(":", 1)
    if not remote or not container:
        fail("account-root targets are forbidden; specify exactly one container")
    if "/" in container or "\\" in container:
        fail("nested paths are forbidden; specify exactly one whole container")
    if container in {".", ".."}:
        fail("invalid container scope")
    return remote, container


def validate_backend(target: str) -> None:
    features = run_json(["rclone", "backend", "features", target])
    feature_map = features.get("Features") or {}
    if feature_map.get("SetTier") is not True:
        fail("backend does not expose SetTier")
    description = str(features.get("String", ""))
    if not description.startswith("Azure container "):
        fail("target must use a raw Azure Blob container, not a crypt overlay")


def inventory(target: str) -> dict[str, Any]:
    validate_target(target)
    validate_backend(target)
    objects = run_json(
        [
            "rclone",
            "lsjson",
            "--files-only",
            "--recursive",
            "--metadata",
            target,
        ]
    )
    if not isinstance(objects, list):
        fail("unexpected inventory response")

    tiers: Counter[str] = Counter()
    total_bytes = 0
    for item in objects:
        if item.get("IsDir"):
            continue
        tier = item.get("Tier")
        tiers[str(tier) if tier else "UNKNOWN"] += 1
        size = item.get("Size")
        if not isinstance(size, int) or size < 0:
            fail("inventory contains an invalid file size")
        total_bytes += size

    return {
        "target": target,
        "count": sum(tiers.values()),
        "bytes": total_bytes,
        "tiers": dict(sorted(tiers.items())),
    }


def print_inventory(label: str, data: dict[str, Any]) -> None:
    print(
        json.dumps(
            {
                "label": label,
                "target": data["target"],
                "count": data["count"],
                "bytes": data["bytes"],
                "tiers": data["tiers"],
            },
            indent=2,
            sort_keys=True,
        )
    )


def require_all_tier(data: dict[str, Any], tier: str) -> None:
    if data["count"] == 0:
        return
    expected = {tier: data["count"]}
    if data["tiers"] != expected:
        fail(f"expected every file to be {tier}; found {data['tiers']}")


def write_state(path: Path, data: dict[str, Any]) -> None:
    payload = {
        "schema": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "preflight": data,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_state(path: Path, target: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read state file: {exc}")
    if payload.get("schema") != 1:
        fail("unsupported state-file schema")
    preflight = payload.get("preflight")
    if not isinstance(preflight, dict) or preflight.get("target") != target:
        fail("state target does not match requested target")
    return payload


def same_inventory(left: dict[str, Any], right: dict[str, Any]) -> bool:
    fields = ("target", "count", "bytes", "tiers")
    return all(left.get(field) == right.get(field) for field in fields)


def command_preflight(args: argparse.Namespace) -> None:
    data = inventory(args.target)
    require_all_tier(data, "Cool")
    write_state(args.state, data)
    print_inventory("preflight", data)
    print(f"State written to {args.state}")


def command_dry_run(args: argparse.Namespace) -> None:
    payload = read_state(args.state, args.target)
    before = inventory(args.target)
    if not same_inventory(payload["preflight"], before):
        fail("inventory drifted since preflight; run preflight again")
    require_all_tier(before, "Cool")
    result = subprocess.run(
        [
            "rclone",
            "settier",
            "Cold",
            args.target,
            "--dry-run",
            "--fast-list",
            "--log-level",
            "ERROR",
        ],
        check=False,
    )
    if result.returncode:
        fail("native rclone dry run failed", result.returncode)
    print("Dry run completed successfully; no tiers were changed.")


def command_apply(args: argparse.Namespace) -> None:
    validate_target(args.target)
    if args.confirm_target != args.target:
        fail("--confirm-target must exactly match target")
    payload = read_state(args.state, args.target)
    expected = payload["preflight"]
    if args.confirm_count != expected.get("count"):
        fail("--confirm-count must exactly match the preflight count")

    before = inventory(args.target)
    if not same_inventory(expected, before):
        fail("inventory drifted since preflight; run preflight again")
    require_all_tier(before, "Cool")

    result = subprocess.run(
        ["rclone", "settier", "Cold", args.target, "--fast-list"],
        check=False,
    )
    if result.returncode:
        fail("rclone settier failed", result.returncode)

    after = inventory(args.target)
    if after["count"] != before["count"] or after["bytes"] != before["bytes"]:
        print_inventory("before", before)
        print_inventory("after", after)
        fail("post-change count or bytes do not match preflight")
    require_all_tier(after, "Cold")

    payload["applied_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    payload["post_change"] = after
    args.state.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print_inventory("verified-post-change", after)


def command_verify(args: argparse.Namespace) -> None:
    payload = read_state(args.state, args.target)
    expected = payload["preflight"]
    current = inventory(args.target)
    if current["count"] != expected["count"] or current["bytes"] != expected["bytes"]:
        print_inventory("current", current)
        fail("current count or bytes do not match preflight")
    require_all_tier(current, "Cold")
    print_inventory("verified", current)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely convert one raw Azure Blob container from Cool to Cold."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, function in (
        ("preflight", command_preflight),
        ("dry-run", command_dry_run),
        ("verify", command_verify),
    ):
        child = subparsers.add_parser(name)
        child.add_argument("target", help="Raw Azure target: REMOTE:container")
        child.add_argument("--state", type=Path, required=True)
        child.set_defaults(function=function)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("target", help="Raw Azure target: REMOTE:container")
    apply_parser.add_argument("--state", type=Path, required=True)
    apply_parser.add_argument("--confirm-target", required=True)
    apply_parser.add_argument("--confirm-count", type=int, required=True)
    apply_parser.set_defaults(function=command_apply)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
