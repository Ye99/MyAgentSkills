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
    target: str = ""
    remaining_chain: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Disk:
    target: str
    path: str


# virsh subcommands that only read; they run even in --dry-run so the plan shown is the real one.
READ_ONLY_VIRSH = {"snapshot-list", "snapshot-dumpxml", "dominfo", "domblklist", "dumpxml", "list"}


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


def parse_disks(output: str) -> list[Disk]:
    """Disk devices with a source file from `virsh domblklist --details` (CD-ROMs and empty drives skipped)."""
    disks: list[Disk] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Type") or set(stripped) == {"-"}:
            continue
        parts = stripped.split(None, 3)
        if len(parts) == 4 and parts[1] == "disk" and parts[3] != "-":
            disks.append(Disk(target=parts[2], path=parts[3]))
    return disks


def parse_snapshot_overlays(xml: str) -> dict[str, str]:
    """{target: overlay file} for the external overlays a snapshot created (`virsh snapshot-dumpxml`)."""
    overlays: dict[str, str] = {}
    root = ET.fromstring(xml)
    for disk in root.findall("./disks/disk"):
        source = disk.find("source")
        if disk.get("snapshot") == "external" and source is not None and source.get("file"):
            overlays[disk.get("name", "")] = source.get("file")
    return overlays


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
    return run_command(["virsh", "-c", uri, *args], dry_run=dry_run and args[0] not in READ_ONLY_VIRSH)


def sudo_qemu_img(*args: str, dry_run: bool = False) -> str:
    return run_command(["sudo", "-n", "qemu-img", *args], dry_run=dry_run and args[0] != "info")


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


def get_disks(*, vm: str, uri: str, dry_run: bool = False) -> list[Disk]:
    return parse_disks(virsh(uri, "domblklist", vm, "--details", dry_run=dry_run))


def backing_chain(path: str, *, force_share: bool = False, dry_run: bool = False) -> list[str]:
    extra = ["-U"] if force_share else []  # another domain may be running and hold the image lock
    return parse_backing_chain_images(sudo_qemu_img("info", *extra, "--backing-chain", path, dry_run=dry_run))


def plan_reclaim(*, vm: str, uri: str, snapshots: list[str], dry_run: bool = False) -> list[VmDiskChain]:
    """Work out, before anything is changed, which overlays to commit for every disk.

    Only overlays the snapshots created (from snapshot-dumpxml) are committed, from the top of each
    chain down to the first image no snapshot created, which becomes the disk's new source. For a
    linked clone that is the VM's own disk, and the shared template below it is never written. Refuses
    when any image it would write or delete is in another domain's backing chain.
    """
    created: set[str] = set()
    for snapshot in snapshots:
        created.update(parse_snapshot_overlays(virsh(uri, "snapshot-dumpxml", vm, snapshot, dry_run=dry_run)).values())
    disks = get_disks(vm=vm, uri=uri, dry_run=dry_run)
    if not disks:
        raise ValueError(f"No disk with a source file found for {vm}")
    plans: list[VmDiskChain] = []
    for disk in disks:
        chain = backing_chain(disk.path, dry_run=dry_run)
        k = 0
        while k < len(chain) and chain[k] in created:
            k += 1
        stray = [image for image in chain[k:] if image in created]
        if stray:
            raise ValueError(
                f"Disk {disk.target}: {chain[k]} sits above snapshot overlay {stray[0]} but no snapshot created it; "
                "flatten this chain by hand")
        if k == len(chain):
            raise ValueError(f"Disk {disk.target}: no base image below the snapshot overlays {chain}")
        if k:
            plans.append(VmDiskChain(active_disk=disk.path, overlays=chain[:k], base_disk=chain[k],
                                     target=disk.target, remaining_chain=tuple(chain[k:])))
    touched = {image for plan in plans for image in [*plan.overlays, plan.base_disk]}
    for line in virsh(uri, "list", "--all", "--name", dry_run=dry_run).splitlines():
        other = line.strip()
        if not other or other == vm:
            continue
        for disk in get_disks(vm=other, uri=uri, dry_run=dry_run):
            shared = touched & set(backing_chain(disk.path, force_share=True, dry_run=dry_run))
            if shared:
                raise ValueError(
                    f"{sorted(shared)[0]} is in the backing chain of domain {other} ({disk.target}); "
                    "committing into it or deleting it would corrupt that domain")
    return plans


