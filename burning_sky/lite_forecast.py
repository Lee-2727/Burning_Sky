"""Lite-only presentation adapter for the existing Burning Sky forecast engine.

This module does not implement any forecast physics. It executes exactly one
sunrise OR sunset using the same shared FireCloudApp engine as Pro.
"""
from __future__ import annotations
from datetime import datetime
import math
import re


class _Value:
    def __init__(self, value=''):
        self.value = str(value)
    def get(self):
        return self.value
    def set(self, value):
        self.value = str(value)


class _Check:
    def get(self):
        return True  # Skip optional rainbow calculations in Lite.


class _Label:
    def configure(self, **kwargs):
        return None


def create_runner(engine, *, place, lat, lon, elevation, date, model_label):
    class Runner(engine.FireCloudApp):
        def after(self, *_a, **_k):
            return None
        def _set_main_progress(self, *_a, **_k):
            return None
        def _set_text(self, *_a, **_k):
            return None
        def _set_judgment_text(self, *_a, **_k):
            return None
        def _draw_peak_color_swatch(self, *_a, **_k):
            return None
        def _draw_colorbar(self, *_a, **_k):
            return None
    runner = object.__new__(Runner)
    values = {
        'place': place, 'date': date,
        'lat': str(lat), 'lon': str(lon), 'elevation': str(elevation),
        'weather_model': model_label, 'rainbow_enabled': False,
        'cloud_base_mode': '自动剖面推算',
        'morning_cloud_base_km': '2.00', 'evening_cloud_base_km': '2.00',
        'aod_source': 'CAMS', 'morning_aod': '', 'evening_aod': '',
    }
    runner.vars = {k: _Value(v) for k, v in values.items()}
    runner.direction_results = {}
    runner.last_report = ''
    runner.last_fetch_time = None
    runner.aod_manual_override = False
    runner._setting_aod_programmatically = False
    runner.web_aod_value = None
    runner.web_aod_valid_time = None
    runner.aod_path_values = []
    runner.cloud_path_samples = []
    runner.cloud_path_result = None
    runner.fetch_in_progress = False
    runner.skip_rainbow_single_var = _Check()
    for key in (
        'morning_summary', 'evening_summary', 'morning_score_label',
        'evening_score_label', 'morning_upper_label', 'evening_upper_label',
        'morning_color_score_label', 'evening_color_score_label',
        'morning_judgment_label', 'evening_judgment_label',
        'morning_rainbow_label', 'evening_rainbow_label',
        'morning_aod_status_var', 'evening_aod_status_var',
    ):
        setattr(runner, key, _Label() if not key.endswith('_var') else _Value(''))
    return runner


def run_single(engine, *, place, lat, lon, elevation, day, model_label, direction):
    if direction not in ('morning', 'evening'):
        raise ValueError('Lite只允许一次查询一个方向')
    runner = create_runner(engine, place=place, lat=lat, lon=lon,
                           elevation=elevation, date=day, model_label=model_label)
    previous = getattr(engine, '_CASE_REQUEST_CONTEXT', None)
    request_cache = {}
    try:
        engine._CASE_REQUEST_CONTEXT = {'mode':'capture', 'cache':request_cache}
        engine.messagebox.last_error = None
        runner._fetch_weather_task(direction, raise_errors=True, notify_success=False)
        if engine.messagebox.last_error:
            raise RuntimeError(str(engine.messagebox.last_error))
        record = runner.direction_results.get(direction)
        if not isinstance(record, dict) or not isinstance(record.get('inferred'), dict):
            raise RuntimeError('当前时段暂时未返回有效的预测结果')
        return {
            'result': record,
            'cache': request_cache,
            'fetched_at': runner.last_fetch_time,
            'meta': {'place':place, 'lat':float(lat), 'lon':float(lon),
                     'elev':float(elevation), 'date':day,
                     'model_label':model_label, 'direction':direction},
        }
    finally:
        engine._CASE_REQUEST_CONTEXT = previous


def number(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def verdict(result):
    """One short forecast sentence; never reproduce the engine's long report."""
    inf = result.get('inferred') or {}
    special = str(inf.get('verdict_override') or '').strip()
    known = {'光板天','云底过低','低云遮挡过多','光线难以穿透','云量不足','低云上烧','不烧','可能少云'}
    if special in known and special != '低云上烧':
        return '预计' + special + '，朝晚霞表现有限。'
    prob = number(inf.get('regular_score'))
    quality = number(inf.get('color_score'))
    scale = number(inf.get('burn_scale_index'))
    if prob is None or quality is None or scale is None:
        return '当前预测信息尚不完整。'
    p = '高概率' if prob > 75 else ('有一定概率' if prob >= 45 else ('小概率' if prob >= 20 else '几乎不烧'))
    q = str(inf.get('color_score_label') or '').strip()
    if q not in ('平平无奇','普通','绚烂','史诗','世纪'):
        q = '世纪' if quality >= 90 else ('史诗' if quality >= 70 else ('绚烂' if quality >= 50 else ('普通' if quality >= 30 else '平平无奇')))
    grade = '大烧' if scale >= 60 else ('中烧' if scale >= 30 else ('小烧' if scale >= 10 else ('微烧' if scale > 0 else '不烧')))
    if special == '低云上烧':
        return f'可能出现低云上烧，预计{p}、{q}的{grade}。'
    return f'预计{p}出现{q}的{grade}。'


def source_time(result, fetched_at=None):
    text = str(result.get('summary') or '')
    for line in text.splitlines()[:4]:
        if line.startswith(('模型数据上线：','模型数据时间：')):
            return line.split('：',1)[-1].strip()
    return str(fetched_at or '暂不可用')


def event_time(result):
    value = result.get('event_dt')
    if not value:
        return '—'
    try:
        return datetime.fromisoformat(str(value).replace('Z','+00:00')).strftime('%H:%M')
    except (ValueError, TypeError):
        return str(value)[11:16] if len(str(value)) >= 16 else '—'


def clouds(result):
    inf = result.get('inferred') or {}
    return tuple(number(inf.get(k)) for k in ('low','mid','high'))
