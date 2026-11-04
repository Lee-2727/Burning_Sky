"""Low-resolution fixed-viewer geometry using saved regional candidate data.

No network I/O. A sample's angular proximity to the 42/51 degree cone
is only candidate evidence, not resolved arc completeness.
"""
import math
from datetime import datetime
from .algorithm import solar_position


def _bearing_distance(lat1, lon1, lat2, lon2):
    a,b,c,d=map(math.radians,(lat1,lon1,lat2,lon2))
    dl=d-b
    h=math.sin((c-a)/2)**2+math.cos(a)*math.cos(c)*math.sin(dl/2)**2
    dist=6371.0*2*math.asin(min(1.,math.sqrt(h)))
    y=math.sin(dl)*math.cos(c)
    x=math.cos(a)*math.sin(c)-math.sin(a)*math.cos(c)*math.cos(dl)
    return dist, math.degrees(math.atan2(y,x))%360


def evaluate_observer(result, latitude, longitude):
    """Analyze every available time without fetching weather.

    Returns relative opportunity indicators and *sample support*, not full-arc
    coverage. Uses a small range of plausible droplet heights.
    """
    latitude=float(latitude);longitude=float(longitude)
    if not -90<=latitude<=90 or not -180<=longitude<=180:
        raise ValueError('观察点经纬度超出范围')
    timeline=[]
    for row in result.get('timeline',[]):
        try:
            when=datetime.fromisoformat(row['time'])
            alt, sunaz=solar_position(when,0,latitude,longitude)
        except Exception:
            continue
        anti=(sunaz+180)%360
        candidates={'primary':[],'secondary':[]}
        assessed=0
        for site in row.get('details',[]):
            if site.get('reason')!='有雨滴证据且粗层直射通过':
                continue
            if site.get('latitude') is None or site.get('longitude') is None:
                continue
            distance,bearing=_bearing_distance(latitude,longitude,site['latitude'],site['longitude'])
            if distance<0.3 or distance>110:
                continue
            assessed+=1
            delta=math.radians((bearing-anti+180)%360-180)
            for height in (0.3,1.,2.):
                elevation=math.atan2(height-distance*distance/(2*6371.),distance)
                # Angle from antisolar point in local horizontal frame.
                anti_alt=math.radians(-alt)
                cosine=(math.sin(elevation)*math.sin(anti_alt)+
                        math.cos(elevation)*math.cos(anti_alt)*math.cos(delta))
                theta=math.degrees(math.acos(max(-1.,min(1.,cosine))))
                for name,radius in (('primary',42.),('secondary',51.)):
                    error=abs(theta-radius)
                    if error<=9.:
                        weight=max(0.,1-error/9.)
                        score=float(site.get(name+'_index') or 0)*weight
                        candidates[name].append({'bearing_deg':round(bearing,1),
                            'distance_km':round(distance,1),'height_km':height,
                            'angular_error_deg':round(error,1),'score':round(score,1),
                            'side':'右侧' if ((bearing-anti+180)%360-180)>0 else '左侧'})
        outcome={'time':row.get('time'),'solar_altitude_deg':round(alt,2),
                 'assessed_illuminated_sites':assessed}
        for name in ('primary','secondary'):
            matches=sorted(candidates[name],key=lambda x:x['score'],reverse=True)
            outcome[name+'_index']=round(matches[0]['score'],1) if matches else 0.
            outcome[name+'_matched_samples']=len(matches)
            outcome[name+'_best_matches']=matches[:4]
            outcome[name+'_side_hint']=('、'.join(sorted(set(m['side'] for m in matches[:8]))) if matches else '未找到匹配采样点')
        timeline.append(outcome)
    best=max(timeline,key=lambda x:(x['primary_index'],x['secondary_index']),default=None)
    return {'latitude':latitude,'longitude':longitude,'best':best,'timeline':timeline,
            'method':'已缓存受光雨区的固定观察点散射角几何匹配（42°/51°，雨滴高度0.3/1/2km）',
            'confidence':'低：20个稀疏区域点不能代表连续雨区',
            'completeness':'无法可靠给出弧长比例；未匹配采样点不等于该方向无虹',
            'extra_api_requests':0}
