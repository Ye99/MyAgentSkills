import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
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
            "GPSLatitude": 40.748440,
            "GPSLongitude": -73.985664,
        }

        def tz_lookup(_lat: float, _lon: float) -> str | None:
            return "America/Los_Angeles"

        resolved, source, timezone_name = mod.resolve_capture_datetime(
            record=record,
            creation_dt=None,
            mtime_dt=datetime(2024, 9, 19, 0, 0, 0),
            timezone_lookup=tz_lookup,
            recording_tz=ZoneInfo("America/Los_Angeles"),
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
            recording_tz=ZoneInfo("America/Los_Angeles"),
        )
        self.assertIn("file-creation-time", source_creation)

        resolved_mtime, source_mtime, _ = mod.resolve_capture_datetime(
            record=record,
            creation_dt=None,
            mtime_dt=datetime(2024, 9, 18, 12, 0, 0),
            timezone_lookup=lambda _lat, _lon: None,
            recording_tz=ZoneInfo("America/Los_Angeles"),
        )
        self.assertIn("file-mtime", source_mtime)

    def test_resolve_uses_recording_timezone_for_offset_kept(self) -> None:
        """When EXIF has timezone info but no GPS, use recording_timezone to convert."""
        from datetime import timezone

        # Simulate QuickTimeUTC=1 output: exiftool returns PST time with offset
        # Real recording was in China (UTC+8): 2025-12-31 11:07 CST = 2025-12-31 03:07 UTC
        # exiftool converts UTC to system tz: 2025-12-30 19:07-08:00 (PST)
        pst = timezone(timedelta(hours=-8))
        exif_dt_pst = datetime(2025, 12, 30, 19, 7, 6, tzinfo=pst)
        record = {"CreateDate": exif_dt_pst.strftime("%Y:%m:%d %H:%M:%S%z")}

        resolved, source, tz_name = mod.resolve_capture_datetime(
            record=record,
            creation_dt=None,
            mtime_dt=datetime(2025, 12, 30, 19, 8, 0),
            timezone_lookup=lambda _lat, _lon: None,
            recording_tz=ZoneInfo("Asia/Shanghai"),
        )

        # Should convert to China time: Dec 31 11:07 CST, not Dec 30 19:07 PST
        self.assertEqual(resolved, datetime(2025, 12, 31, 11, 7, 6))
        self.assertIn("recording-tz-converted", source)
        self.assertEqual(tz_name, "Asia/Shanghai")

    def test_resolve_treats_mtime_fallback_as_recording_local_wall_time(self) -> None:
        """When falling back to mtime, preserve the recording-local wall date."""
        record = {}

        mtime_local = datetime(2025, 12, 30, 9, 13, 20)

        resolved, source, tz_name = mod.resolve_capture_datetime(
            record=record,
            creation_dt=None,
            mtime_dt=mtime_local,
            timezone_lookup=lambda _lat, _lon: None,
            recording_tz=ZoneInfo("Asia/Shanghai"),
        )

        self.assertEqual(resolved, datetime(2025, 12, 30, 9, 13, 20))
        self.assertEqual(source, "file-mtime-recording-local-assumed")
        self.assertEqual(tz_name, "Asia/Shanghai")

    def test_resolve_treats_creation_fallback_as_recording_local_wall_time(self) -> None:
        """When falling back to file creation time, preserve the local wall date."""
        record = {}
        creation_local = datetime(2025, 12, 30, 8, 0, 0)

        resolved, source, tz_name = mod.resolve_capture_datetime(
            record=record,
            creation_dt=creation_local,
            mtime_dt=datetime(2025, 12, 30, 9, 13, 20),
            timezone_lookup=lambda _lat, _lon: None,
            recording_tz=ZoneInfo("Asia/Shanghai"),
        )

        self.assertEqual(resolved, creation_local)
        self.assertEqual(source, "file-creation-time-recording-local-assumed")
        self.assertEqual(tz_name, "Asia/Shanghai")

    def test_build_sequence_capture_overrides_uses_adjacent_anchored_clip(self) -> None:
        records = [
            {
                "SourceFile": "/mnt/DCIM/100GOPRO/GX011737.MP4",
                "MIMEType": "video/mp4",
            },
            {
                "SourceFile": "/mnt/DCIM/100GOPRO/GX011738.MP4",
                "MIMEType": "video/mp4",
                "CreateDate": "2025:12:15 02:34:16",
            },
        ]

        overrides = mod.build_sequence_capture_overrides(
            records=records,
            timezone_lookup=lambda _lat, _lon: None,
            recording_tz=ZoneInfo("Asia/Shanghai"),
        )

        override = overrides["/mnt/DCIM/100GOPRO/GX011737.MP4"]
        self.assertEqual(override[0], datetime(2025, 12, 15, 2, 34, 16))
        self.assertEqual(override[1], "sequence-neighbor-CreateDate-naive-local")
        self.assertIsNone(override[2])

    def test_build_sequence_capture_overrides_ignores_non_adjacent_anchor(self) -> None:
        records = [
            {
                "SourceFile": "/mnt/DCIM/100GOPRO/GX011737.MP4",
                "MIMEType": "video/mp4",
            },
            {
                "SourceFile": "/mnt/DCIM/100GOPRO/GX011742.MP4",
                "MIMEType": "video/mp4",
                "CreateDate": "2025:12:15 03:49:19",
            },
        ]

        overrides = mod.build_sequence_capture_overrides(
            records=records,
            timezone_lookup=lambda _lat, _lon: None,
            recording_tz=ZoneInfo("Asia/Shanghai"),
        )

        self.assertNotIn("/mnt/DCIM/100GOPRO/GX011737.MP4", overrides)

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

    def test_count_media_missing_gps_counts_only_media_without_gps(self) -> None:
        records = [
            # media with GPS — should not count
            {"SourceFile": "/tmp/a.jpg", "MIMEType": "image/jpeg",
             "GPSLatitude": 37.8199, "GPSLongitude": -122.4783},
            # media without GPS — should count
            {"SourceFile": "/tmp/b.mp4", "MIMEType": "video/mp4"},
            # non-media (explicit exclusion) — should not count
            {"SourceFile": "/tmp/c.txt", "MIMEType": "text/plain"},
            # system metadata — should not count
            {"SourceFile": "/tmp/.DS_Store", "MIMEType": "application/octet-stream"},
        ]
        count = mod.count_media_missing_gps(records)
        self.assertEqual(count, 1)

    def test_count_media_missing_gps_returns_zero_when_all_have_gps(self) -> None:
        records = [
            {"SourceFile": "/tmp/a.jpg", "MIMEType": "image/jpeg",
             "GPSLatitude": 37.8199, "GPSLongitude": -122.4783},
            {"SourceFile": "/tmp/b.mov", "MIMEType": "video/quicktime",
             "GPSLatitude": 31.2, "GPSLongitude": 121.5},
        ]
        count = mod.count_media_missing_gps(records)
        self.assertEqual(count, 0)

    def test_resolve_without_recording_tz_falls_back_to_offset_kept(self) -> None:
        """Without recording_tz, offset timestamps are kept as-is (old behavior)."""
        from datetime import timezone

        pst = timezone(timedelta(hours=-8))
        exif_dt_pst = datetime(2025, 12, 30, 19, 7, 6, tzinfo=pst)
        record = {"CreateDate": exif_dt_pst.strftime("%Y:%m:%d %H:%M:%S%z")}

        resolved, source, tz_name = mod.resolve_capture_datetime(
            record=record,
            creation_dt=None,
            mtime_dt=datetime(2025, 12, 30, 19, 8, 0),
            timezone_lookup=lambda _lat, _lon: None,
            recording_tz=None,
        )

        self.assertEqual(resolved, datetime(2025, 12, 30, 19, 7, 6))
        self.assertIn("offset-kept", source)
        self.assertIsNone(tz_name)

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

    def test_emit_phase_writes_named_phase_line(self) -> None:
        import io
        out = io.StringIO()
        mod._emit_phase("verification started", file=out)
        self.assertEqual(out.getvalue(), "[phase] verification started\n")

    def test_emit_done_writes_report_written_signal(self) -> None:
        import io
        out = io.StringIO()
        mod._emit_done(Path("/tmp/report.json"), file=out)
        self.assertEqual(out.getvalue(), "[done] report written: /tmp/report.json\n")

    def test_verify_with_find_missing_emits_phase_signals(self) -> None:
        fake_module = mock.Mock()
        fake_module.normalized_extensions.return_value = ()
        fake_module.build_dest_index.return_value = {1: [Path("/tmp/dest.bin")]}
        fake_module.build_dest_hash_sets.return_value = {1: {"abc"}}
        fake_module.find_missing_files.return_value = []

        phase_messages: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "a.jpg"
            destination_root = Path(tmp) / "dest"
            destination_root.mkdir()
            source_path.write_bytes(b"x")

            with mock.patch.object(mod, "_load_find_missing_module", return_value=fake_module):
                missing = mod.verify_with_find_missing(
                    media_source_paths=[source_path],
                    destination_root=destination_root,
                    workers=1,
                    verbose=False,
                    phase_callback=phase_messages.append,
                )

        self.assertEqual(missing, [])
        self.assertEqual(
            phase_messages,
            [
                "verification started",
                "verification: preparing shadow tree",
                "verification: building destination index",
                "verification: hashing destination files",
                "verification: comparing source files",
                "verification complete",
            ],
        )


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



