"""Supplemental rainbow weather/air quality data with explicit missing-data status.

Best Match visibility and DNI are independent of the chosen cloud/precipitation model.
Air quality is a forecast proxy and is not a medical health classification.
"""
import urllib.parse
import urllib.request


def _batch_payload(coords, base, hourly, request_json, extra=None):
    params={'latitude':','.join(f'{a:.5f}' for a,b in coords),
            'longitude':','.join(f'{b:.5f}' for a,b in coords),
            'hourly':','.join(hourly),'timezone':'UTC','forecast_days':7,'past_days':2}
    if extra:params.update(extra)
    url=base+'?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'User-Agent':'BurningSkyRainbow/1.8.1'})
    payload=request_json(req,timeout=30,cache_key=url)
    locations=payload if isinstance(payload,list) else [payload]
    if len(locations)!=len(coords):raise ValueError(f'预期{len(coords)}个位置，返回{len(locations)}个')
    return [x.get('hourly',{}) for x in locations]


def fetch_environment(coords,request_json,replay=False):
    """Return exactly one environmental record per site; missing data is never imputed.

    In replay mode a missing new cache URL is reported, without making a network request.
    """
    env=[{'visibility_hourly':{},'air_quality_hourly':{}} for _ in coords]
    status={}
    tasks=[('visibility_hourly','visibility_and_dni','https://api.open-meteo.com/v1/forecast',
            ('visibility','direct_normal_irradiance'),{'wind_speed_unit':'ms'}),
           ('air_quality_hourly','pm_and_aod','https://air-quality-api.open-meteo.com/v1/air-quality',
            ('pm2_5','pm10','aerosol_optical_depth'),{})]
    for key,label,base,fields,extra in tasks:
        try:
            for start in range(0,len(coords),20):
                rows=_batch_payload(coords[start:start+20],base,fields,request_json,extra)
                for i,hourly in enumerate(rows,start):env[i][key]=hourly
            counts={f:sum(any(x is not None for x in record[key].get(f,[])) for record in env) for f in fields}
            status[label]={'status':'ok' if any(counts.values()) else 'all_null',
                           'valid_sites':counts,'site_count':len(coords),
                           'source':base,'replay':replay}
        except Exception as exc:
            status[label]={'status':'cache_unavailable' if replay else 'request_failed',
                           'error':str(exc),'source':base,'replay':replay}
    return env,status
