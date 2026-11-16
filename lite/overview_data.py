"""Read-only five-day overview: no weather API, no workflow dispatch."""
from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import copy
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote
import requests

ROOT = Path(__file__).resolve().parents[1]


def china_today():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


def _repository():
    import os
    value = os.environ.get('BURNING_SKY_GITHUB_REPOSITORY') or os.environ.get('GITHUB_REPOSITORY') or ''
    if not value:
        try:
            out = subprocess.run(['git','remote','get-url','origin'],cwd=ROOT,
                                 capture_output=True,text=True,timeout=2)
            found=re.search(r'github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$',out.stdout.strip())
            if found:value=found.group(1)
        except (OSError, subprocess.TimeoutExpired):
            pass
    # Grounded fallback for this Burning_Sky repository when Cloud hides git remotes.
    return value if re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',value) else 'Lee-2727/Burning_Sky'


def file_data(filename, *, repository=None, branch=None, getter=requests.get):
    import os
    if not re.fullmatch(r'[A-Za-z0-9_\u4e00-\u9fff.-]+\.json', filename):
        raise ValueError('非法概览文件名')
    local = None
    try:
        local=json.loads((ROOT/'data'/filename).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    repo = repository if repository is not None else _repository()
    ref = branch or os.environ.get('BURNING_SKY_GITHUB_BRANCH','main')
    if repo and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):
        try:
            url=f'https://raw.githubusercontent.com/{repo}/{quote(ref,safe="")}/data/{quote(filename)}'
            response=getter(url,timeout=8,headers={'Cache-Control':'no-cache'})
            response.raise_for_status()
            data=response.json()
            if isinstance(data,dict):
                return data
        except (requests.RequestException, ValueError, OSError):
            pass
    return local


def from_sources(city, sources, *, today=None):
    """Select fresh completed forecasts or mask incomplete checkpoint entries."""
    today = today or china_today()
    city_data = sources.get('city')
    global_data=sources.get('global') or {}
    aggregate = (global_data.get('cities') or {}).get(city) if isinstance(global_data.get('cities'),dict) else None
    if aggregate is None and city=='合肥' and isinstance(global_data.get('days'),list):
        aggregate=global_data
    def stamp(record):
        if not isinstance(record,dict):return ''
        return max([str(record.get('updated_at') or '')]+[str(x) for x in (record.get('direction_updated_at') or {}).values()])
    options=[item for item in (city_data,aggregate) if isinstance(item,dict) and item.get('days')]
    selected=copy.deepcopy(max(options,key=stamp)) if options else None
    checkpoint=sources.get('checkpoint')
    if isinstance(checkpoint,dict) and checkpoint.get('place')==city:
        batch_dates=[(today+timedelta(days=i)).isoformat() for i in range(5)]
        snapshot=checkpoint.get('snapshot')
        completed=set(checkpoint.get('completed') or [])
        cp_stamp=str(checkpoint.get('saved_at') or '')
        old_stamp=str((selected or {}).get('updated_at') or '')
        if (checkpoint.get('batch_day')==today.isoformat()
                and checkpoint.get('dates')==batch_dates
                and isinstance(snapshot,dict) and snapshot.get('days') and completed
                and (not selected or cp_stamp>old_stamp)):
            selected=copy.deepcopy(snapshot)
            for d in selected.get('days',[]):
                for direction in ('morning','evening'):
                    records=d.get(direction)
                    if isinstance(records,dict):
                        for model in list(records):
                            if '|'.join((city,str(d.get('date')),model,direction)) not in completed:
                                records.pop(model,None)
            selected['updated_at']=cp_stamp
            selected['lite_checkpoint_count']=len(completed)
    dates=[(today+timedelta(days=i)).isoformat() for i in range(5)]
    stored={d.get('date'):d for d in (selected or {}).get('days',[]) if isinstance(d,dict)}
    # Never label yesterday's cached forecast as today's outlook.
    days=[copy.deepcopy(stored.get(day,{'date':day,'morning':{},'evening':{}})) for day in dates]
    return {'days':days,'updated_at':(selected or {}).get('updated_at',''),
            'has_data':any(bool(d.get(direction)) for d in days for direction in ('morning','evening')),
            'checkpoint_count':(selected or {}).get('lite_checkpoint_count')}


def get_overview(city, *, reader=file_data, today=None):
    sources={'city':reader(f'overview_{city}.json'), 'global':reader('overview.json'),
             'checkpoint':reader(f'overview_checkpoint_{city}.json')}
    return from_sources(city,sources,today=today)
