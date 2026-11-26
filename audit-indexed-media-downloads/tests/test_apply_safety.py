"""apply re-checks hashes before deleting and refuses before changing anything."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_indexed_media.py"
spec = importlib.util.spec_from_file_location("audit_indexed_media", SCRIPT)
aim = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = aim
spec.loader.exec_module(aim)


def _plan(directory):
    files = aim.inventory(directory)
    actions, conflicts = aim.plan_changes(files)
    assert not conflicts
    return files, actions


def test_duplicate_edited_after_inventory_is_not_deleted(tmp_path):
    (tmp_path / "001. Talk.mp4").write_bytes(b"video")
    (tmp_path / "001. Talk (1).mp4").write_bytes(b"video")
    files, actions = _plan(tmp_path)
    assert any(a.kind == "delete" for a in actions)
    (tmp_path / "001. Talk (1).mp4").write_bytes(b"edited video")
    with pytest.raises(OSError, match="changed since"):
        aim.apply_changes(actions, {f.path: f.digest for f in files})
    assert (tmp_path / "001. Talk (1).mp4").read_bytes() == b"edited video"
    assert (tmp_path / "001. Talk.mp4").read_bytes() == b"video"


def test_keeper_edited_after_inventory_blocks_the_delete(tmp_path):
    (tmp_path / "001. Talk.mp4").write_bytes(b"video")
    (tmp_path / "001. Talk (1).mp4").write_bytes(b"video")
    files, actions = _plan(tmp_path)
    (tmp_path / "001. Talk.mp4").write_bytes(b"changed keeper")
    with pytest.raises(OSError, match="changed since"):
        aim.apply_changes(actions, {f.path: f.digest for f in files})
    assert (tmp_path / "001. Talk (1).mp4").read_bytes() == b"video"


def test_a_blocked_rename_deletes_nothing(tmp_path):
    (tmp_path / "001. Talk.mp4").write_bytes(b"video")
    (tmp_path / "001. Talk (1).mp4").write_bytes(b"video")
    (tmp_path / "Talk.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    files, actions = _plan(tmp_path)
    target = next(a.target for a in actions if a.kind == "rename")
    target.write_text("appeared after the inventory")
    with pytest.raises(FileExistsError):
        aim.apply_changes(actions, {f.path: f.digest for f in files})
    assert (tmp_path / "001. Talk (1).mp4").exists()
    assert (tmp_path / "Talk.en.srt").exists()
