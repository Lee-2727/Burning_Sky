# -*- coding: utf-8 -*-
"""Six observer-view sky sectors, with explicit coverage/uncertainty semantics.

All six sectors are *view angles*, not six points on the ground.
This diagnostic requires meteorology at three bearings; it does not synthesize
missing side data from the centre-line. Cloud cover is a fractional proxy.
"""
from __future__ import annotations
import math
from datetime import datetime, timedelta
from .algorithm import solar_position
from .spherical_raytrace import destination, EARTH_RADIUS_KM, profile_cloud_cells

AZ_BINS = [(-60,-20),(-20,20),(20,60)]
EL_BINS = [(45,90),(0,45)]

def apparent_elevation(distance_km, height_km, observer_km=0.0):
    if distance_km <= 0: return 90.0 if height_km > observer_km else 0.0
    theta=distance_km/EARTH_RADIUS_KM
    r=EARTH_RADIUS_KM+height_km; r0=EARTH_RADIUS_KM+observer_km
    return math.degrees(math.atan2(r*math.cos(theta)-r0,r*math.sin(theta)))

def target_ray_height(target_height_km, solar_alt_deg, upstream_km):
    theta=upstream_km/EARTH_RADIUS_KM
    a=math.radians(solar_alt_deg)
    den=math.cos(theta+a)
    if den<=0:return float('inf')
    return (EARTH_RADIUS_KM+target_height_km)*math.cos(a)/den-EARTH_RADIUS_KM

def _intersects(target_h, sun_alt, cell, target_d, max_d):
    a=max(target_d+0.05,float(cell['start_km']))
    b=min(max_d,float(cell['end_km']))
    if b<=a:return False
    # Ray is parameterized by distance from target, not from observer.
    local_a=a-target_d;local_b=b-target_d
    critical=max(local_a,min(local_b,-math.radians(sun_alt)*EARTH_RADIUS_KM))
    hs=[target_ray_height(target_h,sun_alt,v) for v in (local_a,local_b,critical)]
    return min(hs)<=cell['top_km'] and max(hs)>=cell['bottom_km']

def _sun_ray_earth_occluded(target_height_km, solar_alt_deg):
    """Exact Earth occultation along the COMPLETE straight ray toward the Sun.

    Unlike a sampled-cloud end-height gate, this is an actual intersection
    with the spherical Earth: for a below-horizon solar direction, the
    outward ray's impact parameter is (R+h)*cos(altitude). No cloud-height
    prediction or arbitrary source-distance cutoff is involved.
    """
    alt=math.radians(float(solar_alt_deg))
    if alt>=0.0:
        return False
    return ((EARTH_RADIUS_KM+float(target_height_km))*math.cos(alt)
            < EARTH_RADIUS_KM-1e-8)


def _earth_blocked(target_h, sun_alt, max_upstream_km):
    if sun_alt>=0:return False
    closest=min(max_upstream_km,-math.radians(sun_alt)*EARTH_RADIUS_KM)
    return target_ray_height(target_h,sun_alt,closest)<0

def multi_target_profile(samples, profiles, event_dt, utc_offset, origin_lat, origin_lon,
                         direction='morning', target_limit_km=100.0, step_minutes=1):
    """Check multiple targets and cloud heights along the original solar-axis profile.

    The source profiles are a 1-D section. A clear ray is only verified within
    sampled profile coverage, and is not an assertion of full solar visibility.
    """
    cells=profile_cloud_cells(profiles)
    valid=sorted((p for p in profiles if p.get('levels') and isinstance(p.get('distance_km'),(int,float))),
                 key=lambda p:p['distance_km'])
    if not valid:return {'targets':[],'times':[],'clear_count':0,'note':'缺少气压层剖面'}
    end=max(float(p['distance_km']) for p in valid)
    target_limit=min(max(0.,float(target_limit_km)),end)
    # Use each available profile's actual cloud heights; do not invent 4/8 km targets.
    targets=[]
    for p in valid:
        d=float(p['distance_km'])
        if d>target_limit+1e-6:continue
        cloudy=[lv for lv in p['levels'] if isinstance(lv.get('height_agl_km'),(int,float))
                and isinstance(lv.get('cloud_cover'),(int,float)) and lv['cloud_cover']>=25
                and float(lv['height_agl_km'])>=1.5]
        for lv in cloudy:
            h=float(lv['height_agl_km'])
            # Cap the number of levels per target; retain distinct heights.
            if targets and len(targets)>500:break
            targets.append({'distance_km':d,'height_km':h,'cover':float(lv['cloud_cover']),
                            'elevation_deg':apparent_elevation(d,h),
                            'latitude':p.get('lat'),'longitude':p.get('lon')})
    minutes=range(-30,1,step_minutes) if direction=='morning' else range(0,31,step_minutes)
    times=[event_dt+timedelta(minutes=m) for m in minutes]
    for target in targets:
        d=target['distance_km'];h=target['height_km']
        lat=target['latitude'];lon=target['longitude']
        if not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)):
            lat,lon=destination(origin_lat,origin_lon,0,d)
        flags=[]
        for dt in times:
            alt,_=solar_position(dt,utc_offset,lat,lon)
            if _earth_blocked(h,alt,end-d):flags.append(False);continue
            # Exclude target's own cell, test only the actual upstream segment.
            blocked=any(_intersects(h,alt,c,d,end) for c in cells
                        if abs(float(c['sample_distance_km'])-d)>1e-6)
            flags.append(not blocked)
        target['illuminated']=flags
        target['any_illuminated']=any(flags)
    return {'targets':targets,'times':[t.isoformat() for t in times],
            'clear_count':sum(t['any_illuminated'] for t in targets),
            'target_limit_km':target_limit,'profile_end_km':end,
            'note':'一维多目标气压层光路；仅验证已有剖面范围，不代表完整三维遮挡或太阳方向以外的云。'}



