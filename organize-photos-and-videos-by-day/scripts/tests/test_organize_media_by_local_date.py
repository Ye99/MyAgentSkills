import unittest
from datetime import datetime
from pathlib import Path
import tempfile
import sys
from unittest import mock

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

    def test_classify_cache_does_not_suppress_lookup_for_all_unknowns_key(self) -> None:
        # The key unknown|unknown|unknown is too broad to trust from cache.
        # Even if cached as "media", needs_lookup must remain True so ffprobe
        # can verify on a per-file basis.
        key = "unknown|unknown|unknown"
        is_media, reason, needs_lookup = mod.classify_media_signature(
            mime_type=None,
            file_type=None,
            extension="",
            cache={key: "media"},
        )

        self.assertTrue(needs_lookup)

    def test_auto_triage_unknown_signature_marks_media_on_video_stream(self) -> None:
        ffprobe_payload = '{"streams":[{"codec_type":"video"}]}'
        completed = mock.Mock(returncode=0, stdout=ffprobe_payload, stderr="")

        with mock.patch.object(mod.shutil, "which", return_value="/usr/bin/ffprobe"):
            with mock.patch.object(mod.subprocess, "run", return_value=completed):
                classification, reason = mod.auto_triage_unknown_signature(Path("/tmp/example.bin"))

        self.assertEqual(classification, "media")
        self.assertEqual(reason, "ffprobe:auto-media")

    def test_auto_triage_unknown_signature_marks_non_media_on_invalid_data(self) -> None:
        completed = mock.Mock(returncode=1, stdout="", stderr="Invalid data found when processing input")

        with mock.patch.object(mod.shutil, "which", return_value="/usr/bin/ffprobe"):
            with mock.patch.object(mod.subprocess, "run", return_value=completed):
                classification, reason = mod.auto_triage_unknown_signature(Path("/tmp/example.bin"))

        self.assertEqual(classification, "non_media")
        self.assertEqual(reason, "ffprobe:invalid-data-non-media")

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
            Path("/tmp/2024/2024_09_18/IMG_0001_col001.JPG"),
        }

        resolved = mod.next_collision_path(Path("/tmp/2024/2024_09_18/IMG_0001.JPG"), existing)

        self.assertEqual(resolved, Path("/tmp/2024/2024_09_18/IMG_0001_col002.JPG"))

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
        self.assertTrue(mod.is_explicit_non_media_path(Path("/mnt/foo/desktop.ini")))
        self.assertTrue(mod.is_explicit_non_media_path(Path("/mnt/foo/anything.bk")))
        self.assertTrue(mod.is_explicit_non_media_path(Path("/mnt/foo/anything.sav")))
        self.assertTrue(mod.is_explicit_non_media_path(Path("/mnt/foo/anything.db")))
        self.assertTrue(mod.is_explicit_non_media_path(Path("/mnt/foo/anything.log")))
        self.assertFalse(mod.is_explicit_non_media_path(Path("/mnt/foo/IMG_0001.MOV")))

    def test_is_explicit_non_media_path_rejects_txt_files(self) -> None:
        # .txt files like GoProTrashList.txt are not media; ffprobe falsely classifies
        # plain text as tty/ansi video so we must exclude by extension before lookup.
        self.assertTrue(mod.is_explicit_non_media_path(Path("/mnt/foo/GoProTrashList.txt")))
        self.assertTrue(mod.is_explicit_non_media_path(Path("/mnt/foo/README.txt")))

    def test_extract_metadata_records_tolerates_exiftool_exit_code_1(self) -> None:
        # exiftool exits with code 1 (minor error) when it encounters non-image files
        # mixed in with media files. This is normal and should not raise an exception.
        valid_json = '[{"SourceFile": "/tmp/photo.jpg", "MIMEType": "image/jpeg"}]'
        completed = mock.Mock(returncode=1, stdout=valid_json, stderr="1 directories scanned\n1 image files read")

        with mock.patch.object(mod.subprocess, "run", return_value=completed):
            records = mod._extract_metadata_records(Path("/tmp"))

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["SourceFile"], "/tmp/photo.jpg")

    def test_emit_progress_writes_count_and_percentage_to_stream(self) -> None:
        import io
        out = io.StringIO()
        mod._emit_progress(10, 84, file=out)
        text = out.getvalue()
        self.assertIn("10", text)
        self.assertIn("84", text)
        self.assertIn("11%", text)  # floor(10/84*100) == 11

    def test_emit_progress_writes_100_percent_when_done(self) -> None:
        import io
        out = io.StringIO()
        mod._emit_progress(84, 84, file=out)
        text = out.getvalue()
        self.assertIn("100%", text)


class IdempotentCopyTests(unittest.TestCase):
    def test_files_are_identical_returns_true_for_same_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.bin"
            b = Path(tmp) / "b.bin"
            a.write_bytes(b"identical-content-12345")
            b.write_bytes(b"identical-content-12345")
            self.assertTrue(mod._files_are_identical(a, b))

    def test_files_are_identical_returns_false_for_different_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.bin"
            b = Path(tmp) / "b.bin"
            a.write_bytes(b"content-aaa")
            b.write_bytes(b"content-bbb")
            self.assertFalse(mod._files_are_identical(a, b))

    def test_files_are_identical_returns_false_for_different_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.bin"
            b = Path(tmp) / "b.bin"
            a.write_bytes(b"short")
            b.write_bytes(b"much-longer-content")
            self.assertFalse(mod._files_are_identical(a, b))

    def test_next_collision_path_returns_none_when_identical_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source" / "photo.jpg"
            dst_dir = Path(tmp) / "dest" / "2024" / "2024_01_01"
            src.parent.mkdir(parents=True)
            dst_dir.mkdir(parents=True)
            src.write_bytes(b"photo-data-xyz")
            (dst_dir / "photo.jpg").write_bytes(b"photo-data-xyz")

            existing_paths: set[Path] = set()
            result = mod.next_collision_path(
                dst_dir / "photo.jpg", existing_paths, source_path=src
            )
            self.assertIsNone(result)

    def test_next_collision_path_returns_col_when_different_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source" / "photo.jpg"
            dst_dir = Path(tmp) / "dest" / "2024" / "2024_01_01"
            src.parent.mkdir(parents=True)
            dst_dir.mkdir(parents=True)
            src.write_bytes(b"new-photo-data")
            (dst_dir / "photo.jpg").write_bytes(b"different-photo-data")

            existing_paths: set[Path] = set()
            result = mod.next_collision_path(
                dst_dir / "photo.jpg", existing_paths, source_path=src
            )
            self.assertIsNotNone(result)
            self.assertEqual(result.name, "photo_col001.jpg")

    def test_next_collision_path_skips_col_when_identical_col_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source" / "photo.jpg"
            dst_dir = Path(tmp) / "dest" / "2024" / "2024_01_01"
            src.parent.mkdir(parents=True)
            dst_dir.mkdir(parents=True)
            src.write_bytes(b"photo-data-xyz")
            (dst_dir / "photo.jpg").write_bytes(b"other-photo")
            (dst_dir / "photo_col001.jpg").write_bytes(b"photo-data-xyz")

            existing_paths: set[Path] = set()
            result = mod.next_collision_path(
                dst_dir / "photo.jpg", existing_paths, source_path=src
            )
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
