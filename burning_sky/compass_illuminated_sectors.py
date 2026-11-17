"""OM 八方位云洞偏移与中高云加权候选优势。

朝霞仅日出前；晚霞仅日落后。二维云场只辅助方位判读，
不另设正式火烧云评分，也不宣称未经三维验证的通光。
"""
from __future__ import annotations

from datetime import timedelta
import math
import numpy as np

from burning_sky.algorithm import solar_position
from desktop.spatial_area_analysis import CloudField

HEADINGS = [('北', 0), ('东北', 45), ('东', 90), ('东南', 135),
            ('南', 180), ('西南', 225), ('西', 270), ('西北', 315)]
EARTH_KM = 6371.0


def _azimuth_and_distance(lat, lon, latitudes, longitudes):
    lat1 = math.radians(float(lat))
    lon1 = math.radians(float(lon))
    la2 = np.radians(np.asarray(latitudes, dtype=float))
    lo2 = np.radians(np.asarray(longitudes, dtype=float))
    dlon = lo2 - lon1
    dlat = la2 - lat1
    a = np.sin(dlat/2)**2 + math.cos(lat1)*np.cos(la2)*np.sin(dlon/2)**2
    distance = EARTH_KM * 2*np.arcsin(np.sqrt(np.clip(a, 0., 1.)))
    east = np.sin(dlon)*np.cos(la2)
    north = math.cos(lat1)*np.sin(la2)-math.sin(lat1)*np.cos(la2)*np.cos(dlon)
    bearing = (np.degrees(np.arctan2(east, north))+360.)%360.
    return bearing, distance


def _regional_targets(field, lat, lon, near_km=18., max_km=180., max_cells=350):
    bearings, distances = _azimuth_and_distance(lat, lon, field.positions[:,0], field.positions[:,1])
    usable = ((distances >= near_km) & (distances <= max_km)
              & np.isfinite(field.values).all(axis=1))
    ids = np.flatnonzero(usable)
    if len(ids) > max_cells:
        # Stable near-weighted downsampling; do NOT interpolate unseen clouds.
        ids = ids[np.argsort(distances[ids])[:max_cells]]
    return [{'lat':float(field.positions[i,0]),'lon':float(field.positions[i,1]),
             'distance_km':float(distances[i]),'bearing_deg':float(bearings[i]),
             'low':float(field.values[i,0]),'mid':float(field.values[i,1]),
             'high':float(field.values[i,2])} for i in ids]


def _gap_support(field, lat, lon, bearing):
    """二维云场的 200–700km 云洞候选强度；缺测不当晴空。"""
    from desktop.spatial_area_analysis import _destination
    distances=range(200,701,50)
    pts=np.array([_destination(lat,lon,float(bearing),float(d)) for d in distances])
    covers=field.lookup(pts[:,0],pts[:,1])
    if not np.isfinite(covers).all():
        return None
    clear=1.-np.clip(np.max(covers,axis=1),0.,100.)/100.
    # 全路径的连续净空候选指数。没有“某公里有一处洞就算通过”的捷径。
    return float(np.clip(.65*clear.mean()+.35*clear.min(),0.,1.))


def _angle_diff(a,b):
    return abs((float(a)-float(b)+180.)%360.-180.)


