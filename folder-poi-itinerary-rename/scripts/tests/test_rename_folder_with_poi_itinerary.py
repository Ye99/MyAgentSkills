import unittest
from datetime import datetime
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock
import subprocess
import os
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rename_folder_with_poi_itinerary as mod


class RenameFolderWithPoiItineraryTests(unittest.TestCase):
    def test_is_supported_date_folder_path(self) -> None:
        self.assertTrue(mod.is_supported_date_folder_path(Path("/tmp/2025/2025_07_02")))
        self.assertFalse(mod.is_supported_date_folder_path(Path("/tmp/2025/2025_07_02_Seattle")))
        self.assertFalse(mod.is_supported_date_folder_path(Path("/tmp/trips/2025_07_02")))

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

    def test_build_parser_defaults_for_max_tags_and_opencode_timeout(self) -> None:
        parser = mod.build_parser()
        args = parser.parse_args(["/tmp/2025/2025_07_02"])
        self.assertEqual(args.max_tags, 8)
        self.assertEqual(args.opencode_timeout_sec, 60)
        self.assertEqual(args.event_distance_m, 2000.0)
        self.assertEqual(args.opencode_model, os.getenv("OPENCODE_MODEL"))
        self.assertFalse(args.use_nominatim_reverse)
        self.assertEqual(args.nominatim_zoom, 18)
        self.assertEqual(args.nominatim_layer, "poi,natural,manmade")
        self.assertEqual(args.nominatim_requests_per_second, 1.0)
        self.assertTrue(args.cache_file.endswith("/folder-poi-itinerary-rename/scripts/cache/geo_api_cache.json"))
        self.assertNotIn("/.cache/", args.cache_file)

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
                    tag="all",
                    radius=1000,
                    region="us1",
                    api_cache=cache,
                )
            with patch("rename_folder_with_poi_itinerary.urlopen") as open_mock_second:
                second = mod.fetch_nearby_poi(
                    api_key="k",
                    lat=1.0,
                    lon=2.0,
                    tag="all",
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
                    tag="all",
                    radius=1000,
                    region="us1",
                )
        request_mock.assert_not_called()

    def test_build_parser_reads_opencode_model_env(self) -> None:
        with patch.dict("os.environ", {"OPENCODE_MODEL": "openai/gpt-4o-mini"}, clear=False):
            parser = mod.build_parser()
            args = parser.parse_args(["/tmp/2025/2025_07_02"])
        self.assertEqual(args.opencode_model, "openai/gpt-4o-mini")

    def test_significant_labels_pick_top_media_count_then_restore_itinerary_order(self) -> None:
        sets = [
            mod.LocationSet(points=[mod.MediaPoint("a1.jpg", 0.0, 0.0, datetime(2025, 7, 4, 9, 0, 0))], label="A"),
            mod.LocationSet(
                points=[
                    mod.MediaPoint("b1.jpg", 0.0, 0.0, datetime(2025, 7, 4, 10, 0, 0)),
                    mod.MediaPoint("b2.jpg", 0.0, 0.0, datetime(2025, 7, 4, 10, 1, 0)),
                    mod.MediaPoint("b3.jpg", 0.0, 0.0, datetime(2025, 7, 4, 10, 2, 0)),
                ],
                label="B",
            ),
            mod.LocationSet(
                points=[
                    mod.MediaPoint("c1.jpg", 0.0, 0.0, datetime(2025, 7, 4, 11, 0, 0)),
                    mod.MediaPoint("c2.jpg", 0.0, 0.0, datetime(2025, 7, 4, 11, 1, 0)),
                ],
                label="C",
            ),
            mod.LocationSet(points=[mod.MediaPoint("d1.jpg", 0.0, 0.0, datetime(2025, 7, 4, 12, 0, 0))], label="D"),
        ]
        self.assertEqual(mod.significant_labels_in_itinerary_order(sets, max_tags=2), ["B", "C"])

    def test_select_highlight_labels_uses_opencode_response(self) -> None:
        labels = ["StatueOfLiberty", "WallStreet", "BatteryPark", "FerryTerminal"]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["opencode"],
                returncode=0,
                stdout='{"primary_tags":["StatueOfLiberty","WallStreet"],"secondary_tags":["BatteryPark"]}\n',
                stderr="",
            ),
        ):
            selected = mod.select_highlight_labels(
                labels,
                max_tags=8,
                opencode_timeout_sec=60,
                opencode_model=None,
            )
        self.assertEqual(selected, ["StatueOfLiberty", "WallStreet", "BatteryPark"])

    def test_select_highlight_labels_passes_model_flag_to_opencode(self) -> None:
        labels = ["StatueOfLiberty", "WallStreet"]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["opencode"],
                returncode=0,
                stdout='{"primary_tags":["StatueOfLiberty","WallStreet"],"secondary_tags":[]}\n',
                stderr="",
            ),
        ) as run_mock:
            selected = mod.select_highlight_labels(
                labels,
                max_tags=8,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
            )

        call_args = run_mock.call_args[0][0]
        self.assertEqual(call_args[:4], ["opencode", "-m", "openai/gpt-4o-mini", "run"])
        self.assertEqual(selected, ["StatueOfLiberty", "WallStreet"])

    def test_select_highlight_labels_falls_back_when_opencode_fails(self) -> None:
        labels = ["StatueOfLiberty", "WallStreet", "BatteryPark"]
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["opencode"], returncode=1, stdout="", stderr="boom"),
        ):
            selected = mod.select_highlight_labels(
                labels,
                max_tags=2,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
            )
        self.assertEqual(selected, ["StatueOfLiberty", "WallStreet"])

    def test_assign_labels_prefers_nominatim_when_enabled(self) -> None:
        sets = [
            mod.LocationSet(
                points=[mod.MediaPoint("x.jpg", 64.027411, -16.975069, datetime(2025, 7, 10, 10, 0, 0))],
                label=None,
            )
        ]
        with patch("rename_folder_with_poi_itinerary.fetch_nominatim_reverse", return_value={"name": "Svartifoss"}):
            with patch("rename_folder_with_poi_itinerary.fetch_nearby_poi", return_value=[{"name": "NoisyPoi"}]):
                mod._assign_labels(
                    sets,
                    api_key="fake",
                    tag="all",
                    radius=1000,
                    region="us1",
                    use_nominatim_reverse=True,
                    use_dual_source=False,
                    opencode_timeout_sec=60,
                    opencode_model="openai/gpt-4o-mini",
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

    def test_choose_best_label_from_candidates_uses_opencode_pick(self) -> None:
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
        with patch(
            "rename_folder_with_poi_itinerary.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["opencode"],
                returncode=0,
                stdout='{"label":"Svartifoss"}\n',
                stderr="",
            ),
        ):
            label = mod.choose_best_label_from_candidates(
                candidates,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
            )
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
        self.assertIsNone(
            mod.choose_best_label_from_candidates(
                candidates,
                opencode_timeout_sec=60,
                opencode_model="openai/gpt-4o-mini",
            )
        )

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
                        tag="all",
                        radius=1000,
                        region="us1",
                        use_nominatim_reverse=True,
                        use_dual_source=True,
                        opencode_timeout_sec=60,
                        opencode_model="openai/gpt-4o-mini",
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
                    tag="all",
                    radius=1000,
                    region="us1",
                    use_nominatim_reverse=False,
                    use_dual_source=True,
                    opencode_timeout_sec=60,
                    opencode_model="openai/gpt-4o-mini",
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
                    tag="all",
                    radius=1000,
                    region="us1",
                    use_nominatim_reverse=False,
                    use_dual_source=True,
                    opencode_timeout_sec=60,
                    opencode_model="openai/gpt-4o-mini",
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
                    tag="all",
                    radius=1000,
                    region="us1",
                    use_nominatim_reverse=False,
                    use_dual_source=True,
                    opencode_timeout_sec=60,
                    opencode_model="openai/gpt-4o-mini",
                    locationiq_requests_per_second=1.0,
                    nominatim_zoom=18,
                    nominatim_layer="poi,natural,manmade",
                )
        self.assertEqual(sets[0].label, "UNKNOWN_LOCATION")


if __name__ == "__main__":
    unittest.main()
