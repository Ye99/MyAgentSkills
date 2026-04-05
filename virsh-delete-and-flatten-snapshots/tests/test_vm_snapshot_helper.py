import importlib.util
import json
import pathlib
import sys

import pytest


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "vm_snapshot_helper.py"
SPEC = importlib.util.spec_from_file_location("vm_snapshot_helper", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


SNAPSHOT_LIST_OUTPUT = """\
 Name                                                  Creation Time               State
--------------------------------------------------------------------------------------------
 2025_2026_03_01DataProcessed                          2026-03-06 00:20:37 -0800   shutoff
 Backup                                                2026-02-14 00:03:41 -0800   shutoff
 InstalledCodingAgents_beforeProcessing2025_2026Data   2026-03-04 00:26:07 -0800   shutoff
"""


DOMBLKLIST_OUTPUT = """\
 Target   Source
---------------------------------------------------------------------------------
 vda      /var/lib/libvirt/images/process-photo-vm.2025_2026_03_01DataProcessed
"""


BACKING_CHAIN_OUTPUT = """\
image: /var/lib/libvirt/images/process-photo-vm.2025_2026_03_01DataProcessed
file format: qcow2
backing file: /var/lib/libvirt/images/process-photo-vm.InstalledCodingAgents_beforeProcessing2025_2026Data

image: /var/lib/libvirt/images/process-photo-vm.InstalledCodingAgents_beforeProcessing2025_2026Data
file format: qcow2
backing file: /var/lib/libvirt/images/process-photo-vm.qcow2

image: /var/lib/libvirt/images/process-photo-vm.qcow2
file format: qcow2
"""


def test_parse_snapshot_list_names() -> None:
    assert MODULE.parse_snapshot_list_names(SNAPSHOT_LIST_OUTPUT) == [
        "2025_2026_03_01DataProcessed",
        "Backup",
        "InstalledCodingAgents_beforeProcessing2025_2026Data",
    ]


def test_parse_snapshot_list_names_empty() -> None:
    output = """\
 Name   Creation Time   State
-------------------------------
"""
    assert MODULE.parse_snapshot_list_names(output) == []


def test_parse_active_disk_path() -> None:
    assert (
        MODULE.parse_active_disk_path(DOMBLKLIST_OUTPUT)
        == "/var/lib/libvirt/images/process-photo-vm.2025_2026_03_01DataProcessed"
    )


def test_parse_backing_chain_images() -> None:
    assert MODULE.parse_backing_chain_images(BACKING_CHAIN_OUTPUT) == [
        "/var/lib/libvirt/images/process-photo-vm.2025_2026_03_01DataProcessed",
        "/var/lib/libvirt/images/process-photo-vm.InstalledCodingAgents_beforeProcessing2025_2026Data",
        "/var/lib/libvirt/images/process-photo-vm.qcow2",
    ]


def test_split_overlays_and_base() -> None:
    overlays, base = MODULE.split_overlays_and_base(
        MODULE.parse_backing_chain_images(BACKING_CHAIN_OUTPUT)
    )
    assert overlays == [
        "/var/lib/libvirt/images/process-photo-vm.2025_2026_03_01DataProcessed",
        "/var/lib/libvirt/images/process-photo-vm.InstalledCodingAgents_beforeProcessing2025_2026Data",
    ]
    assert base == "/var/lib/libvirt/images/process-photo-vm.qcow2"


def test_metadata_only_accepts_subset() -> None:
    MODULE.validate_mode_selection(
        mode="metadata-only",
        available_snapshots=["a", "b"],
        selected_snapshots=["a"],
        confirm_all=False,
    )


def test_reclaim_space_requires_confirm_all() -> None:
    with pytest.raises(ValueError, match="requires confirm_all"):
        MODULE.validate_mode_selection(
            mode="reclaim-space",
            available_snapshots=["a", "b"],
            selected_snapshots=[],
            confirm_all=False,
        )


def test_reclaim_space_requires_all_snapshots() -> None:
    with pytest.raises(ValueError, match="all available snapshots"):
        MODULE.validate_mode_selection(
            mode="reclaim-space",
            available_snapshots=["a", "b"],
            selected_snapshots=["a"],
            confirm_all=True,
        )


def test_validate_mode_selection_rejects_unknown_snapshot() -> None:
    with pytest.raises(ValueError, match="Unknown snapshot"):
        MODULE.validate_mode_selection(
            mode="metadata-only",
            available_snapshots=["a", "b"],
            selected_snapshots=["c"],
            confirm_all=False,
        )


def test_replace_disk_source_path() -> None:
    xml = """\
<domain>
  <devices>
    <disk type='file' device='disk'>
      <source file='/old/overlay.qcow2'/>
    </disk>
  </devices>
</domain>
"""
    updated = MODULE.replace_disk_source_path(
        xml=xml,
        old_path="/old/overlay.qcow2",
        new_path="/base/disk.qcow2",
    )
    assert "/base/disk.qcow2" in updated
    assert "/old/overlay.qcow2" not in updated


def test_replace_disk_source_path_requires_exact_match() -> None:
    xml = "<domain><devices/></domain>"
    with pytest.raises(ValueError, match="exactly one"):
        MODULE.replace_disk_source_path(
            xml=xml,
            old_path="/old/overlay.qcow2",
            new_path="/base/disk.qcow2",
        )


def test_cleanup_reclaim_space_dry_run_plans_commands(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    commands: list[list[str]] = []

    snapshot_output = """\
 Name   Creation Time   State
-------------------------------
 snap1  2026-04-01      shutoff
 snap2  2026-04-02      shutoff
"""
    domblklist_output = """\
 Target   Source
----------------------------------------------------------
 vda      /var/lib/libvirt/images/vm.snap2
"""
    backing_chain_output = """\
image: /var/lib/libvirt/images/vm.snap2
file format: qcow2
backing file: /var/lib/libvirt/images/vm.snap1

image: /var/lib/libvirt/images/vm.snap1
file format: qcow2
backing file: /var/lib/libvirt/images/vm.qcow2

image: /var/lib/libvirt/images/vm.qcow2
file format: qcow2
"""
    dumpxml_output = """\
<domain>
  <devices>
    <disk type='file' device='disk'>
      <source file='/var/lib/libvirt/images/vm.snap2'/>
    </disk>
  </devices>
</domain>
"""

    def fake_run_command(args: list[str], *, dry_run: bool = False) -> str:
        commands.append(args)
        if args[:4] == ["virsh", "-c", "qemu:///system", "snapshot-list"]:
            return snapshot_output
        if args[:4] == ["virsh", "-c", "qemu:///system", "domblklist"]:
            return domblklist_output
        if args[:4] == ["virsh", "-c", "qemu:///system", "dominfo"]:
            return "State: shut off\n"
        if args[:4] == ["virsh", "-c", "qemu:///system", "dumpxml"]:
            return dumpxml_output
        if args[:4] == ["sudo", "-n", "qemu-img", "info"]:
            return backing_chain_output
        return ""

    monkeypatch.setattr(MODULE, "run_command", fake_run_command)

    exit_code = MODULE.main(
        [
            "cleanup",
            "--vm",
            "vm",
            "--mode",
            "reclaim-space",
            "--all",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert commands == [
        ["virsh", "-c", "qemu:///system", "snapshot-list", "vm"],
        ["virsh", "-c", "qemu:///system", "dominfo", "vm"],
        ["virsh", "-c", "qemu:///system", "snapshot-delete", "vm", "--snapshotname", "snap1", "--metadata"],
        ["virsh", "-c", "qemu:///system", "snapshot-delete", "vm", "--snapshotname", "snap2", "--metadata"],
        ["virsh", "-c", "qemu:///system", "snapshot-list", "vm"],
        ["virsh", "-c", "qemu:///system", "domblklist", "vm"],
        ["sudo", "-n", "qemu-img", "info", "--backing-chain", "/var/lib/libvirt/images/vm.snap2"],
        ["sudo", "-n", "qemu-img", "commit", "-p", "/var/lib/libvirt/images/vm.snap2"],
        ["sudo", "-n", "qemu-img", "commit", "-p", "/var/lib/libvirt/images/vm.snap1"],
        ["virsh", "-c", "qemu:///system", "dumpxml", "vm"],
        ["virsh", "-c", "qemu:///system", "define", "<temp-xml>"],
        ["virsh", "-c", "qemu:///system", "domblklist", "vm"],
        ["sudo", "-n", "qemu-img", "info", "--backing-chain", "/var/lib/libvirt/images/vm.qcow2"],
        ["sudo", "-n", "rm", "-f", "/var/lib/libvirt/images/vm.snap2", "/var/lib/libvirt/images/vm.snap1"],
        ["sudo", "-n", "test", "-e", "/var/lib/libvirt/images/vm.qcow2"],
        ["sudo", "-n", "test", "!", "-e", "/var/lib/libvirt/images/vm.snap2"],
        ["sudo", "-n", "test", "!", "-e", "/var/lib/libvirt/images/vm.snap1"],
    ]
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "mode": "reclaim-space",
        "vm": "vm",
        "uri": "qemu:///system",
        "deleted_snapshots": ["snap1", "snap2"],
        "active_disk": "/var/lib/libvirt/images/vm.snap2",
        "base_disk": "/var/lib/libvirt/images/vm.qcow2",
        "overlays": [
            "/var/lib/libvirt/images/vm.snap2",
            "/var/lib/libvirt/images/vm.snap1",
        ],
        "dry_run": True,
    }


def test_cleanup_metadata_only_dry_run_plans_commands(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    commands: list[list[str]] = []

    snapshot_output = """\
 Name   Creation Time   State
-------------------------------
 snap1  2026-04-01      shutoff
 snap2  2026-04-02      shutoff
"""
    domblklist_output = """\
 Target   Source
----------------------------------------------------------
 vda      /var/lib/libvirt/images/vm.snap2
"""

    def fake_run_command(args: list[str], *, dry_run: bool = False) -> str:
        commands.append(args)
        if args[:4] == ["virsh", "-c", "qemu:///system", "snapshot-list"]:
            return snapshot_output
        if args[:4] == ["virsh", "-c", "qemu:///system", "domblklist"]:
            return domblklist_output
        return ""

    monkeypatch.setattr(MODULE, "run_command", fake_run_command)

    exit_code = MODULE.main(
        [
            "cleanup",
            "--vm",
            "vm",
            "--mode",
            "metadata-only",
            "--snapshot",
            "snap1",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert commands == [
        ["virsh", "-c", "qemu:///system", "snapshot-list", "vm"],
        ["virsh", "-c", "qemu:///system", "snapshot-delete", "vm", "--snapshotname", "snap1", "--metadata"],
        ["virsh", "-c", "qemu:///system", "snapshot-list", "vm"],
        ["virsh", "-c", "qemu:///system", "domblklist", "vm"],
    ]
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "mode": "metadata-only",
        "vm": "vm",
        "uri": "qemu:///system",
        "deleted_snapshots": ["snap1"],
        "active_disk": "/var/lib/libvirt/images/vm.snap2",
        "base_disk": None,
        "overlays": [],
        "dry_run": True,
    }


def test_cleanup_reclaim_space_refuses_non_shutoff_vm(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot_output = """\
 Name   Creation Time   State
-------------------------------
 snap1  2026-04-01      shutoff
"""

    def fake_run_command(args: list[str], *, dry_run: bool = False) -> str:
        if args[:4] == ["virsh", "-c", "qemu:///system", "snapshot-list"]:
            return snapshot_output
        if args[:4] == ["virsh", "-c", "qemu:///system", "domblklist"]:
            return " Target   Source\n vda /var/lib/libvirt/images/vm.snap1\n"
        if args[:4] == ["virsh", "-c", "qemu:///system", "dominfo"]:
            return "State: running\n"
        return ""

    monkeypatch.setattr(MODULE, "run_command", fake_run_command)

    with pytest.raises(ValueError, match="must be shut off"):
        MODULE.main(
            [
                "cleanup",
                "--vm",
                "vm",
                "--mode",
                "reclaim-space",
                "--all",
                "--dry-run",
            ]
        )