def compass_from_verified_rays(data, latitude, longitude, event_dt, direction,
                               verified_rays=None, utc_offset=8., max_cells=350):
    """太阳方位两侧的云洞偏移决定光照优势，再乘观测方位中高云量。

    朝霞严格限定日出前、晚霞严格限定日落后；反烧也复用太阳侧云洞。
    只输出候选优势，不增加/修改正式概率、色彩分、规模或严格光路。
    """
    if direction not in ('morning','evening'):
        raise ValueError('direction must be morning or evening')
    field=CloudField(data)
    targets=_regional_targets(field,latitude,longitude,max_cells=max_cells)
    # 目标中高云按观测方位聚合，避免将“云洞方向”冒充已经照亮的目标位置。
    sector_clouds=[[] for _ in HEADINGS]
    for item in targets:
        idx=int((item['bearing_deg']+22.5)//45)%8
        c=max(item['mid'],item['high'])/100.
        visibility=max(0.,1.-.55*item['low']/100.)
        sector_clouds[idx].append(c*visibility)
    cloud=[float(np.mean(x)) if x else None for x in sector_clouds]
    # 与旧主光路框架一致：日出前、日落后，绝不延伸至反侧时间窗口。
    offsets=range(-30,0,3) if direction=='morning' else range(1,31,3)
    gap_offsets=(-67.5,-45.,-30.,-15.,0.,15.,30.,45.,67.5)
    forward=[[] for _ in HEADINGS]
    reverse=[[] for _ in HEADINGS]
    scanned=0;uncovered=0
    for minute in offsets:
        _,sun_az=solar_position(event_dt+timedelta(minutes=minute),utc_offset,
                                float(latitude),float(longitude))
        holes=[]
        for shift in gap_offsets:
            hole_az=(float(sun_az)+shift)%360.
            value=_gap_support(field,latitude,longitude,hole_az)
            if value is None:
                uncovered+=1
            else:
                scanned+=1
                holes.append((hole_az,value))
        if not holes:continue
        for i,(_,bearing) in enumerate(HEADINGS):
            # 云洞偏南/北时，分配到偏南/北的摄影方向（不使用目标光路投影）。
            forward[i].append(max(value*math.exp(-.5*(_angle_diff(bearing,az)/25.)**2)
                                  for az,value in holes))
        front=[cloud[i] for i,(_,a) in enumerate(HEADINGS)
               if cloud[i] is not None and _angle_diff(a,sun_az)<=67.5]
        back=[cloud[i] for i,(_,a) in enumerate(HEADINGS)
              if cloud[i] is not None and _angle_diff(a,sun_az)>=112.5]
        # 迎日侧云较少、背日侧有云时，才在反太阳方向形成反烧候选。
        if front and back and np.mean(back)>0.10 and np.mean(front)<.65*np.mean(back):
            for i,(_,bearing) in enumerate(HEADINGS):
                if _angle_diff(bearing,sun_az)<112.5:continue
                reverse[i].append(max(value*math.exp(-.5*(_angle_diff(bearing,(az+180.)%360.)/30.)**2)
                                      for az,value in holes))
    bins=[]
    for i,(name,bearing) in enumerate(HEADINGS):
        f=float(np.mean(forward[i])) if forward[i] else 0.
        b=float(np.mean(reverse[i])) if reverse[i] else 0.
        available=bool(forward[i] or reverse[i]);c=cloud[i]
        score=(round(float(np.clip(100.*c*max(f,.8*b),0.,100.)),1)
               if c is not None and available else None)
        bins.append({'name':name,'bearing':bearing,'relative_score':score,
                     'candidate_targets':len(sector_clouds[i]),'cloud_cells':len(sector_clouds[i]),
                     'sunward_gap_support':round(f,4) if forward[i] else None,
                     'backfire_gap_support':round(b,4) if reverse[i] else None,
                     'view_type':'反烧候选' if b>f and b>0 else '正/侧向候选',
                     'evidence':'om_gap_offset_cloud_weight_candidate'})
    traces=[r for r in (verified_rays or []) if isinstance(r,dict)]
    return {'sectors':bins,'model':field.source.get('domain'),
            'run':field.source.get('reference_time'),
            'valid':field.source.get('valid_time'),'direction':direction,
            'sun_azimuth_deg':solar_position(event_dt,utc_offset,float(latitude),float(longitude))[1],
            'strict_source_rays':len(traces),'evaluated_solar_paths':scanned,
            'uncovered_solar_paths':uncovered,
            'backfire_candidate_checks':sum(bool(x) for x in reverse),
            'method':'solar_gap_offset_cloud_weight_v3',
            'note':'朝霞仅日出前、晚霞仅日落后。OM 云洞相对太阳方位偏南/偏北决定光照方位，乘对应方向中高云权重；背日侧有云时产生反烧候选。全为二维候选优势，不改正式评分，也不冒充严格受光。'}
