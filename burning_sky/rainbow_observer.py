"""Observer-anchored rainbow-cone / illuminated precipitation intersection.

Regional weather sites are sparse, so the resulting arc fraction is explicitly
sample-supported *estimated* coverage, never measured true completeness.
No extra weather requests; unknown sectors remain unknown, not 'no rainbow'.
"""
import math
from datetime import datetime
from .algorithm import solar_position

R_EARTH_KM = 6371.0


def _bearing_distance(lat1, lon1, lat2, lon2):
    a,b,c,d=map(math.radians,(lat1,lon1,lat2,lon2))
    dl=d-b
    h=math.sin((c-a)/2)**2+math.cos(a)*math.cos(c)*math.sin(dl/2)**2
    dist=R_EARTH_KM*2*math.asin(min(1.,math.sqrt(h)))
    y=math.sin(dl)*math.cos(c)
    x=math.cos(a)*math.sin(c)-math.sin(a)*math.cos(c)*math.cos(dl)
    return dist, math.degrees(math.atan2(y,x))%360


def _unit_from_az_el(az_deg,el_deg):
    a,e=map(math.radians,(az_deg,el_deg))
    return (math.cos(e)*math.sin(a),math.cos(e)*math.cos(a),math.sin(e))


def _cone_vector(anti_az, anti_alt, radius, phase):
    """Unit view vector on rainbow cone; phase=0 is highest point of bow."""
    axis=_unit_from_az_el(anti_az,anti_alt)
    east=_unit_from_az_el((anti_az+90)%360,0)
    # tangent toward zenith; orthogonal to axis
    up=(-math.sin(math.radians(anti_alt))*math.sin(math.radians(anti_az)),
        -math.sin(math.radians(anti_alt))*math.cos(math.radians(anti_az)),
         math.cos(math.radians(anti_alt)))
    r,p=map(math.radians,(radius,phase))
    return tuple(math.cos(r)*axis[i]+math.sin(r)*(math.cos(p)*up[i]+math.sin(p)*east[i]) for i in range(3))


def _dot(a,b):return sum(x*y for x,y in zip(a,b))


def evaluate_observer(result, latitude, longitude, observer_elevation_m=0., angular_step_deg=2., cell_radius_km=7.5):
    """Intersect 42/51-degree cones with sparse, sunlit rain candidate volumes.

    Rain cells are approximated as horizontal disks (cell_radius_km) and droplet
    altitude bands 0.3-2 km above local terrain. Both are uncertain assumptions.
    Unknown precipitation between samples is NOT counted as clear.
    """
    latitude=float(latitude); longitude=float(longitude)
    if not -90<=latitude<=90 or not -180<=longitude<=180:
        raise ValueError('观察点经纬度超出范围')
    step=float(angular_step_deg)
    if not 0.5<=step<=10:raise ValueError('角度步长需为0.5-10度')
    timeline=[]
    for row in result.get('timeline',[]):
        try:
            when=datetime.fromisoformat(row['time'])
            alt,sunaz=solar_position(when,0,latitude,longitude)
        except (ValueError,KeyError,TypeError):continue
        anti=(sunaz+180)%360
        sites=[]
        for site in row.get('details',[]):
            if site.get('reason')!='有雨滴证据且粗层直射通过':continue
            if site.get('latitude') is None or site.get('longitude') is None:continue
            dist,bearing=_bearing_distance(latitude,longitude,float(site['latitude']),float(site['longitude']))
            if not 0.3<=dist<=110:continue
            # Coarse vertical droplet uncertainty; elevation relative to observer.
            heights=[h-float(observer_elevation_m)/1000 for h in (0.3,1.,2.)]
            directions=[]
            for h in heights:
                el=math.degrees(math.atan2(h-dist*dist/(2*R_EARTH_KM),dist))
                directions.append(_unit_from_az_el(bearing,el))
            # This is an assumed footprint, not a resolved rain volume.
            tolerance=math.degrees(math.atan2(float(cell_radius_km),dist))
            sites.append((directions,min(12.,tolerance),site,dist,bearing))
        outcome={'time':row.get('time'),'solar_altitude_deg':round(alt,2),
                 'assessed_illuminated_sites':len(sites)}
        for name,radius in (('primary',42.),('secondary',51.)):
            n=max(1,round(360/step)); visible=[];supported=[];max_scores=[]
            for i in range(n):
                phase=i*360/n
                v=_cone_vector(anti,-alt,radius,phase)
                if v[2]<=0:continue  # below observer's geometric horizon
                visible.append(phase)
                hits=[]
                for dirs,tolerance,site,dist,bearing in sites:
                    best_error=min(math.degrees(math.acos(max(-1.,min(1.,_dot(v,d))))) for d in dirs)
                    # Approximate cell extent and height uncertainty. Do not
                    # claim that a hit proves rain droplets along the exact ray.
                    if best_error<=tolerance:
                        hits.append((float(site.get(name+'_index') or 0),dist,bearing,best_error))
                if hits:
                    supported.append(phase)
                    max_scores.append(max(h[0] for h in hits))
            ratio=100*len(supported)/len(visible) if visible else 0.
            outcome[name+'_index']=round(max(max_scores,default=0),1)
            outcome[name+'_theoretical_above_horizon_deg']=round(len(visible)*360/n,1)
            outcome[name+'_sample_supported_arc_deg']=round(len(supported)*360/n,1)
            outcome[name+'_estimated_coverage_pct']=round(ratio,1) if visible else None
            outcome[name+'_supported_phases_deg']=[round(p,1) for p in supported]
            outcome[name+'_side_hint']=('左侧及右侧' if any(p>180 for p in supported) and any(p<180 for p in supported)
                                         else '左侧' if any(p>180 for p in supported) else
                                         '右侧' if supported else '未匹配受光雨区')
        timeline.append(outcome)
    best=max(timeline,key=lambda x:(x['primary_index'],x['primary_sample_supported_arc_deg']),default=None)
    return {'latitude':latitude,'longitude':longitude,'observer_elevation_m':observer_elevation_m,
            'best':best,'timeline':timeline,
            'method':'固定观察点42°/51°彩虹锥逐角度求交，采用已确认直射的稀疏雨区候选点及假设雨区足迹/雨滴高度',
            'confidence':'低：雨区网格稀疏、垂直雨滴分布未知，无法保证实际虹弧连续性',
            'completeness':'显示的是采样支持的估计虹弧覆盖率，不是真实完整度；未覆盖角度仍可能存在彩虹',
            'extra_api_requests':0}
