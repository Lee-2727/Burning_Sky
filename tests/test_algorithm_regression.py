import json
import unittest
from datetime import date
from pathlib import Path

from burning_sky import algorithm
from burning_sky.services.forecast_service import ForecastService

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "v1_4_67_regression.json").read_text(encoding="utf-8"))

class AlgorithmRegressionTests(unittest.TestCase):
    def test_cloud_path_score_and_color_match_v1_4_67(self):
        f = FIXTURE
        inferred = ForecastService.analyze_cloud_path(f["path_input"], aod=f["path_aod"])
        scored = algorithm.calculate_color_performance_score(inferred)
        expected = f["path_expected"]
        self.assertEqual(scored["score"], expected["score"])
        self.assertEqual(scored["color_score"], expected["color_score"])
        self.assertEqual(list(scored["color_rgb"]), expected["color_rgb"])
        self.assertEqual(scored["color_pos"], expected["color_pos"])
        self.assertAlmostEqual(scored["low"], expected["low"], places=10)
        self.assertAlmostEqual(scored["mid"], expected["mid"], places=10)
        self.assertAlmostEqual(scored["high"], expected["high"], places=10)
        self.assertAlmostEqual(scored["total"], expected["total"], places=10)

    def test_manual_case_geometry_matches_v1_4_67(self):
        f = FIXTURE
        data = dict(f["manual_input"])
        data["date"] = date.fromisoformat(data["date"])
        result = ForecastService.analyze_manual_case(data)
        expected = f["manual_expected"]
        self.assertEqual(result["score"], expected["score"])
        self.assertAlmostEqual(result["geo"]["sunset_azimuth"], expected["sunset_azimuth"], places=10)
        self.assertAlmostEqual(result["geo"]["geometry_duration_min"], expected["geometry_duration_min"], places=10)
        self.assertEqual(result["effective_base"], expected["effective_base"])

    def test_algorithm_module_is_ui_and_network_independent(self):
        source = (Path(__file__).parents[1] / "burning_sky" / "algorithm.py").read_text(encoding="utf-8").lower()
        for forbidden in ("import tkinter", "import streamlit", "urllib.request", "requests.get", "open("):
            self.assertNotIn(forbidden, source)

if __name__ == "__main__":
    unittest.main()
