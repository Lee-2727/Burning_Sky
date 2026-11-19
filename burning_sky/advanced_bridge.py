# -*- coding: utf-8 -*-
"""China-local-day single-forecast <-> satellite-advanced cache exchange.

Only used for EC/GFS single-direction results. It never schedules or fetches data.
"""
from __future__ import annotations
import hashlib
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

CACHE_DIR=Path(__file__).resolve().parents[1]/'data'/'advanced_analysis_cache'
LOCAL_TZ=ZoneInfo('Asia/Shanghai')

def _today(): return datetime.now(LOCAL_TZ).date().isoformat()
def _stamp_day(value, fallback_path):
    try:
        dt=datetime.fromisoformat(str(value))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=ZoneInfo('UTC'))
        return dt.astimezone(LOCAL_TZ).date().isoformat()
    except (ValueError,TypeError):
        return datetime.fromtimestamp(fallback_path.stat().st_mtime,LOCAL_TZ).date().isoformat()

def identity(config):
    payload=json.dumps([config['place'],round(float(config['lat']),5),
        round(float(config['lon']),5),round(float(config['elev']),1),
        config['date'],config['model'],config['direction']],ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:26]

def config_from_main(meta,direction):
    label=str(meta.get('model') or '').casefold()
    if 'gfs' in label:model='GFS'
    elif 'ecmwf ifs' in label or label.startswith('ec '):model='EC'
    else:return None
    if direction not in ('morning','evening'):return None
    return {'place':str(meta.get('place') or ''),
            'lat':float(meta['lat']),'lon':float(meta['lon']),
            'elev':float(meta.get('elevation') or 0),
            'date':str(meta['date']),'model':model,'direction':direction}

def _prune():
    if not CACHE_DIR.is_dir(): return
    today=_today()
    for path in list(CACHE_DIR.glob('*.json'))+list(CACHE_DIR.glob('*.jpg')):
        try:
            if path.suffix=='.json':
                obj=json.loads(path.read_text(encoding='utf-8'))
                stamp=str(obj.get('saved_at') or obj.get('created_at') or '')
                day=_stamp_day(stamp,path)
            else:
                sibling=path.with_suffix('.json')
                if sibling.exists():
                    obj=json.loads(sibling.read_text(encoding='utf-8'))
                    stamp=str(obj.get('saved_at') or obj.get('created_at') or '')
                    day=_stamp_day(stamp,path)
                else: day=datetime.fromtimestamp(path.stat().st_mtime,LOCAL_TZ).date().isoformat()
            if day!=today: path.unlink(missing_ok=True)
        except (OSError,ValueError,TypeError):
            try: path.unlink(missing_ok=True)
            except OSError: pass

def prune_daily_cache():
    _prune()


def read(key):
    _prune()
    path=CACHE_DIR/(key+'.json')
    try:
        if not path.is_file():return None
        obj=json.loads(path.read_text(encoding='utf-8'))
        stamp=str(obj.get('saved_at') or obj.get('created_at') or '')
        if stamp and _stamp_day(stamp,path)!=_today(): return None
        return obj
    except (OSError,ValueError,TypeError):return None

def write(key,data):
    CACHE_DIR.mkdir(parents=True,exist_ok=True)
    _prune()
    path=CACHE_DIR/(key+'.json')
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,default=str),encoding='utf-8')
    os.replace(tmp,path)

def same_config(a,b):
    try:return identity(a)==identity(b)
    except (KeyError,ValueError,TypeError):return False

def prepare_handoff(meta,direction,original_results,request_cache,to_safe):
    config=config_from_main(meta,direction)
    if not config:return None
    result=(original_results or {}).get(direction)
    if not isinstance(result,dict) or not result.get('inferred'):return None
    key=identity(config)
    prior=read(key+'_base')
    # Preserve the original starting point, even if the user revisits after calibration.
    # A fresh query replaces it only if its underlying results differ.
    safe_result=to_safe(result)
    safe_cache=to_safe(request_cache or {})
    record={'config':config,'result':safe_result,'request_cache':safe_cache,
            'saved_at':datetime.now(LOCAL_TZ).isoformat(timespec='seconds'),
            'source':'single_forecast_handoff'}
    if not prior or prior.get('result')!=safe_result:
        write(key+'_base',record)
        # Invalidate stale corrections if baseline forecast has changed.
        if prior is not None:
            for suffix in ('_corrected','_pending_satellite','_partial_cache'):
                path=CACHE_DIR/(key+suffix+'.json')
                try:path.unlink(missing_ok=True)
                except OSError:pass
    elif safe_cache and len(safe_cache)>len(prior.get('request_cache') or {}):
        prior['request_cache'].update(safe_cache)
        write(key+'_base',prior)
    return config
