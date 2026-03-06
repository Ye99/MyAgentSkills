import json
import subprocess
import time
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from typing import cast
from unittest.mock import patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rename_folder_by_ai_itinerary as mod

M = mod


@pytest.fixture(autouse=True)
def set_home_gps_env(monkeypatch):
    """Ensure HOME_GPS is set for all tests to prevent hard fail."""
    monkeypatch.setenv("HOME_GPS", "0.001,0.001")


class RenameFolderByAiItineraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._start_server_patch = patch(
            "rename_folder_by_ai_itinerary.start_opencode_server",
            side_effect=self._fake_start_opencode_server,
        )
        self._stop_server_patch = patch(
            "rename_folder_by_ai_itinerary.stop_opencode_server",
            return_value=None,
        )
        self._start_server_patch.start()
        self._stop_server_patch.start()

    def tearDown(self) -> None:
        self._stop_server_patch.stop()
        self._start_server_patch.stop()

    @staticmethod
    def _fake_start_opencode_server(worker_id: int, startup_timeout_sec: float = 30.0) -> mod.OpencodeServerHandle:
        _ = startup_timeout_sec
        port = 4100 + worker_id
        return mod.OpencodeServerHandle(
            worker_id=worker_id,
            port=port,
            url=f"http://127.0.0.1:{port}",
            process=None,
        )

    def test_build_input_fingerprint_changes_when_media_metadata_changes(self) -> None:
        source = "/tmp/day/a.jpg"
        points_a = [
            mod.MediaPoint(source, 36.0670, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
        ]
        points_b = [
            mod.MediaPoint(source, 36.0671, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
        ]

        fingerprint_a = mod.build_input_fingerprint(points_a, without_gps=[])
        fingerprint_b = mod.build_input_fingerprint(points_b, without_gps=[])

        self.assertNotEqual(fingerprint_a, fingerprint_b)

    def test_extract_media_points_skips_records_with_missing_sourcefile(self) -> None:
        records = [
            {
                "GPSLatitude": 36.0670,
                "GPSLongitude": 120.3150,
                "DateTimeOriginal": "2025:07:23 09:00:00",
            },
            {
                "SourceFile": "/tmp/day/a.jpg",
                "GPSLatitude": 64.2500,
                "GPSLongitude": -15.2040,
                "DateTimeOriginal": "2025:07:23 10:00:00",
            },
        ]

        with patch(
            "rename_folder_by_ai_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["exiftool"], returncode=0, stdout=json.dumps(records), stderr=""),
        ):
            points, without_gps = mod.extract_media_points(Path("/tmp/day"))

        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].source_file, "/tmp/day/a.jpg")
        self.assertEqual(without_gps, [])

    def test_extract_media_points_excludes_png_extension(self) -> None:
        with patch(
            "rename_folder_by_ai_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["exiftool"], returncode=0, stdout="[]", stderr=""),
        ) as run_mock:
            mod.extract_media_points(Path("/tmp/day"))

        cmd = cast(list[str], run_mock.call_args.args[0])
        self.assertNotIn("png", cmd)

    def test_parse_json_payload_accepts_wrapped_output(self) -> None:
        payload = mod.parse_json_payload('notes\n{"landmark_name":"Skogafoss"}\nmore')
        self.assertEqual(payload, {"landmark_name": "Skogafoss"})

    def test_infer_landmark_info_retries_with_exponential_backoff(self) -> None:
        timeout_error = subprocess.TimeoutExpired(cmd=["opencode"], timeout=1)
        with patch("rename_folder_by_ai_itinerary.shutil.which", return_value="/usr/bin/opencode"):
            with patch(
                "rename_folder_by_ai_itinerary.subprocess.run",
                side_effect=[
                    timeout_error,
                    subprocess.CompletedProcess(
                        args=["opencode"],
                        returncode=0,
                        stdout='{"landmark_name":"Skogafoss","country_name":"ISL"}\n',
                        stderr="",
                    ),
                ],
            ):
                with patch("rename_folder_by_ai_itinerary.time.sleep") as sleep_mock:
                    info = mod.infer_landmark_info(
                        63.5321,
                        -19.5116,
                        opencode_timeout_sec=1,
                        opencode_retries=3,
                        opencode_backoff_sec=2.0,
                        strict=True,
                    )

        self.assertEqual(info["landmark"], "Skogafoss")
        self.assertEqual(info["country"], "ISL")
        sleep_mock.assert_called_once_with(2.0)

    def test_infer_landmark_info_attach_uses_http_endpoint_without_subprocess(self) -> None:
        class FakeResponse:
            def __init__(self, status: int, payload: dict[str, object]) -> None:
                self.status = status
                self._payload = payload

            def read(self) -> bytes:
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
                _ = (exc_type, exc, tb)
                return False

        def fake_urlopen(request: Any, timeout: int = 0) -> FakeResponse:
            _ = timeout
            url = request.full_url if hasattr(request, "full_url") else str(request)
            if url.endswith("/session"):
                return FakeResponse(200, {"id": "ses_test"})
            if "/session/ses_test/message" in url:
                return FakeResponse(
                    200,
                    {
                        "info": {
                            "structured": {
                                "landmark_name": "Skogafoss",
                                "country_name": "ISL",
                            }
                        },
                        "parts": [],
                    },
                )
            raise AssertionError(f"unexpected url: {url}")

        with patch("rename_folder_by_ai_itinerary.shutil.which", return_value="/usr/bin/opencode"):
            with patch("rename_folder_by_ai_itinerary.subprocess.run") as run_mock:
                with patch("rename_folder_by_ai_itinerary.urllib.request.urlopen", side_effect=fake_urlopen):
                    diagnostics: dict[str, object] = {}
                    info = mod.infer_landmark_info(
                        63.5321,
                        -19.5116,
                        strict=True,
                        opencode_attach_url="http://127.0.0.1:4100",
                        diagnostics=diagnostics,
                    )

        self.assertEqual(info["landmark"], "Skogafoss")
        self.assertEqual(info["country"], "ISL")
        self.assertEqual(diagnostics.get("opencode_session_id"), "ses_test")
        run_mock.assert_not_called()

    def test_infer_landmark_info_non_iso_country_normalizes_to_unknown(self) -> None:
        with patch("rename_folder_by_ai_itinerary.shutil.which", return_value="/usr/bin/opencode"):
            with patch(
                "rename_folder_by_ai_itinerary.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["opencode"],
                    returncode=0,
                    stdout='{"landmark_name":"Skogafoss","country_name":"Iceland"}\n',
                    stderr="",
                ),
            ):
                info = mod.infer_landmark_info(63.5321, -19.5116, strict=True)

        self.assertEqual(info["country"], "UnknownCountry")

    def test_sample_points_respects_ratio(self) -> None:
        base_time = datetime(2025, 1, 1, 0, 0, 0)
        points = [mod.MediaPoint(f"{i}.jpg", 0.0, 0.0, base_time + timedelta(seconds=i)) for i in range(100)]
        sampled = mod.sample_points(points, ratio=0.05)
        self.assertEqual(len(sampled), 5)

    def test_cluster_media_points_ignores_time_gap(self) -> None:
        points = [
            mod.MediaPoint("a.jpg", 10.0, 10.0, datetime(2025, 7, 24, 9, 0, 0)),
            mod.MediaPoint("b.jpg", 10.0002, 10.0002, datetime(2025, 7, 24, 20, 0, 0)),
        ]

        clusters = mod.cluster_media_points(points, cluster_distance_m=2_000)

        self.assertEqual(len(clusters), 1)

    def test_select_top_landmarks_keeps_time_order_after_count_selection(self) -> None:
        counts = {
            "CalgaryInternationalAirport": 120,
            "SeattleTacomaInternationalAirport": 80,
            "CalgaryTower": 70,
            "MicrosoftCampus": 60,
        }
        first_seen = {
            "SeattleTacomaInternationalAirport": 0,
            "MicrosoftCampus": 1,
            "CalgaryInternationalAirport": 2,
            "CalgaryTower": 3,
        }

        selected = mod.select_top_landmarks_by_count(counts, first_seen, max_landmarks=3)

        self.assertEqual(
            selected,
            [
                "SeattleTacomaInternationalAirport",
                "CalgaryInternationalAirport",
                "CalgaryTower",
            ],
        )

    def test_build_parser_defaults_cluster_distance_and_timeout(self) -> None:
        parser = mod.build_parser()
        args = parser.parse_args(["/tmp/2025_07_24"])

        self.assertEqual(args.ratio, 1.0)
        self.assertEqual(args.cluster_distance_m, 2_000.0)
        self.assertEqual(args.opencode_timeout_sec, 180)
        self.assertEqual(args.opencode_max_attempts, 5)
        self.assertEqual(args.opencode_initial_backoff_sec, 3.0)
        self.assertEqual(args.max_landmarks, 8)
        self.assertEqual(args.inference_workers, 3)
        self.assertFalse(hasattr(args, "time_gap_minutes"))
        self.assertFalse(hasattr(args, "split_distance_m"))
        self.assertFalse(hasattr(args, "split_by_location"))
        self.assertFalse(hasattr(args, "opencode_retries"))
        self.assertFalse(hasattr(args, "opencode_backoff_sec"))

    def test_build_parser_rejects_invalid_ratio_values(self) -> None:
        parser = mod.build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["/tmp/2025_07_24", "--ratio", "0"])

        with self.assertRaises(SystemExit):
            parser.parse_args(["/tmp/2025_07_24", "--ratio", "1.1"])

    def test_build_parser_rejects_invalid_numeric_values(self) -> None:
        parser = mod.build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["/tmp/2025_07_24", "--cluster-distance-m", "0"])

        with self.assertRaises(SystemExit):
            parser.parse_args(["/tmp/2025_07_24", "--max-landmarks", "0"])

        with self.assertRaises(SystemExit):
            parser.parse_args(["/tmp/2025_07_24", "--opencode-max-attempts", "0"])

        with self.assertRaises(SystemExit):
            parser.parse_args(["/tmp/2025_07_24", "--opencode-initial-backoff-sec", "-1"])

        with self.assertRaises(SystemExit):
            parser.parse_args(["/tmp/2025_07_24", "--opencode-timeout-sec", "0"])

        with self.assertRaises(SystemExit):
            parser.parse_args(["/tmp/2025_07_24", "--inference-workers", "0"])

    def test_rename_folder_mixed_country_stays_single_target(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_23"
            day.mkdir()
            for name in ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 36.0670, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 64.2500, -15.2040, datetime(2025, 7, 23, 9, 1, 0)),
                mod.MediaPoint(str(day / "c.jpg"), 36.0680, 120.3160, datetime(2025, 7, 23, 10, 0, 0)),
                mod.MediaPoint(str(day / "d.jpg"), 64.2510, -15.2050, datetime(2025, 7, 23, 10, 1, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "MountLaoshan", "country": "China"},
                        {"landmark": "Berufjordur", "country": "Iceland"},
                        {"landmark": "QingdaoOlympicSailingCenter", "country": "China"},
                        {"landmark": "Vatnajokull", "country": "Iceland"},
                    ],
                ):
                    result = mod.rename_folder_from_itinerary(
                        day,
                        apply=False,
                        ratio=1.0,
                    )

        self.assertEqual(result["status"], "planned-rename")
        self.assertEqual(
            result["landmarks"],
            ["MountLaoshan", "Berufjordur", "QingdaoOlympicSailingCenter", "Vatnajokull"],
        )
        self.assertEqual(
            result["target_name"],
            "2025_07_23_MountLaoshan,Berufjordur,QingdaoOlympicSailingCenter,Vatnajokull",
        )
        self.assertNotIn("split_folders", result)

    def test_unknown_country_keeps_single_target_when_one_known_country(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_23"
            day.mkdir()
            for name in ["a.jpg", "b.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 36.0670, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 37.1000, 121.2000, datetime(2025, 7, 23, 10, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "MayFourthSquare", "country": "China"},
                        {"landmark": "UnknownLandmark", "country": "UnknownCountry"},
                    ],
                ):
                    result = mod.rename_folder_from_itinerary(day, apply=False, ratio=1.0, cluster_distance_m=10)

        self.assertEqual(result["status"], "planned-rename")
        self.assertEqual(result["landmarks"], ["MayFourthSquare"])

    def test_us_canada_us_roundtrip_stays_single_itinerary_thread(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_05"
            day.mkdir()
            for name in ["a.jpg", "b.jpg", "c.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 59.4600, -135.3150, datetime(2025, 7, 5, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 59.7000, -135.0600, datetime(2025, 7, 5, 10, 0, 0)),
                mod.MediaPoint(str(day / "c.jpg"), 59.4700, -135.3000, datetime(2025, 7, 5, 12, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "KlondikeHwy", "country": "USA"},
                        {"landmark": "FraserBorder", "country": "CAN"},
                        {"landmark": "SkagwayHarbor", "country": "USA"},
                    ],
                ):
                    result = mod.rename_folder_from_itinerary(
                        day,
                        apply=False,
                        ratio=1.0,
                        cluster_distance_m=500,
                    )

        self.assertEqual(result["status"], "planned-rename")
        self.assertNotIn("split_folders", result)

    def test_family_member_abroad_stays_single_itinerary_thread(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_05"
            day.mkdir()
            for name in ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 59.4600, -135.3150, datetime(2025, 7, 5, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 48.8566, 2.3522, datetime(2025, 7, 5, 9, 5, 0)),
                mod.MediaPoint(str(day / "c.jpg"), 59.4700, -135.3000, datetime(2025, 7, 5, 9, 10, 0)),
                mod.MediaPoint(str(day / "d.jpg"), 48.8570, 2.3530, datetime(2025, 7, 5, 9, 15, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "KlondikeHwy", "country": "USA"},
                        {"landmark": "EiffelTower", "country": "FRA"},
                        {"landmark": "SkagwayHarbor", "country": "USA"},
                        {"landmark": "Louvre", "country": "FRA"},
                    ],
                ):
                    result = mod.rename_folder_from_itinerary(
                        day,
                        apply=False,
                        ratio=1.0,
                        cluster_distance_m=500,
                    )

        self.assertEqual(result["status"], "planned-rename")
        self.assertEqual(result["landmarks"], ["KlondikeHwy", "EiffelTower", "SkagwayHarbor", "Louvre"])
        self.assertNotIn("split_folders", result)

    def test_rename_folder_applies_max_landmarks_ranked_by_media_count(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_24"
            day.mkdir()
            for name in ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 10.0000, 10.0000, datetime(2025, 7, 24, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 10.0001, 10.0001, datetime(2025, 7, 24, 9, 1, 0)),
                mod.MediaPoint(str(day / "c.jpg"), 10.0002, 10.0002, datetime(2025, 7, 24, 9, 2, 0)),
                mod.MediaPoint(str(day / "d.jpg"), 20.0000, 20.0000, datetime(2025, 7, 24, 11, 0, 0)),
                mod.MediaPoint(str(day / "e.jpg"), 20.0001, 20.0001, datetime(2025, 7, 24, 11, 1, 0)),
                mod.MediaPoint(str(day / "f.jpg"), 30.0000, 30.0000, datetime(2025, 7, 24, 13, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "TopLandmark", "country": "Iceland"},
                        {"landmark": "SecondLandmark", "country": "Iceland"},
                        {"landmark": "ThirdLandmark", "country": "Iceland"},
                    ],
                ) as infer_mock:
                    result = mod.rename_folder_from_itinerary(
                        day,
                        apply=False,
                        ratio=1.0,
                        cluster_distance_m=2_000,
                        max_landmarks=2,
                    )

        self.assertEqual(result["status"], "planned-rename")
        self.assertEqual(result["landmarks"], ["TopLandmark", "SecondLandmark"])
        self.assertEqual(result["target_name"], "2025_07_24_TopLandmark,SecondLandmark")
        self.assertEqual(infer_mock.call_count, 2)

    def test_ratio_one_reuses_sampled_clusters_for_full_ranking(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_24"
            day.mkdir()
            for name in ["a.jpg", "b.jpg", "c.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 10.0000, 10.0000, datetime(2025, 7, 24, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 20.0000, 20.0000, datetime(2025, 7, 24, 10, 0, 0)),
                mod.MediaPoint(str(day / "c.jpg"), 30.0000, 30.0000, datetime(2025, 7, 24, 11, 0, 0)),
            ]

            original_cluster = mod.cluster_media_points

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "FirstSpot", "country": "ISL"},
                        {"landmark": "SecondSpot", "country": "ISL"},
                        {"landmark": "ThirdSpot", "country": "ISL"},
                    ],
                ):
                    with patch(
                        "rename_folder_by_ai_itinerary.cluster_media_points",
                        side_effect=lambda *args, **kwargs: original_cluster(*args, **kwargs),
                    ) as cluster_mock:
                        result = mod.rename_folder_from_itinerary(day, apply=False, ratio=1.0, cluster_distance_m=1.0)

        self.assertEqual(result["status"], "planned-rename")
        self.assertEqual(cluster_mock.call_count, 1)

    def test_ratio_one_adaptive_fallback_infers_additional_clusters_for_unique_landmarks(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_24"
            day.mkdir()
            for name in ["a.jpg", "b.jpg", "c.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 10.0000, 10.0000, datetime(2025, 7, 24, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 20.0000, 20.0000, datetime(2025, 7, 24, 10, 0, 0)),
                mod.MediaPoint(str(day / "c.jpg"), 30.0000, 30.0000, datetime(2025, 7, 24, 11, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "SameSpot", "country": "ISL"},
                        {"landmark": "SameSpot", "country": "ISL"},
                        {"landmark": "OtherSpot", "country": "ISL"},
                    ],
                ) as infer_mock:
                    result = mod.rename_folder_from_itinerary(
                        day,
                        apply=False,
                        ratio=1.0,
                        cluster_distance_m=1.0,
                        max_landmarks=2,
                    )

        self.assertEqual(result["status"], "planned-rename")
        self.assertEqual(result["landmarks"], ["SameSpot", "OtherSpot"])
        self.assertEqual(infer_mock.call_count, 3)

    def test_ratio_less_than_one_skips_pretrim_and_infers_all_sampled_clusters(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_24"
            day.mkdir()
            for name in ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 10.0000, 10.0000, datetime(2025, 7, 24, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 10.0001, 10.0001, datetime(2025, 7, 24, 9, 1, 0)),
                mod.MediaPoint(str(day / "c.jpg"), 20.0000, 20.0000, datetime(2025, 7, 24, 10, 0, 0)),
                mod.MediaPoint(str(day / "d.jpg"), 20.0001, 20.0001, datetime(2025, 7, 24, 10, 1, 0)),
                mod.MediaPoint(str(day / "e.jpg"), 30.0000, 30.0000, datetime(2025, 7, 24, 11, 0, 0)),
                mod.MediaPoint(str(day / "f.jpg"), 30.0001, 30.0001, datetime(2025, 7, 24, 11, 1, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "Alpha", "country": "ISL"},
                        {"landmark": "Bravo", "country": "ISL"},
                        {"landmark": "Charlie", "country": "ISL"},
                    ],
                ) as infer_mock:
                    result = mod.rename_folder_from_itinerary(
                        day,
                        apply=False,
                        ratio=0.5,
                        cluster_distance_m=1.0,
                        max_landmarks=1,
                    )

        self.assertEqual(result["status"], "planned-rename")
        self.assertEqual(infer_mock.call_count, 3)

    def test_failed_inference_writes_state_and_report_without_rename(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_23"
            day.mkdir()
            for name in ["a.jpg", "b.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 36.0670, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 64.2500, -15.2040, datetime(2025, 7, 23, 10, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "MayFourthSquare", "country": "China"},
                        mod.InferenceExhaustedError(
                            "timeout",
                            attempt_count=4,
                            attempt_failures=[
                                {"attempt": 1, "failure_type": "timeout", "detail": "timeout", "wait_before_next_sec": 3.0},
                                {"attempt": 2, "failure_type": "timeout", "detail": "timeout", "wait_before_next_sec": 6.0},
                                {"attempt": 3, "failure_type": "timeout", "detail": "timeout", "wait_before_next_sec": 12.0},
                                {"attempt": 4, "failure_type": "timeout", "detail": "timeout"},
                            ],
                        ),
                    ],
                ):
                    result = mod.rename_folder_from_itinerary(day, apply=False, ratio=1.0)

            state = mod.read_json_file(mod.default_state_file(day))
            report = mod.read_json_file(mod.default_report_file(day))

        self.assertEqual(result["status"], "failed-inference")
        self.assertEqual(result["media_without_gps_count"], 0)
        self.assertEqual(result["media_without_gps_examples"], [])
        self.assertEqual(result["media_without_gps_ratio"], 0.0)
        self.assertIsInstance(state, dict)
        state_dict = cast(dict[str, object], state)
        self.assertEqual(state_dict["next_cluster_index"], 1)
        self.assertEqual(state_dict["persistent_failure_count"], 1)
        persistent_log = cast(list[dict[str, object]], state_dict["persistent_failure_log"])
        self.assertEqual(len(persistent_log), 1)
        self.assertEqual(persistent_log[0]["cluster_index"], 1)
        self.assertEqual(persistent_log[0]["attempt_count"], 4)
        self.assertEqual(len(cast(list[dict[str, object]], persistent_log[0]["attempt_failures"])), 4)
        self.assertIsInstance(report, dict)
        report_dict = cast(dict[str, object], report)
        self.assertEqual(report_dict["status"], "failed-inference")
        self.assertEqual(report_dict["media_without_gps_count"], 0)
        self.assertEqual(report_dict["media_without_gps_examples"], [])
        self.assertEqual(report_dict["media_without_gps_ratio"], 0.0)
        summary = cast(dict[str, object], report_dict["persistent_failure_summary"])
        self.assertEqual(summary["persistent_failure_count"], 1)
        self.assertEqual(summary["last_failed_cluster_index"], 1)

    def test_failed_extract_writes_state_and_report_without_rename(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_23"
            day.mkdir()

            with patch(
                "rename_folder_by_ai_itinerary.extract_media_points",
                side_effect=subprocess.CalledProcessError(returncode=1, cmd=["exiftool"], stderr="boom"),
            ):
                result = mod.rename_folder_from_itinerary(day, apply=False, ratio=1.0)

            state = mod.read_json_file(mod.default_state_file(day))
            report = mod.read_json_file(mod.default_report_file(day))

        self.assertEqual(result["status"], "failed-extract")
        self.assertIsInstance(state, dict)
        self.assertEqual(cast(dict[str, object], state)["status"], "failed-extract")
        self.assertIsInstance(report, dict)
        self.assertEqual(cast(dict[str, object], report)["status"], "failed-extract")

    def test_resume_uses_saved_state_and_completes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_23"
            day.mkdir()
            for name in ["a.jpg", "b.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 36.0670, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 64.2500, -15.2040, datetime(2025, 7, 23, 10, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "MayFourthSquare", "country": "China"},
                        mod.InferenceExhaustedError("timeout", attempt_count=4, attempt_failures=[]),
                    ],
                ):
                    first_result = mod.rename_folder_from_itinerary(day, apply=False, ratio=1.0)

            self.assertEqual(first_result["status"], "failed-inference")

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    return_value={"landmark": "JokulsarlonGlacierLagoon", "country": "Iceland"},
                ) as infer_mock:
                    second_result = mod.rename_folder_from_itinerary(day, apply=False, ratio=1.0)

            self.assertEqual(second_result["status"], "planned-rename")
            self.assertEqual(infer_mock.call_count, 1)

            final_state = mod.read_json_file(mod.default_state_file(day))
            self.assertIsInstance(final_state, dict)
            final_state_dict = cast(dict[str, object], final_state)
            self.assertEqual(final_state_dict["persistent_failure_count"], 1)

    def test_resume_skips_completed_fallback_clusters_after_failed_inference(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_23"
            day.mkdir()
            for name in ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 10.0, 10.0, datetime(2025, 7, 23, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 20.0, 20.0, datetime(2025, 7, 23, 10, 0, 0)),
                mod.MediaPoint(str(day / "c.jpg"), 30.0, 30.0, datetime(2025, 7, 23, 11, 0, 0)),
                mod.MediaPoint(str(day / "d.jpg"), 40.0, 40.0, datetime(2025, 7, 23, 12, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "A", "country": "ISL"},
                        {"landmark": "A", "country": "ISL"},
                        {"landmark": "C", "country": "ISL"},
                        mod.InferenceExhaustedError("timeout", attempt_count=2, attempt_failures=[]),
                    ],
                ):
                    first = mod.rename_folder_from_itinerary(
                        day,
                        apply=False,
                        ratio=1.0,
                        cluster_distance_m=1.0,
                        max_landmarks=2,
                        inference_workers=1,
                    )

            self.assertEqual(first["status"], "failed-inference")

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    return_value={"landmark": "D", "country": "ISL"},
                ) as infer_mock:
                    second = mod.rename_folder_from_itinerary(
                        day,
                        apply=False,
                        ratio=1.0,
                        cluster_distance_m=1.0,
                        max_landmarks=2,
                        inference_workers=1,
                    )

            self.assertEqual(second["status"], "planned-rename")
            self.assertEqual(infer_mock.call_count, 0)

    def test_resume_ignores_completed_clusters_when_input_fingerprint_changes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_23"
            day.mkdir()
            for name in ["a.jpg", "b.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 36.0670, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 64.2500, -15.2040, datetime(2025, 7, 23, 10, 0, 0)),
            ]

            stale_state = {
                "folder_path": str(day),
                "status": "in-progress",
                "config": {
                    "ratio": 1.0,
                    "cluster_distance_m": 2_000.0,
                    "max_landmarks": 8,
                    "opencode_timeout_sec": 180,
                    "opencode_max_attempts": 5,
                    "opencode_initial_backoff_sec": 3.0,
                    "opencode_model": None,
                    "inference_workers": 3,
                },
                "input_fingerprint": "stale-fingerprint",
                "next_cluster_index": 1,
                "completed_cluster_infos": [{"landmark": "StaleSpot", "country": "China"}],
                "persistent_failure_count": 0,
                "persistent_failure_log": [],
            }
            mod.write_json_file(mod.default_state_file(day), stale_state)

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "FreshSpotA", "country": "China"},
                        {"landmark": "FreshSpotB", "country": "China"},
                    ],
                ) as infer_mock:
                    result = mod.rename_folder_from_itinerary(day, apply=False, ratio=1.0)

        self.assertEqual(result["status"], "planned-rename")
        self.assertEqual(infer_mock.call_count, 2)

    def test_parallel_inference_preserves_cluster_order(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_24"
            day.mkdir()
            for name in ["a.jpg", "b.jpg", "c.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 10.0000, 10.0000, datetime(2025, 7, 24, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 20.0000, 20.0000, datetime(2025, 7, 24, 10, 0, 0)),
                mod.MediaPoint(str(day / "c.jpg"), 30.0000, 30.0000, datetime(2025, 7, 24, 11, 0, 0)),
            ]

            def fake_infer(lat: float, lon: float, **_kwargs: object) -> dict[str, str]:
                if lat < 15:
                    time.sleep(0.06)
                    return {"landmark": "FirstSpot", "country": "Iceland"}
                if lat < 25:
                    time.sleep(0.01)
                    return {"landmark": "SecondSpot", "country": "Iceland"}
                time.sleep(0.03)
                return {"landmark": "ThirdSpot", "country": "Iceland"}

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch("rename_folder_by_ai_itinerary.infer_landmark_info", side_effect=fake_infer):
                    result = mod.rename_folder_from_itinerary(
                        day,
                        apply=False,
                        ratio=1.0,
                        cluster_distance_m=1.0,
                        inference_workers=3,
                    )

        self.assertEqual(result["status"], "planned-rename")
        self.assertEqual(result["landmarks"], ["FirstSpot", "SecondSpot", "ThirdSpot"])
        report = cast(dict[str, object], result["inference_worker_report"])
        self.assertEqual(report["workers_requested"], 3)
        self.assertEqual(report["servers_started"], 3)
        self.assertEqual(report["tasks_total"], 3)
        self.assertEqual(report["tasks_succeeded"], 3)

    def test_parallel_inference_failure_keeps_first_failed_index(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_24"
            day.mkdir()
            for name in ["a.jpg", "b.jpg", "c.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 10.0000, 10.0000, datetime(2025, 7, 24, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 20.0000, 20.0000, datetime(2025, 7, 24, 10, 0, 0)),
                mod.MediaPoint(str(day / "c.jpg"), 30.0000, 30.0000, datetime(2025, 7, 24, 11, 0, 0)),
            ]

            def fake_infer(lat: float, lon: float, **_kwargs: object) -> dict[str, str]:
                if lat < 15:
                    time.sleep(0.05)
                    return {"landmark": "FirstSpot", "country": "Iceland"}
                if lat < 25:
                    raise mod.InferenceExhaustedError(
                        "timeout",
                        attempt_count=2,
                        attempt_failures=[
                            {"attempt": 1, "failure_type": "timeout", "detail": "timeout", "wait_before_next_sec": 3.0},
                            {"attempt": 2, "failure_type": "timeout", "detail": "timeout"},
                        ],
                    )
                time.sleep(0.01)
                return {"landmark": "ThirdSpot", "country": "Iceland"}

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch("rename_folder_by_ai_itinerary.infer_landmark_info", side_effect=fake_infer):
                    result = mod.rename_folder_from_itinerary(
                        day,
                        apply=False,
                        ratio=1.0,
                        cluster_distance_m=1.0,
                        inference_workers=3,
                    )

            state = mod.read_json_file(mod.default_state_file(day))

        self.assertEqual(result["status"], "failed-inference")
        self.assertEqual(result["next_cluster_index"], 1)
        report = cast(dict[str, object], result["inference_worker_report"])
        self.assertEqual(report["tasks_failed"], 1)
        self.assertIsInstance(state, dict)
        state_dict = cast(dict[str, object], state)
        completed = cast(list[dict[str, object]], state_dict["completed_cluster_infos"])
        self.assertEqual(len(completed), 1)

    def test_parallel_server_pool_dedupes_same_inference_key(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_24"
            day.mkdir()
            for name in ["a.jpg", "b.jpg", "c.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 10.00001, 10.00001, datetime(2025, 7, 24, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 10.00003, 10.00003, datetime(2025, 7, 24, 10, 0, 0)),
                mod.MediaPoint(str(day / "c.jpg"), 30.00000, 30.00000, datetime(2025, 7, 24, 11, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "SameSpot", "country": "Iceland"},
                        {"landmark": "FarSpot", "country": "Iceland"},
                    ],
                ) as infer_mock:
                    result = mod.rename_folder_from_itinerary(
                        day,
                        apply=False,
                        ratio=1.0,
                        cluster_distance_m=1.0,
                        inference_workers=3,
                    )

        self.assertEqual(result["status"], "planned-rename")
        self.assertEqual(infer_mock.call_count, 2)

    def test_rename_folder_reports_media_without_gps_visibility(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_23"
            day.mkdir()
            for name in ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "nogps.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 36.0670, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 36.0680, 120.3160, datetime(2025, 7, 23, 9, 1, 0)),
                mod.MediaPoint(str(day / "c.jpg"), 64.2500, -15.2040, datetime(2025, 7, 23, 10, 0, 0)),
                mod.MediaPoint(str(day / "d.jpg"), 64.2510, -15.2050, datetime(2025, 7, 23, 10, 1, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [str(day / "nogps.jpg")])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "MayFourthSquare", "country": "China"},
                        {"landmark": "Jokulsarlon", "country": "Iceland"},
                    ],
                ):
                    result = mod.rename_folder_from_itinerary(day, apply=False, ratio=1.0)

        self.assertEqual(result["status"], "planned-rename")
        self.assertEqual(result["media_without_gps_count"], 1)
        self.assertEqual(result["media_without_gps_examples"], [str(day / "nogps.jpg")])
        self.assertEqual(result["media_without_gps_ratio"], 0.2)

    def test_single_target_skips_unknown_landmark_placeholder(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_23"
            day.mkdir()
            for name in ["a.jpg", "b.jpg"]:
                (day / name).write_bytes(b"x")

            points = [
                mod.MediaPoint(str(day / "a.jpg"), 36.0670, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
                mod.MediaPoint(str(day / "b.jpg"), 64.2500, -15.2040, datetime(2025, 7, 23, 10, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "UnknownLandmark", "country": "China"},
                        {"landmark": "UnknownLandmark", "country": "Iceland"},
                    ],
                ):
                    result = mod.rename_folder_from_itinerary(day, apply=False, ratio=1.0)

        self.assertEqual(result["status"], "skipped-no-landmark")
        self.assertEqual(result["target_name"], "2025_07_23")

    def test_apply_rename_does_not_touch_outside_source_paths(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            day = root / "2025_07_23"
            day.mkdir()
            inside_file = day / "inside.jpg"
            outside_file = root / "outside.jpg"
            inside_file.write_bytes(b"x")
            outside_file.write_bytes(b"y")

            points = [
                mod.MediaPoint(str(inside_file), 36.0670, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
                mod.MediaPoint(str(outside_file), 64.2500, -15.2040, datetime(2025, 7, 23, 10, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "MayFourthSquare", "country": "China"},
                        {"landmark": "Jokulsarlon", "country": "Iceland"},
                    ],
                ):
                    result = mod.rename_folder_from_itinerary(day, apply=True, ratio=1.0)

            self.assertEqual(result["status"], "renamed")
            self.assertTrue(outside_file.exists())
            self.assertEqual(result["target_name"], "2025_07_23_MayFourthSquare,Jokulsarlon")

    def test_apply_rename_keeps_non_gps_media_in_renamed_folder(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_23"
            day.mkdir()
            china = day / "china.jpg"
            usa = day / "usa.jpg"
            nogps = day / "nogps.jpg"
            china.write_bytes(b"a")
            usa.write_bytes(b"b")
            nogps.write_bytes(b"c")

            points = [
                mod.MediaPoint(str(china), 36.0670, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
                mod.MediaPoint(str(usa), 59.5228, -140.1400, datetime(2025, 7, 23, 16, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [str(nogps)])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "ChinaSpot", "country": "CHN"},
                        {"landmark": "UsaSpot", "country": "USA"},
                    ],
                ):
                    result = mod.rename_folder_from_itinerary(day, apply=True, ratio=1.0, cluster_distance_m=500)

            self.assertEqual(result["status"], "renamed")
            self.assertFalse(day.exists())
            renamed = Path(tmpdir) / "2025_07_23_ChinaSpot,UsaSpot"
            self.assertTrue((renamed / "nogps.jpg").exists())

    def test_single_folder_apply_returns_failed_rename_when_rename_raises(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_23"
            day.mkdir()
            photo = day / "a.jpg"
            photo.write_bytes(b"x")
            points = [
                mod.MediaPoint(str(photo), 36.0670, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    return_value={"landmark": "MayFourthSquare", "country": "China"},
                ):
                    with patch("pathlib.Path.rename", side_effect=OSError("perm denied")):
                        result = mod.rename_folder_from_itinerary(day, apply=True, ratio=1.0)

            report = mod.read_json_file(mod.default_report_file(day))

        self.assertEqual(result["status"], "failed-rename")
        self.assertEqual(result["media_without_gps_count"], 0)
        self.assertEqual(result["media_without_gps_examples"], [])
        self.assertEqual(result["media_without_gps_ratio"], 0.0)
        self.assertIsInstance(report, dict)
        report_dict = cast(dict[str, object], report)
        self.assertEqual(report_dict["status"], "failed-rename")
        self.assertEqual(report_dict["media_without_gps_count"], 0)
        self.assertEqual(report_dict["media_without_gps_examples"], [])
        self.assertEqual(report_dict["media_without_gps_ratio"], 0.0)

    def test_main_rejects_state_or_report_file_for_tree_mode(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            argv_state = [
                "rename_folder_by_ai_itinerary.py",
                str(root),
                "--state-file",
                str(root / "custom-state.json"),
            ]
            with patch.object(sys, "argv", argv_state):
                with self.assertRaises(SystemExit):
                    mod.main()

            argv_report = [
                "rename_folder_by_ai_itinerary.py",
                str(root),
                "--report-file",
                str(root / "custom-report.json"),
            ]
            with patch.object(sys, "argv", argv_report):
                with self.assertRaises(SystemExit):
                    mod.main()

    def test_main_treats_date_prefixed_root_with_day_children_as_tree(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "2025_07_23_batch"
            day = root / "2025_07_24"
            day.mkdir(parents=True)

            argv = ["rename_folder_by_ai_itinerary.py", str(root)]
            with patch.object(sys, "argv", argv):
                with patch("rename_folder_by_ai_itinerary.process_folder_tree", return_value={"status": "completed"}) as tree_mock:
                    with patch(
                        "rename_folder_by_ai_itinerary.rename_folder_from_itinerary",
                        return_value={"status": "planned-rename"},
                    ) as rename_mock:
                        mod.main()

            tree_mock.assert_called_once()
            rename_mock.assert_not_called()

    def test_main_rejects_nonexistent_folder(self) -> None:
        with TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing"
            argv = ["rename_folder_by_ai_itinerary.py", str(missing)]
            with patch.object(sys, "argv", argv):
                with self.assertRaises(SystemExit):
                    mod.main()

    def test_build_target_folder_name_uses_comma_join(self) -> None:
        target = mod.build_target_folder_name("2025_07_24", ["Magnusarfoss", "Fjarargljufur", "VikChurch"])
        self.assertEqual(target, "2025_07_24_Magnusarfoss,Fjarargljufur,VikChurch")

    def test_find_available_target_appends_numeric_suffix(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "2025_07_24"
            source.mkdir()
            (root / "2025_07_24_Skogafoss").mkdir()

            target = mod.find_available_target(source, "2025_07_24_Skogafoss")

            self.assertEqual(target.name, "2025_07_24_Skogafoss_2")

    def test_discover_day_folders_from_tree(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "2025" / "2025_07_22").mkdir(parents=True)
            (root / "2025" / "2025_07_23").mkdir(parents=True)
            (root / "2025" / "2025_07_23_Jokulsarlon").mkdir(parents=True)
            (root / "misc" / "random").mkdir(parents=True)

            discovered = mod.discover_day_folders(root)

        self.assertEqual([path.name for path in discovered], ["2025_07_22", "2025_07_23"])

    def test_verify_tree_integrity_detects_target_count_mismatch(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            folder = root / "2025_07_22"
            folder.mkdir()
            results = [
                {
                    "folder_path": str(folder),
                    "status": "renamed",
                    "target_name": "2025_07_22_Skogafoss",
                }
            ]

            integrity = mod.verify_tree_integrity(results, apply=True)

        self.assertFalse(integrity["passed"])
        self.assertFalse(integrity["target_folder_count_ok"])

    def test_process_folder_tree_writes_summary_and_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            day_a = root / "2025" / "2025_07_22"
            day_b = root / "2025" / "2025_07_23"
            day_a.mkdir(parents=True)
            day_b.mkdir(parents=True)

            with patch(
                "rename_folder_by_ai_itinerary.rename_folder_from_itinerary",
                side_effect=[
                    {"folder_path": str(day_a), "status": "planned-rename", "target_name": "2025_07_22_A"},
                    {"folder_path": str(day_b), "status": "failed-inference", "state_file": "s.json", "report_file": "r.json"},
                ],
            ):
                summary = mod.process_folder_tree(root, apply=False, ratio=0.01)

            tree_report = mod.read_json_file(mod.default_tree_report_file(root))
            tree_state = mod.read_json_file(mod.default_tree_state_file(root))

        self.assertEqual(summary["total_folder_count"], 2)
        self.assertEqual(summary["failed_folder_count"], 1)
        self.assertEqual(summary["planned_folder_count"], 1)
        self.assertIsInstance(tree_report, dict)
        self.assertEqual(cast(dict[str, object], tree_report)["failed_folder_count"], 1)
        self.assertIsInstance(tree_state, dict)
        self.assertEqual(cast(dict[str, object], tree_state)["total_folder_count"], 2)

    def test_process_folder_tree_reuses_server_pool_across_folders(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            day_a = root / "2025" / "2025_07_22"
            day_b = root / "2025" / "2025_07_23"
            day_a.mkdir(parents=True)
            day_b.mkdir(parents=True)

            seen_pool_ids: list[int] = []

            def fake_rename(
                folder: Path,
                apply: bool,
                ratio: float,
                cluster_distance_m: float,
                max_landmarks: int,
                opencode_timeout_sec: int,
                opencode_retries: int,
                opencode_backoff_sec: float,
                opencode_model: str | None,
                inference_workers: int,
                server_pool: list[mod.OpencodeServerHandle] | None,
                inference_scheduler: mod.SharedInferenceScheduler | None,
                state_file: Path,
                report_file: Path,
                resume: bool,
                home_gps: tuple[float, float] = (0.0, 0.0),
            ) -> dict[str, object]:
                _ = (
                    apply,
                    ratio,
                    cluster_distance_m,
                    max_landmarks,
                    opencode_timeout_sec,
                    opencode_retries,
                    opencode_backoff_sec,
                    opencode_model,
                    inference_workers,
                    inference_scheduler,
                    state_file,
                    report_file,
                    resume,
                    home_gps,
                )
                seen_pool_ids.append(id(server_pool))
                return {"folder_path": str(folder), "status": "planned-rename", "target_name": f"{folder.name}_A"}

            with patch(
                "rename_folder_by_ai_itinerary.start_opencode_server",
                side_effect=self._fake_start_opencode_server,
            ) as start_mock:
                with patch("rename_folder_by_ai_itinerary.stop_opencode_server", return_value=None) as stop_mock:
                    with patch("rename_folder_by_ai_itinerary.rename_folder_from_itinerary", side_effect=fake_rename):
                        summary = mod.process_folder_tree(root, apply=False, ratio=0.01, inference_workers=3)

        self.assertEqual(summary["total_folder_count"], 2)
        self.assertEqual(start_mock.call_count, 3)
        self.assertEqual(stop_mock.call_count, 3)
        self.assertEqual(len(set(seen_pool_ids)), 1)

    def test_process_folder_tree_continues_after_failed_extract(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            day_a = root / "2025" / "2025_07_22"
            day_b = root / "2025" / "2025_07_23"
            day_a.mkdir(parents=True)
            day_b.mkdir(parents=True)

            def fake_extract(folder: Path) -> tuple[list[mod.MediaPoint], list[str]]:
                if folder == day_a:
                    raise subprocess.CalledProcessError(returncode=1, cmd=["exiftool"], stderr="broken")
                return ([], [])

            with patch("rename_folder_by_ai_itinerary.extract_media_points", side_effect=fake_extract):
                summary = mod.process_folder_tree(root, apply=False, ratio=1.0)

        self.assertEqual(summary["total_folder_count"], 2)
        self.assertEqual(summary["failed_folder_count"], 1)
        self.assertEqual(summary["skipped_folder_count"], 1)

    # ── Coverage-gap tests for review recommendations ───────────────

    def test_write_json_file_atomic_creates_file(self) -> None:
        """write_json_file uses atomic write (temp + replace), producing valid JSON."""
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "out.json"
            mod.write_json_file(target, {"key": "value"})
            self.assertTrue(target.exists())
            loaded = json.loads(target.read_text())
            self.assertEqual(loaded, {"key": "value"})

    def test_write_json_file_atomic_no_leftover_temp_on_success(self) -> None:
        """After successful atomic write, no .tmp files remain."""
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "out.json"
            mod.write_json_file(target, {"a": 1})
            leftovers = list(Path(tmpdir).glob("*.tmp"))
            self.assertEqual(leftovers, [])

    def test_write_json_file_atomic_cleans_temp_on_error(self) -> None:
        """If os.write raises, the temp file is cleaned up."""
        with TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "out.json"
            with patch("rename_folder_by_ai_itinerary.os.write", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    mod.write_json_file(target, {"a": 1})
            leftovers = list(Path(tmpdir).glob("*.tmp"))
            self.assertEqual(leftovers, [])
            self.assertFalse(target.exists())

    def test_load_completed_infos_renormalizes_landmarks(self) -> None:
        """Landmarks loaded from state file are re-normalized through normalize_landmark_token."""
        state: dict[str, Any] = {
            "completed_cluster_infos_by_index": {
                "0": {"landmark": "fjaðrárgljúfur canyon", "country": "ISL"},
                "1": {"landmark": "Skógafoss", "country": "ISL"},
            }
        }
        result = mod.load_completed_infos_by_index(state)
        self.assertEqual(result[0]["landmark"], "FjarargljufurCanyon")
        self.assertEqual(result[1]["landmark"], "Skogafoss")

    def test_load_completed_infos_renormalizes_countries(self) -> None:
        """Countries loaded from state file are re-normalized through normalize_country_name."""
        state: dict[str, Any] = {
            "completed_cluster_infos_by_index": {
                "0": {"landmark": "Skogafoss", "country": "Iceland"},
            }
        }
        result = mod.load_completed_infos_by_index(state)
        # "Iceland" is not a 3-letter ISO code, so normalize_country_name returns UnknownCountry
        self.assertEqual(result[0]["country"], "UnknownCountry")

    def test_validate_target_within_parent_rejects_traversal(self) -> None:
        """_validate_target_within_parent raises ValueError on path traversal."""
        with TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir) / "parent"
            parent.mkdir()
            outside = Path(tmpdir) / "outside"
            with self.assertRaises(ValueError):
                mod._validate_target_within_parent(parent, outside)

    def test_validate_target_within_parent_accepts_child(self) -> None:
        """_validate_target_within_parent does not raise for a path within parent."""
        with TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            child = parent / "child_dir"
            # Should not raise
            mod._validate_target_within_parent(parent, child)

    def test_find_available_target_rejects_path_traversal(self) -> None:
        """find_available_target calls _validate_target_within_parent and rejects traversal."""
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "2025_07_24"
            source.mkdir()
            with self.assertRaises(ValueError):
                mod.find_available_target(source, "../escape")

    def test_extract_media_points_filters_zero_zero_gps(self) -> None:
        """GPS coordinates (0, 0) are treated as missing GPS."""
        records = [
            {
                "SourceFile": "/tmp/day/a.jpg",
                "GPSLatitude": 0.0,
                "GPSLongitude": 0.0,
                "DateTimeOriginal": "2025:07:23 09:00:00",
            },
            {
                "SourceFile": "/tmp/day/b.jpg",
                "GPSLatitude": 64.2500,
                "GPSLongitude": -15.2040,
                "DateTimeOriginal": "2025:07:23 10:00:00",
            },
        ]
        with patch(
            "rename_folder_by_ai_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["exiftool"], returncode=0, stdout=json.dumps(records), stderr=""),
        ):
            points, without_gps = mod.extract_media_points(Path("/tmp/day"))

        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].source_file, "/tmp/day/b.jpg")
        self.assertIn("/tmp/day/a.jpg", without_gps)

    def test_discover_day_folders_skips_symlinks(self) -> None:
        """discover_day_folders skips symlinked subdirectories."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            real_dir = root / "real"
            real_dir.mkdir()
            day = real_dir / "2025_07_24"
            day.mkdir()
            # Create a symlink loop
            link = root / "link"
            link.symlink_to(real_dir)

            folders = mod.discover_day_folders(root)
            # Only the real day folder should be found, not the symlinked one
            folder_paths = [str(f) for f in folders]
            self.assertIn(str(day), folder_paths)
            # The symlinked day folder should be excluded
            symlinked_day = link / "2025_07_24"
            self.assertNotIn(str(symlinked_day), folder_paths)

    def test_handle_inference_failure_writes_state_and_report(self) -> None:
        """_handle_inference_failure writes state/report files and returns correct dict."""
        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir) / "2025_07_24"
            folder.mkdir()
            state_file = Path(tmpdir) / ".state.json"
            report_file = Path(tmpdir) / ".report.json"

            exc = mod.InferenceExhaustedError("boom", attempt_count=3, attempt_failures=[])
            cluster = mod.LocationCluster(
                points=[mod.MediaPoint("a.jpg", 10.0, 20.0, datetime(2025, 7, 24, 9, 0, 0))]
            )

            result = mod._handle_inference_failure(
                failure=(2, cluster, exc),
                folder=folder,
                state_file=state_file,
                report_file=report_file,
                current_config={"ratio": 1.0},
                input_fingerprint="abc123",
                completed_infos_by_index={},
                persistent_failure_log=[],
                inference_worker_report={},
                points_count=5,
                sampled_count=5,
                ratio=1.0,
                media_without_gps_count=1,
                media_without_gps_examples=["/tmp/x.jpg"],
                media_without_gps_ratio_value=0.2,
            )

            self.assertEqual(result["status"], "failed-inference")
            self.assertEqual(result["next_cluster_index"], 2)
            self.assertTrue(state_file.exists())
            self.assertTrue(report_file.exists())
            state_data = json.loads(state_file.read_text())
            self.assertEqual(state_data["status"], "failed-inference")
            report_data = json.loads(report_file.read_text())
            self.assertEqual(report_data["status"], "failed-inference")

    def test_orphaned_state_files_cleaned_after_tree_rename(self) -> None:
        """After a tree apply-rename, orphaned state/report files for renamed folders are removed."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            day_folder = root / "2025_07_24"
            day_folder.mkdir()
            renamed_target = root / "2025_07_24_Landmark"

            # Pre-create state/report files named for the original folder
            state_file = mod.default_state_file(day_folder)
            report_file = mod.default_report_file(day_folder)

            points = [
                mod.MediaPoint(str(day_folder / "a.jpg"), 63.5, -19.5, datetime(2025, 7, 24, 9, 0)),
            ]

            def fake_extract(folder: Path) -> tuple[list[mod.MediaPoint], list[str]]:
                return (points, [])

            def fake_http_retry(**kwargs: Any) -> tuple[dict[str, Any], str]:
                return {"landmark_name": "Landmark", "country_name": "ISL"}, "ses1"

            with patch("rename_folder_by_ai_itinerary.extract_media_points", side_effect=fake_extract):
                with patch("rename_folder_by_ai_itinerary._run_opencode_http_with_retry", side_effect=fake_http_retry):
                    summary = mod.process_folder_tree(root, apply=True, ratio=1.0)

            # After rename, the original day_folder should not exist
            self.assertFalse(day_folder.exists())
            # The renamed folder should exist
            self.assertTrue(renamed_target.exists())
            # Orphaned state/report files should be cleaned up
            self.assertFalse(state_file.exists(), f"State file should be cleaned up: {state_file}")
            self.assertFalse(report_file.exists(), f"Report file should be cleaned up: {report_file}")

    def test_batch_state_write_only_once_after_all_inferences(self) -> None:
        """State file should be written once after all inferences complete, not per-cluster."""
        with TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir) / "2025_07_24"
            folder.mkdir()

            points = [
                mod.MediaPoint(str(folder / "a.jpg"), 10.0, 10.0, datetime(2025, 7, 24, 9, 0)),
                mod.MediaPoint(str(folder / "b.jpg"), 20.0, 20.0, datetime(2025, 7, 24, 12, 0)),
            ]

            call_count = 0

            def fake_extract(f: Path) -> tuple[list[mod.MediaPoint], list[str]]:
                return (points, [])

            call_sequence: list[str] = []
            real_write_json_file = mod.write_json_file

            def tracking_write_json_file(path: Path, payload: dict[str, Any]) -> None:
                if ".ai-itinerary-state" in str(path):
                    call_sequence.append(f"state-write:{payload.get('status', 'unknown')}")
                real_write_json_file(path, payload)

            http_call_count = 0

            def fake_http_retry(**kwargs: Any) -> tuple[dict[str, Any], str]:
                nonlocal http_call_count
                http_call_count += 1
                names = ["Landmark1", "Landmark2"]
                name = names[http_call_count - 1] if http_call_count <= len(names) else "Other"
                return {"landmark_name": name, "country_name": "TST"}, "ses1"

            with patch("rename_folder_by_ai_itinerary.extract_media_points", side_effect=fake_extract):
                with patch("rename_folder_by_ai_itinerary._run_opencode_http_with_retry", side_effect=fake_http_retry):
                    with patch("rename_folder_by_ai_itinerary.write_json_file", side_effect=tracking_write_json_file):
                        mod.rename_folder_from_itinerary(
                            folder=folder,
                            apply=False,
                            ratio=1.0,
                            cluster_distance_m=1_000.0,
                        )

            # State should be written only once as "in-progress" (batch) and once as "completed",
            # not once per cluster inference
            state_writes = [s for s in call_sequence if s.startswith("state-write:")]
            in_progress_writes = [s for s in state_writes if "in-progress" in s]
            # With 2 clusters there should be at most 1 in-progress write (batch), not 2
            self.assertLessEqual(len(in_progress_writes), 1,
                f"Expected at most 1 batch in-progress state write, got {len(in_progress_writes)}")


if __name__ == "__main__":
    unittest.main()


# ── pytest-style tests for home-photo feature (Tasks 1–2) ──


def test_extract_home_gps_valid(tmp_path, monkeypatch):
    """extract_home_gps returns (lat, lon) from exiftool output."""
    fake_json = json.dumps([{"SourceFile": "x.heic", "GPSLatitude": 47.694, "GPSLongitude": -122.101}])
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"stdout": fake_json})(),
    )
    lat, lon = M.extract_home_gps(tmp_path / "x.heic")
    assert abs(lat - 47.694) < 1e-6
    assert abs(lon - (-122.101)) < 1e-6


def test_extract_home_gps_no_gps(tmp_path, monkeypatch):
    """extract_home_gps raises SystemExit when photo has no GPS."""
    fake_json = json.dumps([{"SourceFile": "x.heic"}])
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"stdout": fake_json})(),
    )
    with pytest.raises(SystemExit):
        M.extract_home_gps(tmp_path / "x.heic")


def test_extract_home_gps_zero_gps(tmp_path, monkeypatch):
    """extract_home_gps rejects (0,0) GPS as invalid."""
    fake_json = json.dumps([{"SourceFile": "x.heic", "GPSLatitude": 0.0, "GPSLongitude": 0.0}])
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"stdout": fake_json})(),
    )
    with pytest.raises(SystemExit):
        M.extract_home_gps(tmp_path / "x.heic")


def test_resolve_home_gps_from_env(monkeypatch):
    """HOME_GPS env var takes priority."""
    monkeypatch.setenv("HOME_GPS", "47.694,-122.101")
    lat, lon = M.resolve_home_gps(home_photo=None)
    assert abs(lat - 47.694) < 1e-6
    assert abs(lon - (-122.101)) < 1e-6


def test_resolve_home_gps_from_photo(tmp_path, monkeypatch):
    """Falls back to --home-photo when HOME_GPS not set."""
    monkeypatch.delenv("HOME_GPS", raising=False)
    fake_json = json.dumps([{"SourceFile": "x.heic", "GPSLatitude": 47.694, "GPSLongitude": -122.101}])
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"stdout": fake_json})(),
    )
    lat, lon = M.resolve_home_gps(home_photo=tmp_path / "x.heic")
    assert abs(lat - 47.694) < 1e-6