# --- --camera-timezone: camera clock set to a different zone than the recording location ---

import pytest

NO_TZ_LOOKUP = lambda _lat, _lon: None  # noqa: E731
SEATTLE = ZoneInfo("America/Los_Angeles")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _resolve(record, camera_tz, timezone_lookup=NO_TZ_LOOKUP):
    return mod.resolve_capture_datetime(
        record=record,
        creation_dt=None,
        mtime_dt=datetime(2000, 1, 1),
        timezone_lookup=timezone_lookup,
        recording_tz=SHANGHAI,
        camera_tz=camera_tz,
    )


def test_camera_tz_corrects_quicktime_value():
    # GoPro left on Seattle time films at 2025-12-31 11:07 in Shanghai.
    # Its clock reads 2025-12-30 19:07 and it stores that raw in QuickTime CreateDate.
    # exiftool (QuickTimeUTC=1, system tz UTC) reports it as 19:07+00:00.
    record = {"CreateDate": "2025:12:30 19:07:06+00:00"}

    without = _resolve(record, camera_tz=None)
    assert without[0] == datetime(2025, 12, 31, 3, 7, 6)  # wrong: raw value taken as UTC

    resolved, source, tz_name = _resolve(record, camera_tz=SEATTLE)
    assert resolved == datetime(2025, 12, 31, 11, 7, 6)
    assert source == "CreateDate-camera-tz-recording-tz-converted"
    assert tz_name == "Asia/Shanghai"


