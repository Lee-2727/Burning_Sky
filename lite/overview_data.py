"""Read-only Lite overview, with the same non-destructive publication rules as Pro.

No weather API and no workflow dispatch. The published full snapshot is authoritative;
a same-day partial checkpoint can only fill genuinely absent completed records.
"""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
import copy
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import quote
import requests
from burning_sky.overview_snapshot_guard import (
    MODELS, DIRECTIONS, count_valid, expected_dates, restore_missing_only, valid_record,
)

ROOT = Path(__file__).resolve().parents[1]


def china_today():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


def _repository():
    import os
    value = os.environ.get('BURNING_SKY_GITHUB_REPOSITORY') or os.environ.get('GITHUB_REPOSITORY') or ''
    if not value:
        try:
            out = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=ROOT,
                                 capture_output=True, text=True, timeout=2)
            found = re.search(r'github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$', out.stdout.strip())
            if found:
                value = found.group(1)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return value if re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value) else 'Lee-2727/Burning_Sky'


def file_data(filename, *, repository=None, branch=None, getter=requests.get):
    """Prefer freshly published GitHub JSON; fall back to bundled local JSON."""
    import os
    if not re.fullmatch(r'[A-Za-z0-9_\u4e00-\u9fff.-]+\.json', filename):
        raise ValueError('非法概览文件名')
    local = None
    try:
        local = json.loads((ROOT / 'data' / filename).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    repo = repository if repository is not None else _repository()
    ref = branch or os.environ.get('BURNING_SKY_GITHUB_BRANCH', 'main')
    if repo and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        try:
            # raw.githubusercontent.com can be cached separately from Streamlit's
            # TTL. A minute-bucket query parameter prevents indefinite stale CDN
            # responses while retaining cache benefits during a burst of users.
            bucket = int(time.time() // 60)
            url = (f'https://raw.githubusercontent.com/{repo}/{quote(ref,safe="")}/data/'
                   f'{quote(filename)}?_={bucket}')
            response = getter(url, timeout=8, headers={'Cache-Control': 'no-cache'})
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data
        except (requests.RequestException, ValueError, OSError):
            pass
    return local


def _stamp(record):
    if not isinstance(record, dict):
        return ''
    times = record.get('direction_updated_at') or {}
    stamps = [str(record.get('updated_at') or '')]
    if isinstance(times, dict):
        stamps.extend(str(x or '') for x in times.values())
    # Compare standardized date/time text, not ISO 'T' versus ' ' sorting.
    return max((s.replace('T', ' ').replace(' CST', '').replace('+08:00', '')
                for s in stamps), default='')


def _from_global(city, global_data):
    if not isinstance(global_data, dict):
        return None
    cities = global_data.get('cities') or {}
    aggregate = cities.get(city) if isinstance(cities, dict) else None
    if aggregate is None and city == '合肥' and isinstance(global_data.get('days'), list):
        aggregate = global_data
    return aggregate


def from_sources(city, sources, *, today=None):
    """Merge complete publications and fill absent rows from a verified checkpoint.

    Never replace a successful model/day/direction entry with a newer but
    incomplete checkpoint. Preserve actual 0 values as valid forecasts.
    """
    today = today or china_today()
    dates = expected_dates(today)
    city_data = sources.get('city')
    aggregate = _from_global(city, sources.get('global'))
    candidates = [(name, obj) for name, obj in (('城市独立文件', city_data), ('总览文件', aggregate))
                  if isinstance(obj, dict) and isinstance(obj.get('days'), list)]
    # Publication completeness is more important than its timestamp.
    source_name, selected = max(candidates,
                                key=lambda t: (count_valid(t[1], dates), _stamp(t[1]))) \
        if candidates else ('暂无发布数据', None)
    selected = copy.deepcopy(selected) if selected is not None else {'place': city, 'days': []}
    by_date = {str(day.get('date')): day for day in selected.get('days', [])
               if isinstance(day, dict)}
    # Independent city / aggregate files may each contain a different subset.
    # Fill missing entries regardless of source order; never overwrite a valid one.
    for _name, obj in candidates:
        for day in obj.get('days', []):
            if not isinstance(day, dict):
                continue
            date_string = str(day.get('date'))
            if date_string not in dates:
                continue
            target = by_date.setdefault(date_string, {'date': date_string, 'morning': {}, 'evening': {}})
            for direction in DIRECTIONS:
                incoming = day.get(direction) or {}
                if not isinstance(incoming, dict):
                    continue
                group = target.setdefault(direction, {})
                if not isinstance(group, dict):
                    group = {}
                    target[direction] = group
                for model in MODELS:
                    if not valid_record(group.get(model)) and valid_record(incoming.get(model)):
                        group[model] = copy.deepcopy(incoming[model])
    selected['days'] = [by_date.setdefault(date_string, {'date':date_string,'morning':{},'evening':{}})
                        for date_string in dates]
    checkpoint_count = None
    checkpoint_filled = 0
    checkpoint = sources.get('checkpoint')
    if (count_valid(selected, dates) < 20 and isinstance(checkpoint, dict)
            and checkpoint.get('place') == city
            and checkpoint.get('batch_day') == today.isoformat()
            and checkpoint.get('dates') == dates
            and (_stamp(checkpoint.get('snapshot') or {}) >= _stamp(selected)
                 or _stamp({'updated_at': checkpoint.get('saved_at')}) >= _stamp(selected))):
        completed = checkpoint.get('completed') or []
        snapshot = checkpoint.get('snapshot')
        if isinstance(snapshot, dict) and isinstance(snapshot.get('days'), list) and completed:
            selected, checkpoint_filled = restore_missing_only(
                selected, snapshot, dates, completed, city)
            checkpoint_count = len(completed)
    selected['days'] = sorted(selected['days'], key=lambda x: x['date'])
    valid_total = count_valid(selected, dates)
    per_model = {model: sum(valid_record(((day.get(direction) or {}).get(model)))
                            for day in selected['days'] for direction in DIRECTIONS)
                 for model in MODELS}
    return {
        'days': selected['days'], 'updated_at': selected.get('updated_at', ''),
        'has_data': valid_total > 0, 'valid_count': valid_total, 'total_count':20,
        'model_counts': per_model, 'checkpoint_count': checkpoint_count,
        'checkpoint_filled': checkpoint_filled, 'source_name': source_name,
    }


def get_overview(city, *, reader=file_data, today=None):
    today = today or china_today()
    dates = expected_dates(today)
    city_data = reader(f'overview_{city}.json')
    # Avoid two unnecessary HTTP requests when a complete 20/20 city file exists.
    if count_valid(city_data, dates) == 20:
        return from_sources(city, {'city': city_data}, today=today)
    global_data = reader('overview.json')
    tentative = from_sources(city, {'city': city_data, 'global': global_data}, today=today)
    if tentative['valid_count'] == 20:
        return tentative
    checkpoint = reader(f'overview_checkpoint_{city}.json')
    return from_sources(city, {'city': city_data, 'global': global_data,
                               'checkpoint': checkpoint}, today=today)
