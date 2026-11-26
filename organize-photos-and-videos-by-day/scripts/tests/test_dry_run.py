"""A dry run writes nothing under the destination, and the cache lives outside the skill."""
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import organize_media_by_local_date as mod  # noqa: E402


def _run(tmp_path, monkeypatch, *extra):
    src = tmp_path / "src"
    src.mkdir()
    photo = src / "a.jpg"
    photo.write_bytes(b"jpeg")
    records = [{"SourceFile": str(photo), "MIMEType": "image/jpeg", "FileType": "JPEG",
                "DateTimeOriginal": "2025:07:24 09:00:00"}]
    dest = tmp_path / "dest"
    report = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["prog", str(src), str(dest), "--report", str(report),
                                      "--recording-timezone", "UTC", *extra])
    with mock.patch.object(mod, "_extract_metadata_records", return_value=records), \
         mock.patch.object(mod, "_create_timezone_lookup", return_value=lambda lat, lon: None):
        code = mod.main()
    return code, dest, report


def test_dry_run_creates_no_destination_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    code, dest, report = _run(tmp_path, monkeypatch)
    assert code == 0
    assert not dest.exists()
    planned = json.loads(report.read_text())
    assert "2025_07_24" in json.dumps(planned)


def test_default_signature_cache_is_in_the_user_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = mod.default_signature_cache_path()
    assert path == tmp_path / "cache" / "organize-photos-and-videos-by-day" / "signature_type_cache.json"
    assert Path(mod.__file__).resolve().parent not in path.parents


def test_an_old_cache_in_the_skill_folder_is_still_read(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"sig": "media"}))
    monkeypatch.setattr(mod, "LEGACY_SIGNATURE_CACHE", legacy)
    assert mod._load_signature_cache(mod.default_signature_cache_path()) == {"sig": "media"}
