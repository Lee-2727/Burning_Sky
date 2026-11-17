# -*- coding: utf-8 -*-
"""View-direction candidate irradiance from already verified rays + cached OM raster.

The *same* event-window solar geometry is reused; no per-bearing HTTP calls.
A source ray verified for one target never certifies a different target:
new off-axis / antisolar cloud targets remain explicitly *candidates* until
an actual 3-D vertical ray check can be completed at their coordinates.
"""
from __future__ import annotations

from datetime import timedelta
import math
import numpy as np

from burning_sky.algorithm import solar_position
from burning_sky.spherical_raytrace import _ray_height_km, _earth_occluded
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


def _target_height_for_minute(traces, minute):
    eligible = [float(t['target_height_km']) for t in traces
                if isinstance(t, dict) and isinstance(t.get('minute_offset'), (float, int))
                and abs(float(t['minute_offset']) - minute) < 2.6
                and isinstance(t.get('target_height_km'), (float, int))
                and math.isfinite(float(t['target_height_km']))
                and float(t['target_height_km']) > 1.5]
    # Without a height from a validated ray, this is an *explicit* proxy,
    # never described as an established cloud-top or a verified light path.
    return float(np.median(eligible)) if eligible else 6.0, bool(eligible)


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


def _projected_solar_ray_support(field, lat, lon, sun_az, sun_alt, height_km,
                                 upstream_km=700., step_km=50.):
    """Areal-cover proxy along the *candidate target's actual solar ray*.

    This never asserts that a cloud with 70% grid cover blocks exactly 70%
    of a pencil beam. Unobserved nodes remain unknown, not clear.
    """
    from desktop.spatial_area_analysis import _destination
    if _earth_occluded(height_km, sun_alt, upstream_km):
        return None, 'earth_occluded'
    nodes = np.arange(step_km, upstream_km + .001, step_km)
    coords = np.array([_destination(lat, lon, sun_az, float(d)) for d in nodes])
    covers = field.lookup(coords[:,0], coords[:,1])
    finite = np.isfinite(covers).all(axis=1)
    if not np.all(finite):
        return None, 'upstream_uncovered'
    # Heuristic vertical plausibility using existing coarse low/mid/high bands;
    # NOT an invented cloud base or fully solved 3-D cloud obstruction.
    heights = np.array([_ray_height_km(height_km, sun_alt, float(d)) for d in nodes])
    possible_low = (heights >= 0.) & (heights <= 3.5)
    possible_mid = (heights >= 2.) & (heights <= 9.)
    possible_high = (heights >= 5.) & (heights <= 16.)
    obst = np.maximum.reduce([np.where(possible_low,covers[:,0],0.),
                              np.where(possible_mid,covers[:,1],0.),
                              np.where(possible_high,covers[:,2],0.)])
    # No hard-coded 85% conflict/illumination threshold and no premature
    # 250-km hole success: every node to 700 km contributes to screening.
    strength = max(0., 1. - (.55*float(np.mean(obst)) + .45*float(np.max(obst)))/100.)
    if not math.isfinite(strength):
        return None, 'invalid'
    # Even if the ray has low cloud-cover proxy, it must terminate ABOVE
    # the plausible cloudy atmosphere, on the outward-rising part of the ray.
    rising = math.radians(sun_alt) + upstream_km/EARTH_KM > 0
    if not rising or float(heights[-1]) < 18.:
        return None, 'upper_boundary_unresolved'
    return strength, 'horizontal_candidate'


