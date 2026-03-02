import unittest
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rename_folder_with_poi_itinerary as mod


class RenameFolderWithPoiItineraryTests(unittest.TestCase):
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

    def test_choose_preferred_label_falls_back_city_then_street(self) -> None:
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
        self.assertEqual(mod.choose_preferred_label(street_results), "NE Broadway")

    def test_dedupe_labels_preserves_first_occurrence(self) -> None:
        labels = ["CITYX", "CITYX", "Landmark Y", "cityx", "Landmark Y"]
        self.assertEqual(mod.dedupe_labels(labels), ["CITYX", "LANDMARK_Y"])

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
            label="CITY_LATE",
        )
        cluster_early = mod.LocationSet(
            points=[mod.MediaPoint("early.jpg", 47.0, -122.0, datetime(2024, 9, 18, 8, 0, 0))],
            label="LANDMARK_EARLY",
        )

        ordered = mod.labels_in_itinerary_order([cluster_late, cluster_early])

        self.assertEqual(ordered, ["LANDMARK_EARLY", "CITY_LATE"])

    def test_build_target_name_appends_labels(self) -> None:
        self.assertEqual(
            mod.build_target_name("2024_09_18", ["Space Needle", "Seattle"]),
            "2024_09_18_SPACE_NEEDLE_SEATTLE",
        )


if __name__ == "__main__":
    unittest.main()
