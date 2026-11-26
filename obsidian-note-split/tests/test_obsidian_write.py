"""obsidian_write.py: each file lands in one step, originals are backed up, late edits are not overwritten.

Obsidian itself is faked: the JS the script sends is recorded, and writes are applied to a temp vault.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import obsidian_write as ow  # noqa: E402


def sha(b):
    return hashlib.sha256(b).hexdigest()


class FakeObsidian:
    """Records eval calls; can fail the n-th one."""

    def __init__(self, fail_at=None):
        self.calls, self.fail_at = [], fail_at

    def eval_js(self, name, code):
        self.calls.append(code)
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            return "Error: boom"
        return "=> ok"


def test_a_multi_chunk_file_is_staged_then_written_in_one_call(monkeypatch):
    fake = FakeObsidian()
    monkeypatch.setattr(ow, "eval_js", fake.eval_js)
    monkeypatch.setattr(ow, "CHUNK", 10)
    data = b"".join(f"line {i}\n".encode() for i in range(10))
    assert ow.write_file("v", "a.md", data, "modify") is None
    writes = [c for c in fake.calls if "app.vault.modify" in c or "app.vault.create" in c]
    assert len(writes) == 1 and writes[0] is fake.calls[-1]
    assert not any("app.vault.append" in c for c in fake.calls)
    assert len(fake.calls) > 2  # several chunks were staged first


def test_a_failed_chunk_writes_nothing(monkeypatch):
    fake = FakeObsidian(fail_at=2)
    monkeypatch.setattr(ow, "eval_js", fake.eval_js)
    monkeypatch.setattr(ow, "CHUNK", 10)
    data = b"".join(f"line {i}\n".encode() for i in range(10))
    assert ow.write_file("v", "a.md", data, "modify")
    assert not any("app.vault.modify" in c or "app.vault.create" in c for c in fake.calls)


@pytest.fixture
def split(tmp_path):
    vault, out = tmp_path / "vault", tmp_path / "out"
    (vault / "notes").mkdir(parents=True)
    (out / "notes" / "notes").mkdir(parents=True)
    (out / "rewrites" / "other").mkdir(parents=True)
    (vault / "other").mkdir()
    src, other = vault / "notes" / "Big.md", vault / "other" / "Links.md"
    src.write_bytes(b"# Big\n## A\ntext\n")
    other.write_bytes(b"see [[Big#A]]\n")
    (out / "notes" / "notes" / "Topic A.md").write_bytes(b"## A\ntext\n")
    (out / "notes" / "notes" / "Big.md").write_bytes(b"# Big\n- [[Topic A#A]]\n")
    (out / "rewrites" / "other" / "Links.md").write_bytes(b"see [[Topic A#A]]\n")
    (out / "report.json").write_text(json.dumps({
        "stage": "done", "errors": [], "source": "notes/Big.md", "notes": ["Topic A"],
        "rewrites": ["other/Links.md"],
        "read_sha256": {"notes/Big.md": sha(src.read_bytes()), "other/Links.md": sha(other.read_bytes())},
    }))
    return vault, out


def _fake_env(monkeypatch, vault, on_write=None):
    def write_file(name, vp, data, mode):
        if on_write:
            on_write(vp)
        (vault / vp).write_bytes(data)
        return None

    monkeypatch.setattr(ow, "write_file", write_file)
    monkeypatch.setattr(ow, "unresolved", lambda name: set())
    monkeypatch.setattr(ow, "eval_js", lambda name, code: '=> {"n":0,"bad":[]}')
    monkeypatch.setattr(ow.time, "sleep", lambda s: None)


def _main(monkeypatch, vault, out):
    monkeypatch.setattr(sys, "argv", ["ow", "--vault", str(vault), "--vault-name", "v", "--out", str(out)])
    return ow.main()


def test_originals_are_backed_up_before_they_are_replaced(monkeypatch, split, capsys):
    vault, out = split
    before = {p: (vault / p).read_bytes() for p in ("notes/Big.md", "other/Links.md")}
    _fake_env(monkeypatch, vault)
    assert _main(monkeypatch, vault, out) == 0
    for rel, data in before.items():
        assert (out / "backup" / rel).read_bytes() == data
    assert "backup" in capsys.readouterr().out


def test_a_note_edited_during_the_write_is_not_overwritten(monkeypatch, split):
    vault, out = split

    def user_edits(vp):
        if vp == "notes/Topic A.md":  # while topic notes are written, the user edits the source
            (vault / "notes" / "Big.md").write_bytes(b"# Big\n## A\ntext\nNEW LINE\n")

    _fake_env(monkeypatch, vault, on_write=user_edits)
    assert _main(monkeypatch, vault, out) == 1
    assert (vault / "notes" / "Big.md").read_bytes().endswith(b"NEW LINE\n")