def compass_from_verified_rays(data, latitude, longitude, event_dt, direction,
                               verified_rays=None, utc_offset=8., max_cells=350):
    """Allocate candidate *illuminated target clouds* to actual view azimuths.

    Original strictly verified target rays determine seed minutes/heights.
    Other geographic targets (including backfire clouds opposite the sun) are
    projected through OM, but remain unverified 2.5D hypotheses. No weather
    API is called here. Morning and evening both scan their 30-minute windows.
    """
    if direction not in ('morning','evening'):
        raise ValueError('direction must be morning or evening')
    field = CloudField(data)
    targets = _regional_targets(field, latitude, longitude, max_cells=max_cells)
    traces = [r for r in (verified_rays or []) if isinstance(r,dict)]
    verified_minutes = {int(r['minute_offset']) for r in traces if isinstance(r.get('minute_offset'),(int,float))}
    offsets = range(-30, 1, 5) if direction=='morning' else range(0, 31, 5)
    # Continue to detect *new* OM cloud-hole candidates when the original
    # 1-D geometry missed all source rays; do not call them verified.
    target_totals = np.zeros(8, dtype=float)
    target_weights = np.zeros(8, dtype=float)
    eligible = np.zeros(8, dtype=int)
    measured = np.zeros(8, dtype=int)
    obstructed = np.zeros(8, dtype=int)
    uncertain = np.zeros(8, dtype=int)
    cloudy = np.zeros(8, dtype=int)
    candidate_count=0; ray_checks=0; seed_count=0; backfire_targets=0
    for minute in offsets:
        dt = event_dt + timedelta(minutes=minute)
        height,seed = _target_height_for_minute(traces,minute)
        if seed:seed_count+=1
        _,sun_az_obs=solar_position(dt,utc_offset,float(latitude),float(longitude))
        for target in targets:
            sector=int((target['bearing_deg']+22.5)//45)%8
            # Each target cell contributes its *viewable* cloud area, not the
            # direction of the upstream hole (which can be offset from it).
            weight = 1. / max(30.,target['distance_km'])
            eligible[sector]+=1
            cover = max(target['mid'],target['high'])/100.
            if cover<.05:
                target_weights[sector]+=weight
                continue
            cloudy[sector]+=1
            alt, solar_az = solar_position(dt,utc_offset,target['lat'],target['lon'])
            if direction=='morning' and not (0.<=solar_az<180.):
                continue
            if direction=='evening' and not (180.<=solar_az<360.):
                continue
            support,status = _projected_solar_ray_support(
                field,target['lat'],target['lon'],solar_az,alt,height)
            ray_checks += 1
            if support is None:
                if status == 'earth_occluded':
                    target_weights[sector]+=weight
                    obstructed[sector]+=1
                else:
                    uncertain[sector]+=1
                continue
            measured[sector]+=1
            target_weights[sector]+=weight
            if (target['bearing_deg']-sun_az_obs+360.)%360. > 90. and (target['bearing_deg']-sun_az_obs+360.)%360. < 270.:
                backfire_targets+=1
            # Original field cloud weighting and low-cloud visibility *proxy*.
            # Does not alter existing regular probability/color/scale formulas.
            visibility = max(0., 1.-.55*(target['low']/100.))
            target_totals[sector] += weight*cover*visibility*support
            candidate_count+=1
    bins=[]
    for (name,bearing),total,denom,n_eligible,n_checked in zip(
            HEADINGS,target_totals,target_weights,eligible,measured):
        # Unobserved directions are unknown rather than a fabricated zero.
        i=bearing//45
        # No cloud in the *observed* local sector is a real zero. But a cloudy
        # direction whose source beams leave the OM raster is *unknown*.
        if denom<=0 or n_eligible==0 or (cloudy[i]>0 and n_checked==0 and uncertain[i]>0):
            value=None
        else:
            value=round(float(100*total/denom),1)
        bins.append({'name':name,'bearing':bearing,'relative_score':value,
                     'candidate_targets':int(n_checked), 'cloud_cells':int(n_eligible),
                     'coverage':float(n_checked/max(1,n_eligible)),
                     'unknown_source_paths':int(uncertain[i]),
                     'earth_blocked_paths':int(obstructed[i]),
                     'evidence':'om_projected_candidate'})
    sun_az=solar_position(event_dt,utc_offset,float(latitude),float(longitude))[1]
    # Original ray-target locations are along the event-axis section. Mark
    # that one view sector as already containing strict 3-D evidence; every
    # other projected OM target is still a candidate, not certified sunlight.
    for sector in bins:
        offset=abs((float(sector['bearing'])-sun_az+180.)%360.-180.)
        sector['view_type']=('反烧候选' if offset>=112.5 else
                             '正向候选' if offset<=67.5 else '侧向候选')
    strict_sector=int((sun_az+22.5)//45)%8
    if traces:
        bins[strict_sector]['strict_original_ray_seeds']=len(traces)
    return {'sectors':bins,'model':field.source.get('domain'),
            'run':field.source.get('reference_time'),
            'valid':field.source.get('valid_time'),
            'direction':direction,'sun_azimuth_deg':sun_az,
            'seeded_verified_minutes':seed_count,
            'strict_source_rays':len(traces),
            'evaluated_solar_paths':ray_checks,
            'candidate_illuminated_cells':candidate_count,
            'backfire_candidate_checks':backfire_targets,
            'method':'projected_target_clouds_from_solar_rays_v2',
            'note':('先按真实太阳方向检查各目标云的上游700km二维云场，再按目标云观察方位汇总。'
                    '正烧、侧烧、反烧共用同一几何；原始严格光路用于时间与高度参考，'
                    '新增离轴/反向目标尚无各自完整垂直剖面，不得称为严格已验证通光或发生概率。'
                    '所有分钟沿用单个OM有效时刻的云场，云层时间演变未解析。')}
