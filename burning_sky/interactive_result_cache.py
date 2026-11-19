"""Same-day interactive forecast cache shared by Pro and Lite.

Stores the latest complete result and its full request-response replay cache for
one location/date/model/direction. Cache entries are local-China-day scoped and
are removed as soon as a later local day is observed.
"""
from __future__ import annotations
import gzip
import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CACHE_DIR = Path(__file__).resolve().parents[1] / 'data' / 'interactive_forecast_cache'
LOCAL_TZ = ZoneInfo('Asia/Shanghai')
MODEL_CODES = {'EC': 'ECMWF IFS 0.25°', 'GFS': 'GFS Global'}


def local_now():
    return datetime.now(LOCAL_TZ)


def local_today():
    return local_now().date().isoformat()


def model_code(value):
    text = str(value or '').strip().casefold()
    if text == 'ec' or 'ecmwf ifs' in text:
        return 'EC'
    if text == 'gfs' or 'gfs global' in text:
        return 'GFS'
    return None


def identity(place, lat, lon, elevation, forecast_date, model, direction):
    code = model_code(model)
    if code not in MODEL_CODES or direction not in ('morning', 'evening'):
        raise ValueError('interactive cache identity requires EC/GFS and morning/evening')
    raw = json.dumps([
        str(place or '').strip(), round(float(lat), 5), round(float(lon), 5),
        round(float(elevation or 0), 1), str(forecast_date), code, direction,
    ], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:28]


def _path(key):
    return CACHE_DIR / (str(key) + '.json.gz')


def _read_path(path):
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as fp:
            obj = json.load(fp)
        return obj if isinstance(obj, dict) else None
    except (OSError, EOFError, ValueError, TypeError):
        return None


def prune_previous_days(today=None):
    """Delete every interactive result written on a previous China-local day."""
    today = str(today or local_today())
    if not CACHE_DIR.is_dir():
        return 0
    removed = 0
    for path in CACHE_DIR.glob('*.json.gz'):
        record = _read_path(path)
        if not record or record.get('saved_local_date') != today:
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    # Remove abandoned atomic-write files too.
    for path in CACHE_DIR.glob('*.tmp'):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, LOCAL_TZ).date().isoformat() != today:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
    return removed


def save(*, place, lat, lon, elevation, forecast_date, model, direction,
         result, request_cache, meta=None, values=None, spatial_source_log=None,
         fetched_at=None, to_safe=None):
    """Persist a successful result; failures must never call this function."""
    code = model_code(model)
    if code is None:
        raise ValueError('only EC/GFS forecasts are stored in the same-day cache')
    if not isinstance(result, dict) or not isinstance(result.get('inferred'), dict):
        raise ValueError('refuse to cache an incomplete forecast result')
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    prune_previous_days()
    key = identity(place, lat, lon, elevation, forecast_date, code, direction)
    safe = to_safe or (lambda value: value)
    now = local_now()
    record = {
        'schema_version': 1,
        'cache_key': key,
        'saved_local_date': now.date().isoformat(),
        'saved_at': now.isoformat(timespec='seconds'),
        'fetched_at': fetched_at,
        'place': str(place or ''), 'lat': float(lat), 'lon': float(lon),
        'elevation': float(elevation or 0), 'date': str(forecast_date),
        'model': code, 'model_label': MODEL_CODES[code], 'direction': direction,
        'result': safe(result),
        'request_cache': safe(request_cache or {}),
        'meta': safe(meta or {}),
        'values': safe(values or {}),
        'spatial_source_log': safe(spatial_source_log or []),
    }
    path = _path(key)
    tmp = path.with_suffix(path.suffix + f'.{os.getpid()}.{threading.get_ident()}.tmp')
    with gzip.open(tmp, 'wt', encoding='utf-8', compresslevel=5) as fp:
        json.dump(record, fp, ensure_ascii=False, separators=(',', ':'), default=str)
    os.replace(tmp, path)
    return record


def load(*, place, lat, lon, elevation, forecast_date, model, direction):
    prune_previous_days()
    try:
        key = identity(place, lat, lon, elevation, forecast_date, model, direction)
    except (TypeError, ValueError):
        return None
    record = _read_path(_path(key))
    if not record or record.get('saved_local_date') != local_today():
        return None
    if record.get('cache_key') != key:
        return None
    if not isinstance(record.get('result'), dict) or not isinstance(record['result'].get('inferred'), dict):
        return None
    return record


def load_models(*, place, lat, lon, elevation, forecast_date, direction):
    return {
        code: load(place=place, lat=lat, lon=lon, elevation=elevation,
                   forecast_date=forecast_date, model=code, direction=direction)
        for code in ('EC', 'GFS')
    }
