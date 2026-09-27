import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "compare_gopro_backup.py"
spec = importlib.util.spec_from_file_location("compare_gopro_backup", SCRIPT)
compare = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(compare)


def write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.mark.parametrize(
    ("relative_path", "ignored"),
    [
        ("DCIM/leinfo.sav", True),
        ("mdb12.db", True),
        ("MDB12.DB", True),
        ("mdb_h_12.bk", True),
        ("nested/mdb13.db", True),
        ("mdb_12.log", True),
        ("DCIM/100GOPRO/GX012370.MP4", False),
        ("DCIM/100GOPRO/GX012370.THM", False),
        ("Get_started_with_GoPro.url", False),
    ],
)
def test_gopro_card_file_match(relative_path: str, ignored: bool) -> None:
    assert compare.is_gopro_card_file(relative_path) is ignored


def test_partition_keeps_footage_and_drops_card_files() -> None:
    footage, ignored = compare.partition_missing(
        ["DCIM/leinfo.sav", "mdb12.db", "mdb_h_12.bk", "DCIM/100GOPRO/GX010001.MP4"]
    )
    assert footage == ["DCIM/100GOPRO/GX010001.MP4"]
    assert ignored == ["DCIM/leinfo.sav", "mdb12.db", "mdb_h_12.bk"]


def test_report_must_stay_outside_both_trees(tmp_path: Path) -> None:
    source = tmp_path / "card"
    backup = tmp_path / "backup"
    source.mkdir()
    backup.mkdir()
    with pytest.raises(SystemExit, match="outside both trees"):
        compare.assert_report_outside_trees(source, backup, source / "report.txt")


def test_compare_ignores_rewritten_card_files_and_reports_missing_footage(tmp_path: Path, monkeypatch) -> None:
    card = tmp_path / "card"
    backup = tmp_path / "backup"
    report = tmp_path / "report.txt"
    write_file(card / "DCIM" / "100GOPRO" / "GX010001.MP4", b"clip")
    write_file(backup / "renamed" / "clip.mp4", b"clip")
    write_file(card / "DCIM" / "100GOPRO" / "GX019999.MP4", b"only-on-card")
    write_file(card / "DCIM" / "100GOPRO" / "GX010001.THM", b"proxy-not-compared")
    write_file(card / "DCIM" / "leinfo.sav", b"card-state")
    write_file(backup / "DCIM" / "leinfo.sav", b"later-state")
    write_file(card / "mdb12.db", b"db-v1")
    write_file(backup / "mdb12.db", b"db-v1-rewritten-by-camera")
    write_file(card / "mdb_h_12.bk", b"bk-v1")
    write_file(backup / "mdb_h_12.bk", b"bk-v2")
    write_file(card / "mdb_12.log", b"")
    write_file(backup / "mdb_12.log", b"")

    monkeypatch.setattr(
        sys,
        "argv",
        ["compare_gopro_backup.py", str(card), str(backup), "--output", str(report), "--workers", "1"],
    )
    compare.main()
    text = report.read_text()
    footage, ignored_note = text.split("Ignored GoPro card files", maxsplit=1)
    tree = "\n".join(footage.splitlines()[1:])
    assert "GX019999.MP4" in tree
    assert "GX010001.MP4" not in tree
    assert "GX010001.THM" not in text
    assert "No missing footage" not in footage
    assert "leinfo.sav" not in tree
    assert "mdb12.db" not in tree
    assert "mdb_h_12.bk" not in tree
    assert "leinfo.sav" in ignored_note
    assert "mdb12.db" in ignored_note
    assert "mdb_h_12.bk" in ignored_note


def test_matching_footage_reports_complete_even_when_card_files_differ(tmp_path: Path, monkeypatch) -> None:
    card = tmp_path / "card"
    backup = tmp_path / "backup"
    report = tmp_path / "report.txt"
    write_file(card / "DCIM" / "100GOPRO" / "GX010001.MP4", b"clip")
    write_file(backup / "elsewhere" / "same.MP4", b"clip")
    write_file(card / "mdb12.db", b"old")
    write_file(backup / "mdb12.db", b"new")

    monkeypatch.setattr(
        sys,
        "argv",
        ["compare_gopro_backup.py", str(card), str(backup), "--output", str(report), "--workers", "1"],
    )
    compare.main()
    text = report.read_text()
    assert "|-- No missing footage (everything matched)" in text
    assert "mdb12.db" in text.split("Ignored GoPro card files", maxsplit=1)[1]
