"""apply must never write to the source folder, whatever already sits in OUT."""
import json
import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import photo_cull as pc

np = pytest.importorskip("numpy")


@pytest.fixture(autouse=True)
def _feedback_stays_in_the_test_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTO_CULL_FEEDBACK", str(tmp_path / "feedback"))


def _box(typ, payload):
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


def _heic(irot=0):
    pitm = _box(b"pitm", b"\0\0\0\0" + struct.pack(">H", 1))
    ipma = b"\0\0\0\0" + struct.pack(">I", 1) + struct.pack(">HB", 1, 1) + bytes([1])
    iprp = _box(b"iprp", _box(b"ipco", _box(b"irot", bytes([irot]))) + _box(b"ipma", ipma))
    return _box(b"ftyp", b"heic\0\0\0\0") + _box(b"meta", b"\0\0\0\0" + pitm + iprp) + _box(b"mdat", b"data")


@pytest.fixture
def fake_pixels(monkeypatch):
    """Displayed pixels of a synthetic HEIC: a fixed picture turned by its irot flag (CCW quarter turns)."""
    base = np.arange(6).reshape(2, 3)

    def pixels(path):
        buf = Path(path).read_bytes()
        return np.rot90(base, buf[pc.find_primary_irot(buf)] & 3)
    monkeypatch.setattr(pc, "pixels", pixels)


def test_place_rotates_a_new_copy_and_leaves_the_source_alone(tmp_path, fake_pixels):
    s, out = tmp_path / "a.HEIC", tmp_path / "out"
    s.write_bytes(_heic())
    out.mkdir()
    before = s.read_bytes()
    fields, problem = pc._place(s, out / "a.HEIC", 1)
    assert problem is None, fields
    assert s.read_bytes() == before
    assert (out / "a.HEIC").read_bytes() != before
    assert os.listdir(out) == ["a.HEIC"]


@pytest.mark.parametrize("link", ["hardlink", "symlink"])
def test_place_never_rotates_the_source_through_a_link_in_out(tmp_path, fake_pixels, link):
    s, out = tmp_path / "a.HEIC", tmp_path / "out"
    s.write_bytes(_heic())
    out.mkdir()
    d = out / "a.HEIC"
    os.link(s, d) if link == "hardlink" else d.symlink_to(s)
    before = s.read_bytes()
    fields, problem = pc._place(s, d, 1)
    assert s.read_bytes() == before, "the source was modified through the link"
    assert problem and "link" in problem


def test_place_refuses_an_output_file_shared_with_another_name(tmp_path, fake_pixels):
    s, out = tmp_path / "a.HEIC", tmp_path / "out"
    s.write_bytes(_heic())
    out.mkdir()
    d = out / "a.HEIC"
    d.write_bytes(s.read_bytes())
    os.link(d, tmp_path / "elsewhere.HEIC")       # e.g. an rsync --link-dest backup
    fields, problem = pc._place(s, d, 1)
    assert (tmp_path / "elsewhere.HEIC").read_bytes() == s.read_bytes()
    assert problem and "link" in problem


def _work(tmp_path, names):
    src, work, out = tmp_path / "src", tmp_path / "work", tmp_path / "out"
    src.mkdir()
    work.mkdir()
    for n in names:
        (src / n).write_text(n)
    items = [{"name": n, "kind": "other", "sidecars": [],
              "size": (src / n).stat().st_size, "mtime_ns": (src / n).stat().st_mtime_ns} for n in names]
    (work / "scan.json").write_text(json.dumps({"orphan_json": [], "items": items}))
    (work / "plan.json").write_text(json.dumps({"id": pc.plan_id({}, items), "params": {}, "plan": {}}))
    ov = tmp_path / "overrides.json"
    ov.write_text(json.dumps({"_plan": pc.plan_id({}, items)}))
    return src, work, out, ov


def test_apply_keeps_a_file_whose_own_name_ends_in_part(tmp_path):
    src, work, out, ov = _work(tmp_path, ["clip.mov", "clip.mov.part"])
    pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert sorted(os.listdir(out)) == ["clip.mov", "clip.mov.part"]
    assert (out / "clip.mov.part").read_text() == "clip.mov.part"