@pytest.mark.parametrize("system_offset", ["-08:00", "+05:30"])
def test_camera_tz_result_independent_of_exiftool_system_tz(system_offset):
    # Same raw 19:07 value, as exiftool would print it on hosts in other zones.
    raw_utc = datetime(2025, 12, 30, 19, 7, 6, tzinfo=mod.timezone.utc)
    hours, minutes = int(system_offset[:3]), int(system_offset[0] + system_offset[4:])
    shown = raw_utc.astimezone(mod.timezone(timedelta(hours=hours, minutes=minutes)))
    record = {"CreateDate": shown.strftime("%Y:%m:%d %H:%M:%S%z")}

    resolved, _, _ = _resolve(record, camera_tz=SEATTLE)
    assert resolved == datetime(2025, 12, 31, 11, 7, 6)


def test_camera_tz_corrects_naive_photo_without_gps():
    record = {"DateTimeOriginal": "2025:12:30 19:07:06"}

    assert _resolve(record, camera_tz=None)[0] == datetime(2025, 12, 30, 19, 7, 6)

    resolved, source, _ = _resolve(record, camera_tz=SEATTLE)
    assert resolved == datetime(2025, 12, 31, 11, 7, 6)
    assert source == "DateTimeOriginal-camera-tz-recording-tz-converted"


def test_camera_tz_does_not_override_gps_utc_time():
    record = {
        "DateTimeOriginal": "2025:12:30 19:07:06",
        "GPSDateStamp": "2025:12:31",
        "GPSTimeStamp": "03:07:06",
        "GPSLatitude": 31.23,
        "GPSLongitude": 121.47,
    }

    resolved, source, tz_name = _resolve(record, camera_tz=SEATTLE, timezone_lookup=lambda _a, _b: "Asia/Shanghai")
    assert resolved == datetime(2025, 12, 31, 11, 7, 6)
    assert source == "gps-utc-converted"
    assert tz_name == "Asia/Shanghai"


def test_camera_tz_ignores_exif_offset_tags():
    # A camera that wrote OffsetTimeOriginal already knew its zone; the instant is right.
    record = {"DateTimeOriginal": "2025:12:30 19:07:06", "OffsetTimeOriginal": "-08:00"}

    resolved, source, _ = _resolve(record, camera_tz=SEATTLE)
    assert resolved == datetime(2025, 12, 31, 11, 7, 6)
    assert "camera-tz" not in source


