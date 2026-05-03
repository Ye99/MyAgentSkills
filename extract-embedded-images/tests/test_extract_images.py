from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract_images.py"
DATA = "aGVsbG8="  # "hello"
OTHER_DATA = "d29ybGQ="  # "world"


def load_extract_module():
    spec = importlib.util.spec_from_file_location("extract_images", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    assets_dir = note_dir / "custom-assets"
    note_dir.mkdir()
    note = note_dir / "Note.md"
    note.write_text(f"Before ![][image1]\n\n[image1]: <data:image/png;base64,{DATA}>\n", encoding="utf-8")

    result = run_extract(note, "--assets-dir", str(assets_dir))

    assert result.returncode == 0, result.stderr
    assert (assets_dir / "image1.png").read_bytes() == b"hello"
    assert "![[custom-assets/image1.png]]" in note.read_text(encoding="utf-8")


def test_custom_assets_dir_outside_note_directory_is_rejected(tmp_path: Path) -> None:
    note_dir = tmp_path / "notes"
    assets_dir = tmp_path / "external-assets"
    note_dir.mkdir()
    note = note_dir / "Note.md"
    note.write_text(f"Before ![][image1]\n\n[image1]: <data:image/png;base64,{DATA}>\n", encoding="utf-8")

    result = run_extract(note, "--assets-dir", str(assets_dir))

    assert result.returncode == 1
    assert "outside the note directory" in result.stderr
    assert not (assets_dir / "image1.png").exists()
    assert "data:image" in note.read_text(encoding="utf-8")


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


def test_markdown_reference_labels_match_case_insensitively(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(f"Before ![][image1]\n\n[Image1]: <data:image/png;base64,{DATA}>\n", encoding="utf-8")

    result = run_extract(note)
    text = note.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "![[Note.assets/Image1.png]]" in text
    assert "![][image1]" not in text
    assert "data:image" not in text


def test_fenced_code_blocks_are_not_rewritten_or_removed(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(
        "Before ![][image1]\n\n"
        "```markdown\n"
        "Example ![][example]\n\n"
        f"[example]: <data:image/png;base64,{OTHER_DATA}>\n"
        "```\n\n"
        f"[image1]: <data:image/png;base64,{DATA}>\n",
        encoding="utf-8",
    )

    result = run_extract(note)
    text = note.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "![[Note.assets/image1.png]]" in text
    assert "Example ![][example]" in text
    assert f"[example]: <data:image/png;base64,{OTHER_DATA}>" in text
    assert not (tmp_path / "Note.assets" / "example.png").exists()


def test_fenced_code_spans_are_recomputed_after_rewrites_shift_text(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(
        " ".join("![][image1]" for _ in range(20))
        + "\n\n```markdown\n"
        + "Example ![][example]\n\n"
        + f"[example]: <data:image/png;base64,{OTHER_DATA}>\n"
        + "```\n\n"
        + f"[image1]: <data:image/png;base64,{DATA}>\n",
        encoding="utf-8",
    )

    result = run_extract(note)
    text = note.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert f"[example]: <data:image/png;base64,{OTHER_DATA}>" in text
    assert "Example ![][example]" in text
    assert not (tmp_path / "Note.assets" / "example.png").exists()


def test_inline_code_spans_are_not_rewritten(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(
        "Code `![][image1]` and real ![][image1]\n\n"
        f"[image1]: <data:image/png;base64,{DATA}>\n",
        encoding="utf-8",
    )

    result = run_extract(note)
    text = note.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "Code `![][image1]`" in text
    assert "real ![[Note.assets/image1.png]]" in text


def test_list_item_image_indentation_is_rewritten(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(
        "1. First item with image:\n"
        "    ![][image1]\n\n"
        f"[image1]: <data:image/png;base64,{DATA}>\n",
        encoding="utf-8",
    )

    result = run_extract(note)
    text = note.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "    ![[Note.assets/image1.png]]" in text
    assert "![][image1]" not in text
    assert "data:image" not in text


def test_list_item_continuation_after_blank_line_is_rewritten(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(
        "- Step one\n\n"
        "    ![][image1]\n\n"
        f"[image1]: <data:image/png;base64,{DATA}>\n",
        encoding="utf-8",
    )

    result = run_extract(note)
    text = note.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "    ![[Note.assets/image1.png]]" in text
    assert "![][image1]" not in text
    assert "data:image" not in text


def test_collapsed_reference_image_is_rewritten(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(f"Before ![image1][]\n\n[image1]: <data:image/png;base64,{DATA}>\n", encoding="utf-8")

    result = run_extract(note)
    text = note.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "![[Note.assets/image1.png|image1]]" in text
    assert "![image1][]" not in text
    assert "data:image" not in text


def test_shortcut_reference_image_is_rewritten_without_touching_inline_or_obsidian_images(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(
        "Before ![image1] and ![inline](https://example.com/a.png) and ![[existing.png]]\n\n"
        f"[image1]: <data:image/png;base64,{DATA}>\n",
        encoding="utf-8",
    )

    result = run_extract(note)
    text = note.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "![[Note.assets/image1.png|image1]]" in text
    assert "![inline](https://example.com/a.png)" in text
    assert "![[existing.png]]" in text
    assert "data:image" not in text


def test_generic_or_blank_alt_text_does_not_become_alias(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(
        "Before ![ Image ][image1] and ![   ][image2]\n\n"
        f"[image1]: <data:image/png;base64,{DATA}>\n"
        f"[image2]: <data:image/png;base64,{OTHER_DATA}>\n",
        encoding="utf-8",
    )

    result = run_extract(note)
    text = note.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "![[Note.assets/image1.png| Image ]]" not in text
    assert "![[Note.assets/image2.png|   ]]" not in text
    assert "![[Note.assets/image1.png]]" in text
    assert "![[Note.assets/image2.png]]" in text


def test_meaningful_alt_alias_is_stripped(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(f"Before ![ caption ][image1]\n\n[image1]: <data:image/png;base64,{DATA}>\n", encoding="utf-8")

    result = run_extract(note)
    text = note.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "![[Note.assets/image1.png|caption]]" in text
    assert "| caption " not in text


def test_non_utf8_note_reports_clean_error(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_bytes(b"Before \xff\n\n[image1]: <data:image/png;base64,aGVsbG8=>\n")

    result = run_extract(note)

    assert result.returncode == 1
    assert "is not valid UTF-8" in result.stderr


def test_preserves_crlf_line_endings(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_bytes(f"Before ![][image1]\r\n\r\n[image1]: <data:image/png;base64,{DATA}>\r\n".encode("utf-8"))

    result = run_extract(note)
    raw = note.read_bytes()

    assert result.returncode == 0, result.stderr
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_preserves_crlf_line_endings_when_definition_is_in_middle(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_bytes(
        f"Hi ![][image1]\r\n\r\n[image1]: <data:image/png;base64,{DATA}>\r\nAfter line\r\n".encode("utf-8")
    )

    result = run_extract(note)
    raw = note.read_bytes()

    assert result.returncode == 0, result.stderr
    assert raw == b"Hi ![[Note.assets/image1.png]]\r\n\r\nAfter line\r\n"
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_malformed_multiline_data_url_does_not_consume_later_prose(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    original = (
        "Before ![][image1]\n\n"
        "[image1]: <data:image/png;base64,AAAA\n\n"
        "This prose has a > character.\n"
    )
    note.write_text(original, encoding="utf-8")

    result = run_extract(note)

    assert result.returncode == 0
    assert "no embedded base64 image definitions found" in result.stdout
    assert note.read_text(encoding="utf-8") == original


def test_decodes_each_embedded_image_once(tmp_path: Path, monkeypatch) -> None:
    module = load_extract_module()
    note = tmp_path / "Note.md"
    note.write_text(
        "Before ![][image1] and ![][image2]\n\n"
        f"[image1]: <data:image/png;base64,{DATA}>\n"
        f"[image2]: <data:image/png;base64,{OTHER_DATA}>\n",
        encoding="utf-8",
    )
    original_argv = sys.argv
    original_decode = module.base64.b64decode
    calls = 0

    def count_decode(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_decode(*args, **kwargs)

    monkeypatch.setattr(module.base64, "b64decode", count_decode)
    monkeypatch.setattr(sys, "argv", ["extract_images.py", str(note)])
    try:
        result = module.main()
    finally:
        sys.argv = original_argv

    assert result == 0
    assert calls == 2


def test_preserves_note_mode_and_uses_umask_asset_mode(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(f"Before ![][image1]\n\n[image1]: <data:image/png;base64,{DATA}>\n", encoding="utf-8")
    note.chmod(0o644)

    result = subprocess.run(
        ["/bin/sh", "-c", f"umask 022; exec {sys.executable} {SCRIPT} {note}"],
        text=True,
        capture_output=True,
        check=False,
    )
    asset = tmp_path / "Note.assets" / "image1.png"

    assert result.returncode == 0, result.stderr
    assert (note.stat().st_mode & 0o777) == 0o644
    assert (asset.stat().st_mode & 0o777) == 0o644


def test_existing_asset_mode_is_preserved_when_forced(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(f"Before ![][image1]\n\n[image1]: <data:image/png;base64,{DATA}>\n", encoding="utf-8")
    assets = tmp_path / "Note.assets"
    assets.mkdir()
    existing = assets / "image1.png"
    existing.write_bytes(b"user-edited")
    existing.chmod(0o640)

    result = run_extract(note, "--force")

    assert result.returncode == 0, result.stderr
    assert existing.read_bytes() == b"hello"
    assert (existing.stat().st_mode & 0o777) == 0o640


def test_rejects_wikilink_paths_or_aliases_that_cannot_be_represented_safely(tmp_path: Path) -> None:
    note = tmp_path / "Note.md"
    note.write_text(f"Before ![bad|alias][image1]\n\n[image1]: <data:image/png;base64,{DATA}>\n", encoding="utf-8")

    result = run_extract(note)

    assert result.returncode == 1
    assert "cannot be represented safely" in result.stderr
    assert not (tmp_path / "Note.assets" / "image1.png").exists()
    assert "data:image" in note.read_text(encoding="utf-8")


def test_rolls_back_assets_when_later_asset_replace_fails(tmp_path: Path, monkeypatch) -> None:
    module = load_extract_module()
    note = tmp_path / "Note.md"
    note.write_text(
        "Before ![][image1] and ![][image2]\n\n"
        f"[image1]: <data:image/png;base64,{DATA}>\n"
        f"[image2]: <data:image/png;base64,{OTHER_DATA}>\n",
        encoding="utf-8",
    )
    original_argv = sys.argv
    original_replace = Path.replace

    def fail_second_asset_replace(self: Path, target: Path) -> Path:
        if target.name == "image2.png":
            raise OSError("simulated replace failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_second_asset_replace)
    monkeypatch.setattr(sys, "argv", ["extract_images.py", str(note)])
    try:
        result = module.main()
    finally:
        sys.argv = original_argv

    assert result == 1
    assert not (tmp_path / "Note.assets" / "image1.png").exists()
    assert not (tmp_path / "Note.assets" / "image2.png").exists()
    assert "data:image" in note.read_text(encoding="utf-8")


def test_rolls_back_assets_when_post_write_verification_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    module = load_extract_module()
    note = tmp_path / "Note.md"
    note.write_text(
        "Before ![][image1] and ![][image2]\n\n"
        f"[image1]: <data:image/png;base64,{DATA}>\n"
        f"[image2]: <data:image/png;base64,{OTHER_DATA}>\n",
        encoding="utf-8",
    )
    original_argv = sys.argv
    original_is_file = Path.is_file

    def hide_second_asset(self: Path) -> bool:
        if self.name == "image2.png":
            return False
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", hide_second_asset)
    monkeypatch.setattr(sys, "argv", ["extract_images.py", str(note)])
    try:
        result = module.main()
    finally:
        sys.argv = original_argv

    assert result == 1
    captured = capsys.readouterr()
    assert "  - wrote " not in captured.out
    assert not (tmp_path / "Note.assets" / "image1.png").exists()
    assert not (tmp_path / "Note.assets" / "image2.png").exists()
    assert "data:image" in note.read_text(encoding="utf-8")