def adaptive_profile_diagnostic(profiles, event_dt, utc_offset, origin_lat, origin_lon,
                                direction='morning', target_limit_km=100.,
                                blur_horizontal_km=10., blur_vertical_km=.2):
    """Offline sensitivity study, not calibrated probability or a 3-D cloud reconstruction.

    Solar angles are derived from real times; adaptive subdivision does not extrapolate
    outside the dawn/dusk window. Cloud cover is a fractional occupancy proxy.
    """
    from .spherical_raytrace import _ray_height_km
    import bisect
    valid=sorted((p for p in profiles if p.get('levels') and isinstance(p.get('distance_km'),(int,float))),
                 key=lambda p:float(p['distance_km']))
    if not valid:return {'error':'缺少气压剖面'}
    end=float(valid[-1]['distance_km'])
    targets=[]
    for p in valid:
        d=float(p['distance_km'])
        if d>target_limit_km:continue
        for lv in p['levels']:
            c=lv.get('cloud_cover');h=lv.get('height_agl_km')
            if isinstance(c,(int,float)) and c>=25 and isinstance(h,(int,float)) and h>=1.5:
                targets.append((d,float(h),float(c)))
    # Select distinct representative targets, not arbitrary synthetic cloud heights.
    targets=sorted(targets,key=lambda x:(x[0],-x[2]))[:12]
    raw=[]
    for p in valid:
        d=float(p['distance_km'])
        for lv in p['levels']:
            h=lv.get('height_agl_km');c=lv.get('cloud_cover')
            if isinstance(h,(int,float)) and isinstance(c,(int,float)):
                raw.append((d,float(h),max(0.,min(100.,float(c)))))
    def cover_at(d,h,sx,sz):
        # Gaussian kernel regression on sampled pressure levels, with compact support.
        # This is an experiment, not evidence of a physical cloud boundary.
        total=weight=0.
        for xd,xh,c in raw:
            if abs(xd-d)>3*sx or abs(xh-h)>3*sz:continue
            w=math.exp(-.5*((xd-d)/sx)**2-.5*((xh-h)/sz)**2)
            total+=w*c;weight+=w
        return total/weight if weight>1e-7 else None
    # Avoid repeated O(N^2) regression in angular search: precompute cloud field.
    field={}
    for mode,sx,sz in [('raw',0,0),('blur_10km_200m',blur_horizontal_km,blur_vertical_km),
                       ('blur_20km_400m',2*blur_horizontal_km,2*blur_vertical_km)]:
        cells=[]
        for p in valid:
            d=float(p['distance_km'])
            for lv in p['levels']:
                h=lv.get('height_agl_km');c=lv.get('cloud_cover')
                if not isinstance(h,(int,float)) or not isinstance(c,(int,float)):continue
                cc=float(c) if mode=='raw' else cover_at(d,float(h),sx,sz)
                if cc is not None:cells.append((d,float(h),max(0.,min(100.,cc))))
        field[mode]=cells
    def evaluate(target,minute,mode):
        td,th,_=target
        dt=event_dt+timedelta(minutes=minute)
        # Target position's solar altitude, not observer's.
        lat,lon=destination(origin_lat,origin_lon,solar_position(dt,utc_offset,origin_lat,origin_lon)[1],td)
        alt,_=solar_position(dt,utc_offset,lat,lon)
        if _earth_blocked(th,alt,end-td):return {'state':'earth_blocked','risk':1.,'solar_alt':alt}
        # A finite target cloud has an uncertain edge. Exclude the first 1 km upstream
        # rather than letting the target's own pressure cell self-occlude.
        path=[(d,h,c) for d,h,c in field[mode] if d>td+1. and d<=end]
        # Profile levels represent occupancy, not solid walls. A height proximity
        # weighting avoids treating cloud at 2 km as blocking a ray at 8 km.
        opacity=0.;strong=[]
        for d,h,c in path:
            ray=target_ray_height(th,alt,d-td)
            delta=abs(ray-h)
            vertical_sigma=.25 if mode=='raw' else (.3 if mode=='blur_10km_200m' else .45)
            if delta>3*vertical_sigma:continue
            weight=math.exp(-.5*(delta/vertical_sigma)**2)
            # Correlated neighbouring pressure levels: use strongest evidence per
            # 25 km horizontal bin, avoiding multiplication of overlapping levels.
            strong.append((int(d//25),c/100*weight,d,h))
        bins={}
        for b,r,d,h in strong:
            if b not in bins or r>bins[b][0]:bins[b]=(r,d,h)
        # Heuristic cumulative attenuation, NOT physical optical depth/probability.
        risk=1.-math.prod(1.-min(.95,v[0]) for v in bins.values())
        return {'state':'candidate' if risk<.7 else 'high_obstruction_risk',
                'risk':round(risk,4),'solar_alt':round(alt,4),
                'dominant_intersections':sorted([{'distance_km':round(v[1],1),
                  'height_km':round(v[2],2),'occupancy_risk':round(v[0],3)}
                  for v in bins.values()],key=lambda q:-q['occupancy_risk'])[:3]}
    result=[]
    for target in targets:
        # Coarse time grid then bisect around state transitions; also sample midpoints
        # of blocked/blocked intervals to catch isolated narrow candidate windows.
        times=list(range(-30,0,3)) if direction=='morning' else list(range(1,31,3))
        records={}
        def at(t):
            k=round(t,5)
            if k not in records:records[k]={mode:evaluate(target,t,mode) for mode in field}
            return records[k]
        for t in times:at(t)
        def refine(a,b,depth):
            if depth>=7 or b-a<=.047:return
            mid=(a+b)/2
            ra=at(a)['raw']['state'];rb=at(b)['raw']['state'];rm=at(mid)['raw']['state']
            if ra!=rb or ra!=rm or rm!=rb or depth<2:
                refine(a,mid,depth+1);refine(mid,b,depth+1)
        for a,b in zip(times,times[1:]):refine(a,b,0)
        rows=[{'minute_offset':t,**records[t]} for t in sorted(records)]
        result.append({'target_distance_km':target[0],'target_height_km':target[1],
                       'target_cloud_cover_pct':target[2], 'scan':rows,
                       'candidate_counts':{m:sum(r[m]['state']=='candidate' for r in rows) for m in field}})
    return {'target_count':len(result),'targets':result,'profile_end_km':end,
            'method':'离线气压层经验风险与自适应时间细分；仅对已采样的一维方向有效',
            'warning':'risk为未校准经验指标；不能据此宣称真实云隙或统计透光概率。'}

def _great_circle_km(lat1, lon1, lat2, lon2):
    """Surface separation between projected sunlight and the sampled section."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2-p1
    dl = math.radians((lon2-lon1+180.0)%360.0-180.0)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*EARTH_RADIUS_KM*math.asin(min(1.0, math.sqrt(max(0.0,a))))


def _solar_corridor_coverage(event_azimuth, origin_lat, origin_lon,
                             target_distance_km, solar_azimuth_at_target,
                             upstream_end_km, radius_km=15.0, step_km=10.0):
    """Validate 2D geographic support of the 1D cloud profile.

    Keep the cloud target fixed at its sampled position on the EVENT bearing.
    Project the minute-specific sunlight from that fixed target, and compare it
    with the original EVENT-bearing sample axis at the same surface distance.
    Coverage is an uncertainty gate, never evidence of transparent atmosphere.
    """
    tlat, tlon = destination(origin_lat, origin_lon, event_azimuth, target_distance_km)
    travel = max(0.0, float(upstream_end_km)-float(target_distance_km))
    count = max(1,math.ceil(travel/max(1.0,float(step_km))))
    max_offset = 0.0
    first_outside = None
    for k in range(count+1):
        u = travel*k/count
        rlat, rlon = destination(tlat,tlon,solar_azimuth_at_target,u)
        alat, alon = destination(origin_lat,origin_lon,event_azimuth,target_distance_km+u)
        deviation = _great_circle_km(rlat,rlon,alat,alon)
        max_offset=max(max_offset,deviation)
        if deviation > radius_km+1e-8 and first_outside is None:
            first_outside=target_distance_km+u
    return {'covered':first_outside is None,
            'max_offset_km':round(max_offset,3),
            'first_outside_km':None if first_outside is None else round(first_outside,3),
            'corridor_radius_km':float(radius_km),
            'sample_axis_azimuth_deg':round(event_azimuth,5)}


def plan_off_axis_rays(profiles, event_dt, utc_offset, lat, lon, direction,
                       reach_km, target_limit_km, source_scan_km=700.0,
                       existing_radius_km=9.0, dedupe_radius_km=9.0, step_km=50.0):
    """Fetch catalogue for the SAME cloud-bottom points validated by the solver.

    Previous versions planned only sun-facing cloud fronts, while the solver
    inspected cloud-bottom points every 15 km.  Those rays could never match.
    A (minute, position_km) key now identifies each actual target beam.
    Existing pressure-level stations are reused; additional positions are
    deduplicated and must be measured before being accepted as evidence.
    """
    from .spherical_raytrace import destination, profile_cloud_cells
    base=[p for p in profiles if p.get('levels') and
          isinstance(p.get('lat'),(int,float)) and isinstance(p.get('lon'),(int,float))]
    eaz=solar_position(event_dt,utc_offset,lat,lon)[1]
    limit=min(max(0.,float(reach_km or 0.)),max(0.,float(target_limit_km or 0.)))
    cells=profile_cloud_cells(profiles,threshold=.000001,
                              blur_horizontal_km=0.,blur_vertical_km=0.)
    targets=[]
    for c in cells:
        if (float(c['start_km'])>=limit or float(c['top_km'])<=1.5 or
                float(c['end_km'])>=source_scan_km-15.):
            continue
        t=dict(c)
        t['end_km']=min(float(c['end_km']),limit)
        if t['end_km']>float(t['start_km']): targets.append(t)
    probes={}
    for t in targets:
        for strip in _bottom_sweep_strips(t,limit):
            pos=round(float(strip['position_km']),5)
            probes.setdefault(pos,[]).append(float(t['bottom_km']))
    # Face validation is still a separate required path: plan it as well.
    for t in targets:
        face=round(float(t['end_km']),5)
        probes.setdefault(face,[]).extend((float(t['bottom_km']),float(t['top_km'])))
    new_coords=[];rays={}
    base_positions=[(float(p['lat']),float(p['lon'])) for p in base]
    mins=range(-30,1) if direction=='morning' else range(0,31)
    for minute in mins:
        dt=event_dt+timedelta(minutes=minute)
        for pos,heights in sorted(probes.items()):
            plat,plon=destination(lat,lon,eaz,pos)
            alt,saz=solar_position(dt,utc_offset,plat,plon)
            # If the Earth itself blocks the beam for all candidate cloud
            # heights, extra meteorological requests cannot make it valid.
            if all(_sun_ray_earth_occluded(h,alt) for h in heights):continue
            cover=_solar_corridor_coverage(eaz,lat,lon,pos,saz,source_scan_km)
            if cover['covered']:continue
            span=source_scan_km-pos
            count=max(2,math.ceil(span/max(5.,step_km)))
            step=span/count
            ray=[]
            for i in range(count):
                u=step*(i+.5)
                pt=destination(plat,plon,saz,u)
                nearest=None;nearest_d=float('inf')
                for j,p0 in enumerate(base_positions):
                    d=_great_circle_km(pt[0],pt[1],p0[0],p0[1])
                    if d<nearest_d:nearest=j;nearest_d=d
                if nearest_d<=existing_radius_km:
                    ray.append({'distance_km':pos+u,'source':'base','index':nearest,
                                'offset_km':round(nearest_d,2)})
                    continue
                nearest=None;nearest_d=float('inf')
                for j,p0 in enumerate(new_coords):
                    d=_great_circle_km(pt[0],pt[1],p0[0],p0[1])
                    if d<nearest_d:nearest=j;nearest_d=d
                if nearest_d>dedupe_radius_km:
                    nearest=len(new_coords);new_coords.append(pt);nearest_d=0.
                ray.append({'distance_km':pos+u,'source':'extra','index':nearest,
                            'offset_km':round(nearest_d,2)})
            rays[(minute,pos)]=ray
    return {'coordinates':new_coords,'rays':rays,'base_profiles':base,
            'bottom_positions':sorted(probes),'step_km':step_km,
            'source_scan_km':source_scan_km}


def materialize_off_axis_rays(plan, extra_profiles):
    """Convert plan node indices into distance-aligned 1-D beam sections."""
    out={}
    base=plan['base_profiles']
    for key,nodes in plan['rays'].items():
        rows=[]
        for node in nodes:
            source=base if node['source']=='base' else extra_profiles
            prof=(source[node['index']] if node['index']<len(source) else None)
            if not prof or not prof.get('levels') or not any(
                isinstance(lv.get('cloud_cover'),(int,float)) and
                isinstance(lv.get('height_agl_km'),(int,float)) for lv in prof['levels']):
                rows=[];break
            row=dict(prof)
            row['distance_km']=node['distance_km']
            row['source_position_offset_km']=node['offset_km']
            rows.append(row)
        out[key]=rows
    return out


# All cloud-bottom targets use ONE local-to-sunward scan grid.  The weather
# profiles already cover this domain; adding target points does not fetch data.
BOTTOM_SWEEP_STEP_KM = 15.0
# Kept only for older diagnostic callers/tests, not for the official sweep.
UNDERSIDE_AREA_STRIPS = 9


def _bottom_samples(target, count=5):
    """Legacy midpoint probes for standalone diagnostic callers."""
    start=float(target['start_km']);end=float(target['end_km'])
    if end<=start:return []
    return [start+(end-start)*(i+.5)/count for i in range(count)]


def _cloud_bottom_sweep_grid(limit_km):
    """One probe for a 0..15 km range; otherwise strict 15-km intervals.

    A range shorter than 15 km uses its midpoint to represent the entire
    range. For longer ranges the global stations are 0, 15, 30, ...; the
    last fractional tail belongs to the nearest station for area weighting.
    """
    limit=max(0.,float(limit_km))
    if limit<=0:return []
    if limit<BOTTOM_SWEEP_STEP_KM:
        return [limit*.5]
    count=int((limit+1e-8)//BOTTOM_SWEEP_STEP_KM)
    return [BOTTOM_SWEEP_STEP_KM*i for i in range(count+1)]


def _bottom_sweep_strips(target, limit_km, refine_points=()):
    """Actual widths owned by cloud-bottom positions on the global scan grid.

    Clip to this cloud's horizontal interval.  A cloud cell shorter than one
    grid step is still examined, never silently dropped.  Extra points inserted
    around light/dark changes subdivide area instead of increasing total area.
    """
    a=max(0.,float(target['start_km']))
    b=min(float(target['end_km']),float(limit_km))
    if b<=a:return []
    nodes=[d for d in _cloud_bottom_sweep_grid(limit_km) if a-1e-8<=d<=b+1e-8]
    nodes += [float(d) for d in refine_points if a<float(d)<b]
    if not nodes:nodes=[(a+b)*.5]
    nodes=sorted(set(round(max(a,min(b,d)),9) for d in nodes))
    intervals=[]
    for i,d in enumerate(nodes):
        left=a if i==0 else (nodes[i-1]+d)*.5
        right=b if i==len(nodes)-1 else (d+nodes[i+1])*.5
        intervals.append({'position_km':d,'area_width_km':max(0.,right-left)})
    return intervals


def _cloud_ray_intersects_bottom(target_d, base_height_km, solar_alt_deg, cell,
                                 source_end_km):
    """Exact interior interception of a spherical ray by a cloud rectangle.

    Touching the lower surface at the ray origin is NOT self-obstruction.
    A solar beam arriving from *below* can light the underside, even if it
    never hits the sun-facing cloud front.  Interior intersections are blocked.
    """
    from .spherical_raytrace import _ray_height_km, EARTH_RADIUS_KM
    a=max(target_d+1e-5,float(cell['start_km']))
    b=min(source_end_km,float(cell['end_km']))
    if b<=a:return False
    angle=math.radians(solar_alt_deg)
    tangent=-angle*EARTH_RADIUS_KM
    distances=(a-target_d,b-target_d)
    values=[_ray_height_km(base_height_km,solar_alt_deg,d) for d in distances]
    if distances[0] < tangent < distances[1]:
        values.append(_ray_height_km(base_height_km,solar_alt_deg,tangent))
    # Require positive-volume intersection; a tangent contact at the cloud
    # bottom, including the target's own underside, is not a solid blocker.
    return (min(values)<float(cell['top_km'])-1e-6 and
            max(values)>float(cell['bottom_km'])+1e-6)


def _offaxis_ray_supports_bottom(ray, target_lat, target_lon, solar_az, target_d,
                                  face, horizon, radius_km=9.0):
    """An existing measured face ray may be reused ONLY if its real positions
    also coincide with the new underside beam (no fictitious extrapolation).
    """
    if not ray or len(ray)<2:return False
    ds=[float(x['distance_km']) for x in ray]
    if (ds[0]-(ds[1]-ds[0])/2>face+1e-5 or
        ds[-1]+(ds[-1]-ds[-2])/2<horizon-1e-5 or
        any(b-a>55. for a,b in zip(ds,ds[1:]))):return False
    for row in ray:
        if not row.get('levels') or row.get('lat') is None or row.get('lon') is None:
            return False
        d=float(row['distance_km'])
        if d<face or d>horizon:continue
        expected=destination(target_lat,target_lon,solar_az,d-target_d)
        if _great_circle_km(*expected,float(row['lat']),float(row['lon']))>radius_km:
            return False
    return True




def _measured_station_index(axis_profiles, off_axis_profiles, cell_deg=0.2):
    """Index ACTUALLY measured stations, not synthetic clear positions.

    Face rays are a cache of real pressure-level requests.  Their measurements
    may be reused for other underside targets *only at their actual lat/lon*.
    No spatial extrapolation and no additional meteorological request.
    """
    buckets={}
    seen=set()
    sources=list(axis_profiles or ())
    for ray in (off_axis_profiles or {}).values():
        sources.extend(ray or ())
    for row in sources:
        if not row or not row.get('levels') or not isinstance(row.get('lat'),(int,float)) or not isinstance(row.get('lon'),(int,float)):
            continue
        if not any(isinstance(v.get('height_agl_km'),(int,float)) and isinstance(v.get('cloud_cover'),(int,float)) for v in row['levels']):
            continue
        la=float(row['lat']); lo=float(row['lon'])
        key=(round(la,5),round(lo,5))
        if key in seen:continue
        seen.add(key)
        cell=(math.floor(la/cell_deg),math.floor(lo/cell_deg))
        buckets.setdefault(cell,[]).append(row)
    return (buckets,cell_deg)


def _reproject_measured_ray(index, target_lat, target_lon, solar_azimuth,
                            target_distance_km, source_end_km,
                            radius_km=15., spacing_km=50.):
    """Resample the real underside sunbeam from cached measured points.

    Do not claim a ray verified if even ONE station on the path lacks a
    nearby independent atmospheric profile.  The 15-km tolerance is the
    corridor's existing horizontal spatial support radius, not free sky.
    """
    buckets,cell_deg=index
    if not buckets:return None
    span=float(source_end_km)-float(target_distance_km)
    if span<=0:return None
    count=max(2,math.ceil(span/spacing_km))
    width=span/count
    rows=[]
    for i in range(count):
        u=(i+.5)*width
        pt=destination(target_lat,target_lon,solar_azimuth,u)
        a,b=math.floor(pt[0]/cell_deg),math.floor(pt[1]/cell_deg)
        nearest=None;best=radius_km+1e-8
        # The lat/lon grids are only a fast index: actual matching uses great-
        # circle distance and never invents an intermediate weather sample.
        lat_steps=max(1,math.ceil((radius_km/110.6)/cell_deg)+1)
        lon_steps=max(1,math.ceil((radius_km/(111.3*max(.15,abs(math.cos(math.radians(pt[0]))))))/cell_deg)+1)
        for x in range(a-lat_steps,a+lat_steps+1):
            for y in range(b-lon_steps,b+lon_steps+1):
                for candidate in buckets.get((x,y),()):
                    d=_great_circle_km(pt[0],pt[1],float(candidate['lat']),float(candidate['lon']))
                    if d<best:nearest=candidate;best=d
        if nearest is None:return None
        row=dict(nearest)
        row['distance_km']=target_distance_km+u
        row['source_position_offset_km']=round(best,2)
        rows.append(row)
    return rows


# Conservative upper bound of the ordinary cloud-bearing atmosphere. This is
# a ray EXIT ALTITUDE, never a forced height at 700 km. A ray must be sampled
# at every upstream cloud column until it crosses this bound while ascending.
DEFAULT_CLOUD_ATMOSPHERE_TOP_KM = 20.0


def _solar_cloud_exit_travel_km(target_height_km, solar_alt_deg,
                                cloud_atmosphere_top_km=DEFAULT_CLOUD_ATMOSPHERE_TOP_KM):
    """Exact spherical straight-sun-ray distance to CLOUD-FREE upper atmosphere.

    It is the FARTHER / ASCENDING intersection of a solar ray with the sphere
    R+cloud_atmosphere_top_km. An earlier downward crossing is NOT a source
    exit. A real Earth intersection is rejected separately. No forecast
    outside this upper atmospheric bound is claimed.

    None means Earth blocking or nonphysical input. Return 0 only when the ray
    is already and will remain outside the cloud-bearing sphere.
    """
    try:
        h=float(target_height_km)
        alt=float(solar_alt_deg)
        ceiling=float(cloud_atmosphere_top_km)
    except (TypeError,ValueError):
        return None
    if (not all(map(math.isfinite,(h,alt,ceiling))) or
        h<0 or ceiling<=0 or not (-90.<alt<90.)):
        return None
    if _sun_ray_earth_occluded(h,alt):
        return None
    angle=math.radians(alt)
    # The minimum Earth-centred radius along the forward half-ray.
    tangent_radius=(EARTH_RADIUS_KM+h)*(math.cos(angle) if angle<0 else 1.)
    if tangent_radius>=EARTH_RADIUS_KM+ceiling:
        return 0.0
    ratio=(EARTH_RADIUS_KM+h)*math.cos(angle)/(EARTH_RADIUS_KM+ceiling)
    if not 0. <= ratio <= 1.:
        return None
    travel=EARTH_RADIUS_KM*(math.acos(max(-1.,min(1.,ratio)))-angle)
    return max(0.0,travel)


def dynamic_solar_source_horizon(profiles, event_dt, utc_offset, lat, lon,
                                 direction, reach_km, target_limit_km=None,
                                 min_scan_km=700., extension_factor=1.3,
                                 atmosphere_top_km=DEFAULT_CLOUD_ATMOSPHERE_TOP_KM):
    """Plan upstream cloud samples using REAL target and minute solar altitude.

    This is a conservative PLAN, not proof: the caller MUST fetch the samples
    and the strict tracer MUST validate their actual cloud intersections.
    Invalid targets/angles do not fabricate a clear ray.
    """
    cells=profile_cloud_cells(profiles or (),threshold=0.000001,
                              blur_horizontal_km=0.,blur_vertical_km=0.)
    reach=max(0.,float(reach_km or 0.))
    limit=(min(reach,max(0.,float(target_limit_km)))
           if target_limit_km is not None else reach)
    base=max(float(min_scan_km),reach*max(1.01,float(extension_factor)))
    event_az=solar_position(event_dt,utc_offset,lat,lon)[1]
    minutes=range(-30,1) if direction=='morning' else range(0,31)
    max_required=base
    worst=None
    eligible=0
    # Each bottom is checked at the same fixed 15-km positions as the
    # strict underside tracer. The frontal target includes the lowest height
    # that can physically see over Earth's limb, not just a random midpoint.
    for cell in cells:
        if float(cell['start_km'])>=limit-1e-8 or float(cell['top_km'])<=1.5:
            continue
        target=dict(cell)
        target['end_km']=min(float(cell['end_km']),limit)
        face=float(target['end_km'])
        if face<=float(target['start_km']):continue
        for minute in minutes:
            dt=event_dt+timedelta(minutes=minute)
            for pos in [p["position_km"] for p in _bottom_sweep_strips(target,limit)] + [face]:
                tlat,tlon=destination(lat,lon,event_az,pos)
                alt,_=solar_position(dt,utc_offset,tlat,tlon)
                heights=[float(target['bottom_km'])]
                if abs(pos-face)<1e-8:
                    # For the SUNWARD FACE, the most demanding *visible*
                    # height is usually the Earth-limb boundary.
                    floor=(EARTH_RADIUS_KM/math.cos(math.radians(alt))-EARTH_RADIUS_KM
                           if alt<0 else float(target['bottom_km']))
                    heights.append(max(float(target['bottom_km']),floor+0.00001))
                for height in heights:
                    if height>float(target['top_km'])+1e-8:continue
                    travel=_solar_cloud_exit_travel_km(height,alt,atmosphere_top_km)
                    if travel is None:continue
                    eligible+=1
                    requested=pos+travel
                    if requested>max_required:
                        max_required=requested
                        worst={'minute_offset':minute,'position_km':round(pos,2),
                               'target_height_km':round(height,4),
                               'sun_altitude_deg':round(alt,4),
                               'cloud_exit_distance_km':round(requested,2)}
    # Small geometric tolerance and 50-km provider grid. No 700-km MAXIMUM.
    end=float(math.ceil((max_required+0.1)/50.)*50.)
    return {'required_source_km':end,'raw_required_km':round(max_required,2),
            'minimum_source_km':round(base,2), 'atmosphere_ceiling_km':atmosphere_top_km,
            'eligible_ray_count':eligible,'limiting_ray':worst,
            'method':'minute_target_cloud_bottom_and_face_spherical_sunray_exit'}


def _validated_cloud_bottom(target, minute, dt, event_az, lat, lon, utc_offset,
                            required, axis_cells, axis_profiles, off_axis_profiles,
                            use_horizontal_fusion, target_pressure_by_distance,
                            measured_index=None, sample_count=5, sweep_strips=None):
    """Strict spherical/cloud-occlusion test at every sampled *cloud bottom*.

    Returns individual area-strip observations; inconclusive off-axis paths
    are UNKNOWN, never quietly counted as unblocked or as zero illumination.
    """
    from .spherical_raytrace import (_earth_occluded,_ray_height_km,
                                     profile_cloud_cells, EARTH_RADIUS_KM)
    from .spatial_ray_fusion import (check_horizontal_ray_consistency,
                                     check_horizontal_target_support)
    face=float(target['end_km']); bottom=float(target['bottom_km'])
    key=[round(float(target[k]),6) for k in
         ('start_km','end_km','bottom_km','top_km')]
    outcome=[]
    strips=(sweep_strips if sweep_strips is not None else
            [{'position_km':x,'area_width_km':(
                float(target['end_km'])-float(target['start_km']))/sample_count}
             for x in _bottom_samples(target,sample_count)])
    for strip in strips:
        pos=float(strip['position_km'])
        width=float(strip['area_width_km'])
        tlat,tlon=destination(lat,lon,event_az,pos)
        alt,az=solar_position(dt,utc_offset,tlat,tlon)
        # Use the SAME effective profile cloud field for both the visible
        # cross-section and the blocker test. Do not invent hidden blockers
        # from a second unseen cloud set.
        if _sun_ray_earth_occluded(bottom,alt):
            outcome.append({'status':'earth_or_source_horizon','position_km':pos,
                            'solar_altitude_deg':alt,'target_key':key});continue
        obstacles=[c for c in axis_cells if float(c['end_km'])>pos and
                   float(c['start_km'])<required]
        travel=required-pos
        if travel<=0:
            outcome.append({'status':'unresolved_source_boundary','position_km':pos,
                            'solar_altitude_deg':alt,'target_key':key,
                            'reason':'target beyond sampled upper horizon'});continue
        # Strict solar geometry, independent of the length of weather samples.
        if _sun_ray_earth_occluded(bottom,alt):
            outcome.append({'status':'earth_or_source_horizon','position_km':pos,
                            'solar_altitude_deg':alt,'target_key':key});continue
        # A gap near the observer NEVER bypasses cloud obstacles farther away.
        # Every measured upstream rectangle is checked at the ray's real height.
        if any(_cloud_ray_intersects_bottom(pos,bottom,alt,c,required)
               for c in obstacles):
            outcome.append({'status':'cloud_blocked','position_km':pos,
                            'solar_altitude_deg':alt,'target_key':key});continue
        exit_travel=_solar_cloud_exit_travel_km(bottom,alt,
                                               DEFAULT_CLOUD_ATMOSPHERE_TOP_KM)
        if exit_travel is None:
            outcome.append({'status':'earth_or_source_horizon','position_km':pos,
                            'solar_altitude_deg':alt,'target_key':key});continue
        if travel+1e-5<exit_travel:
            outcome.append({'status':'unresolved_source_boundary','position_km':pos,
                            'solar_altitude_deg':alt,'target_key':key,
                            'required_source_km':round(pos+exit_travel,2)});continue
        outcome.append({'status':'verified_bottom','position_km':pos,
                        'solar_altitude_deg':alt,'target_key':key,
                        'source_boundary_altitude_km':round(_ray_height_km(bottom,alt,travel),3)})
    for row,strip in zip(outcome,strips):
        row['area_width_km']=float(strip['area_width_km'])
    return outcome


def adaptive_equivalent_gaps(profiles, event_dt, utc_offset, lat, lon, direction,
                             reach_km, min_width_km=15.0, extension_factor=1.3,
                             target_limit_km=None, source_scan_km=None,
                             off_axis_ray_profiles=None, use_horizontal_fusion=True):
    """Strict *sampled-corridor* spherical validation for illuminated cloud faces.

    The local cloud-target reach and the upstream solar-source verification
    distance are independent. A nonzero local reach must NEVER allow a 35-km
    locally clear segment to certify a source path extending hundreds of km.
    The ray needs clear air AT ITS ACTUAL HEIGHT at every sampled column.
    The legacy >=25% cloud threshold remains an explicit *blocking* assumption,
    while all nonzero mid/high cloud targets receive continuous area weights.
    Verified target height bands at each minute gate the area estimator.
    """
    from .critical_light_envelope import analyze_target_face
    from .spherical_raytrace import (_ray_height_km, _earth_occluded,
                                     profile_cloud_cells, destination)
    valid = sorted((p for p in (profiles or []) if p.get('levels') and
                    isinstance(p.get('distance_km'), (int, float))),
                   key=lambda p: float(p['distance_km']))
    reach = max(0.0, float(reach_km or 0.0))
    # The source must be sampled until the *actual solar beam* has escaped
    # the ordinary cloud-bearing atmosphere on its ascending branch. Never
    # convert the old 700-km request horizon into a hard sunlight cutoff.
    source_plan=dynamic_solar_source_horizon(
        valid,event_dt,utc_offset,lat,lon,direction,reach,
        target_limit_km=target_limit_km,min_scan_km=(700. if source_scan_km is None
                                                     else max(0.,float(source_scan_km))),
        extension_factor=extension_factor)
    required=source_plan['required_source_km']
    available = max((float(p['distance_km']) for p in valid), default=0.0)
    # Never confuse a missing 50-km profile with an observed clear corridor.
    all_samples = sorted((p for p in (profiles or []) if isinstance(p.get('distance_km'), (int,float))),
                         key=lambda p: float(p['distance_km']))
    missing = [float(p['distance_km']) for p in all_samples
               if float(p['distance_km']) <= required + 1e-6 and not p.get('levels')]
    gaps = [float(q['distance_km'])-float(p['distance_km'])
            for p,q in zip(valid,valid[1:]) if float(p['distance_km']) < required]
    unresolved = (len(valid)<2 or reach<=0 or available+1e-6<required
                  or missing or any(g>100.0+1e-6 for g in gaps))
    if unresolved:
        return {'gaps': [], 'status': 'unresolved_data', 'evaluations': 0, 'verified_ray_traces': [], 'verified_target_bands': [],
                'required_profile_km':round(required,2),'available_profile_km':round(available,2),
                'missing_profile_distances_km':missing,
                'note':'上游气压层未覆盖太阳角度决定的完整云层遮挡检查范围，或有剖面缺失；未采样区域不得当作晴空。', 'dynamic_source_horizon':source_plan}
    # Separate low-cover targets from the conservative obstacle screen.
    # Never treat the nonzero target cloud as a completely opaque blocker
    # merely because it contributes some fractional cloud coverage.
    cells=profile_cloud_cells(valid,threshold=25.,blur_horizontal_km=0.,blur_vertical_km=0.)
    target_cells=profile_cloud_cells(valid,threshold=0.000001,blur_horizontal_km=0.,blur_vertical_km=0.)
    limit = min(reach, max(0.0, float(target_limit_km))) if target_limit_km is not None else reach
    # Target is its real SUNWARD face, not the profile-centre point. A ray
    # starting at centre would either ignore part of its cloud or self-block.
    targets=[]
    for c in target_cells:
        if (float(c['start_km'])>=limit-1e-8 or float(c['top_km'])<=1.5
                or float(c['end_km'])>=required-15.0):
            continue
        t=dict(c)
        t['end_km']=min(float(c['end_km']),limit)
        if t['end_km'] > float(t['start_km']):
            targets.append(t)
    if not targets:
        return {'gaps':[], 'status':'no_target_cloud','evaluations':0, 'verified_ray_traces': [], 'verified_target_bands': [],
                'required_profile_km':round(required,2), 'available_profile_km':round(available,2),
                'note':'霞云距内没有满足剖面条件的中高云目标'}
    verified=[]; evaluations=0; confirmed_faces=set(); by_minute={}
    # Per-minute verification collects every target; no selection of a winner.
    verified_target_bands=[]
    verified_target_bottom_points=[]
    bottom_unknown_count=0
    bottom_verified_count=0
    event_az = solar_position(event_dt,utc_offset,lat,lon)[1]
    uncovered_minutes=set(); coverage_debug=[]
    source_boundary_unresolved_minutes=set()
    off_axis_used=set(); off_axis_missing=set()
    horizontal_conflict_minutes=set()
    horizontal_conflict_count=0
    horizontal_target_conflict_count=0
    horizontal_evaluated_nodes=0
    horizontal_scanned_rays=0
    horizontal_conflict_examples=[]
    from .spatial_ray_fusion import (check_horizontal_ray_consistency,
                                     check_horizontal_target_support)
    target_pressure_by_distance = {round(float(p['distance_km']),5):p for p in valid}
    measured_index=_measured_station_index(valid,off_axis_ray_profiles)
    # A valid model-domain ray requires sampled profiles throughout the full
    # upstream window, exact solar/Earth non-intersection and NO actual cloud
    # intersections. Neither an arbitrary 20-km endpoint threshold nor a
    # forecast of post-700km minimum altitude is an optical obstruction.
    minutes=range(-30,1) if direction=='morning' else range(0,31)
    for minute in minutes:
        dt=event_dt+timedelta(minutes=minute)
        minute_verified=[]
        minute_uncovered=False
        for target in targets:
            face=float(target['end_km'])
            # Profile targets are fixed geographic clouds, NOT moving with the sun.
            plat,plon=destination(lat,lon,event_az,face)
            alt,solar_az=solar_position(dt,utc_offset,plat,plon)
            coverage=_solar_corridor_coverage(event_az,lat,lon,face,solar_az,required)
            if not coverage['covered']:
                ray_key=(minute,round(face,5))
                ray=(off_axis_ray_profiles or {}).get(ray_key)
                # The beam was sampled at real minute-dependent locations.
                # For 50-km midpoint nodes, the first/last cell boundaries
                # must also cover the target face and the full source horizon.
                if ray and len(ray)>=2:
                    ds=[float(p['distance_km']) for p in ray]
                    first=ds[0]-(ds[1]-ds[0])/2
                    last=ds[-1]+(ds[-1]-ds[-2])/2
                    complete=(first<=face+1e-5 and last>=required-1e-5
                              and all(b-a<=55. for a,b in zip(ds,ds[1:])))
                else: complete=False
                if complete:
                    upstream=profile_cloud_cells(ray,threshold=25.,
                        blur_horizontal_km=0.,blur_vertical_km=0.)
                    off_axis_used.add(ray_key)
                else:
                    minute_uncovered=True
                    off_axis_missing.add(ray_key)
                    if len(coverage_debug)<30:
                        coverage_debug.append({'minute_offset':minute,'target_face_km':round(face,3),
                                               'solar_azimuth_deg':round(solar_az,5),**coverage})
                    continue
            else:
                upstream=[c for c in cells if
                          float(c['end_km'])>face+1e-7 and float(c['start_km'])<required]
            # Attach the horizontal OM samples at the ACTUAL beam nodes.
            # This is a conflict screen, not an opaque 3-D raster cloud model.
            horizontal_ray_profiles = ((ray if not coverage['covered'] else valid)
                                       if use_horizontal_fusion else [])
            # Intersections are tested at real positions along this solar ray,
            # not at fixed sunrise-axis positions if the ray diverges.
            bands=analyze_target_face(target,upstream,alt,
                                      max_distance_km=required).get('possible_face_height_bands_km',[])
            evaluations+=1
            for original_low,high in bands:
                if high-original_low<1e-6 or required<=face:continue
                # The sun-facing height interval may straddle the solar limb.
                # Clip rather than midpoint-vote the entire face as valid.
                a=math.radians(alt)
                limb=(EARTH_RADIUS_KM/max(1e-9,math.cos(a))-EARTH_RADIUS_KM
                      if alt<0 else -float('inf'))
                low=max(original_low,limb+1e-6)
                if high-low<1e-6:continue
                # Require the ascending ray to have passed out of cloud-bearing
                # altitudes by the END OF THE MEASURED SOURCE CORRIDOR.
                # The corresponding height floor comes from the exact spherical
                # height function; no arbitrary '700 km = enough' assumption.
                u=required-face
                theta=u/EARTH_RADIUS_KM
                if math.cos(a+theta)<=0:continue
                source_height_floor=(
                    (EARTH_RADIUS_KM+DEFAULT_CLOUD_ATMOSPHERE_TOP_KM)*
                    math.cos(a+theta)/max(1e-9,math.cos(a))-EARTH_RADIUS_KM)
                low=max(low,source_height_floor+1e-6)
                if high-low<1e-6:continue
                h=(low+high)/2.0
                exit_distance=_solar_cloud_exit_travel_km(h,alt)
                if (exit_distance is None or required-face+1e-5<exit_distance):
                    continue
                # All measured intermediate cloud obstacles remain mandatory.
                # Independent ray-position cross-check at every exact cell.
                def intersects(cell):
                    a=max(face+1e-7,float(cell['start_km']))
                    b=min(required,float(cell['end_km']))
                    if b<=a:return False
                    u,v=a-face,b-face
                    q=max(u,min(v,-math.radians(alt)*EARTH_RADIUS_KM))
                    hs=[_ray_height_km(h,alt,d) for d in (u,v,q)]
                    return min(hs)<=float(cell['top_km']) and max(hs)>=float(cell['bottom_km'])
                if any(intersects(c) for c in upstream):continue
                # Confirm that the target cloud implied by the pressure sample
                # is not contradicted by horizontal OM near that SAME target.
                source_sample = target_pressure_by_distance.get(
                    round(float(target.get('sample_distance_km',0)),5))
                support = (check_horizontal_target_support(source_sample, h)
                           if use_horizontal_fusion else {'checked':False,'unresolved':False})
                if support['unresolved']:
                    horizontal_conflict_minutes.add(minute)
                    horizontal_target_conflict_count += 1
                    if len(horizontal_conflict_examples)<12:
                        horizontal_conflict_examples.append({
                            'distance_km':round(float(target.get('sample_distance_km',0)),1),
                            'ray_height_km':round(h,2),
                            'max_horizontal_cover_pct':round(support['max_horizontal_cover_pct'],1),
                            'reason':'pressure-cloud-target / horizontal-near-clear'})
                    continue
                horizontal = check_horizontal_ray_consistency(
                    horizontal_ray_profiles, face, h, alt, required)
                horizontal_evaluated_nodes += horizontal['evaluated_nodes']
                if horizontal['horizontal_nodes']:
                    horizontal_scanned_rays += 1
                if horizontal['unresolved']:
                    # A cloudy horizontal model pixel with no corresponding
                    # pressure-layer obstruction is an uncertainty, NOT a
                    # scientifically established opaque cloud or clear ray.
                    horizontal_conflict_minutes.add(minute)
                    horizontal_conflict_count += 1
                    if len(horizontal_conflict_examples)<12:
                        horizontal_conflict_examples.extend(horizontal['conflicts'][:2])
                    continue
                width=high-low
                weight=width*float(target.get('cloud_cover',0))*(float(target['end_km'])-float(target['start_km']))
                candidate={'minute_offset':minute,'time':dt.isoformat(),
                           'target_face_km':round(face,3), 'target_height_km':round(h,6),
                           'solar_altitude_deg':round(alt,7),
                           'solar_azimuth_deg':round(solar_az,7),
                           'profile_max_lateral_offset_km':(max((p.get('source_position_offset_km',0.) for p in ray),default=0.) if not coverage['covered'] else coverage['max_offset_km']),
                           'profile_corridor_radius_km':(9.0 if not coverage['covered'] else coverage['corridor_radius_km']),
                           'source_check_km':round(required,2),
                           'clear_face_band_km':[round(low,6),round(high,6)],
                           'status':'verified_sunward_face_full_sampled_corridor',
                           'source_boundary_altitude_km':round(_ray_height_km(h,alt,required-face),3),
                           'verification_scope':'sampled_upstream_clouds_and_global_earth_geometry',
                           'weight':weight}
                # Record EVERY verified target, not only the winner of this minute.
                # Downstream area weighting must never illuminate other targets
                # merely because one face somewhere had a valid light path.
                verified_target_bands.append({
                    'minute_offset': minute,
                    'target_key': [round(float(target[k]),6) for k in
                                   ('start_km','end_km','bottom_km','top_km')],
                    'height_band_km': [round(low,6),round(high,6)],
                    'solar_altitude_deg': round(alt,7),
                })
                minute_verified.append(candidate)
        # Uniform 15-km cloud-bottom target grid from the observer.
        # <15 km: one representative sample. Do not increase sampling for
        # short ranges; this pass reuses existing weather profiles.
        for target in targets:
            base_strips=_bottom_sweep_strips(target,limit)
            underside=_validated_cloud_bottom(
                target,minute,dt,event_az,lat,lon,utc_offset,required,
                cells,valid,off_axis_ray_profiles,use_horizontal_fusion,
                target_pressure_by_distance,measured_index,
                sweep_strips=base_strips)
            verified_target_bottom_points.extend(
                {'minute_offset':minute,**point} for point in underside)
            for point in underside:
                if point['status'].startswith('unresolved'):
                    bottom_unknown_count+=1
                    if point['status']=='unresolved_source_boundary':
                        source_boundary_unresolved_minutes.add(minute)
                    else:
                        minute_uncovered=True
                if point['status']!='verified_bottom':continue
                bottom_verified_count+=1
                face=float(target['end_km'])
                candidate={'minute_offset':minute,'time':dt.isoformat(),
                    'target_face_km':round(face,3),
                    'target_bottom_position_km':round(point['position_km'],3),
                    'target_height_km':round(float(target['bottom_km']),6),
                    'solar_altitude_deg':round(point['solar_altitude_deg'],7),
                    'source_check_km':round(required,2),
                    'status':'verified_cloud_bottom_spherical',
                    'surface':'cloud_bottom',
                    'source_boundary_altitude_km':point['source_boundary_altitude_km'],
                    'weight':float(target.get('cloud_cover',0))*point['area_width_km']}
                minute_verified.append(candidate)
        if minute_uncovered:
            uncovered_minutes.add(minute)
        if minute_verified:
            verified.extend(minute_verified)
            by_minute[minute]=len(minute_verified)
            confirmed_faces.update(v['target_face_km'] for v in minute_verified)
    return {'gaps':[], 'status':('local_targets_verified' if verified else
                                  'unresolved_data' if (uncovered_minutes or source_boundary_unresolved_minutes or horizontal_conflict_minutes)
                                  else 'no_verified_source'),
            'evaluations':evaluations,'illuminated_target_count':len(confirmed_faces),
            'verified_minutes':sorted(by_minute),
            'verified_target_bands':verified_target_bands,
            'verified_target_bottom_points':verified_target_bottom_points,
            'bottom_verified_point_count':bottom_verified_count,
            'bottom_unresolved_point_count':bottom_unknown_count,
            'bottom_inner_sweep_method':'global_fixed_15km_single_if_short',
            'target_surface_methods':['cloud_bottom','sunward_face'],
            'spatially_unverified_minutes':sorted(uncovered_minutes),
            'source_boundary_unresolved_minutes':sorted(source_boundary_unresolved_minutes),
            'horizontal_conflict_unresolved_minutes':sorted(horizontal_conflict_minutes),
            'horizontal_conflict_count':horizontal_conflict_count,
            'horizontal_target_conflict_count':horizontal_target_conflict_count,
            'horizontal_fusion_enabled':bool(use_horizontal_fusion),
            'horizontal_ray_checks':horizontal_scanned_rays,
            'horizontal_evaluated_nodes':horizontal_evaluated_nodes,
            'horizontal_conflict_examples':horizontal_conflict_examples[:12],
            'horizontal_evidence_mode':'OM_2D_api_vertical_conflict_veto_only',

            'off_axis_rays_measured':len(off_axis_used),
            'off_axis_rays_missing':len(off_axis_missing),
            'cloud_top_clearance_km':DEFAULT_CLOUD_ATMOSPHERE_TOP_KM,  # source sampling altitude, NOT fixed 700km veto
            'source_termination_rule':'dynamic_solar_ascending_escape_plus_full_cloud_intersections',
            'dynamic_source_horizon':source_plan,
            'spatial_coverage_diagnostics':coverage_debug,
            'corridor_radius_km':15.0,
            'verified_ray_traces':[
                {k:v for k,v in row.items() if k!='weight'} for row in verified],
            'required_profile_km':round(required,2), 'available_profile_km':round(available,2),
            'target_limit_km':round(limit,2),
            'note':'上游验证距离按每分钟、每个目标云底与云锋面的太阳高度角动态计算；700km仅是基础采样距离。沿完整实际采样光线逐段验证云层遮挡，直至光线在上升段脱离普通云层高度范围。若所需距离未覆盖或偏轴气压层缺失，返回未验证而非假定晴空。20km是普通气象云层的保守垂直模型上界，不是700km终点达标门槛。'}

def grid_points(origin_lat,origin_lon,bearing,radius_km):
    """Three angular columns x three distances + local; 10 locations maximum."""
    radius=max(1.,min(350.,float(radius_km)))
    positions=[]
    for j,offset in enumerate((-40.,0.,40.)):
        for i,fraction in enumerate((.25,.55,.85)):
            d=radius*fraction
            lat,lon=destination(origin_lat,origin_lon,bearing+offset,d)
            positions.append({'column':j,'distance_km':d,'bearing_offset':offset,'lat':lat,'lon':lon})
    return positions

def _nearest_hour(hourly,dt_utc):
    t=hourly.get('time') or []
    if not t:return None
    return min(range(len(t)),key=lambda i:abs((datetime.fromisoformat(t[i])-dt_utc).total_seconds()))

def _value(hourly,key,idx):
    v=hourly.get(key) or []
    return v[idx] if idx is not None and idx<len(v) else None

def evaluate(points,records,event_dt,utc_offset,lat,lon,direction,aod=0.2):
    """Conservative *approximate* 6-bin diagnostic from 10 actual locations.

    Broad low/mid/high cloud data do not resolve pressure-layer height or the
    three-dimensional upstream path. Mark results 'candidate' not verified.
    """
    if len(points)!=len(records):raise ValueError('采样位置数不匹配')
    minute_offsets=list(range(-30,0,3)) if direction=='morning' else list(range(1,31,3))
    times=[event_dt+timedelta(minutes=m) for m in minute_offsets]
    bins=[{'azimuth':AZ_BINS[j],'elevation':EL_BINS[k], 'targets':[], 'colors':[],
           'state':'unknown'} for k in range(2) for j in range(3)]
    for p,rec in zip(points,records):
        hourly=rec.get('hourly') or {}
        idx=_nearest_hour(hourly,event_dt-timedelta(hours=utc_offset))
        for layer,reference_h in (('cloud_cover_mid',4.5),('cloud_cover_high',8.5)):
            cover=_value(hourly,layer,idx)
            if not isinstance(cover,(int,float)) or cover<5:continue
            elev=apparent_elevation(p['distance_km'],reference_h)
            if not 0<=elev<=90:continue
            k=0 if elev>=45 else 1
            b=bins[k*3+p['column']]
            b['targets'].append({'distance_km':p['distance_km'],'height_km':reference_h,
                                 'cover':float(cover),'lat':p['lat'],'lon':p['lon']})
    for b in bins:
        if not b['targets']:
            b['state']='no_cloud';b['colors']=[None]*len(times);continue
        colors=[]
        for dt in times:
            illuminated=[]
            for target in b['targets']:
                alt,_=solar_position(dt,utc_offset,target['lat'],target['lon'])
                if not _earth_blocked(target['height_km'],alt,350):
                    illuminated.append(target)
            if not illuminated:colors.append(None);continue
            phase=abs((dt-event_dt).total_seconds())/1800
            haze=max(.25,min(1.,1-.58*float(aod or .2)))
            raw=(245,105+100*(1-phase),115+80*phase)
            colors.append(tuple(int(max(0,min(255,200*(1-haze)+v*haze))) for v in raw))
        b['colors']=colors;b['state']='candidate' if any(c is not None for c in colors) else 'unlit'
    return {'bins':bins,'times':[t.isoformat() for t in times],
            'candidate_bins':sum(b['state']=='candidate' for b in bins),
            'verified_scale_pct':None,
            'note':'六分区候选云色：左右真实云量采样，但采用参考层高；尚无二维气压层与完整上游遮挡验证，不作为正式霞云规模。'}

def draw(result,title='二维霞云图'):
    import numpy as np
    import matplotlib.pyplot as plt
    from matplotlib import rcParams
    rcParams["font.sans-serif"]=["Microsoft YaHei","SimHei","Noto Sans CJK JP","DejaVu Sans"]
    rcParams["axes.unicode_minus"]=False
    from matplotlib.patches import Rectangle
    from matplotlib.colors import to_rgb
    fig,axes=plt.subplots(1,2,figsize=(12,6))
    bins=result['bins']
    base=np.ones((240,360,3))*.93
    weights=np.zeros((240,360))
    yy,xx=np.mgrid[0:240,0:360]
    for k,b in enumerate(bins):
        if b['state']!='candidate':continue
        c=next((c for c in reversed(b['colors']) if c is not None),None)
        if c is None:continue
        col=k%3;row=k//3
        cx=60+120*col;cy=180 if row==0 else 60
        w=np.exp(-((xx-cx)**2/(2*80**2)+(yy-cy)**2/(2*67**2)))
        # Blend only within the target's own cell; do not invent colored gaps.
        x0=120*col;x1=x0+120;y0=120 if row==0 else 0;y1=y0+120
        mask=np.zeros_like(w);mask[y0:y1,x0:x1]=1
        w*=mask
        base+=w[...,None]*(np.array(c)/255-.93)
        weights+=w
    axes[0].imshow(np.clip(base,0,1),origin='lower',extent=(-60,60,0,90),interpolation='bilinear',aspect='auto')
    for k,b in enumerate(bins):
        col=k%3;row=k//3
        x0=-60+40*col;y0=45 if row==0 else 0
        ax=axes[1]
        face='#ece9ee' if b['state']=='unknown' else '#f4f4f4'
        ax.add_patch(Rectangle((x0,y0),40,45,facecolor=face,edgecolor='#888',lw=1.2))
        seq=b['colors'];cx=x0+20
        if any(c is not None for c in seq):
            for i,c in enumerate(seq):
                if c is not None:
                    ax.add_patch(Rectangle((cx-5,y0+3+(39*i/len(seq))),10,39/len(seq)+.05,
                                           facecolor=tuple(v/255 for v in c),edgecolor='none'))
            ax.add_patch(Rectangle((cx-5,y0+3),10,39,fill=False,edgecolor='#555',lw=.7))
        ax.text(x0+2,y0+41,str(k+1),fontsize=9,color='#555',va='top')
    for ax,ttl in zip(axes,('主导云色 · 分区内平滑','六分区云色时序 · 下早上晚')):
        ax.set_xlim(-60,60);ax.set_ylim(0,90)
        ax.set_xticks([-60,-20,20,60]);ax.set_yticks([0,45,90]);ax.grid(False)
        ax.set_xlabel('相对太阳方位角 °');ax.set_ylabel('观察者仰角 °');ax.set_title(ttl)
    fig.suptitle(title+'（候选受光，非完整三维光路验证）',fontsize=12)
    fig.tight_layout();return fig
