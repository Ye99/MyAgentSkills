import json
import subprocess
import unittest
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rename_folder_by_ai_itinerary as mod


class RenameFolderByAiItineraryTests(unittest.TestCase):
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

    def test_parse_json_payload_accepts_wrapped_output(self) -> None:
        payload = mod.parse_json_payload('notes\n{"landmark_name":"Skogafoss"}\nmore')
        self.assertEqual(payload, {"landmark_name": "Skogafoss"})

    def test_infer_landmark_token_uses_opencode_and_normalizes(self) -> None:
        with patch("rename_folder_by_ai_itinerary.shutil.which", return_value="/usr/bin/opencode"):
            with patch(
                "rename_folder_by_ai_itinerary.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["opencode"],
                    returncode=0,
                    stdout='{"landmark_name":"Fjaðrárgljúfur canyon"}\n',
                    stderr="",
                ),
            ):
                token = mod.infer_landmark_token(63.7789, -18.1767)
        self.assertEqual(token, "FjarargljufurCanyon")

    def test_infer_landmark_token_returns_unknown_on_failure(self) -> None:
        with patch("rename_folder_by_ai_itinerary.shutil.which", return_value="/usr/bin/opencode"):
            with patch(
                "rename_folder_by_ai_itinerary.subprocess.run",
                return_value=subprocess.CompletedProcess(args=["opencode"], returncode=1, stdout="", stderr="boom"),
            ):
                token = mod.infer_landmark_token(64.048, -16.181)
        self.assertEqual(token, "UnknownLandmark")

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
                        stdout='{"landmark_name":"Skogafoss","country_name":"Iceland"}\n',
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
        self.assertEqual(info["country"], "Iceland")
        sleep_mock.assert_called_once_with(2.0)

    def test_build_itinerary_landmarks_keeps_order_and_dedupes(self) -> None:
        points = [
            mod.MediaPoint("a.jpg", 10.0, 10.0, datetime(2025, 7, 24, 9, 0, 0)),
            mod.MediaPoint("b.jpg", 10.0001, 10.0001, datetime(2025, 7, 24, 9, 5, 0)),
            mod.MediaPoint("c.jpg", 20.0, 20.0, datetime(2025, 7, 24, 12, 0, 0)),
            mod.MediaPoint("d.jpg", 10.0002, 10.0002, datetime(2025, 7, 24, 16, 0, 0)),
        ]

        def fake_infer(lat: float, lon: float, _: datetime | None, __: datetime | None, ___: int) -> str:
            if lat < 15:
                return "FirstSpot"
            return "SecondSpot"

        tokens = mod.build_itinerary_landmarks(points, infer_func=fake_infer, cluster_distance_m=1_000)

        self.assertEqual(tokens, ["FirstSpot", "SecondSpot"])

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

    def test_build_parser_defaults_cluster_distance_and_timeout(self) -> None:
        parser = mod.build_parser()
        args = parser.parse_args(["/tmp/2025_07_24"])

        self.assertEqual(args.ratio, 1.0)
        self.assertEqual(args.cluster_distance_m, 2_000.0)
        self.assertEqual(args.opencode_timeout_sec, 180)
        self.assertEqual(args.opencode_max_attempts, 5)
        self.assertEqual(args.opencode_initial_backoff_sec, 3.0)
        self.assertEqual(args.max_landmarks, 8)
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

    def test_group_clusters_by_country_keeps_first_seen_country_order(self) -> None:
        cluster_a = mod.LocationCluster(
            points=[mod.MediaPoint("a.jpg", 10.0, 10.0, datetime(2025, 7, 23, 9, 0, 0))]
        )
        cluster_b = mod.LocationCluster(
            points=[mod.MediaPoint("b.jpg", 20.0, 20.0, datetime(2025, 7, 23, 10, 0, 0))]
        )
        cluster_c = mod.LocationCluster(
            points=[mod.MediaPoint("c.jpg", 11.0, 11.0, datetime(2025, 7, 23, 11, 0, 0))]
        )

        grouped = mod.group_clusters_by_country(
            [
                (cluster_a, {"landmark": "A", "country": "China"}),
                (cluster_b, {"landmark": "B", "country": "Iceland"}),
                (cluster_c, {"landmark": "C", "country": "China"}),
            ]
        )

        self.assertEqual([entry["country"] for entry in grouped], ["China", "Iceland"])

    def test_rename_folder_returns_multiple_targets_for_multi_country_day(self) -> None:
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

        self.assertEqual(result["status"], "planned-split")
        split_folders = cast(list[dict[str, object]], result["split_folders"])
        split_targets = [str(entry["target_name"]) for entry in split_folders]
        self.assertEqual(
            split_targets,
            [
                "2025_07_23_MountLaoshan,QingdaoOlympicSailingCenter",
                "2025_07_23_Berufjordur,Vatnajokull",
            ],
        )

    def test_unknown_country_does_not_force_split_when_only_one_known_country(self) -> None:
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
                ):
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

            self.assertEqual(second_result["status"], "planned-split")
            self.assertEqual(infer_mock.call_count, 1)

            final_state = mod.read_json_file(mod.default_state_file(day))
            self.assertIsInstance(final_state, dict)
            final_state_dict = cast(dict[str, object], final_state)
            self.assertEqual(final_state_dict["persistent_failure_count"], 1)

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

    def test_rename_folder_split_reports_leftover_non_gps_files(self) -> None:
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

        self.assertEqual(result["status"], "planned-split")
        self.assertEqual(result["leftover_media_count"], 1)
        self.assertEqual(result["leftover_media_examples"], [str(day / "nogps.jpg")])

    def test_split_mode_skips_unknown_landmark_placeholder(self) -> None:
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

        self.assertEqual(result["status"], "planned-split")
        split_folders = cast(list[dict[str, object]], result["split_folders"])
        self.assertEqual([str(entry["target_name"]) for entry in split_folders], ["2025_07_23", "2025_07_23_2"])

    def test_split_apply_does_not_move_sources_outside_day_folder(self) -> None:
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

            self.assertEqual(result["status"], "split-renamed")
            self.assertTrue(outside_file.exists())
            self.assertGreaterEqual(cast(int, result["invalid_source_media_count"]), 1)

    def test_split_apply_returns_failed_apply_when_move_raises(self) -> None:
        with TemporaryDirectory() as tmpdir:
            day = Path(tmpdir) / "2025_07_23"
            day.mkdir()
            inside_a = day / "inside_a.jpg"
            inside_b = day / "inside_b.jpg"
            inside_a.write_bytes(b"x")
            inside_b.write_bytes(b"y")

            points = [
                mod.MediaPoint(str(inside_a), 36.0670, 120.3150, datetime(2025, 7, 23, 9, 0, 0)),
                mod.MediaPoint(str(inside_b), 64.2500, -15.2040, datetime(2025, 7, 23, 10, 0, 0)),
            ]

            with patch("rename_folder_by_ai_itinerary.extract_media_points", return_value=(points, [])):
                with patch(
                    "rename_folder_by_ai_itinerary.infer_landmark_info",
                    side_effect=[
                        {"landmark": "MayFourthSquare", "country": "China"},
                        {"landmark": "Jokulsarlon", "country": "Iceland"},
                    ],
                ):
                    with patch(
                        "rename_folder_by_ai_itinerary._safe_move_media_file",
                        side_effect=OSError("disk full"),
                    ):
                        result = mod.rename_folder_from_itinerary(day, apply=True, ratio=1.0)

            report = mod.read_json_file(mod.default_report_file(day))

        self.assertEqual(result["status"], "failed-apply")
        self.assertIsInstance(report, dict)
        self.assertEqual(cast(dict[str, object], report)["status"], "failed-apply")

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
        self.assertIsInstance(report, dict)
        self.assertEqual(cast(dict[str, object], report)["status"], "failed-rename")

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


if __name__ == "__main__":
    unittest.main()
