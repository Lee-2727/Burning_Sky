"""Conservative rainbow candidate evaluator. Shared spherical geometry, rainbow-specific gates.

Cloud-band cover is an *unresolved obstruction proxy*, not a pressure sounding.
Indices are uncalibrated, never statistical occurrence probabilities.
"""
import math
from datetime import datetime, timedelta
from .algorithm import solar_position
from .spherical_raytrace import _ray_height_km, _earth_occluded, _cell_intersection

RAIN_DISTANCES = (10., 25., 45., 70.)
RAIN_BEARING_OFFSETS = (-40., -20., 0., 20., 40.)
SUN_DISTANCES = (0., 15., 35., 65., 100., 150.)


def _num(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def _at(hourly, key, index):
    arr=hourly.get(key) or []
    return _num(arr[index]) if 0<=index<len(arr) else None


def _hour_index(hourly, dt):
    times=hourly.get('time') or []
    try:
        indices=[(abs((datetime.fromisoformat(t)-dt).total_seconds()),i) for i,t in enumerate(times)]
        seconds,i=min(indices)
        return i if seconds<=3600 else None
    except (ValueError,TypeError):return None


def _rain_signal(hourly, index):
    """Past hour can support persistence but never substitute for present rain drops."""
    amount=_at(hourly,'precipitation',index)
    prob=_at(hourly,'precipitation_probability',index)
    humidity=_at(hourly,'relative_humidity_2m',index)
    prev=_at(hourly,'precipitation',index-1)
    if amount is None and prob is None:return None
    current=max(0.,amount or 0.)
    # Dry now: past rain alone cannot establish suspended droplets.
    if current<0.03 and (prob or 0)<35:return 0.
    wet=0.8 if current>=0.03 else (0.22 if (prev or 0)>=0.1 and (humidity or 0)>=85 else 0.)
    if wet==0:return 0.
    strength=min(1.,current/1.5) if current>=0.03 else 0.1
    return min(1.,wet*(0.4+0.6*strength)*(.65+.35*min(1.,(prob or 0)/80)))


def _light_status(cloud_hourlies, indices, alt, target_height_km=1.0):
    """First test a contiguous typical gap, then use exact spherical ray vs band cells.

    Missing coverage is unknown, not clear. Geometry uses fixed broad layer bands
    because the rainbow path has no retrieved pressure-level sounding.
    """
    if alt<=0:return '无地面直射',0.
    if len(cloud_hourlies)!=len(SUN_DISTANCES) or any(i is None for i in indices):return '光路数据不足',0.
    samples=[]
    for d,h,i in zip(SUN_DISTANCES,cloud_hourlies,indices):
        vals=[_at(h,k,i) for k in ('cloud_cover_low','cloud_cover_mid','cloud_cover_high')]
        if any(v is None for v in vals):return '光路数据不足',0.
        samples.append((d,vals))
    # A typical gap is at least 35 km with every band under 15% cloud cover.
    gap=0.;gap_end=0.
    for j in range(len(samples)-1):
        a,vs=samples[j];b,vs2=samples[j+1]
        if max(vs)<15 and max(vs2)<15:
            gap+=b-a;gap_end=b
        else:break
    if gap>=35 and gap_end>=SUN_DISTANCES[-1]:
        if not _earth_occluded(target_height_km,alt,gap_end):return '典型云洞直射（150km内）',0.9
    # Strict ray tracing: use broad band proxies with 35% cloud-cover threshold.
    bands=((0.,2.,0),(2.,6.,1),(6.,12.,2))
    blocked=0;checked=0
    for j,(d,vs) in enumerate(samples):
        left=max(0.,(samples[j-1][0]+d)/2) if j else 0.
        right=(d+samples[j+1][0])/2 if j+1<len(samples) else d
        if _earth_occluded(target_height_km,alt,right):return '地球遮挡',0.
        for bottom,top,k in bands:
            if vs[k]>=35:
                cell={'start_km':left,'end_km':right,'bottom_km':bottom,'top_km':top}
                if _cell_intersection(target_height_km,alt,cell,end_distance_km=SUN_DISTANCES[-1]):blocked+=1
            checked+=1
    if blocked:return '球面光路被云遮挡',0.
    return '球面云隙直射（150km粗层近似）',0.65


def evaluate(lat,lon,event_utc,rain_sites,light_paths):
    """rain_sites: [(distance,bearing_offset,lat,lon,hourly)]; light_paths parallel.

    Each site is evaluated at its own solar azimuth. Scan both sides of sunrise/sunset.
    """
    best=None;timeline=[]
    for minutes in range(-120,151,30):
        dt=event_utc+timedelta(minutes=minutes)
        alt,az=solar_position(dt,0,lat,lon)
        if not 0<alt<=42:
            timeline.append({'time':dt.isoformat(),'solar_altitude_deg':round(alt,2),'score':0,'status':'太阳高度角不满足地面主虹'});continue
        sector_scores=[]; details=[]
        for (dist,offset,rlat,rlon,hourly),path in zip(rain_sites,light_paths):
            idx=_hour_index(hourly,dt)
            if idx is None:continue
            rain=_rain_signal(hourly,idx)
            if rain is None or rain<=0:continue
            hlist=[h for h in path]
            indices=[_hour_index(h,dt) for h in hlist]
            status,light=_light_status(hlist,indices,alt)
            if light<=0:continue
            wind=_at(hourly,'wind_speed_10m',idx)
            visibility=_at(hourly,'visibility',idx)
            humidity=_at(hourly,'relative_humidity_2m',idx)
            wind_factor=1 if wind is None else max(.5,1-max(0,wind-3)*.055)
            vis_factor=.65 if visibility is None else max(.3,min(1.,visibility/12000))
            humid_factor=1 if humidity is None else (.85 if humidity<60 else 1.)
            # Main rainbow arc geometrically requires drops near anti-solar viewing cone;
            # this is only a coarse azimuth-sector proxy, not a solved 3D rainbow cone.
            value=rain*light*wind_factor*vis_factor*humid_factor
            sector_scores.append((offset,value))
            details.append({'distance_km':dist,'offset_deg':offset,'light':status,'rain_signal':round(rain,3),'score':round(100*value)})
        score=round(100*max((v for _,v in sector_scores),default=0))
        sectors={o for o,v in sector_scores if v>=.20}
        # Coverage proxy across five azimuth sectors; NOT physical arc completeness.
        coverage=round(100*len(sectors)/len(RAIN_BEARING_OFFSETS))
        row={'time':dt.isoformat(),'solar_altitude_deg':round(alt,2),'score':score,'sector_coverage_pct':coverage,'details':details}
        timeline.append(row)
        if best is None or score>best['score']:best=row
    if best is None:best={'score':0,'sector_coverage_pct':0,'details':[]}
    score=best['score'];label='较低' if score<20 else '偏低' if score<45 else '中等' if score<70 else '较高'
    return {'score':score,'label':label,'time':best.get('time'),
            'sector_coverage_pct':best['sector_coverage_pct'],
            'completeness_pct':None, 'rainbow_completeness':'无法可靠估算（缺少三维雨滴分布）',
            'light_method':best['details'][0]['light'] if best['details'] else '未证实有效直射',
            'timeline':timeline,'note':'基于典型云洞+球面直射光路的实验指数；云层仅有粗层云量代理，未校准为发生概率。弧段覆盖不是完整度。'}
