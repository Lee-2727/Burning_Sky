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

    def test_record_reads_local_cloud_from_zero_distance_sample(self):
        value = update_overview._record({"inferred": {
            "regular_score": 62, "color_score": 78,
            "tintable_ratio": 24.5, "burn_scale_label": "微烧",
        }, "samples": [
            {"distance_km": 5, "cloud_cover_mid": 99, "cloud_cover_high": 99},
            {"distance_km": 0, "cloud_cover_mid": 60, "cloud_cover_high": 30},
        ]})
        self.assertEqual(value["probability"], 62.0)
        self.assertEqual(value["color_score"], 78.0)
        self.assertEqual(value["burn_scale"], 24.5)
        self.assertEqual(value["local_mid_cloud"], 60.0)
        self.assertEqual(value["local_high_layer_cloud"], 30.0)
        self.assertEqual(value["local_high_cloud"], 90.0)
        self.assertAlmostEqual(value["fire_score_no_color"], 13.671)

    def test_local_high_cloud_is_capped_at_100(self):
        value = update_overview._record({"inferred": {
            "regular_score": 80, "tintable_ratio": 50,
        }, "samples": [
            {"distance_km": 0, "cloud_cover_mid": 75, "cloud_cover_high": 60},
        ]})
        self.assertEqual(value["local_high_cloud"], 100.0)
        self.assertEqual(value["fire_score_no_color"], 40.0)

    def test_missing_local_sample_keeps_fields_present_as_null(self):
        value = update_overview._record({"inferred": {
            "regular_score": 80, "tintable_ratio": 50,
        }, "samples": [{"distance_km": 10, "cloud_cover_mid": 75, "cloud_cover_high": 60}]})
        self.assertIn("local_high_cloud", value)
        self.assertIsNone(value["local_high_cloud"])
        self.assertIsNone(value["fire_score_no_color"])


if __name__ == "__main__":
    unittest.main()
