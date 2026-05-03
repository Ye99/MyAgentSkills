from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract_images.py"
DATA = "aGVsbG8="  # "hello"
OTHER_DATA = "d29ybGQ="  # "world"


def run_extract(note: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(note), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_existing_asset_without_force_refuses_to_overwrite(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(f"Before ![][image1]\n\n[image1]: <data:image/png;base64,{DATA}>\n", encoding="utf-8")
    assets = tmp_path / "Note.assets"
    assets.mkdir()
    existing = assets / "image1.png"
    existing.write_bytes(b"user-edited")

    result = run_extract(note)

    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert existing.read_bytes() == b"user-edited"
    assert "data:image" in note.read_text(encoding="utf-8")


def test_existing_asset_with_force_overwrites(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(f"Before ![][image1]\n\n[image1]: <data:image/png;base64,{DATA}>\n", encoding="utf-8")
    assets = tmp_path / "Note.assets"
    assets.mkdir()
    existing = assets / "image1.png"
    existing.write_bytes(b"user-edited")

    result = run_extract(note, "--force")

    assert result.returncode == 0, result.stderr
    assert existing.read_bytes() == b"hello"
    assert "![[Note.assets/image1.png]]" in note.read_text(encoding="utf-8")


def test_custom_assets_dir_uses_real_relative_link_and_verifies_written_file(tmp_path: Path) -> None:
    note_dir = tmp_path / "notes"
    assets_dir = tmp_path / "external-assets"
    note_dir.mkdir()
    note = note_dir / "Note.md"
    note.write_text(f"Before ![][image1]\n\n[image1]: <data:image/png;base64,{DATA}>\n", encoding="utf-8")

    result = run_extract(note, "--assets-dir", str(assets_dir))

    assert result.returncode == 0, result.stderr
    assert (assets_dir / "image1.png").read_bytes() == b"hello"
    assert "![[../external-assets/image1.png]]" in note.read_text(encoding="utf-8")


def test_warns_about_unreferenced_embedded_definitions(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(
        "Before ![][image1]\n\n"
        f"[image1]: <data:image/png;base64,{DATA}>\n"
        f"[unused]: <data:image/png;base64,{OTHER_DATA}>\n",
        encoding="utf-8",
    )

    result = run_extract(note)

    assert result.returncode == 0, result.stderr
    assert "warning" in result.stderr.lower()
    assert "unreferenced" in result.stderr.lower()
    assert "unused" in result.stderr
    assert (tmp_path / "Note.assets" / "unused.png").read_bytes() == b"world"


def test_unsafe_ref_name_cannot_escape_assets_dir(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(f"Before ![][../outside]\n\n[../outside]: <data:image/png;base64,{DATA}>\n", encoding="utf-8")

    result = run_extract(note)

    assert result.returncode == 1
    assert "unsafe" in result.stderr.lower()
    assert not (tmp_path / "outside.png").exists()
    assert "data:image" in note.read_text(encoding="utf-8")


def test_duplicate_output_names_abort_before_writing(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(
        "Before ![][image1]\n\n"
        f"[image1]: <data:image/png;base64,{DATA}>\n"
        f"[image1]: <data:image/png;base64,{OTHER_DATA}>\n",
        encoding="utf-8",
    )

    result = run_extract(note)

    assert result.returncode == 1
    assert "duplicate" in result.stderr.lower()
    assert not (tmp_path / "Note.assets" / "image1.png").exists()
    assert "data:image" in note.read_text(encoding="utf-8")


def test_malformed_base64_aborts_before_writing(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text("Before ![][image1]\n\n[image1]: <data:image/png;base64,not-valid***>\n", encoding="utf-8")

    result = run_extract(note)

    assert result.returncode == 1
    assert "base64 decode failed" in result.stderr
    assert not (tmp_path / "Note.assets" / "image1.png").exists()
    assert "data:image" in note.read_text(encoding="utf-8")


def test_rewrite_count_excludes_unknown_refs_left_unchanged(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(
        "Before ![][image1] and ![][unknown]\n\n"
        f"[image1]: <data:image/png;base64,{DATA}>\n",
        encoding="utf-8",
    )

    result = run_extract(note)

    text = note.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "rewrote 1 inline image reference(s)" in result.stdout
    assert "![[Note.assets/image1.png]]" in text
    assert "![][unknown]" in text
