# -*- coding: utf-8 -*-
"""Standalone 18-item manual overview, run by GitHub Actions, not Streamlit."""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine
from scripts.update_overview import _new_runner, _record

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
    def automatic_priority_pending():
        """Yield before scheduled runs, including queued runs waiting for concurrency."""
        now = datetime.now(TZ)
        scheduled_minutes = (180, 540, 870, 1260)
        minute = now.hour * 60 + now.minute
        # Leave a small admission window around scheduled starts, even if Actions is delayed.
        if any(-5 <= minute - target <= 20 for target in scheduled_minutes):
            return True
        token = os.environ.get('GH_COORDINATION_TOKEN', '')
        repo = os.environ.get('GITHUB_REPOSITORY', '')
        if not token or not repo:
            return False
        req = urllib.request.Request(
            f'https://api.github.com/repos/{repo}/actions/workflows/update-overview.yml/runs?per_page=15',
            headers={'Authorization':f'Bearer {token}', 'Accept':'application/vnd.github+json'})
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                runs = json.load(response).get('workflow_runs', [])
            return any(run.get('status') in ('queued','in_progress','waiting','pending','requested') for run in runs)
        except Exception as exc:
            print(f'Coordination status unavailable: {exc}; deferring manual work conservatively', flush=True)
            return True

    limited=False
    deferred=False
    old_info, old_error = engine.messagebox.showinfo, engine.messagebox.showerror
    engine.messagebox.showinfo = lambda *args, **kwargs: None
    engine.messagebox.showerror = lambda title, msg, **kwargs: setattr(engine.messagebox, 'last_error', (str(title), str(msg)))
    for i in range(3):
        for model in models:
            for direction in ('morning','evening'):
                if automatic_priority_pending():
                    deferred=True
                    print('Manual overview yielding to scheduled update; remaining work can be resubmitted later.',flush=True)
                    break
                day=start+timedelta(days=i)
                try:
                    engine.messagebox.last_error = None
                    runner=_new_runner(a.place,day,model,coordinates=(lat,lon,elev))
                    runner.vars['lat'].set(str(lat));runner.vars['lon'].set(str(lon));runner.vars['elevation'].set(str(elev))
                    runner._fetch_weather_task(direction)
                    if engine.messagebox.last_error:
                        raise RuntimeError(engine.messagebox.last_error[1])
                    result=runner.direction_results.get(direction)
                    if not result: raise RuntimeError('预测未返回结果')
                    extracted = _record(result)  # Same validated extraction/fallback as automatic overview.
                    required = ('probability', 'color_score', 'burn_scale')
                    missing = [key for key in required if extracted.get(key) is None]
                    if missing:
                        raise RuntimeError('预测结果不完整：缺少 ' + ', '.join(missing))
                    records.append({'date':day.isoformat(),'model':model,'direction':direction,
                                    'probability':extracted['probability'],
                                    'color_score':extracted['color_score'],
                                    'burn_scale':extracted['burn_scale']})
                except Exception as exc:
                    reason=str(exc)
                    errors.append(f'{day} {model} {direction}: {reason}')
                    if '429' in reason or 'limit exceeded' in reason.lower() or '限流' in reason: limited=True
                publish('running')
                print(f'MANUAL_OVERVIEW_PROGRESS {len(records)+len(errors)}/18',flush=True)
                if limited or deferred: break
            if limited or deferred: break
        if limited or deferred: break
    engine.messagebox.showinfo, engine.messagebox.showerror = old_info, old_error
    publish('completed' if len(records)==18 and not errors else ('deferred' if deferred else 'partial'))
    print(f'OUTPUT {output} records={len(records)} errors={len(errors)}',flush=True)

if __name__=='__main__': main()
