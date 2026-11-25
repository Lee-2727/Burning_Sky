from __future__ import annotations
import sys
from pathlib import Path
from datetime import date, timedelta, datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from burning_sky.overview_partial_publish import prepare_publication
from burning_sky.overview_snapshot_guard import count_valid, expected_dates
from burning_sky.overview_status_ui import status_view
from lite.overview_data import from_sources

MODELS = ['ECMWF IFS 0.25°', 'GFS Global']


def make_inputs(old_failed=False, no_old_failed=False):
    today = date(2026, 11, 18)
    dates = [today + timedelta(days=i) for i in range(5)]
    days = []
    complete = set()
    failed_date = dates[-1].isoformat()
    failed_key = '|'.join(('合肥', failed_date, MODELS[0], 'morning'))
    for day in dates:
        groups = {'morning': {}, 'evening': {}}
        for direction in ('morning', 'evening'):
            for model in MODELS:
                key = '|'.join(('合肥', day.isoformat(), model, direction))
                if key != failed_key:
                    groups[direction][model] = {'probability': 42, 'color_score': 50, 'burn_scale': 18}
                    complete.add(key)
                elif old_failed and not no_old_failed:
                    groups[direction][model] = {'probability': 15, 'color_score': 31, 'burn_scale': 8}
        days.append({'date': day.isoformat(), **groups})
    snapshots = {'合肥': {'days': days, 'updated_at': '2026-11-18 01:00:00 CST',
                          'direction_updated_at': {'morning': '2026-11-18 01:00:00 CST'}}}
    failures = [{'city': '合肥', 'date': failed_date, 'model': MODELS[0], 'direction': 'morning',
                 'error': 'incomplete color'}]
    return dates, snapshots, complete, failures


def test_partial_19_out_of_20_is_published_and_missing_stays_unknown():
    dates, old, done, failures = make_inputs()
    result, info = prepare_publication(old, dates, MODELS, done, failures,
                                       '2026-11-18 09:10:00 CST', '2026-11-18')
    assert info['current_valid'] == 19
    assert info['visible_valid'] == 19
    assert len(info['missing']) == 1
    assert count_valid(result['合肥'], expected_dates(dates[0])) == 19
    assert result['合肥']['publication']['state'] == 'partial'
    assert result['合肥']['days'][-1]['morning'].get(MODELS[0]) is None
    assert old['合肥']['updated_at'] == '2026-11-18 01:00:00 CST'  # no mutation


def test_partial_preserves_matching_old_valid_record_but_does_not_claim_fresh():
    dates, old, done, failures = make_inputs(old_failed=True)
    result, info = prepare_publication(old, dates, MODELS, done, failures,
                                       '2026-11-18 09:10:00 CST', '2026-11-18')
    assert info['current_valid'] == 19
    assert info['visible_valid'] == 20
    assert len(info['stale']) == 1
    stale_row = result['合肥']['days'][-1]['morning'][MODELS[0]]
    assert stale_row == {'probability': 15, 'color_score': 31, 'burn_scale': 8}
    status = {'state':'partial','partial_published':True,'task_total':20,'success_count':19,
              'retained_old_count':1,'missing_count':0}
    kind, msg = status_view(status, result['合肥'], now=datetime(2026,11,18,10,tzinfo=timezone(timedelta(hours=8))))
    assert kind == 'warning'
    assert '19/20' in msg


def test_lite_shows_partial_publication_metadata():
    dates, old, done, failures = make_inputs(old_failed=True)
    result, info = prepare_publication(old, dates, MODELS, done, failures,
                                       '2026-11-18 09:10:00 CST', '2026-11-18')
    view = from_sources('合肥', {'city': result['合肥']}, today=dates[0])
    assert view['valid_count'] == 20
    assert view['publication']['state'] == 'partial'
    assert view['publication']['valid_in_batch'] == 19
    assert len(view['publication']['retained_old_keys']) == 1