def test_resolve_home_gps_neither_set(monkeypatch):
    """Hard fail when neither HOME_GPS nor --home-photo is set."""
    monkeypatch.delenv("HOME_GPS", raising=False)
    with pytest.raises(SystemExit):
        M.resolve_home_gps(home_photo=None)


def test_resolve_home_gps_malformed_env(monkeypatch):
    """Malformed HOME_GPS env var causes SystemExit."""
    monkeypatch.setenv("HOME_GPS", "not-a-coordinate")
    with pytest.raises(SystemExit):
        M.resolve_home_gps(home_photo=None)


def test_home_cluster_skips_inference(monkeypatch):
    """Cluster within 200m of home gets Home landmark without full inference."""
    home_gps = (47.694, -122.101)
    cluster = M.LocationCluster(points=[
        M.MediaPoint(source_file="a.jpg", lat=47.694, lon=-122.101,
                     timestamp=datetime(2025, 7, 1, 10, 0)),
    ])
    pending = [(0, cluster)]
    call_count = {"n": 0}
    def fake_infer(*a, **kw):
        call_count["n"] += 1
        return {"landmark": "ShouldBeOverridden", "country": "UnitedStates"}
    monkeypatch.setattr(M, "infer_landmark_info", fake_infer)
    completed, failure, report = M.infer_pending_cluster_infos(
        pending,
        opencode_timeout_sec=10,
        opencode_retries=1,
        opencode_backoff_sec=0.1,
        opencode_model=None,
        inference_workers=1,
        home_gps=home_gps,
    )
    assert len(completed) == 1
    assert completed[0][2]["landmark"] == "Home"
    assert completed[0][2]["country"] == "UnitedStates"
    assert call_count["n"] == 1  # country inferred once