def test_camera_tz_applies_to_sequence_neighbor_overrides():
    records = [
        {"SourceFile": "/mnt/DCIM/100GOPRO/GX011737.MP4"},
        {"SourceFile": "/mnt/DCIM/100GOPRO/GX011738.MP4", "CreateDate": "2025:12:30 19:07:06+00:00"},
    ]

    overrides = mod.build_sequence_capture_overrides(
        records=records,
        timezone_lookup=NO_TZ_LOOKUP,
        recording_tz=SHANGHAI,
        camera_tz=SEATTLE,
    )

    resolved, source, _ = overrides["/mnt/DCIM/100GOPRO/GX011737.MP4"]
    assert resolved == datetime(2025, 12, 31, 11, 7, 6)
    assert "camera-tz" in source


def test_camera_timezone_requires_recording_timezone(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prog", str(tmp_path), str(tmp_path / "out"), "--camera-timezone", "America/Los_Angeles"])

    assert mod.main() == 2
    assert "--camera-timezone requires --recording-timezone" in capsys.readouterr().err


def test_camera_timezone_rejects_invalid_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "prog", str(tmp_path), str(tmp_path / "out"),
        "--recording-timezone", "Asia/Shanghai", "--camera-timezone", "Not/AZone",
    ])

    assert mod.main() == 2
    assert "Invalid camera timezone" in capsys.readouterr().err



# --- same-stem sidecars (e.g. GoPro .THM) borrow the matching clip's embedded time ---

def _overrides(records, camera_tz=None):
    return mod.build_sequence_capture_overrides(
        records=records,
        timezone_lookup=NO_TZ_LOOKUP,
        recording_tz=SHANGHAI,
        camera_tz=camera_tz,
    )


def test_thm_reuses_matching_clip_time_with_camera_tz():
    records = [
        {"SourceFile": "/mnt/DCIM/100GOPRO/GX010001.THM"},
        {"SourceFile": "/mnt/DCIM/100GOPRO/GX010001.MP4", "CreateDate": "2025:06:01 03:00:00+00:00"},
    ]

    resolved, source, tz_name = _overrides(records, camera_tz=SEATTLE)["/mnt/DCIM/100GOPRO/GX010001.THM"]
    assert resolved == datetime(2025, 6, 1, 18, 0, 0)
    assert source == "same-stem-CreateDate-camera-tz-recording-tz-converted"
    assert tz_name == "Asia/Shanghai"


def test_same_stem_beats_sequence_neighbor():
    records = [
        {"SourceFile": "/mnt/d/GX0001.WAV"},
        {"SourceFile": "/mnt/d/GX0001.MP4", "DateTimeOriginal": "2025:06:01 10:00:00"},
        {"SourceFile": "/mnt/d/GX0002.WAV", "DateTimeOriginal": "2025:06:05 10:00:00"},
    ]

    resolved, source, _ = _overrides(records)["/mnt/d/GX0001.WAV"]
    assert resolved == datetime(2025, 6, 1, 10, 0, 0)
    assert source.startswith("same-stem-")


def test_same_stem_matches_case_insensitively_and_non_sequence_names():
    records = [
        {"SourceFile": "/mnt/d/img_1234.aae"},
        {"SourceFile": "/mnt/d/IMG_1234.HEIC", "DateTimeOriginal": "2025:06:01 10:00:00"},
    ]

    assert _overrides(records)["/mnt/d/img_1234.aae"][0] == datetime(2025, 6, 1, 10, 0, 0)


def test_same_stem_requires_same_folder():
    records = [
        {"SourceFile": "/mnt/a/CLIP.THM"},
        {"SourceFile": "/mnt/b/CLIP.MP4", "DateTimeOriginal": "2025:06:01 10:00:00"},
    ]

    assert "/mnt/a/CLIP.THM" not in _overrides(records)


def test_same_stem_does_not_override_files_with_their_own_time():
    records = [
        {"SourceFile": "/mnt/d/GL0001.LRV", "CreateDate": "2025:06:01 03:00:00+00:00"},
        {"SourceFile": "/mnt/d/GL0001.MP4", "CreateDate": "2025:06:02 03:00:00+00:00"},
    ]

    assert _overrides(records) == {}


def test_same_stem_pick_is_deterministic():
    records = [
        {"SourceFile": "/mnt/d/C1.THM"},
        {"SourceFile": "/mnt/d/C1.WAV", "DateTimeOriginal": "2025:06:02 10:00:00"},
        {"SourceFile": "/mnt/d/C1.MP4", "DateTimeOriginal": "2025:06:01 10:00:00"},
    ]

    assert _overrides(records)["/mnt/d/C1.THM"][0] == datetime(2025, 6, 1, 10, 0, 0)  # ".mp4" < ".wav"
    assert _overrides(list(reversed(records)))["/mnt/d/C1.THM"][0] == datetime(2025, 6, 1, 10, 0, 0)


if __name__ == "__main__":
    unittest.main()