def redefine_vm_disks(*, vm: str, uri: str, replacements: list[tuple[str, str]], dry_run: bool = False) -> None:
    xml = virsh(uri, "dumpxml", vm, dry_run=dry_run)
    updated_xml = xml
    for old_path, new_path in replacements:
        updated_xml = replace_disk_source_path(xml=updated_xml, old_path=old_path, new_path=new_path)
    if dry_run:
        run_command(["virsh", "-c", uri, "define", "<temp-xml>"], dry_run=True)
        return
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
    disks: list[dict] | None = None,
) -> None:
    summary = {
        "mode": mode,
        "vm": vm,
        "uri": uri,
        "deleted_snapshots": deleted_snapshots,
        "active_disk": active_disk,
        "base_disk": base_disk,
        "overlays": overlays,
        "dry_run": dry_run,
    }
    if disks is not None:
        summary["disks"] = disks
    print(json.dumps(summary))


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

    plans: list[VmDiskChain] = []
    if args.mode == "reclaim-space":
        dominfo_output = virsh(uri, "dominfo", args.vm, dry_run=dry_run)
        if dominfo_output.strip() and parse_dom_state(dominfo_output) != "shut off":
            raise ValueError("VM must be shut off before reclaim-space")
        # Everything is validated before the first change: the snapshot metadata is needed to know
        # which overlays the snapshots created, and is gone once deleted.
        plans = plan_reclaim(vm=args.vm, uri=uri, snapshots=selected, dry_run=dry_run)

    for snapshot in selected:
        virsh(uri, "snapshot-delete", args.vm, "--snapshotname", snapshot, "--metadata", dry_run=dry_run)

    virsh(uri, "snapshot-list", args.vm, dry_run=dry_run)

    if args.mode == "metadata-only":
        disks = get_disks(vm=args.vm, uri=uri, dry_run=dry_run)
        emit_summary(
            mode=args.mode,
            vm=args.vm,
            uri=uri,
            deleted_snapshots=selected,
            active_disk=disks[0].path if disks else None,
            base_disk=None,
            overlays=[],
            dry_run=dry_run,
        )
        return 0

    for plan in plans:
        for overlay in plan.overlays:  # top to bottom
            sudo_qemu_img("commit", "-p", overlay, dry_run=dry_run)

    if plans:
        redefine_vm_disks(
            vm=args.vm,
            uri=uri,
            replacements=[(plan.active_disk, plan.base_disk) for plan in plans],
            dry_run=dry_run,
        )

    # Verify before deleting anything: each disk now points at its new base, and that base's chain is
    # exactly what was below the overlays (no backing file for a plain VM, only the template for a
    # linked clone), so no overlay about to be deleted is still in use.
    sources = {disk.target: disk.path for disk in get_disks(vm=args.vm, uri=uri, dry_run=dry_run)}
    for plan in plans:
        chain = backing_chain(plan.base_disk, dry_run=dry_run)
        if dry_run:
            continue
        if sources.get(plan.target) != plan.base_disk:
            raise ValueError(f"Disk {plan.target} points at {sources.get(plan.target)}, not {plan.base_disk}; "
                             "overlays kept")
        if tuple(chain) != plan.remaining_chain:
            raise ValueError(f"Unexpected backing chain for {plan.base_disk}: {chain}; overlays kept")

    overlays = [overlay for plan in plans for overlay in plan.overlays]
    if overlays:
        sudo_rm(overlays, dry_run=dry_run)
    for plan in plans:
        sudo_test_exists(plan.base_disk, dry_run=dry_run)
    for overlay in overlays:
        sudo_test_missing(overlay, dry_run=dry_run)
    emit_summary(
        mode=args.mode,
        vm=args.vm,
        uri=uri,
        deleted_snapshots=selected,
        active_disk=plans[0].active_disk if plans else None,
        base_disk=plans[0].base_disk if plans else None,
        overlays=overlays,
        dry_run=dry_run,
        disks=[{"target": p.target, "active_disk": p.active_disk, "base_disk": p.base_disk,
                "overlays": p.overlays} for p in plans],
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