def test_home_cluster_beyond_threshold_gets_normal_inference(monkeypatch):
    """Cluster >200m from home gets normal inference."""
    home_gps = (47.694, -122.101)
    # ~5km away
    cluster = M.LocationCluster(points=[
        M.MediaPoint(source_file="a.jpg", lat=47.74, lon=-122.101,
                     timestamp=datetime(2025, 7, 1, 10, 0)),
    ])
    pending = [(0, cluster)]
    def fake_infer(*a, **kw):
        return {"landmark": "SomePark", "country": "UnitedStates"}
    monkeypatch.setattr(M, "infer_landmark_info", fake_infer)
    completed, failure, report = M.infer_pending_cluster_infos(
        pending,
        opencode_timeout_sec=10,
        opencode_retries=1,
        opencode_backoff_sec=0.1,
        opencode_model=None,
        inference_workers=1,
        home_gps=home_gps,
    )
    assert completed[0][2]["landmark"] == "SomePark"


def test_home_country_inferred_once_for_multiple_clusters(monkeypatch):
    """Multiple home clusters: country inferred once, reused for all."""
    home_gps = (47.694, -122.101)
    c1 = M.LocationCluster(points=[
        M.MediaPoint(source_file="a.jpg", lat=47.694, lon=-122.101,
                     timestamp=datetime(2025, 7, 1, 10, 0)),
    ])
    c2 = M.LocationCluster(points=[
        M.MediaPoint(source_file="b.jpg", lat=47.6941, lon=-122.1008,
                     timestamp=datetime(2025, 7, 1, 11, 0)),
    ])
    pending = [(0, c1), (1, c2)]
    call_count = {"n": 0}
    def fake_infer(*a, **kw):
        call_count["n"] += 1
        return {"landmark": "X", "country": "UnitedStates"}
    monkeypatch.setattr(M, "infer_landmark_info", fake_infer)
    completed, failure, report = M.infer_pending_cluster_infos(
        pending,
        opencode_timeout_sec=10,
        opencode_retries=1,
        opencode_backoff_sec=0.1,
        opencode_model=None,
        inference_workers=1,
        home_gps=home_gps,
    )
    assert len(completed) == 2
    assert all(c[2]["landmark"] == "Home" for c in completed)
    assert all(c[2]["country"] == "UnitedStates" for c in completed)
    assert call_count["n"] == 1  # only one inference call for country


def test_home_landmark_in_folder_name():
    """Home landmark appears in the final folder name via rank_landmarks_by_location_set_size."""
    cluster_home = M.LocationCluster(points=[
        M.MediaPoint("a.jpg", 47.694, -122.101, datetime(2025, 7, 1, 10)),
    ])
    cluster_pike = M.LocationCluster(points=[
        M.MediaPoint("b.jpg", 47.6, -122.3, datetime(2025, 7, 1, 14)),
    ])
    cluster_infos = [
        (cluster_home, {"landmark": "Home", "country": "UnitedStates"}),
        (cluster_pike, {"landmark": "PikePlaceMarket", "country": "UnitedStates"}),
    ]
    landmarks = M.rank_landmarks_by_location_set_size(
        reference_clusters=cluster_infos,
        full_clusters=[cluster_home, cluster_pike],
        max_landmarks=8,
    )
    assert "Home" in landmarks
    assert "PikePlaceMarket" in landmarks
