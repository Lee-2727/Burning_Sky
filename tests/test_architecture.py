import unittest
from datetime import datetime, timezone

from burning_sky.models import ForecastDataset, ModelMetadata
from burning_sky.services.forecast_service import ForecastService

class ArchitectureTests(unittest.TestCase):
    def test_shared_engine_and_service_import(self):
        import engine
        self.assertEqual(engine.APP_TITLE, "火烧云预报工具 v1.4.67（本地200公里烧云规模评估）")
        self.assertTrue(hasattr(engine, "FireCloudApp"))
        self.assertEqual(ForecastService.algorithm_version, "1.4.67")

    def test_dataset_validation(self):
        ds = ForecastDataset(
            metadata=ModelMetadata(provider="test", model_id="test-model"),
            valid_times=[datetime(2026, 10, 30, tzinfo=timezone.utc)],
            hourly={"cloud_cover_low": [20.0]},
        )
        ds.validate()
        bad = ForecastDataset(
            metadata=ds.metadata,
            valid_times=ds.valid_times,
            hourly={"cloud_cover_low": [20.0, 30.0]},
        )
        with self.assertRaises(ValueError):
            bad.validate()

if __name__ == "__main__":
    unittest.main()
