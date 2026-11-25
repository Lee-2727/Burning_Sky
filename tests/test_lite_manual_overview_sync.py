from __future__ import annotations
import unittest
from datetime import date

import engine
from burning_sky.lite_forecast import create_runner
from burning_sky.overview_visual import manual_records_to_days, render_direction

EC = 'ECMWF IFS 0.25°'
GFS = 'GFS Global'


class LiteEngineParityTests(unittest.TestCase):
    def test_lite_runner_uses_shared_spatial_engine_configuration(self):
        runner = create_runner(engine, place='合肥', lat=31.8, lon=117.2,
                               elevation=30, date='2026-11-20', model_label=GFS)
        self.assertIsInstance(runner, engine.FireCloudApp)
        self.assertIs(runner._fetch_weather_task.__func__, engine.FireCloudApp._fetch_weather_task)
        self.assertTrue(runner.spatial_experiment)
        self.assertTrue(runner.spatial_enabled)
        self.assertTrue(runner.skip_rainbow_single_var.get())
        self.assertEqual(runner.vars['aod_source'].get(), 'CAMS')

    def test_lite_color_metric_is_out_of_100(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'lite' / 'app.py').read_text(encoding='utf-8')
        self.assertIn("('常规概率','色彩分','火烧云规模'),vals,('%','/100','%')", source)
        self.assertNotIn("vals,('%','/60','%')", source)
        self.assertIn("color_angle_diagnostic", source)


class ManualOverviewPresentationTests(unittest.TestCase):
    def test_manual_records_normalize_to_scheduled_chart_schema(self):
        records = [
            {'date':'2026-11-20','direction':'morning','model':EC,
             'probability':70,'color_score':82,'burn_scale':35},
            {'date':'2026-11-20','direction':'morning','model':GFS,
             'probability':20,'color_score':55,'burn_scale':12},
            {'date':'2026-11-20','direction':'evening','model':EC,
             'probability':0,'color_score':0,'burn_scale':0},
        ]
        days = manual_records_to_days(records, ['2026-11-20','2026-11-21','2026-11-22'])
        self.assertEqual(len(days), 3)
        self.assertEqual(days[0]['morning'][EC]['color_score'], 82)
        self.assertEqual(days[0]['evening'][EC]['burn_scale'], 0)
        self.assertEqual(days[1]['morning'], {})
        chart = render_direction(days, 'morning', '朝霞')
        self.assertIn('EC', chart)
        self.assertIn('GFS', chart)
        self.assertIn('火烧云规模', chart)

    def test_manual_action_uses_scheduled_record_serializer(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / 'scripts' / 'manual_overview_action.py').read_text(encoding='utf-8')
        self.assertIn('from scripts.update_overview import _new_runner, _record', source)
        self.assertIn('row=_record(result)', source)


if __name__ == '__main__':
    unittest.main()
