# -*- coding: utf-8 -*-
"""Debug31: Offline rainbow re-evaluation from a saved case directory/ZIP.
Usage: python replay_rainbow_case.py path/to/case.zip
"""
import json,sys,zipfile,os
from datetime import datetime
from burning_sky.rainbow_geometry import evaluate

def load(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name=next((n for n in z.namelist() if n.endswith('rainbow_diagnostics.json')),None)
            if not name:raise ValueError('ZIP 缺少 rainbow_diagnostics.json')
            return json.loads(z.read(name))
    with open(os.path.join(path,'rainbow_diagnostics.json'),encoding='utf-8') as f:return json.load(f)

def replay(path):
    data=load(path)
    for direction,entry in data.items():
        rainbow=entry.get('rainbow') or {}
        raw=rainbow.get('raw_candidate_data')
        if not raw:
            print(f'{direction}: 缺少 Debug31 原始雨区和太阳光路缓存，旧案例不能完整离线重算。')
            continue
        rain=[(s['distance_km'],s['offset_deg'],s['lat'],s['lon'],s['hourly']) for s in raw['rain_sites']]
        paths=[[h['hourly'] for h in p['samples']] for p in raw['sunlight_paths']]
        origin=entry.get('origin')
        if not origin:raise ValueError('缺少 origin')
        result=evaluate(origin[0],origin[1],datetime.fromisoformat(raw['event_utc']),rain,paths)
        print(f"{direction}: 指数={result['score']}, 时间UTC={result['time']}, 候选区域={result['candidate_area_fraction_pct']}%, 不代表虹弧完整度")
        for row in result['timeline']:
            print(row['time'], '太阳高度',row['solar_altitude_deg'], '候选数',row.get('illuminated_candidate_count',0), '指数',row['score'])
if __name__=='__main__':
    if len(sys.argv)!=2:sys.exit('用法：python replay_rainbow_case.py 案例.zip')
    replay(sys.argv[1])
