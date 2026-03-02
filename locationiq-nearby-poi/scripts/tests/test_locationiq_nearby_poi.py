import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import locationiq_nearby_poi


class LocationIQNearbyPoiCliTests(unittest.TestCase):
    def test_json_mode_prints_raw_array_only(self) -> None:
        fake_pois = [
            {
                "name": "A",
                "distance": 12,
                "display_name": "A Place",
            }
        ]

        with patch("locationiq_nearby_poi.fetch_nearby_poi", return_value=fake_pois):
            with patch(
                "sys.argv",
                [
                    "locationiq_nearby_poi.py",
                    "--lat",
                    "40.68917",
                    "--lon",
                    "-74.04444",
                    "--key",
                    "dummy",
                    "--json",
                ],
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = locationiq_nearby_poi.main()

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), fake_pois)

    def test_json_pretty_mode_prints_indented_json(self) -> None:
        fake_pois = [
            {
                "name": "A",
                "distance": 12,
                "display_name": "A Place",
            }
        ]

        with patch("locationiq_nearby_poi.fetch_nearby_poi", return_value=fake_pois):
            with patch(
                "sys.argv",
                [
                    "locationiq_nearby_poi.py",
                    "--lat",
                    "40.68917",
                    "--lon",
                    "-74.04444",
                    "--key",
                    "dummy",
                    "--json",
                    "--pretty",
                ],
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = locationiq_nearby_poi.main()

        output = out.getvalue()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), fake_pois)
        self.assertIn("\n  {\n", output)


if __name__ == "__main__":
    unittest.main()
