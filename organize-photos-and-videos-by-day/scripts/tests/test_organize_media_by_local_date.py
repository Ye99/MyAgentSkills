import unittest
from datetime import datetime
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import organize_media_by_local_date as mod


class OrganizeMediaByLocalDateTests(unittest.TestCase):
    def test_classify_media_signature_by_mime_prefix(self) -> None:
        is_media, reason, needs_lookup = mod.classify_media_signature(
            mime_type="image/heic",
            file_type="HEIC",
            extension="heic",
            cache={},
        )

        self.assertTrue(is_media)
        self.assertEqual(reason, "mime-prefix")
        self.assertFalse(needs_lookup)

    def test_classify_unknown_signature_defaults_to_media_candidate(self) -> None:
        is_media, reason, needs_lookup = mod.classify_media_signature(
            mime_type="application/octet-stream",
            file_type="BINARY",
            extension="bin",
            cache={},
        )

        self.assertTrue(is_media)
        self.assertEqual(reason, "unknown-signature-default-media-candidate")
        self.assertTrue(needs_lookup)

    def test_classify_signature_uses_cache(self) -> None:
        key = "application/octet-stream|BINARY|bin"
        is_media, reason, needs_lookup = mod.classify_media_signature(
            mime_type="application/octet-stream",
            file_type="BINARY",
            extension="bin",
            cache={key: "non_media"},
        )

        self.assertFalse(is_media)
        self.assertEqual(reason, "cache:non_media")
        self.assertFalse(needs_lookup)

    def test_resolve_capture_datetime_uses_gps_utc_conversion(self) -> None:
        record = {
            "DateTimeOriginal": "2024:09:18 00:30:00",
            "GPSDateStamp": "2024:09:18",
            "GPSTimeStamp": "00:30:00",
            "GPSLatitude": 47.691375,
            "GPSLongitude": -122.1127555,
        }

        def tz_lookup(_lat: float, _lon: float) -> str | None:
            return "America/Los_Angeles"

        resolved, source, timezone_name = mod.resolve_capture_datetime(
            record=record,
            creation_dt=None,
            mtime_dt=datetime(2024, 9, 19, 0, 0, 0),
            timezone_lookup=tz_lookup,
        )

        self.assertEqual(resolved, datetime(2024, 9, 17, 17, 30, 0))
        self.assertEqual(source, "gps-utc-converted")
        self.assertEqual(timezone_name, "America/Los_Angeles")

    def test_resolve_capture_datetime_fallbacks_to_creation_then_mtime(self) -> None:
        record = {}

        resolved_creation, source_creation, _ = mod.resolve_capture_datetime(
            record=record,
            creation_dt=datetime(2024, 9, 18, 10, 0, 0),
            mtime_dt=datetime(2024, 9, 18, 12, 0, 0),
            timezone_lookup=lambda _lat, _lon: None,
        )
        self.assertEqual(resolved_creation, datetime(2024, 9, 18, 10, 0, 0))
        self.assertEqual(source_creation, "file-creation-time")

        resolved_mtime, source_mtime, _ = mod.resolve_capture_datetime(
            record=record,
            creation_dt=None,
            mtime_dt=datetime(2024, 9, 18, 12, 0, 0),
            timezone_lookup=lambda _lat, _lon: None,
        )
        self.assertEqual(resolved_mtime, datetime(2024, 9, 18, 12, 0, 0))
        self.assertEqual(source_mtime, "file-mtime")

    def test_next_collision_path_uses_incrementing_suffix(self) -> None:
        existing = {
            Path("/tmp/2024/2024_09_18/IMG_0001.JPG"),
            Path("/tmp/2024/2024_09_18/IMG_0001_dup001.JPG"),
        }

        resolved = mod.next_collision_path(Path("/tmp/2024/2024_09_18/IMG_0001.JPG"), existing)

        self.assertEqual(resolved, Path("/tmp/2024/2024_09_18/IMG_0001_dup002.JPG"))

    def test_load_find_missing_module_imports_without_dataclass_error(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        loaded = mod._load_find_missing_module(repo_root)

        self.assertIsNotNone(loaded)
        self.assertTrue(hasattr(loaded, "find_missing_files"))

    def test_merge_with_source_files_includes_files_missing_from_exif_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_a = root / "a.jpg"
            file_b = root / "b.unknown"
            file_a.write_bytes(b"a")
            file_b.write_bytes(b"b")

            merged = mod.merge_with_source_files(
                source_root=root,
                metadata_records=[{"SourceFile": str(file_a), "MIMEType": "image/jpeg"}],
            )

            paths = {Path(record["SourceFile"]).resolve() for record in merged}
            self.assertEqual(paths, {file_a.resolve(), file_b.resolve()})

    def test_is_system_metadata_path_detects_mac_artifacts(self) -> None:
        self.assertTrue(mod.is_system_metadata_path(Path("/mnt/.Trashes/._501")))
        self.assertTrue(mod.is_system_metadata_path(Path("/mnt/foo/.DS_Store")))
        self.assertFalse(mod.is_system_metadata_path(Path("/mnt/foo/IMG_0001.JPG")))

    def test_is_explicit_non_media_path_for_known_files_and_extension(self) -> None:
        self.assertTrue(mod.is_explicit_non_media_path(Path("/mnt/foo/10_Bit_Log_Encoding.url")))
        self.assertTrue(mod.is_explicit_non_media_path(Path("/mnt/foo/anything.bk")))
        self.assertTrue(mod.is_explicit_non_media_path(Path("/mnt/foo/anything.sav")))
        self.assertTrue(mod.is_explicit_non_media_path(Path("/mnt/foo/anything.db")))
        self.assertTrue(mod.is_explicit_non_media_path(Path("/mnt/foo/anything.log")))
        self.assertFalse(mod.is_explicit_non_media_path(Path("/mnt/foo/IMG_0001.MOV")))


if __name__ == "__main__":
    unittest.main()
