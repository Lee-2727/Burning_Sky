# -*- coding: utf-8 -*-
"""Local mid/high cloud illumination proxy from one spherical solar-ray field.

Target domain is the inferred potential cloud range.  Upstream clouds are
obstructions only, not additional colored-cloud targets.  Partial cloud cover
is uncertain: effective illumination is an optical-cover proxy, NOT calibrated
radiative transfer.  No additional meteorological requests are made.
"""
import math
from datetime import timedelta
from .spherical_raytrace import profile_cloud_cells, destination
from .critical_light_envelope import cloud_shadow_interval, inverse_target_height, analyze_target_face
from .algorithm import solar_position


def _clip(v, lo=0., hi=1.):
    return max(lo, min(hi, v))


def local_illumination(profiles, event_dt, direction, utc_offset, lat, lon,
                       potential_km, minutes=30, samples_per_face=32):
    if not profiles or not event_dt or potential_km is None or potential_km <= 0:
        return {'available': False, 'reason': '缺少剖面、时间或有效霞云距', 'times': []}
    # No smoothing across exact user-edited clear corridors.
    cells=profile_cloud_cells(profiles,threshold=25.0,blur_horizontal_km=0.,blur_vertical_km=0.)
    limit=float(potential_km)
    # Keep the original cell identity.  In the previous integrated build the
    # target was copied to a new dict, so `c is target` was always false and
    # the target cloud was incorrectly treated as its own upstream blocker.
    # Use the original continuous-face experiment's threshold/height selection.
    targets=[]
    for cell in cells:
        a=max(0.,float(cell['start_km'])); b=float(cell['end_km'])
        if b>limit+1e-7 or b<=a or float(cell['top_km'])<=1.5 or float(cell['cloud_cover'])<25.:
            continue
        low=float(cell['bottom_km']); high=float(cell['top_km'])
        if high<=low: continue
        cell['target_weight']=(b-a)*(high-low)*_clip(float(cell['cloud_cover'])/100.)
        targets.append(cell)
    den=sum(t['target_weight'] for t in targets)
    if den<=0: return {'available':False,'reason':'霞云距内没有满足连续云面实验条件的中高云剖面','times':[]}
    result=[]
    for m in range(-minutes,1) if direction=='morning' else range(0,minutes+1):
        dt=event_dt+timedelta(minutes=m)
        _,az=solar_position(dt,utc_offset,lat,lon)
        numerator=0.; strict_numerator=0.; possible_numerator=0.
        biggest=None
        for target in targets:
            face=target['end_km']
            p_lat,p_lon=destination(lat,lon,az,face)
            solar_alt,_=solar_position(dt,utc_offset,p_lat,p_lon)
            # Each target's illuminated face is sampled over its continuous
            # vertical extent, not replaced by a single pressure-level point.
            a=target['bottom_km'];b=target['top_km']; N=samples_per_face
            # Original analytic continuous-face interval subtraction.  The
            # target itself is excluded by identity, and only upstream cells
            # can shade the sun-facing target face.  Cloud-cover is NEVER a
            # light-transmission coefficient.
            analysis=analyze_target_face(target,cells,solar_alt)
            fraction=_clip(analysis.get('possible_face_height_fraction_proxy',0.))
            strict_fraction=fraction
            # Diagnostic upper bound: only fully overcast upstream cells are
            # assumed to be definite opaque obstacles; not a probability.
            hard=[c for c in cells if float(c['cloud_cover'])>=99.999]
            possible_fraction=_clip(analyze_target_face(target,hard,solar_alt).get('possible_face_height_fraction_proxy',0.))
            w=target['target_weight']; numerator+=w*fraction
            strict_numerator+=w*strict_fraction; possible_numerator+=w*possible_fraction
            dominance=w*fraction
            if biggest is None or dominance>biggest['dominance']:
                biggest={'dominance':dominance,'face_km':face,'solar_altitude_deg':solar_alt,
                         'height_km':(a,b),'illumination_fraction':fraction,
                         'cloud_cover_pct':100*target['cloud_cover']/100 if False else target['cloud_cover']}
        frac=_clip(numerator/den); lower=_clip(strict_numerator/den); upper=_clip(possible_numerator/den)
        result.append({'datetime':dt,'minutes_from_event':m,'fraction':frac,
                       'strict_fraction':lower,'possible_fraction':upper,
                       'dominant':biggest if biggest and biggest['dominance']>1e-9 else None})
    return {'available':True,'potential_km':limit,'target_count':len(targets),
            'target_weight':den,'times':result,'method':'continuous_target_faces_analytic_restored',
            'caveat':'原连续云面解析求交的严格无遮挡代理量；目标云自身不作为上游遮挡；云量仅用于目标权重，不作为透射率；非实测染色比例'}


