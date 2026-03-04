import unittest
import json
import io
from email.message import Message
from datetime import datetime
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock
import subprocess
import os
from tempfile import TemporaryDirectory
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rename_folder_with_poi_itinerary as mod


class RenameFolderWithPoiItineraryTests(unittest.TestCase):
    def test_is_supported_date_folder_path(self) -> None:
        self.assertTrue(mod.is_supported_date_folder_path(Path("/tmp/2025/2025_07_02")))
        self.assertFalse(mod.is_supported_date_folder_path(Path("/tmp/2025/2025_07_02_Seattle")))
        self.assertTrue(mod.is_supported_date_folder_path(Path("/tmp/trips/2025_07_02")))

    def test_discover_day_folders_classifies_eligible_and_already_named(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "2025" / "2025_07_01").mkdir(parents=True)
            (root / "2025" / "2025_07_02_Seattle").mkdir(parents=True)
            (root / "misc" / "2025_07_03").mkdir(parents=True)

            discovered = mod.discover_day_folders(root)

        by_name = {Path(entry["folder_path"]).name: entry["status"] for entry in discovered}
        self.assertEqual(by_name["2025_07_01"], "eligible-date-folder")
        self.assertEqual(by_name["2025_07_02_Seattle"], "already-landmark-named")
        self.assertEqual(by_name["2025_07_03"], "eligible-date-folder")

    def test_build_rename_report_contains_required_counts_and_paths(self) -> None:
        report = mod.build_rename_report(
            root_path=Path("/tmp/root"),
            apply_mode=False,
            folder_results=[
                {"folder_path": "/tmp/root/2025_07_01", "status": "planned-rename"},
                {"folder_path": "/tmp/root/2025_07_02", "status": "renamed"},
                {"folder_path": "/tmp/root/2025_07_03_Seattle", "status": "skipped-already-landmark-named"},
                {"folder_path": "/tmp/root/2025_07_04", "status": "skipped-no-landmark-name-proposed"},
                {"folder_path": "/tmp/root/2025_07_05", "status": "skipped-no-gps-media"},
                {"folder_path": "/tmp/root/2025_07_06", "status": "error"},
            ],
            discovered_folders=[
                {"folder_path": "/tmp/root/2025_07_01", "status": "eligible-date-folder"},
                {"folder_path": "/tmp/root/2025_07_02", "status": "eligible-date-folder"},
                {"folder_path": "/tmp/root/2025_07_03_Seattle", "status": "already-landmark-named"},
                {"folder_path": "/tmp/root/2025_07_04", "status": "eligible-date-folder"},
            ],
            started_at=datetime(2026, 3, 3, 12, 0, 0),
            finished_at=datetime(2026, 3, 3, 12, 0, 5),
            run_stats={
                "processed_this_run_count": 2,
                "skipped_frozen_applied_count": 1,
                "retried_error_count": 1,
                "retried_no_landmark_count": 1,
            },
        )

        self.assertEqual(report["summary"]["renamed_count"], 1)
        self.assertEqual(report["summary"]["already_landmark_named_count"], 1)
        self.assertEqual(report["summary"]["no_landmark_name_proposed_count"], 1)
        self.assertEqual(report["summary"]["planned_rename_count"], 1)
        self.assertEqual(report["summary"]["rename_failed_count"], 1)
        self.assertEqual(report["summary"]["no_gps_media_count"], 1)
        self.assertEqual(report["summary"]["processed_this_run_count"], 2)
        self.assertEqual(report["summary"]["skipped_frozen_applied_count"], 1)
        self.assertEqual(report["summary"]["retried_error_count"], 1)
        self.assertEqual(report["summary"]["retried_no_landmark_count"], 1)
        self.assertEqual(
            report["no_landmark_name_proposed_paths"],
            ["/tmp/root/2025_07_04"],
        )

    def test_resume_state_round_trip(self) -> None:
        with TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            state = {
                "version": 1,
                "folders": {
                    "/tmp/root/2025_07_02": {
                        "latest_status": "renamed",
                        "attempt_count": 1,
                    }
                },
            }
            mod.save_resume_state(state_path, state)
            loaded = mod.load_resume_state(state_path)

        self.assertEqual(loaded, state)

    def test_should_process_folder_honors_freeze_and_retry_caps(self) -> None:
        retry_cfg = {"error_retry_max": 2, "no_landmark_retry_max": 1}
        self.assertFalse(
            mod.should_process_folder(
                {
                    "latest_status": "renamed",
                    "error_attempt_count": 0,
                    "no_landmark_attempt_count": 0,
                },
                retry_cfg,
            )
        )
        self.assertTrue(
            mod.should_process_folder(
                {
                    "latest_status": "error",
                    "error_attempt_count": 2,
                    "no_landmark_attempt_count": 0,
                },
                retry_cfg,
            )
        )
        self.assertFalse(
            mod.should_process_folder(
                {
                    "latest_status": "error",
                    "error_attempt_count": 3,
                    "no_landmark_attempt_count": 0,
                },
                retry_cfg,
            )
        )
        self.assertTrue(
            mod.should_process_folder(
                {
                    "latest_status": "skipped-no-landmark-name-proposed",
                    "error_attempt_count": 0,
                    "no_landmark_attempt_count": 1,
                },
                retry_cfg,
            )
        )
        self.assertFalse(
            mod.should_process_folder(
                {
                    "latest_status": "skipped-no-landmark-name-proposed",
                    "error_attempt_count": 0,
                    "no_landmark_attempt_count": 2,
                },
                retry_cfg,
            )
        )

    def test_shutdown_controller_first_and_second_signal(self) -> None:
        controller = mod.ShutdownController()
        self.assertFalse(controller.request_shutdown("signal"))
        self.assertTrue(controller.shutdown_requested)
        self.assertFalse(controller.force_exit)
        self.assertTrue(controller.request_shutdown("signal"))
        self.assertTrue(controller.force_exit)

    def test_coverage_invariant_detects_missed_folder(self) -> None:
        discovered_ids = {"/tmp/root/2025_07_20", "/tmp/root/2025_07_21"}
        folder_results = [{"folder_id": "/tmp/root/2025_07_20", "status": "renamed"}]
        check = mod.compute_coverage_check(discovered_ids, folder_results, pending_folder_ids=[])
        self.assertTrue(check["coverage_check_failed"])
        self.assertEqual(check["missing_folder_ids"], ["/tmp/root/2025_07_21"])

    def test_main_graceful_interrupt_stops_before_next_folder(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "2025_07_02").mkdir()
            (root / "2025_07_03").mkdir()
            state_path = root / "state.json"
            report_path = root / "report.json"

            call_count = {"n": 0}

            def fake_process(folder: Path, args, api_cache):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    assert mod._ACTIVE_SHUTDOWN_CONTROLLER is not None
                    mod._ACTIVE_SHUTDOWN_CONTROLLER.request_shutdown("signal")
                return {
                    "folder_path": str(folder),
                    "status": "planned-rename",
                    "target_name": folder.name + "_X",
                }

            argv = [
                "rename_folder_with_poi_itinerary.py",
                str(root),
                "--key",
                "k",
                "--state-json",
                str(state_path),
                "--report-json",
                str(report_path),
            ]
            with patch.object(sys, "argv", argv):
                with patch("rename_folder_with_poi_itinerary.process_single_folder", side_effect=fake_process):
                    exit_code = mod.main()

            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 130)
            self.assertEqual(call_count["n"], 1)
            self.assertTrue(payload["interrupted"])
            self.assertEqual(payload["interrupt_source"], "signal")
            self.assertEqual(payload["pending_folder_ids"], [str(root / "2025_07_03")])

    def test_process_single_folder_marks_no_landmark_when_target_unchanged(self) -> None:
        folder = Path("/tmp/2025_07_04")
        args = mod.build_parser().parse_args([str(folder), "--key", "k"])
        point = mod.MediaPoint("a.jpg", 1.0, 2.0, datetime(2025, 7, 4, 9, 0, 0))

        with patch("rename_folder_with_poi_itinerary.extract_media_points", return_value=[point]):
            with patch("rename_folder_with_poi_itinerary.sample_points", return_value=[point]):
                with patch("rename_folder_with_poi_itinerary.cluster_points", return_value=[mod.LocationSet(points=[point], label="X")]):
                    with patch("rename_folder_with_poi_itinerary._assign_labels"):
                        with patch("rename_folder_with_poi_itinerary.finalize_landmark_names", return_value=[]):
                            result = mod.process_single_folder(folder, args, api_cache=None)

        self.assertEqual(result["status"], "skipped-no-landmark-name-proposed")
        self.assertEqual(result["target_name"], "2025_07_04")

    def test_main_recursive_writes_json_report(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "root"
            already_named = root / "Trips" / "2025_07_04_Seattle"
            already_named.mkdir(parents=True)
            report_path = root / "report.json"

            argv = [
                "rename_folder_with_poi_itinerary.py",
                str(root),
                "--report-json",
                str(report_path),
            ]
            with patch.object(sys, "argv", argv):
                exit_code = mod.main()

            self.assertEqual(exit_code, 0)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["already_landmark_named_count"], 1)
            self.assertEqual(payload["summary"]["renamed_count"], 0)
            self.assertEqual(payload["summary"]["no_landmark_name_proposed_count"], 0)

    def test_max_folder_name_length_is_120(self) -> None:
        self.assertEqual(mod.MAX_FOLDER_NAME_LEN, 120)

    def test_choose_preferred_label_prioritizes_landmark(self) -> None:
        results = [
            {
                "class": "place",
                "type": "city",
                "address": {"city": "Seattle", "road": "Pine Street"},
            },
            {
                "class": "tourism",
                "type": "attraction",
                "name": "Space Needle",
                "address": {"city": "Seattle"},
            },
        ]

        self.assertEqual(mod.choose_preferred_label(results), "Space Needle")

    def test_choose_preferred_label_falls_back_city_only(self) -> None:
        city_results = [
            {
                "class": "amenity",
                "type": "restaurant",
                "address": {"city": "Portland", "road": "NW 23rd Ave"},
            }
        ]
        street_results = [
            {
                "class": "amenity",
                "type": "restaurant",
                "address": {"road": "NE Broadway"},
            }
        ]

        self.assertEqual(mod.choose_preferred_label(city_results), "Portland")
        self.assertIsNone(mod.choose_preferred_label(street_results))

    def test_choose_nominatim_label_prefers_name(self) -> None:
        payload = {
            "name": "Svartifoss",
            "display_name": "Svartifoss, Sveitarfelagid Hornafjordur, Southern Region, Iceland",
            "address": {"tourism": "Svartifoss", "county": "Sveitarfelagid Hornafjordur"},
        }
        self.assertEqual(mod.choose_nominatim_label(payload), "Svartifoss")

    def test_choose_nominatim_label_falls_back_display_name_segment(self) -> None:
        payload = {
            "display_name": "Skeidararsandur, Sveitarfelagid Hornafjordur, Southern Region, Iceland",
            "address": {"county": "Sveitarfelagid Hornafjordur"},
        }
        self.assertEqual(mod.choose_nominatim_label(payload), "Skeidararsandur")

    def test_choose_nominatim_label_rejects_timezone_boundary(self) -> None:
        payload = {
            "name": "Alaska - Timezone America/Yakutat",
            "category": "boundary",
            "type": "timezone",
            "display_name": "Alaska - Timezone America/Yakutat, United States",
        }
        self.assertIsNone(mod.choose_nominatim_label(payload))

    def test_choose_nominatim_label_keeps_composite_protected_area_for_fragmenting(self) -> None:
        payload = {
            "name": "Kluane / Wrangell-St. Elias / Glacier Bay / Tatshenshini-Alsek",
            "category": "boundary",
            "type": "protected_area",
            "display_name": "Kluane / Wrangell-St. Elias / Glacier Bay / Tatshenshini-Alsek, Canada",
        }
        self.assertEqual(
            mod.choose_nominatim_label(payload),
            "Kluane / Wrangell-St. Elias / Glacier Bay / Tatshenshini-Alsek",
        )

    def test_dedupe_labels_preserves_first_occurrence(self) -> None:
        labels = ["CITYX", "CITYX", "Landmark Y", "cityx", "Landmark Y"]
        self.assertEqual(mod.dedupe_labels(labels), ["Cityx", "LandmarkY"])

    def test_dedupe_labels_excludes_unknown_location(self) -> None:
        labels = ["UNKNOWN_LOCATION", "Space Needle", "unknown location", "Seattle"]
        self.assertEqual(mod.dedupe_labels(labels), ["SpaceNeedle", "Seattle"])

    def test_dedupe_labels_preserves_readable_camel_case(self) -> None:
        labels = ["SummitLakesViewpoint", "KlondikeGoldDredge"]
        self.assertEqual(mod.dedupe_labels(labels), ["SummitLakesViewpoint", "KlondikeGoldDredge"])

    def test_dedupe_labels_excludes_low_signal_labels(self) -> None:
        labels = ["Vancouver", "Monument 5E-92", "Zaozhuang"]
        self.assertEqual(mod.dedupe_labels(labels), ["Vancouver", "Zaozhuang"])

    def test_dedupe_labels_excludes_timezone_noise(self) -> None:
        labels = ["AsiaShanghaiTimezone", "Svartifoss"]
        self.assertEqual(mod.dedupe_labels(labels), ["Svartifoss"])

    def test_cluster_points_geo_first(self) -> None:
        points = [
            mod.MediaPoint("a.jpg", 47.0, -122.0, datetime(2024, 9, 18, 9, 0, 0)),
            mod.MediaPoint("b.jpg", 47.0008, -122.0, datetime(2024, 9, 18, 9, 30, 0)),
            mod.MediaPoint("c.jpg", 47.01, -122.0, datetime(2024, 9, 18, 11, 0, 0)),
        ]

        clusters = mod.cluster_points(points, threshold_m=200.0)

        self.assertEqual(len(clusters), 2)
        self.assertEqual({p.source_file for p in clusters[0].points}, {"a.jpg", "b.jpg"})
        self.assertEqual({p.source_file for p in clusters[1].points}, {"c.jpg"})

    def test_labels_follow_itinerary_order(self) -> None:
        cluster_late = mod.LocationSet(
            points=[mod.MediaPoint("late.jpg", 47.0, -122.0, datetime(2024, 9, 18, 15, 0, 0))],
            label="CityLate",
        )
        cluster_early = mod.LocationSet(
            points=[mod.MediaPoint("early.jpg", 47.0, -122.0, datetime(2024, 9, 18, 8, 0, 0))],
            label="LandmarkEarly",
        )

        ordered = mod.labels_in_itinerary_order([cluster_late, cluster_early])

        self.assertEqual(ordered, ["LandmarkEarly", "CityLate"])

    def test_build_target_name_appends_labels(self) -> None:
        self.assertEqual(
            mod.build_target_name("2024_09_18", ["Space Needle", "Seattle"]),
            "2024_09_18_SpaceNeedle,Seattle",
        )

    def test_build_target_name_compacts_when_over_limit(self) -> None:
        labels = [
            "Alaska Airlines Customer Service",
            "Anchorage",
            "Portage Glacier Cruise",
            "Cliffside Marina",
            "Begich Boggs Visitor Center",
            "Alaska Wildlife Conservation Center",
        ]
        with patch("rename_folder_with_poi_itinerary.MAX_FOLDER_NAME_LEN", 60):
            with patch("rename_folder_with_poi_itinerary.find_available_local_agent", return_value=None):
                result = mod.build_target_name("2025_07_02", labels)
        self.assertGreater(len(result), 60)

    def test_build_target_name_uses_local_agent_compaction(self) -> None:
        labels = [
            "AlaskaAirlinesCustomerService",
            "Anchorage",
            "Zaozhuang",
            "PortageGlacierCruise",
        ]
        with patch("rename_folder_with_poi_itinerary.MAX_FOLDER_NAME_LEN", 35):
            with patch("rename_folder_with_poi_itinerary.find_available_local_agent", return_value="codex"):
                with patch(
                    "rename_folder_with_poi_itinerary.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        args=["codex"],
                        returncode=0,
                        stdout="2025_07_02_AlaskaAirlines,Anchorage\n",
                        stderr="",
                    ),
                ):
                    result = mod.build_target_name("2025_07_02", labels, use_local_agent_compaction=True)
        self.assertEqual(result, "2025_07_02_AlaskaAirlines,Anchorage")

    def test_build_target_name_does_not_use_local_agent_by_default(self) -> None:
        labels = [
            "AlaskaAirlinesCustomerService",
            "Anchorage",
            "Zaozhuang",
            "PortageGlacierCruise",
        ]
        with patch("rename_folder_with_poi_itinerary.MAX_FOLDER_NAME_LEN", 35):
            with patch("rename_folder_with_poi_itinerary.find_available_local_agent", return_value="codex"):
                with patch("rename_folder_with_poi_itinerary.subprocess.run") as run_mock:
                    result = mod.build_target_name("2025_07_02", labels)
        run_mock.assert_not_called()
        self.assertEqual(result, "2025_07_02_AlaskaAirlinesCustomerService,Anchorage,Zaozhuang,PortageGlacierCruise")

    def test_build_target_name_uses_comma_separator(self) -> None:
        result = mod.build_target_name("2025_07_31", ["Nuuk Fitness", "Wall Street"])
        self.assertEqual(result, "2025_07_31_NuukFitness,WallStreet")

    def test_extract_base_date_name_from_already_renamed_folder(self) -> None:
        self.assertEqual(mod.extract_base_date_name("2025_08_21_RedmondPool"), "2025_08_21")
        self.assertEqual(mod.extract_base_date_name("2025_08_21"), "2025_08_21")
        self.assertEqual(mod.extract_base_date_name("VacationPhotos"), "VacationPhotos")

    def test_build_parser_defaults_for_max_landmark_names_and_opencode_timeout(self) -> None:
        parser = mod.build_parser()
        args = parser.parse_args(["/tmp/2025/2025_07_02"])
        self.assertEqual(args.max_landmark_names, 8)
        self.assertEqual(args.opencode_timeout_sec, 60)
        self.assertEqual(args.event_distance_m, 2000.0)
        self.assertEqual(args.opencode_model, os.getenv("OPENCODE_MODEL"))
        self.assertEqual(args.nominatim_zoom, 18)
        self.assertEqual(args.nominatim_layer, "poi,natural,manmade")
        self.assertEqual(args.nominatim_requests_per_second, 1.0)
        self.assertEqual(args.report_json, "folder_poi_itinerary_rename_report.json")
        self.assertEqual(args.state_json, "folder_poi_itinerary_rename_state.json")
        self.assertEqual(args.error_retry_max, 2)
        self.assertEqual(args.no_landmark_retry_max, 1)
        self.assertTrue(args.cache_file.endswith("/folder-poi-itinerary-rename/scripts/cache/geo_api_cache.json"))
        self.assertNotIn("/.cache/", args.cache_file)

    def test_build_parser_rejects_legacy_max_tags_flag(self) -> None:
        parser = mod.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["/tmp/2025/2025_07_02", "--max-tags", "5"])

    def test_build_parser_rejects_removed_source_flags(self) -> None:
        parser = mod.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["/tmp/2025/2025_07_02", "--use-nominatim-reverse"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["/tmp/2025/2025_07_02", "--use-dual-source"])

    def test_nominatim_user_agent_name(self) -> None:
        self.assertEqual(mod.APP_USER_AGENT, "Lookup_POI_withlocalcache")

    def test_local_api_cache_round_trip_keeps_full_payload(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cache = mod.LocalApiCache(Path(tmpdir) / "api_cache.json")
            payload = {
                "raw": [{"name": "A", "meta": {"nested": [1, 2, 3]}}, {"name": "B"}],
                "extra": {"k": "v"},
            }
            cache.set("locationiq", {"lat": "1.0", "lon": "2.0"}, payload)
            loaded = cache.get("locationiq", {"lat": "1.0", "lon": "2.0"})
        self.assertEqual(loaded, payload)

    def test_fetch_locationiq_uses_cache_on_second_call(self) -> None:
        class FakeResponse:
            def __init__(self, body: str) -> None:
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return self._body.encode("utf-8")

        with TemporaryDirectory() as tmpdir:
            cache = mod.LocalApiCache(Path(tmpdir) / "api_cache.json")
            with patch("rename_folder_with_poi_itinerary.urlopen", return_value=FakeResponse('[{"name":"A"}]')) as open_mock:
                first = mod.fetch_nearby_poi(
                    api_key="k",
                    lat=1.0,
                    lon=2.0,
                    landmark_filter="all",
                    radius=1000,
                    region="us1",
                    api_cache=cache,
                )
            with patch("rename_folder_with_poi_itinerary.urlopen") as open_mock_second:
                second = mod.fetch_nearby_poi(
                    api_key="k",
                    lat=1.0,
                    lon=2.0,
                    landmark_filter="all",
                    radius=1000,
                    region="us1",
                    api_cache=cache,
                )
            self.assertEqual(open_mock.call_count, 1)
            open_mock_second.assert_not_called()
            self.assertEqual(first, second)

    def test_fetch_locationiq_does_not_use_custom_user_agent(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b"[]"

        with patch("rename_folder_with_poi_itinerary.Request") as request_mock:
            with patch("rename_folder_with_poi_itinerary.urlopen", return_value=FakeResponse()):
                mod.fetch_nearby_poi(
                    api_key="k",
                    lat=1.0,
                    lon=2.0,
                    landmark_filter="all",
                    radius=1000,
                    region="us1",
                )
        request_mock.assert_not_called()

    def test_build_parser_reads_opencode_model_env(self) -> None:
        with patch.dict("os.environ", {"OPENCODE_MODEL": "openai/gpt-4o-mini"}, clear=False):
            parser = mod.build_parser()
            args = parser.parse_args(["/tmp/2025/2025_07_02"])
        self.assertEqual(args.opencode_model, "openai/gpt-4o-mini")

    def test_build_parser_uses_landmark_filter_argument(self) -> None:
        parser = mod.build_parser()
        args = parser.parse_args(["/tmp/2025/2025_07_02", "--landmark-filter", "tourism"])
        self.assertEqual(args.landmark_filter, "tourism")

    def test_consolidate_itinerary_labels_uses_opencode_response(self) -> None:
        labels = ["StatueOfLiberty", "NewYork", "WallStreet"]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["opencode"],
                returncode=0,
                stdout='{"final_landmark_names":["StatueOfLiberty","WallStreet"]}\n',
                stderr="",
            ),
        ):
            selected = mod.consolidate_itinerary_labels(
                labels,
                max_landmark_names=8,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
            )
        self.assertEqual(selected, ["StatueOfLiberty", "WallStreet"])

    def test_consolidate_itinerary_labels_falls_back_when_opencode_fails(self) -> None:
        labels = ["StatueOfLiberty", "NewYork", "WallStreet"]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["opencode"], returncode=1, stdout="", stderr="boom"),
        ):
            selected = mod.consolidate_itinerary_labels(
                labels,
                max_landmark_names=2,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
            )
        self.assertEqual(selected, ["StatueOfLiberty", "NewYork"])

    def test_consolidate_itinerary_labels_accepts_landmark_name_keys(self) -> None:
        labels = ["StatueOfLiberty", "NewYork", "WallStreet"]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["opencode"],
                returncode=0,
                stdout='{"final_landmark_name":"StatueOfLiberty","final_landmark_names":["WallStreet"]}\n',
                stderr="",
            ),
        ):
            selected = mod.consolidate_itinerary_labels(
                labels,
                max_landmark_names=8,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
            )
        self.assertEqual(selected, ["StatueOfLiberty", "WallStreet"])

    def test_consolidate_itinerary_labels_prompt_mentions_cross_region_segments(self) -> None:
        labels = ["Zaozhuang", "McHughCreekDayUseArea", "PortageGlacierCruise"]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["opencode"],
                returncode=0,
                stdout='{"final_landmark_names":["Zaozhuang","McHughCreekDayUseArea"]}\n',
                stderr="",
            ),
        ) as run_mock:
            mod.consolidate_itinerary_labels(
                labels,
                max_landmark_names=8,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
            )

        prompt = run_mock.call_args[0][0][-1]
        self.assertIn("different countries or distant regions", prompt)
        self.assertIn("do not drop one as redundant", prompt)
        self.assertIn("Statue of Liberty Information Center", prompt)
        self.assertIn("Information Center", prompt)

    def test_select_top_candidates_returns_both_specific_and_generic(self) -> None:
        candidates = [
            {
                "label": "Statue of Liberty Information Center",
                "source": "locationiq",
                "category": "tourism",
                "type": "information",
                "importance_raw": None,
                "place_rank_raw": None,
                "distance_m": 20.0,
            },
            {
                "label": "Information Center",
                "source": "locationiq",
                "category": "tourism",
                "type": "information",
                "importance_raw": None,
                "place_rank_raw": None,
                "distance_m": 10.0,
            },
        ]
        top = mod.select_top_candidates(candidates, top_n=3)
        top_labels = [c["label"] for c in top]
        self.assertIn("Statue of Liberty Information Center", top_labels)
        self.assertIn("Information Center", top_labels)

    def test_consolidate_itinerary_labels_rejects_aggressive_drops(self) -> None:
        labels = ["StatueOfLiberty", "WallStreet", "BatteryPark", "EllisIsland"]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["opencode"],
                returncode=0,
                stdout='{"final_landmark_names":["StatueOfLiberty"]}\n',
                stderr="",
            ),
        ):
            selected = mod.consolidate_itinerary_labels(
                labels,
                max_landmark_names=8,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
            )
        self.assertEqual(selected, labels)

    def test_choose_best_label_from_candidates_avoids_non_ascii_only_pick(self) -> None:
        candidates = [
            {
                "label": "枣庄市立第二医院",
                "source": "locationiq",
                "category": "amenity",
                "type": "hospital",
                "importance_raw": None,
                "place_rank_raw": None,
                "distance_m": 10.0,
            },
            {
                "label": "Zaozhuang",
                "source": "locationiq",
                "category": "place",
                "type": "city",
                "importance_raw": None,
                "place_rank_raw": None,
                "distance_m": 20.0,
            },
        ]
        selected = mod.choose_best_label_from_candidates(candidates)
        self.assertEqual(selected, "Zaozhuang")

    def test_finalize_landmark_names_uses_all_location_sets_before_second_pass(self) -> None:
        sets = [
            mod.LocationSet(points=[mod.MediaPoint("a.jpg", 0.0, 0.0, datetime(2025, 7, 1, 9, 0, 0))], label="A"),
            mod.LocationSet(points=[mod.MediaPoint("b.jpg", 0.0, 0.0, datetime(2025, 7, 1, 10, 0, 0))], label="B"),
            mod.LocationSet(points=[mod.MediaPoint("c.jpg", 0.0, 0.0, datetime(2025, 7, 1, 11, 0, 0))], label="C"),
        ]
        with patch(
            "rename_folder_with_poi_itinerary.consolidate_itinerary_landmark_names",
            return_value=["A", "C"],
        ) as consolidate_mock:
            selected = mod.finalize_landmark_names(
                sets,
                max_landmark_names=2,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
            )

        consolidate_mock.assert_called_once_with(
            ["A", "B", "C"],
            max_landmark_names=2,
            opencode_timeout_sec=60,
            opencode_model="openai/gpt-4o-mini",
            location_set_members=[
                {
                    "landmark_name": "A",
                    "set_member_count": 1,
                    "itinerary_order": 1,
                },
                {
                    "landmark_name": "B",
                    "set_member_count": 1,
                    "itinerary_order": 2,
                },
                {
                    "landmark_name": "C",
                    "set_member_count": 1,
                    "itinerary_order": 3,
                },
            ],
        )
        self.assertEqual(selected, ["A", "C"])

    def test_consolidate_itinerary_labels_prompt_includes_member_metadata(self) -> None:
        labels = ["A", "B", "C"]
        members = [
            {"landmark_name": "A", "set_member_count": 20, "itinerary_order": 1},
            {"landmark_name": "B", "set_member_count": 5, "itinerary_order": 2},
            {"landmark_name": "C", "set_member_count": 3, "itinerary_order": 3},
        ]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["opencode"],
                returncode=0,
                stdout='{"final_landmark_names":["A","B"]}\n',
                stderr="",
            ),
        ) as run_mock:
            mod.consolidate_itinerary_labels(
                labels,
                max_landmark_names=2,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
                location_set_members=members,
            )

        prompt = run_mock.call_args[0][0][-1]
        self.assertIn("Location set members metadata", prompt)
        self.assertIn("trim from the smallest location sets first", prompt)
        self.assertIn('"set_member_count": 3', prompt)

    def test_consolidate_itinerary_labels_fallback_preserves_itinerary_order_when_opencode_fails(self) -> None:
        labels = ["A", "B", "C"]
        members = [
            {"landmark_name": "A", "set_member_count": 1, "itinerary_order": 1},
            {"landmark_name": "B", "set_member_count": 5, "itinerary_order": 2},
            {"landmark_name": "C", "set_member_count": 3, "itinerary_order": 3},
        ]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["opencode"], returncode=1, stdout="", stderr="boom"),
        ):
            selected = mod.consolidate_itinerary_labels(
                labels,
                max_landmark_names=2,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
                location_set_members=members,
            )

        self.assertEqual(selected, ["A", "B"])

    def test_consolidate_itinerary_labels_ignores_runner_up_from_unknown_location_set(self) -> None:
        labels = ["UNKNOWN_LOCATION", "SeaBusTerminal", "UsBorderCrossing"]
        members = [
            {
                "landmark_name": "UnknownLocation",
                "set_member_count": 1,
                "itinerary_order": 1,
                "runner_up_candidates": ["Islands Trust Area"],
            },
            {"landmark_name": "SeaBusTerminal", "set_member_count": 9, "itinerary_order": 2},
            {"landmark_name": "UsBorderCrossing", "set_member_count": 1, "itinerary_order": 3},
        ]
        with patch("rename_folder_with_poi_itinerary.shutil.which", return_value="/usr/bin/opencode"):
            with patch(
                "rename_folder_with_poi_itinerary.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["opencode"],
                    returncode=0,
                    stdout='{"final_landmark_names":["SeaBusTerminal","UsBorderCrossing","IslandsTrustArea"]}\n',
                    stderr="",
                ),
            ):
                selected = mod.consolidate_itinerary_labels(
                    labels,
                    max_landmark_names=8,
                    opencode_timeout_sec=60,
                    opencode_model="openai/gpt-4o-mini",
                    location_set_members=members,
                )

        self.assertEqual(selected, ["SeaBusTerminal", "UsBorderCrossing"])

    def test_consolidate_itinerary_labels_keeps_specific_primary_over_runner_up(self) -> None:
        labels = ["TjaldsviEyjafjararsveitar", "Nesti"]
        members = [
            {
                "landmark_name": "TjaldsviEyjafjararsveitar",
                "set_member_count": 12,
                "itinerary_order": 1,
                "primary_candidate_count": 2,
                "runner_up_candidates": ["IrottamistoEyjafjararsveitar"],
            },
            {"landmark_name": "Nesti", "set_member_count": 5, "itinerary_order": 2},
        ]
        with patch("rename_folder_with_poi_itinerary.shutil.which", return_value="/usr/bin/opencode"):
            with patch(
                "rename_folder_with_poi_itinerary.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["opencode"],
                    returncode=0,
                    stdout='{"final_landmark_names":["IrottamistoEyjafjararsveitar","Nesti"]}\n',
                    stderr="",
                ),
            ):
                selected = mod.consolidate_itinerary_labels(
                    labels,
                    max_landmark_names=8,
                    opencode_timeout_sec=60,
                    opencode_model="openai/gpt-4o-mini",
                    location_set_members=members,
                )

        self.assertEqual(selected, ["TjaldsviEyjafjararsveitar", "Nesti"])

    def test_assign_labels_prefers_nominatim_as_dual_source_fallback(self) -> None:
        sets = [
            mod.LocationSet(
                points=[mod.MediaPoint("x.jpg", 64.027411, -16.975069, datetime(2025, 7, 10, 10, 0, 0))],
                label=None,
            )
        ]
        with patch("rename_folder_with_poi_itinerary.fetch_nominatim_reverse", return_value={"name": "Svartifoss"}):
            with patch("rename_folder_with_poi_itinerary.fetch_nearby_poi", return_value=[{"name": "NoisyPoi"}]):
                with patch("rename_folder_with_poi_itinerary.choose_best_label_from_candidates", return_value="Svartifoss"):
                    mod._assign_labels(
                        sets,
                        api_key="fake",
                        landmark_filter="all",
                        radius=1000,
                        region="us1",
                        locationiq_requests_per_second=1.0,
                        nominatim_zoom=18,
                        nominatim_layer="poi,natural,manmade",
                    )
        self.assertEqual(sets[0].label, "Svartifoss")

    def test_normalize_candidate_metrics_scales_fields(self) -> None:
        candidates = [
            {"label": "A", "source": "locationiq", "importance_raw": 0.1, "place_rank_raw": 10.0, "distance_m": 1000.0},
            {"label": "B", "source": "nominatim", "importance_raw": 0.9, "place_rank_raw": 30.0, "distance_m": 100.0},
        ]
        normalized = mod.normalize_candidate_metrics(candidates)
        self.assertEqual(normalized[0]["importance_norm"], 0.0)
        self.assertEqual(normalized[1]["importance_norm"], 1.0)
        self.assertEqual(normalized[0]["place_rank_norm"], 0.0)
        self.assertEqual(normalized[1]["place_rank_norm"], 1.0)
        self.assertEqual(normalized[0]["proximity_norm"], 0.0)
        self.assertEqual(normalized[1]["proximity_norm"], 1.0)

    def test_choose_best_label_from_candidates_uses_deterministic_scoring(self) -> None:
        candidates = [
            {
                "label": "Svartifoss",
                "source": "nominatim",
                "category": "natural",
                "type": "waterfall",
                "importance_raw": 0.8,
                "place_rank_raw": 22.0,
                "distance_m": 5.0,
            },
            {
                "label": "NearbyParking",
                "source": "locationiq",
                "category": "amenity",
                "type": "parking",
                "importance_raw": 0.2,
                "place_rank_raw": 30.0,
                "distance_m": 4.0,
            },
        ]
        label = mod.choose_best_label_from_candidates(candidates)
        self.assertEqual(label, "Svartifoss")

    def test_choose_best_label_from_candidates_single_generic_returns_none(self) -> None:
        candidates = [
            {
                "label": "Asia/Shanghai timezone",
                "source": "nominatim",
                "category": "boundary",
                "type": "timezone",
                "importance_raw": 0.06,
                "place_rank_raw": 25,
                "distance_m": 1000.0,
            }
        ]
        self.assertIsNone(mod.choose_best_label_from_candidates(candidates))

    def test_build_nominatim_candidates_splits_composite_for_ai_pick(self) -> None:
        payload = {
            "name": "Kluane / Wrangell-St. Elias / Glacier Bay / Tatshenshini-Alsek",
            "category": "boundary",
            "type": "protected_area",
            "importance": 0.06,
            "place_rank": 25,
            "lat": "58.4518666",
            "lon": "-136.0153948",
        }
        candidates = mod.build_nominatim_candidates(payload, 58.4518666, -136.0153948)
        labels = [candidate["label"] for candidate in candidates]
        self.assertEqual(labels, ["Kluane", "Wrangell-St. Elias", "Glacier Bay", "Tatshenshini-Alsek"])

    def test_assign_labels_uses_dual_source_parallel_path(self) -> None:
        sets = [
            mod.LocationSet(
                points=[mod.MediaPoint("x.jpg", 64.027411, -16.975069, datetime(2025, 7, 10, 10, 0, 0))],
                label=None,
            )
        ]
        with patch("rename_folder_with_poi_itinerary.fetch_nominatim_reverse", return_value={"name": "Svartifoss"}) as nom_mock:
            with patch(
                "rename_folder_with_poi_itinerary.fetch_nearby_poi",
                return_value=[{"name": "NoisyPoiA"}, {"name": "NoisyPoiB"}],
            ) as nearby_mock:
                with patch("rename_folder_with_poi_itinerary.choose_best_label_from_candidates", return_value="Svartifoss") as pick_mock:
                    mod._assign_labels(
                        sets,
                        api_key="fake",
                        landmark_filter="all",
                        radius=1000,
                        region="us1",
                        locationiq_requests_per_second=1.0,
                        nominatim_zoom=18,
                        nominatim_layer="poi,natural,manmade",
                    )
        nom_mock.assert_called_once()
        nearby_mock.assert_called_once()
        pick_mock.assert_called_once()
        passed_candidates = pick_mock.call_args.args[0]
        self.assertEqual(len(passed_candidates), 3)
        self.assertEqual(sets[0].label, "Svartifoss")

    def test_assign_labels_dual_source_does_not_mix_candidates_across_location_sets(self) -> None:
        sets = [
            mod.LocationSet(
                points=[mod.MediaPoint("x1.jpg", 64.0, -17.0, datetime(2025, 7, 10, 10, 0, 0))],
                label=None,
            ),
            mod.LocationSet(
                points=[mod.MediaPoint("x2.jpg", 65.0, -18.0, datetime(2025, 7, 10, 11, 0, 0))],
                label=None,
            ),
        ]

        with patch(
            "rename_folder_with_poi_itinerary.fetch_nominatim_reverse",
            side_effect=[{"name": "NomA"}, {"name": "NomB"}],
        ):
            with patch(
                "rename_folder_with_poi_itinerary.fetch_nearby_poi",
                side_effect=[[{"name": "LocA"}], [{"name": "LocB"}]],
            ):
                with patch(
                    "rename_folder_with_poi_itinerary.choose_best_label_from_candidates",
                    side_effect=["ChosenA", "ChosenB"],
                ) as pick_mock:
                    mod._assign_labels(
                        sets,
                        api_key="fake",
                        landmark_filter="all",
                        radius=1000,
                        region="us1",
                        locationiq_requests_per_second=1.0,
                        nominatim_zoom=18,
                        nominatim_layer="poi,natural,manmade",
                    )

        first_candidates = {entry["label"] for entry in pick_mock.call_args_list[0].args[0]}
        second_candidates = {entry["label"] for entry in pick_mock.call_args_list[1].args[0]}
        self.assertEqual(first_candidates, {"LocA", "NomA"})
        self.assertEqual(second_candidates, {"LocB", "NomB"})
        self.assertEqual(sets[0].label, "ChosenA")
        self.assertEqual(sets[1].label, "ChosenB")

    def test_assign_labels_dual_source_uses_two_service_tasks(self) -> None:
        sets = [
            mod.LocationSet(
                points=[mod.MediaPoint("x.jpg", 64.027411, -16.975069, datetime(2025, 7, 10, 10, 0, 0))],
                label=None,
            )
        ]

        nominatim_future = MagicMock()
        nominatim_future.result.return_value = {"name": "Svartifoss"}
        locationiq_future = MagicMock()
        locationiq_future.result.return_value = [{"name": "NoisyPoi"}]

        with patch("rename_folder_with_poi_itinerary.ThreadPoolExecutor") as executor_cls:
            executor = MagicMock()
            executor.submit.side_effect = [nominatim_future, locationiq_future]
            executor_cls.return_value.__enter__.return_value = executor
            with patch("rename_folder_with_poi_itinerary.choose_best_label_from_candidates", return_value="Svartifoss"):
                mod._assign_labels(
                    sets,
                    api_key="fake",
                    landmark_filter="all",
                    radius=1000,
                    region="us1",
                    locationiq_requests_per_second=1.0,
                    nominatim_zoom=18,
                    nominatim_layer="poi,natural,manmade",
                    nominatim_requests_per_second=1.0,
                )

        executor_cls.assert_called_once_with(max_workers=2)
        self.assertEqual(executor.submit.call_count, 2)

    def test_locationiq_rate_limiter_waits_between_calls(self) -> None:
        limiter = mod.LocationIQRateLimiter(1.0)
        with patch("rename_folder_with_poi_itinerary.time.monotonic", side_effect=[10.0, 10.1, 11.1]):
            with patch("rename_folder_with_poi_itinerary.time.sleep") as sleep_mock:
                limiter.wait_for_slot()
                limiter.wait_for_slot()
        sleep_mock.assert_called_once()

    def test_assign_labels_dual_source_handles_locationiq_404(self) -> None:
        sets = [
            mod.LocationSet(
                points=[mod.MediaPoint("x.jpg", 64.027411, -16.975069, datetime(2025, 7, 10, 10, 0, 0))],
                label=None,
            )
        ]
        with patch("rename_folder_with_poi_itinerary.fetch_nominatim_reverse", return_value={"name": "Svartifoss"}):
            with patch("rename_folder_with_poi_itinerary.fetch_nearby_poi", side_effect=RuntimeError("LocationIQ HTTP 404: Unable to geocode")):
                mod._assign_labels(
                    sets,
                    api_key="fake",
                    landmark_filter="all",
                    radius=1000,
                    region="us1",
                    locationiq_requests_per_second=1.0,
                    nominatim_zoom=18,
                    nominatim_layer="poi,natural,manmade",
                )
        self.assertEqual(sets[0].label, "Svartifoss")

    def test_assign_labels_dual_source_drops_timezone_when_locationiq_empty(self) -> None:
        sets = [
            mod.LocationSet(
                points=[mod.MediaPoint("x.jpg", 64.027411, -16.975069, datetime(2025, 7, 10, 10, 0, 0))],
                label=None,
            )
        ]
        timezone_payload = {
            "name": "Alaska - Timezone America/Yakutat",
            "category": "boundary",
            "type": "timezone",
            "display_name": "Alaska - Timezone America/Yakutat, United States",
        }
        with patch("rename_folder_with_poi_itinerary.fetch_nominatim_reverse", return_value=timezone_payload):
            with patch("rename_folder_with_poi_itinerary.fetch_nearby_poi", return_value=[]):
                mod._assign_labels(
                    sets,
                    api_key="fake",
                    landmark_filter="all",
                    radius=1000,
                    region="us1",
                    locationiq_requests_per_second=1.0,
                    nominatim_zoom=18,
                    nominatim_layer="poi,natural,manmade",
                )
        self.assertEqual(sets[0].label, "UNKNOWN_LOCATION")

    def test_assign_labels_dual_source_does_not_use_unfiltered_raw_fallback(self) -> None:
        sets = [
            mod.LocationSet(
                points=[mod.MediaPoint("x.jpg", 59.0, -140.0, datetime(2025, 7, 10, 10, 0, 0))],
                label=None,
            )
        ]
        with patch("rename_folder_with_poi_itinerary.fetch_nominatim_reverse", return_value={}):
            with patch(
                "rename_folder_with_poi_itinerary.fetch_nearby_poi",
                return_value=[{"name": "Seward Highway", "class": "highway", "type": "primary"}],
            ):
                mod._assign_labels(
                    sets,
                    api_key="fake",
                    landmark_filter="all",
                    radius=1000,
                    region="us1",
                    locationiq_requests_per_second=1.0,
                    nominatim_zoom=18,
                    nominatim_layer="poi,natural,manmade",
                )
        self.assertEqual(sets[0].label, "UNKNOWN_LOCATION")


    def test_consolidate_accepts_runner_up_substitution(self) -> None:
        labels = ["NewYork", "StatueOfLiberty", "TimesSquare"]
        members = [
            {
                "landmark_name": "NewYork",
                "set_member_count": 10,
                "itinerary_order": 1,
                "runner_up_candidates": ["Central Park"],
            },
            {
                "landmark_name": "StatueOfLiberty",
                "set_member_count": 5,
                "itinerary_order": 2,
            },
            {
                "landmark_name": "TimesSquare",
                "set_member_count": 3,
                "itinerary_order": 3,
            },
        ]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["opencode"],
                returncode=0,
                stdout='{"final_landmark_names":["CentralPark","StatueOfLiberty","TimesSquare"]}\n',
                stderr="",
            ),
        ):
            selected = mod.consolidate_itinerary_labels(
                labels,
                max_landmark_names=8,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
                location_set_members=members,
            )
        self.assertEqual(selected, ["CentralPark", "StatueOfLiberty", "TimesSquare"])

    def test_consolidate_runner_up_ordered_by_set_position(self) -> None:
        labels = ["NewYork", "StatueOfLiberty"]
        members = [
            {
                "landmark_name": "NewYork",
                "set_member_count": 10,
                "itinerary_order": 1,
                "runner_up_candidates": ["Central Park"],
            },
            {
                "landmark_name": "StatueOfLiberty",
                "set_member_count": 5,
                "itinerary_order": 2,
            },
        ]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["opencode"],
                returncode=0,
                stdout='{"final_landmark_names":["StatueOfLiberty","CentralPark"]}\n',
                stderr="",
            ),
        ):
            selected = mod.consolidate_itinerary_labels(
                labels,
                max_landmark_names=8,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
                location_set_members=members,
            )
        self.assertEqual(selected, ["CentralPark", "StatueOfLiberty"])

    def test_assign_labels_stores_top_candidates(self) -> None:
        sets = [
            mod.LocationSet(
                points=[mod.MediaPoint("x.jpg", 64.027411, -16.975069, datetime(2025, 7, 10, 10, 0, 0))],
                label=None,
            )
        ]
        with patch("rename_folder_with_poi_itinerary.fetch_nominatim_reverse", return_value={"name": "Svartifoss"}):
            with patch(
                "rename_folder_with_poi_itinerary.fetch_nearby_poi",
                return_value=[{"name": "NoisyPoiA"}, {"name": "NoisyPoiB"}],
            ):
                mod._assign_labels(
                    sets,
                    api_key="fake",
                    landmark_filter="all",
                    radius=1000,
                    region="us1",
                    locationiq_requests_per_second=1.0,
                    nominatim_zoom=18,
                    nominatim_layer="poi,natural,manmade",
                )
        self.assertIsNotNone(sets[0].top_candidates)
        top_candidates = sets[0].top_candidates or []
        self.assertGreater(len(top_candidates), 0)
        top_labels = [c["label"] for c in top_candidates]
        self.assertIn("Svartifoss", top_labels)

    def test_select_top_candidates_ranks_by_score(self) -> None:
        candidates = [
            {
                "label": "LowImportance",
                "source": "locationiq",
                "category": "amenity",
                "type": "parking",
                "importance_raw": 0.1,
                "place_rank_raw": 10.0,
                "distance_m": 500.0,
            },
            {
                "label": "HighImportance",
                "source": "nominatim",
                "category": "tourism",
                "type": "attraction",
                "importance_raw": 0.9,
                "place_rank_raw": 20.0,
                "distance_m": 100.0,
            },
        ]
        top = mod.select_top_candidates(candidates, top_n=2)
        self.assertEqual(top[0]["label"], "HighImportance")


    def test_consolidate_prompt_mentions_runner_up_substitution(self) -> None:
        labels = ["A", "B"]
        members = [
            {"landmark_name": "A", "set_member_count": 10, "itinerary_order": 1, "runner_up_candidates": ["AltA"]},
            {"landmark_name": "B", "set_member_count": 5, "itinerary_order": 2},
        ]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["opencode"],
                returncode=0,
                stdout='{"final_landmark_names":["A","B"]}\n',
                stderr="",
            ),
        ) as run_mock:
            mod.consolidate_itinerary_labels(
                labels,
                max_landmark_names=8,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
                location_set_members=members,
            )

        prompt = run_mock.call_args[0][0][-1]
        self.assertIn("runner_up_candidates", prompt)
        self.assertIn("substitute one of its runner-ups", prompt)

    def test_fetch_nearby_poi_raises_stop_on_rate_limited_day(self) -> None:
        error = HTTPError(
            url="https://us1.locationiq.com/v1/nearby",
            code=429,
            msg="Too Many Requests",
            hdrs=Message(),
            fp=io.BytesIO(b'{"error":"Rate Limited Day"}'),
        )

        with patch("rename_folder_with_poi_itinerary.urlopen", side_effect=error):
            with self.assertRaises(mod.LocationIQGracefulStop) as ctx:
                mod.fetch_nearby_poi(
                    api_key="k",
                    lat=1.0,
                    lon=2.0,
                    landmark_filter="all",
                    radius=1000,
                    region="us1",
                )

        self.assertEqual(ctx.exception.reason, "locationiq-rate-limited-day")

    def test_fetch_nearby_poi_unknown_429_stops_when_balance_low(self) -> None:
        class FakeResponse:
            def __init__(self, body: str) -> None:
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return self._body.encode("utf-8")

        def fake_urlopen(req, timeout=30):  # noqa: ANN001
            request_url = req.full_url if hasattr(req, "full_url") else str(req)
            if "balance" in request_url:
                return FakeResponse('{"balance":{"day":99,"bonus":0}}')
            raise HTTPError(
                url=request_url,
                code=429,
                msg="Too Many Requests",
                hdrs=Message(),
                fp=io.BytesIO(b'{"error":"Too Many Requests"}'),
            )

        with patch("rename_folder_with_poi_itinerary.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(mod.LocationIQGracefulStop) as ctx:
                mod.fetch_nearby_poi(
                    api_key="k",
                    lat=1.0,
                    lon=2.0,
                    landmark_filter="all",
                    radius=1000,
                    region="us1",
                )

        self.assertEqual(ctx.exception.reason, "locationiq-balance-low-threshold")

    def test_fetch_nearby_poi_unknown_429_with_balance_100_does_not_auto_stop_low_balance(self) -> None:
        class FakeResponse:
            def __init__(self, body: str) -> None:
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return self._body.encode("utf-8")

        def fake_urlopen(req, timeout=30):  # noqa: ANN001
            request_url = req.full_url if hasattr(req, "full_url") else str(req)
            if "balance" in request_url:
                return FakeResponse('{"balance":{"day":100,"bonus":0}}')
            raise HTTPError(
                url=request_url,
                code=429,
                msg="Too Many Requests",
                hdrs=Message(),
                fp=io.BytesIO(b'{"error":"Too Many Requests"}'),
            )

        with patch("rename_folder_with_poi_itinerary.urlopen", side_effect=fake_urlopen):
            with patch("rename_folder_with_poi_itinerary.time.sleep"):
                with self.assertRaises(mod.LocationIQGracefulStop) as ctx:
                    mod.fetch_nearby_poi(
                        api_key="k",
                        lat=1.0,
                        lon=2.0,
                        landmark_filter="all",
                        radius=1000,
                        region="us1",
                        retries=1,
                    )

        self.assertEqual(ctx.exception.reason, "locationiq-rate-limit-unknown")

    def test_fetch_nearby_poi_rate_limited_minute_retries_then_stops(self) -> None:
        error = HTTPError(
            url="https://us1.locationiq.com/v1/nearby",
            code=429,
            msg="Too Many Requests",
            hdrs=Message(),
            fp=io.BytesIO(b'{"error":"Rate Limited Minute"}'),
        )

        with patch("rename_folder_with_poi_itinerary.urlopen", side_effect=error):
            with patch("rename_folder_with_poi_itinerary.time.sleep"):
                with self.assertRaises(mod.LocationIQGracefulStop) as ctx:
                    mod.fetch_nearby_poi(
                        api_key="k",
                        lat=1.0,
                        lon=2.0,
                        landmark_filter="all",
                        radius=1000,
                        region="us1",
                        retries=1,
                    )

        self.assertEqual(ctx.exception.reason, "locationiq-rate-limit-retry-exhausted")

    def test_fetch_nearby_poi_rate_limited_minute_retries_then_succeeds(self) -> None:
        class FakeResponse:
            def __init__(self, body: str) -> None:
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return self._body.encode("utf-8")

        calls = {"n": 0}

        def fake_urlopen(req, timeout=30):  # noqa: ANN001
            calls["n"] += 1
            request_url = req.full_url if hasattr(req, "full_url") else str(req)
            if calls["n"] == 1:
                raise HTTPError(
                    url=request_url,
                    code=429,
                    msg="Too Many Requests",
                    hdrs=Message(),
                    fp=io.BytesIO(b'{"error":"Rate Limited Minute"}'),
                )
            return FakeResponse('[{"name":"A"}]')

        with patch("rename_folder_with_poi_itinerary.urlopen", side_effect=fake_urlopen):
            with patch("rename_folder_with_poi_itinerary.time.sleep"):
                result = mod.fetch_nearby_poi(
                    api_key="k",
                    lat=1.0,
                    lon=2.0,
                    landmark_filter="all",
                    radius=1000,
                    region="us1",
                    retries=2,
                )

        self.assertEqual(calls["n"], 2)
        self.assertEqual(result, [{"name": "A"}])

    def test_assign_labels_propagates_locationiq_graceful_stop(self) -> None:
        sets = [
            mod.LocationSet(
                points=[mod.MediaPoint("x.jpg", 64.027411, -16.975069, datetime(2025, 7, 10, 10, 0, 0))],
                label=None,
            )
        ]

        with patch("rename_folder_with_poi_itinerary.fetch_nominatim_reverse", return_value={"name": "Svartifoss"}):
            with patch(
                "rename_folder_with_poi_itinerary.fetch_nearby_poi",
                side_effect=mod.LocationIQGracefulStop("locationiq-rate-limited-day", "Rate Limited Day"),
            ):
                with self.assertRaises(mod.LocationIQGracefulStop):
                    mod._assign_labels(
                        sets,
                        api_key="fake",
                        landmark_filter="all",
                        radius=1000,
                        region="us1",
                        locationiq_requests_per_second=1.0,
                        nominatim_zoom=18,
                        nominatim_layer="poi,natural,manmade",
                    )

    def test_main_graceful_stop_for_locationiq_rate_limit_keeps_pending(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "2025_07_02").mkdir()
            (root / "2025_07_03").mkdir()
            state_path = root / "state.json"
            report_path = root / "report.json"

            call_count = {"n": 0}

            def fake_process(folder: Path, args, api_cache):
                call_count["n"] += 1
                raise mod.LocationIQGracefulStop("locationiq-rate-limited-day", "Rate Limited Day")

            argv = [
                "rename_folder_with_poi_itinerary.py",
                str(root),
                "--key",
                "k",
                "--state-json",
                str(state_path),
                "--report-json",
                str(report_path),
            ]
            with patch.object(sys, "argv", argv):
                with patch("rename_folder_with_poi_itinerary.process_single_folder", side_effect=fake_process):
                    exit_code = mod.main()

            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 1)
            self.assertEqual(call_count["n"], 1)
            self.assertTrue(payload["interrupted"])
            self.assertEqual(payload["interrupt_source"], "locationiq-rate-limited-day")
            self.assertEqual(
                payload["pending_folder_ids"],
                [str(root / "2025_07_02"), str(root / "2025_07_03")],
            )


if __name__ == "__main__":
    unittest.main()
