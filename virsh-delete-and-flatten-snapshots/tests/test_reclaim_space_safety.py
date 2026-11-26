"""reclaim-space against a fake libvirt host: what gets committed, repointed and deleted.

FakeHost answers every virsh / qemu-img / rm / test command the helper runs, so nothing here touches
a real hypervisor or disk.
"""
import importlib.util
import pathlib
import sys
import xml.etree.ElementTree as ET

import pytest

SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "vm_snapshot_helper.py"
SPEC = importlib.util.spec_from_file_location("vm_snapshot_helper_safety", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

IMG = "/var/lib/libvirt/images/"


class FakeHost:
    def __init__(self):
        self.backing = {}                 # image -> its backing file (None for a base)
        self.files = set()
        self.domains = {}                 # name -> {"state", "disks": [[device, target, path]], "snapshots": {name: {target: overlay}}}
        self.calls = []
        self.committed = []
        self.commit_rewires = True        # a commit leaves the image below with its own backing intact

    def image(self, path, backing=None):
        self.backing[path] = backing
        self.files.add(path)

    def chain(self, path):
        out = []
        while path is not None:
            out.append(path)
            path = self.backing.get(path)
        return out

    def domain_xml(self, name):
        disks = "".join(
            f"<disk type='file' device='{dev}'><target dev='{tgt}'/>"
            + (f"<source file='{src}'/>" if src != "-" else "") + "</disk>"
            for dev, tgt, src in self.domains[name]["disks"])
        return f"<domain><name>{name}</name><devices>{disks}</devices></domain>"

    def run(self, args, *, dry_run=False):
        self.calls.append(args)
        if args[0] == "virsh":
            cmd, rest = args[3], args[4:]
            if cmd == "list":
                return "".join(n + "\n" for n in self.domains) + "\n"
            if cmd == "define":
                if dry_run:
                    return ""
                root = ET.parse(rest[0]).getroot()
                name = root.findtext("name")
                self.domains[name]["disks"] = [
                    [d.get("device"), d.find("target").get("dev"),
                     d.find("source").get("file") if d.find("source") is not None else "-"]
                    for d in root.iter("disk")]
                return ""
            dom = self.domains[rest[0]]
            if cmd == "snapshot-list":
                return " Name  Creation Time  State\n----------\n" + "".join(
                    f" {s}  2026-01-01  shutoff\n" for s in dom["snapshots"])
            if cmd == "snapshot-dumpxml":
                disks = "".join(f"<disk name='{t}' snapshot='external'><source file='{o}'/></disk>"
                                for t, o in dom["snapshots"][rest[1]].items())
                return f"<domainsnapshot><name>{rest[1]}</name><disks>{disks}</disks></domainsnapshot>"
            if cmd == "dominfo":
                return f"Name: {rest[0]}\nState:          {dom['state']}\n"
            if cmd == "domblklist":
                assert "--details" in rest
                return " Type   Device   Target   Source\n-----------------------------\n" + "".join(
                    f" file   {d}   {t}   {s}\n" for d, t, s in dom["disks"])
            if cmd == "dumpxml":
                return self.domain_xml(rest[0])
            if cmd == "snapshot-delete":
                if not dry_run:
                    del dom["snapshots"][rest[2]]
                return ""
        if args[:3] == ["sudo", "-n", "qemu-img"]:
            sub = args[3]
            if sub == "info":
                return "".join(f"image: {p}\nfile format: qcow2\n" +
                               (f"backing file: {self.backing[p]}\n" if self.backing[p] else "") + "\n"
                               for p in self.chain(args[-1]))
            if sub == "commit":
                if not dry_run:
                    self.committed.append(args[-1])
                    if not self.commit_rewires:   # a broken commit: the image below now backs onto the overlay
                        below = self.backing[args[-1]]
                        self.backing[args[-1]] = None
                        self.backing[below] = args[-1]
                return ""
        if args[:3] == ["sudo", "-n", "rm"]:
            if not dry_run:
                self.files -= set(args[4:])
            return ""
        if args[:3] == ["sudo", "-n", "test"]:
            return ""
        raise AssertionError(f"unexpected command {args}")


@pytest.fixture
def host(monkeypatch):
    h = FakeHost()
    monkeypatch.setattr(MODULE, "run_command", h.run)
    return h


def reclaim(*extra):
    return MODULE.main(["cleanup", "--vm", "vm", "--mode", "reclaim-space", "--all", *extra])


def deleted_snapshots(h):
    return [c for c in h.calls if "snapshot-delete" in c]


def test_plain_chain_is_flattened_to_its_base(host):
    host.image(IMG + "vm.qcow2")
    host.image(IMG + "vm.snap1", IMG + "vm.qcow2")
    host.image(IMG + "vm.snap2", IMG + "vm.snap1")
    host.domains["vm"] = {"state": "shut off", "disks": [["disk", "vda", IMG + "vm.snap2"]],
                          "snapshots": {"snap1": {"vda": IMG + "vm.snap1"}, "snap2": {"vda": IMG + "vm.snap2"}}}
    assert reclaim() == 0
    assert host.committed == [IMG + "vm.snap2", IMG + "vm.snap1"]
    assert host.domains["vm"]["disks"] == [["disk", "vda", IMG + "vm.qcow2"]]
    assert host.files == {IMG + "vm.qcow2"}


def test_linked_clone_never_commits_into_the_shared_template(host):
    host.image(IMG + "template.qcow2")
    host.image(IMG + "vm.qcow2", IMG + "template.qcow2")      # linked clone, made with qemu-img create -b
    host.image(IMG + "vm.snap1", IMG + "vm.qcow2")             # the only overlay a snapshot created
    host.image(IMG + "other.qcow2", IMG + "template.qcow2")
    host.domains["vm"] = {"state": "shut off", "disks": [["disk", "vda", IMG + "vm.snap1"]],
                          "snapshots": {"snap1": {"vda": IMG + "vm.snap1"}}}
    host.domains["other"] = {"state": "running", "disks": [["disk", "vda", IMG + "other.qcow2"]], "snapshots": {}}
    assert reclaim() == 0
    assert host.committed == [IMG + "vm.snap1"]
    assert host.domains["vm"]["disks"] == [["disk", "vda", IMG + "vm.qcow2"]]
    assert IMG + "vm.qcow2" in host.files and IMG + "template.qcow2" in host.files
    assert host.files == {IMG + "template.qcow2", IMG + "vm.qcow2", IMG + "other.qcow2"}


def test_refuses_when_an_image_it_would_write_backs_another_domain(host):
    host.image(IMG + "vm.qcow2")
    host.image(IMG + "vm.snap1", IMG + "vm.qcow2")
    host.image(IMG + "clone.qcow2", IMG + "vm.qcow2")          # another VM is a linked clone of this disk
    host.domains["vm"] = {"state": "shut off", "disks": [["disk", "vda", IMG + "vm.snap1"]],
                          "snapshots": {"snap1": {"vda": IMG + "vm.snap1"}}}
    host.domains["clone"] = {"state": "shut off", "disks": [["disk", "vda", IMG + "clone.qcow2"]],
                             "snapshots": {}}
    with pytest.raises(ValueError, match="clone"):
        reclaim()
    assert deleted_snapshots(host) == [] and host.committed == []
    assert len(host.files) == 3


def test_a_cdrom_listed_first_is_not_taken_for_the_disk(host):
    host.image(IMG + "vm.qcow2")
    host.image(IMG + "vm.snap1", IMG + "vm.qcow2")
    host.domains["vm"] = {"state": "shut off",
                          "disks": [["cdrom", "sda", "/isos/ubuntu.iso"], ["disk", "vda", IMG + "vm.snap1"]],
                          "snapshots": {"snap1": {"vda": IMG + "vm.snap1"}}}
    assert reclaim() == 0
    assert host.committed == [IMG + "vm.snap1"]
    assert host.domains["vm"]["disks"][1] == ["disk", "vda", IMG + "vm.qcow2"]
    assert host.domains["vm"]["disks"][0] == ["cdrom", "sda", "/isos/ubuntu.iso"]


def test_every_disk_is_flattened(host):
    for d in ("vda", "vdb"):
        host.image(IMG + f"vm-{d}.qcow2")
        host.image(IMG + f"vm-{d}.snap1", IMG + f"vm-{d}.qcow2")
    host.domains["vm"] = {"state": "shut off",
                          "disks": [["disk", "vda", IMG + "vm-vda.snap1"], ["disk", "vdb", IMG + "vm-vdb.snap1"]],
                          "snapshots": {"snap1": {"vda": IMG + "vm-vda.snap1", "vdb": IMG + "vm-vdb.snap1"}}}
    assert reclaim() == 0
    assert sorted(host.committed) == [IMG + "vm-vda.snap1", IMG + "vm-vdb.snap1"]
    assert host.domains["vm"]["disks"] == [["disk", "vda", IMG + "vm-vda.qcow2"],
                                           ["disk", "vdb", IMG + "vm-vdb.qcow2"]]
    assert host.files == {IMG + "vm-vda.qcow2", IMG + "vm-vdb.qcow2"}


def test_chain_problems_are_found_before_any_snapshot_metadata_is_deleted(host):
    host.image(IMG + "vm.qcow2")
    host.image(IMG + "vm.snap1", IMG + "vm.qcow2")
    host.image(IMG + "vm.manual", IMG + "vm.snap1")            # top image no snapshot created
    host.domains["vm"] = {"state": "shut off", "disks": [["disk", "vda", IMG + "vm.manual"]],
                          "snapshots": {"snap1": {"vda": IMG + "vm.snap1"}}}
    with pytest.raises(ValueError, match="vm.manual"):
        reclaim()
    assert deleted_snapshots(host) == [] and host.committed == []


def test_overlays_are_not_deleted_when_the_new_base_still_uses_them(host):
    host.image(IMG + "vm.qcow2")
    host.image(IMG + "vm.snap1", IMG + "vm.qcow2")
    host.domains["vm"] = {"state": "shut off", "disks": [["disk", "vda", IMG + "vm.snap1"]],
                          "snapshots": {"snap1": {"vda": IMG + "vm.snap1"}}}
    host.commit_rewires = False
    with pytest.raises(ValueError, match="backing"):
        reclaim()
    assert IMG + "vm.snap1" in host.files
    assert not any(c[:3] == ["sudo", "-n", "rm"] for c in host.calls)


def test_dry_run_reads_the_real_chain_and_changes_nothing(host):
    host.image(IMG + "template.qcow2")
    host.image(IMG + "vm.qcow2", IMG + "template.qcow2")
    host.image(IMG + "vm.snap1", IMG + "vm.qcow2")
    host.domains["vm"] = {"state": "shut off", "disks": [["disk", "vda", IMG + "vm.snap1"]],
                          "snapshots": {"snap1": {"vda": IMG + "vm.snap1"}}}
    before = set(host.files)
    assert reclaim("--dry-run") == 0
    assert host.files == before and host.committed == []
    assert host.domains["vm"]["snapshots"]
    planned = [c for c in host.calls if c[:4] == ["sudo", "-n", "qemu-img", "commit"]]
    assert planned == [["sudo", "-n", "qemu-img", "commit", "-p", IMG + "vm.snap1"]]
