#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Iterable


DEFAULT_URI = "qemu:///system"


@dataclasses.dataclass(frozen=True)
class VmDiskChain:
    active_disk: str
    overlays: list[str]
    base_disk: str


def resolve_common_args(args: argparse.Namespace) -> tuple[str, bool]:
    uri = getattr(args, "uri_local", None) or getattr(args, "uri", None) or DEFAULT_URI
    dry_run = bool(getattr(args, "dry_run_local", False) or getattr(args, "dry_run", False))
    return uri, dry_run


def parse_snapshot_list_names(output: str) -> list[str]:
    names: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Name") or set(stripped) == {"-"}:
            continue
        parts = line.split()
        if parts:
            names.append(parts[0])
    return names


def parse_active_disk_path(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Target") or set(stripped) == {"-"}:
            continue
        parts = line.split()
        if len(parts) >= 2:
            return parts[-1]
    raise ValueError("Could not find an active disk path in domblklist output")


def parse_backing_chain_images(output: str) -> list[str]:
    images: list[str] = []
    for line in output.splitlines():
        if line.startswith("image: "):
            images.append(line.split(": ", 1)[1].strip())
    if not images:
        raise ValueError("Could not find any image entries in qemu-img output")
    return images


def parse_dom_state(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("State:"):
            return line.split(":", 1)[1].strip()
    raise ValueError("Could not determine VM state from dominfo output")


def split_overlays_and_base(images: list[str]) -> tuple[list[str], str]:
    if len(images) < 1:
        raise ValueError("Expected at least one image in the backing chain")
    if len(images) == 1:
        return [], images[0]
    return images[:-1], images[-1]


def validate_mode_selection(
    *,
    mode: str,
    available_snapshots: list[str],
    selected_snapshots: list[str],
    confirm_all: bool,
) -> None:
    available = set(available_snapshots)
    selected = set(selected_snapshots)
    missing = sorted(selected - available)
    if missing:
        raise ValueError(f"Unknown snapshot(s): {', '.join(missing)}")

    if mode == "metadata-only":
        if not selected_snapshots:
            raise ValueError("metadata-only mode requires at least one snapshot")
        return

    if mode != "reclaim-space":
        raise ValueError(f"Unsupported mode: {mode}")

    if not confirm_all:
        raise ValueError("reclaim-space mode requires confirm_all")
    if selected != available:
        raise ValueError("reclaim-space mode requires all available snapshots")


def replace_disk_source_path(*, xml: str, old_path: str, new_path: str) -> str:
    root = ET.fromstring(xml)
    matches: list[ET.Element] = []
    for disk in root.findall(".//devices/disk[@device='disk']"):
        source = disk.find("source")
        if source is not None and source.get("file") == old_path:
            matches.append(source)
    if len(matches) != 1:
        raise ValueError("Expected exactly one disk source match in domain XML")
    matches[0].set("file", new_path)
    return ET.tostring(root, encoding="unicode")


def run_command(args: list[str], *, dry_run: bool = False) -> str:
    print("+", " ".join(args), file=sys.stderr)
    if dry_run:
        return ""
    completed = subprocess.run(
        args,
        check=True,
        text=True,
        capture_output=True,
    )
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    return completed.stdout


def virsh(uri: str, *args: str, dry_run: bool = False) -> str:
    return run_command(["virsh", "-c", uri, *args], dry_run=dry_run)


def sudo_qemu_img(*args: str, dry_run: bool = False) -> str:
    return run_command(["sudo", "-n", "qemu-img", *args], dry_run=dry_run)


def sudo_rm(paths: Iterable[str], *, dry_run: bool = False) -> str:
    return run_command(["sudo", "-n", "rm", "-f", *paths], dry_run=dry_run)


def sudo_test_exists(path: str, *, dry_run: bool = False) -> None:
    run_command(["sudo", "-n", "test", "-e", path], dry_run=dry_run)


def sudo_test_missing(path: str, *, dry_run: bool = False) -> None:
    run_command(["sudo", "-n", "test", "!", "-e", path], dry_run=dry_run)


def get_snapshot_names(*, vm: str, uri: str, dry_run: bool = False) -> list[str]:
    output = virsh(uri, "snapshot-list", vm, dry_run=dry_run)
    if not output.strip():
        return []
    return parse_snapshot_list_names(output)


def get_vm_disk_chain(*, vm: str, uri: str, dry_run: bool = False) -> VmDiskChain:
    domblklist_output = virsh(uri, "domblklist", vm, dry_run=dry_run)
    if not domblklist_output.strip():
        return VmDiskChain(active_disk="<active>", overlays=[], base_disk="<base>")
    active_disk = parse_active_disk_path(domblklist_output)
    chain_output = sudo_qemu_img("info", "--backing-chain", active_disk, dry_run=dry_run)
    if not chain_output.strip():
        return VmDiskChain(active_disk=active_disk, overlays=[], base_disk="<base>")
    images = parse_backing_chain_images(chain_output)
    overlays, base_disk = split_overlays_and_base(images)
    return VmDiskChain(active_disk=active_disk, overlays=overlays, base_disk=base_disk)


def redefine_vm_disk(*, vm: str, uri: str, old_path: str, new_path: str, dry_run: bool = False) -> None:
    xml = virsh(uri, "dumpxml", vm, dry_run=dry_run)
    if dry_run:
        if xml.strip():
            replace_disk_source_path(xml=xml, old_path=old_path, new_path=new_path)
        run_command(["virsh", "-c", uri, "define", "<temp-xml>"], dry_run=True)
        return
    updated_xml = replace_disk_source_path(xml=xml, old_path=old_path, new_path=new_path)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as handle:
        handle.write(updated_xml)
        temp_path = handle.name
    try:
        virsh(uri, "define", temp_path, dry_run=False)
    finally:
        pathlib.Path(temp_path).unlink(missing_ok=True)


def emit_summary(
    *,
    mode: str,
    vm: str,
    uri: str,
    deleted_snapshots: list[str],
    active_disk: str | None,
    base_disk: str | None,
    overlays: list[str],
    dry_run: bool,
) -> None:
    print(
        json.dumps(
            {
                "mode": mode,
                "vm": vm,
                "uri": uri,
                "deleted_snapshots": deleted_snapshots,
                "active_disk": active_disk,
                "base_disk": base_disk,
                "overlays": overlays,
                "dry_run": dry_run,
            }
        )
    )


def cmd_list(args: argparse.Namespace) -> int:
    uri, dry_run = resolve_common_args(args)
    snapshots = get_snapshot_names(vm=args.vm, uri=uri, dry_run=dry_run)
    if dry_run:
        return 0
    for snapshot in snapshots:
        print(snapshot)
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    uri, dry_run = resolve_common_args(args)
    snapshots = get_snapshot_names(vm=args.vm, uri=uri, dry_run=dry_run)
    selected = snapshots if args.all else args.snapshot
    validate_mode_selection(
        mode=args.mode,
        available_snapshots=snapshots,
        selected_snapshots=selected,
        confirm_all=args.all,
    )

    if args.mode == "reclaim-space":
        dominfo_output = virsh(uri, "dominfo", args.vm, dry_run=dry_run)
        if dominfo_output.strip() and parse_dom_state(dominfo_output) != "shut off":
            raise ValueError("VM must be shut off before reclaim-space")

    for snapshot in selected:
        virsh(uri, "snapshot-delete", args.vm, "--snapshotname", snapshot, "--metadata", dry_run=dry_run)

    virsh(uri, "snapshot-list", args.vm, dry_run=dry_run)

    if args.mode == "metadata-only":
        domblklist_output = virsh(uri, "domblklist", args.vm, dry_run=dry_run)
        active_disk = parse_active_disk_path(domblklist_output) if domblklist_output.strip() else None
        emit_summary(
            mode=args.mode,
            vm=args.vm,
            uri=uri,
            deleted_snapshots=selected,
            active_disk=active_disk,
            base_disk=None,
            overlays=[],
            dry_run=dry_run,
        )
        return 0

    chain = get_vm_disk_chain(vm=args.vm, uri=uri, dry_run=dry_run)
    for overlay in chain.overlays:
        sudo_qemu_img("commit", "-p", overlay, dry_run=dry_run)

    redefine_vm_disk(
        vm=args.vm,
        uri=uri,
        old_path=chain.active_disk,
        new_path=chain.base_disk,
        dry_run=dry_run,
    )

    virsh(uri, "domblklist", args.vm, dry_run=dry_run)
    sudo_qemu_img("info", "--backing-chain", chain.base_disk, dry_run=dry_run)

    sudo_rm(chain.overlays, dry_run=dry_run)
    sudo_test_exists(chain.base_disk, dry_run=dry_run)
    for overlay in chain.overlays:
        sudo_test_missing(overlay, dry_run=dry_run)
    emit_summary(
        mode=args.mode,
        vm=args.vm,
        uri=uri,
        deleted_snapshots=selected,
        active_disk=chain.active_disk,
        base_disk=chain.base_disk,
        overlays=chain.overlays,
        dry_run=dry_run,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic helper for virsh external snapshot cleanup")
    parser.add_argument("--uri", default=DEFAULT_URI, help="libvirt connection URI (default: qemu:///system)")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them")

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-snapshots", help="List snapshot names for a VM")
    list_parser.add_argument("--uri", dest="uri_local", help="libvirt connection URI")
    list_parser.add_argument("--dry-run", dest="dry_run_local", action="store_true", help="Print commands without executing them")
    list_parser.add_argument("--vm", required=True, help="VM name")
    list_parser.set_defaults(func=cmd_list)

    cleanup_parser = subparsers.add_parser("cleanup", help="Delete snapshot metadata or reclaim overlay space")
    cleanup_parser.add_argument("--uri", dest="uri_local", help="libvirt connection URI")
    cleanup_parser.add_argument("--dry-run", dest="dry_run_local", action="store_true", help="Print commands without executing them")
    cleanup_parser.add_argument("--vm", required=True, help="VM name")
    cleanup_parser.add_argument(
        "--mode",
        required=True,
        choices=["metadata-only", "reclaim-space"],
        help="Cleanup mode",
    )
    cleanup_parser.add_argument(
        "--snapshot",
        action="append",
        default=[],
        help="Snapshot name to delete; repeat for multiple snapshots",
    )
    cleanup_parser.add_argument(
        "--all",
        action="store_true",
        help="Required for reclaim-space; selects all snapshots",
    )
    cleanup_parser.set_defaults(func=cmd_cleanup)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
