# -*- coding: utf-8 -*-
"""Standalone 18-item manual overview, run by GitHub Actions, not Streamlit."""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine
from scripts.update_overview import _new_runner

TZ = ZoneInfo('Asia/Shanghai')

def main():
    p = argparse.ArgumentParser()
    for key in ('place','lat','lon','elev','date','request_id'):
        p.add_argument('--'+key, required=True)
    a = p.parse_args()
    if not (1 <= len(a.place) <= 80 and len(a.request_id) == 24 and all(c in '0123456789abcdef' for c in a.request_id)):
        raise ValueError('invalid place or request_id')
    lat, lon, elev = float(a.lat), float(a.lon), float(a.elev)
    if not (-90<=lat<=90 and -180<=lon<=180 and -500<=elev<=9000): raise ValueError('coordinates out of range')
    start = date.fromisoformat(a.date)
    models = ['ECMWF IFS 0.25°', 'GFS Global', 'ICON Global']
    identity = {'place':a.place,'lat':round(lat,5),'lon':round(lon,5),'elev':round(elev,1),'date':start.isoformat(),'models':models}
    expected = hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
    if expected != a.request_id: raise ValueError('request_id does not match identity')
    records, errors = [], []
    output = ROOT / 'data' / 'manual_overview_runs' / (a.request_id + '.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    def publish(state):
        payload={'identity':identity,'obtained_at':datetime.now(TZ).isoformat(timespec='seconds'),
                 'records':records,'errors':errors,'completed':len(records)+len(errors),
                 'total':18,'state':state,'request_id':a.request_id}
        tmp=output.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        tmp.replace(output)
    publish('running')
    limited=False
    for i in range(3):
        for model in models:
            for direction in ('morning','evening'):
                day=start+timedelta(days=i)
                try:
                    runner=_new_runner(a.place,day,model)
                    runner.vars['lat'].set(str(lat));runner.vars['lon'].set(str(lon));runner.vars['elevation'].set(str(elev))
                    runner._fetch_weather_task(direction)
                    result=runner.direction_results.get(direction)
                    if not result: raise RuntimeError('预测未返回结果')
                    inf=result.get('inferred') or {}
                    if any(not isinstance(inf.get(k), (int, float)) for k in ('regular_score', 'color_score', 'burn_scale_index')):
                        raise RuntimeError('预测结果不完整：概率、色彩分或火烧云规模缺失')
                    records.append({'date':day.isoformat(),'model':model,'direction':direction,
                                    'probability':inf.get('regular_score'), 'color_score':inf.get('color_score'),
                                    'burn_scale':inf.get('burn_scale_index')})
                except Exception as exc:
                    reason=str(exc)
                    errors.append(f'{day} {model} {direction}: {reason}')
                    if '429' in reason or 'limit exceeded' in reason.lower() or '限流' in reason: limited=True
                publish('running')
                print(f'MANUAL_OVERVIEW_PROGRESS {len(records)+len(errors)}/18',flush=True)
                if limited: break
            if limited: break
        if limited: break
    publish('completed' if len(records)==18 and not errors else 'partial')
    print(f'OUTPUT {output} records={len(records)} errors={len(errors)}',flush=True)

if __name__=='__main__': main()
