import unittest
import engine


class ModelCatalogTests(unittest.TestCase):
    def test_required_models_and_order(self):
        self.assertEqual(
            engine.WEATHER_MODEL_LABELS,
            [
                "ECMWF IFS 0.25°",
                "GFS Global",
                "ICON Global",
                "CMA GRAPES Global",
                "ECMWF (AI)",
            ],
        )

    def test_model_ids_and_horizons(self):
        expected = {
            "ECMWF IFS 0.25°": ("ecmwf_ifs025", 16),
            "GFS Global": ("ncep_gfs_global", 16),
            "ICON Global": ("icon_global", 8),
            "ECMWF (AI)": ("ecmwf_aifs025_single", 15),
            "CMA GRAPES Global": ("cma_grapes_global", 10),
        }
        for label, (model_id, horizon) in expected.items():
            config = engine.WEATHER_MODEL_BY_LABEL[label]
            self.assertEqual(config["id"], model_id)
            self.assertEqual(config["forecast_days"], horizon)
            self.assertGreater(len(config["pressure_levels"]), 0)

    def test_label_to_id_mapping(self):
        for item in engine.WEATHER_MODEL_CATALOG:
            self.assertEqual(engine._weather_model_id_from_label(item["label"]), item["id"])

    def test_jma_is_disabled_and_aifs_old_label_is_compatible(self):
        self.assertNotIn("JMA MSM", engine.WEATHER_MODEL_LABELS)
        self.assertEqual(engine._weather_model_id_from_label("ECMWF (AI)"), "ecmwf_aifs025_single")
        self.assertEqual(engine._weather_model_id_from_label("ECMWF AIFS 0.25° Single"), "ecmwf_aifs025_single")



if __name__ == "__main__":
    unittest.main()
