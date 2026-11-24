import unittest

from scripts.update_overview import _HeadlessForecast, _record


class ManualOverviewHeadlessCallbackTests(unittest.TestCase):
    def test_judgment_callback_accepts_visual_scale_argument(self):
        # The engine calls this static callback with five arguments.
        self.assertIsNone(_HeadlessForecast._set_judgment_text(None, 40, 55, "小烧", 12.5))

    def test_visual_scale_is_optional_not_fabricated(self):
        result = {"inferred": {
            "regular_score": 40,
            "color_score": 55,
            "geometric_burn_scale_index": 12.5,
            "tintable_ratio": 20,
        }}
        row = _record(result)
        self.assertEqual(row["probability"], 40)
        self.assertEqual(row["color_score"], 55)
        self.assertEqual(row["burn_scale"], 12.5)
        self.assertIsNone(row["visual_burn_scale"])