def apply_to_curve(curve, illumination, atmospheric_factor=1., color_fn=None,
                   aod_color_quality=1.):
    if not isinstance(curve,dict) or not illumination.get('available'): return curve
    import colorsys
    by_time={p['datetime'].replace(second=0,microsecond=0):p for p in illumination['times']}
    original=curve.get('samples') or []
    peak_fraction=max((p['fraction'] for p in illumination['times']),default=0.)
    for point in original:
        dt=point.get('datetime')
        row=by_time.get(dt.replace(second=0,microsecond=0)) if dt is not None else None
        active=(row is not None and (point.get('minutes_from_event',0)>0 if
                illumination.get('direction')=='evening' else point.get('minutes_from_event',0)<0))
        frac=row['fraction'] if active else 0.
        point['geometry_valid']=bool(frac>1e-5)
        point['tintable_cloud_area']=round(frac*illumination['target_weight'],6) if active else 0.
        dominant=row.get('dominant') if row else None
        if dominant and active:
            # Brightness represents the dominant illuminated cloud's light,
            # NOT the total colored-cloud area. Atmospheric transmission
            # is an independent brightness/colour adjustment.
            point['brightness']=round(_clip(dominant['illumination_fraction']*atmospheric_factor,0.,1.)*100.,1)
            if color_fn:
                base=color_fn(dominant['solar_altitude_deg'])
                rr,gg,bb=[v/255. for v in base]
                h,l,s=colorsys.rgb_to_hls(rr,gg,bb)
                r,g,b=colorsys.hls_to_rgb(h,l,_clip(s*(0.08+0.92*aod_color_quality)))
                point['color']=(round(255*r),round(255*g),round(255*b))
        else:
            point['brightness']=0.
        point['local_cloud_illumination_fraction']=round(frac,6)
        point['local_cloud_strict_fraction']=round(row['strict_fraction'],6) if row else 0.
        point['local_cloud_possible_fraction']=round(row['possible_fraction'],6) if row else 0.
    # Keep the legacy 3%-of-peak colored-area visibility floor. This prevents
    # isolated sub-percent geometric slivers from producing a misleading
    # second twilight interval after the main cloud-color episode.
    _max_area=max((p['tintable_cloud_area'] for p in original),default=0.)
    _min_area=max(1e-8,.03*_max_area)
    for point in original:
        if point['tintable_cloud_area'] < _min_area:
            point['geometry_valid']=False
            point['brightness']=0.
            point['tintable_cloud_area']=0.
    peak_b=max((p['brightness'] for p in original),default=0.)
    for point in original:
        point['relative_brightness']=round(100*point['brightness']/peak_b,1) if peak_b else 0.
    valid=[p for p in original if p['geometry_valid']]
    # Primary peak criterion is colored area; saturation breaks near-ties.
    area_max=max((p['tintable_cloud_area'] for p in valid),default=0.)
    candidates=[p for p in valid if p['tintable_cloud_area']>=area_max*.92]
    def peak_key(p):
        rgb=p.get('color') or (128,128,128)
        h,l,s=colorsys.rgb_to_hls(*[c/255 for c in rgb])
        return (s,p['tintable_cloud_area'],p['brightness'])
    curve['peak']=max(candidates,key=peak_key) if candidates else None
    curve['raw_peak_brightness']=round(peak_b,1)
    curve['geometry_gate_diagnostic']={'mode':'continuous_local_target_faces',
        'target_count':illumination['target_count'],'potential_range_km':illumination['potential_km'],
        'peak_fraction':peak_fraction,'strict_peak':max(p['strict_fraction'] for p in illumination['times']),
        'possible_peak':max(p['possible_fraction'] for p in illumination['times']),
        'caveat':illumination['caveat']}
    curve['note']='球面连续目标云面解析光路；无部分云量透射；颜色由主导显色云太阳高度角及大气通透度决定，纵轴为亮度指数。'
    return curve
