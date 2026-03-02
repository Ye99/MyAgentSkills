import unittest
from datetime import datetime
from pathlib import Path
import sys
from unittest.mock import patch
import subprocess

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

    def test_dedupe_labels_preserves_first_occurrence(self) -> None:
        labels = ["CITYX", "CITYX", "Landmark Y", "cityx", "Landmark Y"]
        self.assertEqual(mod.dedupe_labels(labels), ["Cityx", "LandmarkY"])

    def test_dedupe_labels_excludes_unknown_location(self) -> None:
        labels = ["UNKNOWN_LOCATION", "Space Needle", "unknown location", "Seattle"]
        self.assertEqual(mod.dedupe_labels(labels), ["SpaceNeedle", "Seattle"])

    def test_dedupe_labels_excludes_low_signal_labels(self) -> None:
        labels = ["Vancouver", "Monument 5E-92", "Zaozhuang"]
        self.assertEqual(mod.dedupe_labels(labels), ["Vancouver", "Zaozhuang"])

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
        self.assertEqual(result, "2025_07_02_Alaskaairlinescustomerservice,Anchorage,Zaozhuang,Portageglaciercruise")

    def test_build_target_name_uses_comma_separator(self) -> None:
        result = mod.build_target_name("2025_07_31", ["Nuuk Fitness", "Wall Street"])
        self.assertEqual(result, "2025_07_31_NuukFitness,WallStreet")

    def test_extract_base_date_name_from_already_renamed_folder(self) -> None:
        self.assertEqual(mod.extract_base_date_name("2025_08_21_RedmondPool"), "2025_08_21")
        self.assertEqual(mod.extract_base_date_name("2025_08_21"), "2025_08_21")
        self.assertEqual(mod.extract_base_date_name("VacationPhotos"), "VacationPhotos")


if __name__ == "__main__":
    unittest.main()
