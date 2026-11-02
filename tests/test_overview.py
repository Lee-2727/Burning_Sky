import unittest
import sys
import os
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update_overview


class OverviewTests(unittest.TestCase):
    def test_default_location_and_model_set(self):
        self.assertEqual((update_overview.LAT, update_overview.LON), (31.8206, 117.2272))
        self.assertEqual([c[0] for c in update_overview.load_city_config()], ["合肥", "深圳"])
        self.assertEqual(len(update_overview.engine.WEATHER_MODEL_CATALOG), 5)
        self.assertNotIn("JMA MSM", update_overview.engine.WEATHER_MODEL_LABELS)

    def test_scheduled_direction_matches_six_beijing_slots(self):
        old_event = os.environ.get("GITHUB_EVENT_NAME")
        old_forced = os.environ.pop("BURNING_SKY_DIRECTION", None)
        os.environ["GITHUB_EVENT_NAME"] = "schedule"
        try:
            expected = {
                0: "morning", 4: "morning", 8: "evening",
                12: "evening", 16: "evening", 20: "morning",
            }
            for hour, direction in expected.items():
                self.assertEqual(update_overview._scheduled_direction(
                    update_overview.TZ.localize(datetime(2026, 11, 1, hour, 0))
                    if hasattr(update_overview.TZ, "localize") else
                    datetime(2026, 11, 1, hour, 0, tzinfo=update_overview.TZ)
                ), direction)
        finally:
            if old_event is None:
                os.environ.pop("GITHUB_EVENT_NAME", None)
            else:
                os.environ["GITHUB_EVENT_NAME"] = old_event
            if old_forced is not None:
                os.environ["BURNING_SKY_DIRECTION"] = old_forced

    def test_record_extracts_overview_metrics_and_weighted_fire_score(self):
        value = update_overview._record({"inferred": {
            "regular_score": 62, "color_score": 78,
            "burn_scale_index": 24.5, "burn_scale_label": "微烧",
            "local_scale_clouds": {"mid": 60, "high": 30}
        }})
        self.assertEqual(value["probability"], 62.0)
        self.assertEqual(value["color_score"], 78.0)
        self.assertEqual(value["burn_scale"], 24.5)
        self.assertEqual(value["local_high_cloud"], 90.0)
        self.assertAlmostEqual(value["fire_score_no_color"], 13.671)

    def test_local_high_cloud_is_capped_at_100(self):
        value = update_overview._record({"inferred": {
            "regular_score": 80, "tintable_ratio": 50,
            "local_scale_clouds": {"mid": 75, "high": 60}
        }})
        self.assertEqual(value["local_high_cloud"], 100.0)
        self.assertEqual(value["fire_score_no_color"], 40.0)


if __name__ == "__main__":
    unittest.main()
