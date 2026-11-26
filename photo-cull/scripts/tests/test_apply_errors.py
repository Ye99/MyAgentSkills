"""apply finishes and reports when one file fails, and refuses HEIC rotation it cannot check."""
import json
import os

import pytest

import photo_cull as pc


def _work(tmp_path, names, plan=None, kind="other"):
    src, work, out = tmp_path / "src", tmp_path / "work", tmp_path / "out"
    src.mkdir()
    work.mkdir()
    for n in names:
        (src / n).write_text(n)
    items = [{"name": n, "kind": kind, "sidecars": [],
              "size": (src / n).stat().st_size, "mtime_ns": (src / n).stat().st_mtime_ns} for n in names]
    plan = plan or {}
    (work / "scan.json").write_text(json.dumps({"orphan_json": [], "items": items}))
    (work / "plan.json").write_text(json.dumps({"id": pc.plan_id(plan, items), "params": {}, "plan": plan}))
    ov = tmp_path / "overrides.json"
    ov.write_text(json.dumps({"_plan": pc.plan_id(plan, items)}))
    return src, work, out, ov


def test_one_failing_file_is_reported_and_the_rest_are_copied(tmp_path, monkeypatch):
    src, work, out, ov = _work(tmp_path, ["a.jpg", "b.jpg"])
    real = pc._place

    def flaky(s, d, turns):
        if s.name == "a.jpg":
            raise OSError("input/output error")
        return real(s, d, turns)

    monkeypatch.setattr(pc, "_place", flaky)
    with pytest.raises(SystemExit) as e:
        pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert "a.jpg" in str(e.value)
    report = json.loads((work / "apply_report.json").read_text())
    assert any("a.jpg" in p and "input/output error" in p for p in report["problems"])
    assert report["kept"]["a.jpg"]["verified"] == "ERROR"
    assert (out / "b.jpg").read_text() == "b.jpg"
    assert not [f for f in os.listdir(out) if f.startswith(pc.PART_PREFIX)]


def test_heic_rotation_is_refused_up_front_without_heif_support(tmp_path, monkeypatch):
    plan = {"a.HEIC": {"keep": True, "reason": None, "rotate": 1}}
    src, work, out, ov = _work(tmp_path, ["a.HEIC"], plan=plan, kind="image")
    monkeypatch.setattr(pc, "_heif_available", lambda: False)
    with pytest.raises(SystemExit) as e:
        pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert "pillow-heif" in str(e.value)
    assert not out.exists()


def test_scan_refuses_heic_photos_without_heif_support(tmp_path, monkeypatch):
    src, work = tmp_path / "src", tmp_path / "work"
    src.mkdir()
    work.mkdir()
    (src / "a.HEIC").write_bytes(b"x")
    monkeypatch.setattr(pc, "read_metadata", lambda *a, **k: {"a.HEIC": {"MIMEType": "image/heic"}})
    monkeypatch.setattr(pc, "_heif_available", lambda: False)
    with pytest.raises(SystemExit) as e:
        pc.scan_list(src, work, pc.Timer(work, "scan"))
    assert "pillow-heif" in str(e.value)
