# -*- coding: utf-8 -*-
"""24-hour single-forecast <-> satellite-advanced cache exchange.

Only used for EC/GFS single-direction results. It never schedules or fetches data.
"""
from __future__ import annotations
import hashlib
import json
import os
import time
from pathlib import Path

CACHE_DIR=Path(__file__).resolve().parents[1]/'data'/'advanced_analysis_cache'
TTL=24*3600

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

def read(key):
    path=CACHE_DIR/(key+'.json')
    try:
        if not path.is_file() or time.time()-path.stat().st_mtime>TTL:return None
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError,TypeError):return None

def write(key,data):
    CACHE_DIR.mkdir(parents=True,exist_ok=True)
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
            'saved_at':__import__('datetime').datetime.now().isoformat(timespec='seconds'),
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
