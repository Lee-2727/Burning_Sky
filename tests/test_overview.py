import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update_overview


class OverviewTests(unittest.TestCase):
    def test_default_location_and_model_set(self):
        self.assertEqual((update_overview.LAT, update_overview.LON), (31.8206, 117.2272))
        self.assertEqual([c[0] for c in update_overview.load_city_config()], ["合肥", "芜湖", "南京", "武汉", "上海", "深圳"])
        self.assertEqual(len(update_overview.engine.WEATHER_MODEL_CATALOG), 5)
        self.assertNotIn("JMA MSM", update_overview.engine.WEATHER_MODEL_LABELS)

    def test_record_extracts_three_metrics(self):
        value = update_overview._record({"inferred": {
            "regular_score": 62, "color_score": 78,
            "burn_scale_index": 24.5, "burn_scale_label": "微烧"
        }})
        self.assertEqual(value["probability"], 62.0)
        self.assertEqual(value["color_score"], 78.0)
        self.assertEqual(value["burn_scale"], 24.5)


if __name__ == "__main__":
    unittest.main()