def test_full_batch_has_no_partial_warning():
    dates, old, done, failures = make_inputs(old_failed=True)
    failed = failures[0]
    key = '|'.join((failed['city'], failed['date'], failed['model'], failed['direction']))
    done.add(key)
    result, info = prepare_publication(old, dates, MODELS, done, [],
                                       '2026-11-18 09:10:00 CST', '2026-11-18')
    assert info['current_valid'] == 20
    assert result['合肥']['publication']['state'] == 'completed'


def test_real_updater_main_writes_partial_public_files_then_resume_only_one(monkeypatch, tmp_path):
    """Offline integration: 19 valid tasks publish; resume retries just the 20th."""
    import importlib
    import os
    sys.path.insert(0, str(ROOT / 'scripts'))
    updater = importlib.import_module('update_overview')
    root = tmp_path
    (root / 'data').mkdir()
    monkeypatch.setattr(updater, 'ROOT', root)
    monkeypatch.setattr(updater, 'CHECKPOINT', root / 'data' / 'overview_checkpoint_合肥.json')
    monkeypatch.setattr(updater, 'OUTPUT', root / 'data' / 'overview_合肥.json')
    monkeypatch.setattr(updater, 'CITY_CONFIG', {'合肥': (31.8, 117.2, 20.0)})
    monkeypatch.setattr(updater.engine, 'WEATHER_MODEL_CATALOG',
                        [{'label': x} for x in MODELS])
    monkeypatch.setattr(updater, '_check_scheduled_rate', lambda: None)
    publishes = []
    def stub_publish(status, include_results=False):
        if include_results:
            import json
            record = root/'data'/'overview_合肥.json'
            publishes.append((status['state'],
                              json.loads(record.read_text())['publication']['valid_in_batch']))
        return True
    monkeypatch.setattr(updater, '_publish_files', stub_publish)
    monkeypatch.setattr(updater.time, 'sleep', lambda s: None)
    monkeypatch.setenv('BURNING_SKY_OVERVIEW_RUN_MODE', 'fresh')
    monkeypatch.setenv('GITHUB_ACTIONS', 'false')
    fail = {'enabled': True}
    queries = []
    class Runner:
        def __init__(self, city, day, label):
            self.direction_results = {}
            self.day, self.label = day, label
        def _fetch_weather_task(self, direction, **kwargs):
            key = (self.day, self.label, direction)
            queries.append(key)
            if fail['enabled'] and self.label == MODELS[0] and direction == 'morning' and self.day == (min(d for d, _, _ in queries) + timedelta(days=4)):
                self.direction_results[direction] = {'inferred': {'regular_score': 12}}
            else:
                self.direction_results[direction] = {'inferred': {'regular_score': 54, 'color_score': 44, 'burn_scale_index': 30}}
    monkeypatch.setattr(updater, '_new_runner', lambda city, day, label: Runner(city, day, label))
    updater.main()
    import json
    status = json.loads((root/'data'/'overview_status.json').read_text())
    overview = json.loads((root/'data'/'overview_合肥.json').read_text())
    assert status['state'] == 'partial'
    assert status['success_count'] == 19
    assert status['partial_published'] is True
    assert status['missing_count'] == 1
    assert len(overview['publication']['missing_keys']) == 1
    assert len(queries) == 20  # incomplete metrics do not trigger repeated API calls
    old_queries = len(queries)
    fail['enabled'] = False
    monkeypatch.setenv('BURNING_SKY_OVERVIEW_RUN_MODE', 'resume')
    updater.main()
    overview2 = json.loads((root/'data'/'overview_合肥.json').read_text())
    status2 = json.loads((root/'data'/'overview_status.json').read_text())
    assert len(queries) - old_queries == 1
    assert status2['state'] == 'completed'
    assert status2['success_count'] == 20
    assert overview2['publication']['state'] == 'completed'
    assert publishes == [('partial', 19), ('partial', 19), ('completed', 20)]
