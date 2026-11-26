"""A plan is only applied to the exact files it was made from."""
import json
import os

import pytest

import photo_cull as pc


def _items(src, names):
    out = []
    for n in names:
        st = (src / n).stat()
        out.append({"name": n, "kind": "other", "sidecars": [], "size": st.st_size, "mtime_ns": st.st_mtime_ns})
    return out


def _work(tmp_path, names):
    src, work, out = tmp_path / "src", tmp_path / "work", tmp_path / "out"
    src.mkdir()
    work.mkdir()
    for n in names:
        (src / n).write_text(n)
    items = _items(src, names)
    (work / "scan.json").write_text(json.dumps({"orphan_json": [], "items": items}))
    pid = pc.plan_id({}, items)
    (work / "plan.json").write_text(json.dumps({"id": pid, "params": {}, "plan": {}}))
    ov = tmp_path / "overrides.json"
    ov.write_text(json.dumps({"_plan": pid}))
    return src, work, out, ov


def test_scan_records_size_and_mtime(tmp_path, monkeypatch):
    src, work = tmp_path / "src", tmp_path / "work"
    src.mkdir()
    work.mkdir()
    (src / "a.txt").write_text("hello")
    monkeypatch.setattr(pc, "read_metadata", lambda *a, **k: {})
    items, _, _ = pc.scan_list(src, work, pc.Timer(work, "scan"))
    st = (src / "a.txt").stat()
    assert items[0]["size"] == st.st_size and items[0]["mtime_ns"] == st.st_mtime_ns


def test_scanning_again_removes_the_old_plan_and_review_page(tmp_path):
    src, work = tmp_path / "src", tmp_path / "work"
    src.mkdir()
    work.mkdir()
    (work / "plan.json").write_text("{}")
    (work / "review.html").write_text("<html>")
    pc.scan_start(src, work)
    assert not (work / "plan.json").exists()
    assert not (work / "review.html").exists()


def test_plan_id_changes_when_a_scanned_file_changes():
    items = [{"name": "a.jpg", "size": 10, "mtime_ns": 1}]
    changed = [{"name": "a.jpg", "size": 11, "mtime_ns": 2}]
    plan = {"a.jpg": {"keep": True, "rotate": 0}}
    assert pc.plan_id(plan, items) != pc.plan_id(plan, changed)
    assert pc.plan_id(plan, items) == pc.plan_id(plan, [dict(items[0])])


def test_apply_refuses_a_file_replaced_since_the_scan(tmp_path):
    src, work, out, ov = _work(tmp_path, ["a.jpg", "b.jpg"])
    (src / "b.jpg").write_text("a different photo, same name")
    with pytest.raises(SystemExit) as e:
        pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert "b.jpg" in str(e.value) and "changed" in str(e.value)
    assert not out.exists()


def test_apply_refuses_a_file_touched_since_the_scan(tmp_path):
    src, work, out, ov = _work(tmp_path, ["a.jpg"])
    st = (src / "a.jpg").stat()
    os.utime(src / "a.jpg", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    with pytest.raises(SystemExit):
        pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert not out.exists()


def test_apply_refuses_a_scan_made_before_sizes_were_recorded(tmp_path):
    src, work, out, ov = _work(tmp_path, ["a.jpg"])
    scan = json.loads((work / "scan.json").read_text())
    for it in scan["items"]:
        del it["size"], it["mtime_ns"]
    (work / "scan.json").write_text(json.dumps(scan))
    with pytest.raises(SystemExit) as e:
        pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert "scan" in str(e.value).lower()
    assert not out.exists()


def test_apply_copies_when_nothing_changed(tmp_path):
    src, work, out, ov = _work(tmp_path, ["a.jpg", "b.jpg"])
    pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert sorted(os.listdir(out)) == ["a.jpg", "b.jpg"]
