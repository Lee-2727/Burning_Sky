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


def projection_zone_illumination(cloud_cells, potential_km, bottom_rows, face_rows,
                                 minute_offsets, step_km=15.0):
    """One count per horizontal mid/high cloud zone, independent of layers.

    One lit underside: 1; only verified sun-facing surface: 1/3; else 0.
    A genuinely unknown bottom point is retained as unknown evidence and is
    not silently promoted to lit. For repeated layers, take MAX, never SUM.
    """
    limit=float(potential_km)
    if limit<=0: return {'zones':[], 'minute_weights':{},'unknown_counts':{}}
    zones=[]
    # Distance bins have no artificial duplication at the potential-range end.
    count=max(1,int(math.ceil(limit / step_km - 1e-10)))
    for i in range(count):
        lo=i*step_km; hi=min(limit,(i+1)*step_km)
        if hi<=lo:continue
        members=[c for c in cloud_cells
                 if c.get('top_km',0)>1.5 and c.get('cloud_cover',0)>0
                 and float(c['start_km'])<hi-1e-7
                 and float(c['end_km'])>lo+1e-7]
        if members:
            zones.append({'index':i,'start_km':lo,'end_km':hi})
    weights={}; unknown_counts={}
    # The bottom sampler returns the geographical position and its represented
    # width; a verified point lights its own horizontal zone, not all layers.
    for m in minute_offsets:
        values={z['index']:0.0 for z in zones}
        unresolved=set()
        for row in face_rows or ():
            if int(row.get('minute_offset',-999)) != m:continue
            key=row.get('target_key') or ()
            if len(key)<4 or not row.get('height_band_km'):continue
            if float(row['height_band_km'][1])<=float(row['height_band_km'][0]):continue
            lo,hi=float(key[0]),float(key[1])
            for zone in zones:
                if lo<zone['end_km']-1e-7 and hi>zone['start_km']+1e-7:
                    values[zone['index']]=max(values[zone['index']],1.0/3.0)
        for row in bottom_rows or ():
            if int(row.get('minute_offset',-999)) != m:continue
            pos=float(row.get('position_km',-999))
            status=str(row.get('status',''))
            width=max(0.,float(row.get('area_width_km') or 0.))
            key=row.get('target_key') or ()
            # A sampled region may lie on a zone boundary. Anchor to the
            # representative target position, not a neighboring cloud layer.
            # The last zone is inclusive at the far endpoint.
            for zone in zones:
                in_zone=(zone['start_km']-1e-7<=pos<zone['end_km']-1e-7 or
                         (abs(pos-limit)<1e-7 and abs(zone['end_km']-limit)<1e-7))
                if not in_zone:continue
                # Check this record really describes a cloud in this zone.
                if len(key)>=2 and not (float(key[0])<zone['end_km'] and
                                         float(key[1])>zone['start_km']):continue
                if status=='verified_bottom':
                    values[zone['index']]=1.0
                elif status.startswith('unresolved'):
                    unresolved.add(zone['index'])
        weights[m]=values
        unknown_counts[m]=len([i for i in unresolved if values[i]<1.0])
    return {'zones':zones,'minute_weights':weights,'unknown_counts':unknown_counts}


def local_illumination(profiles, event_dt, direction, utc_offset, lat, lon,
                       potential_km, minutes=30, samples_per_face=32,
                       verified_target_bands=None, verified_target_bottom_points=None):
    if not profiles or not event_dt or potential_km is None or potential_km<=0:
        return {'available':False,'reason':'缺少剖面、时间或有效霞云距','times':[]}
    cells=profile_cloud_cells(profiles,threshold=0.000001,
                              blur_horizontal_km=0.,blur_vertical_km=0.)
    # Every 15 km zone with at least one mid/high target counts ONCE in the
    # denominator. Multiple cloud layers at the same position never double it.
    minute_offsets=list(range(-minutes,1) if direction=='morning' else range(0,minutes+1))
    projection=projection_zone_illumination(cells,potential_km,
        verified_target_bottom_points,verified_target_bands,minute_offsets)
    zones=projection['zones']
    if not zones:
        return {'available':False,'reason':'霞云距内没有满足条件的中高云采样区','times':[]}
    result=[]
    for m in minute_offsets:
        values=projection['minute_weights'][m]
        # The denominator is the NUMBER of eligible horizontal zones.
        frac=_clip(sum(values.values())/len(zones))
        full_bottom=sum(1 for v in values.values() if v>=1.-1e-9)
        partial_face=sum(1 for v in values.values() if 0<v<1.-1e-9)
        unknown_count=projection['unknown_counts'][m]
        # Area-weighted Sun angle is ONLY for color. The color calculation
        # never changes the zone weights or the strict geometry result.
        valid_bottom=[r for r in (verified_target_bottom_points or [])
                      if int(r.get('minute_offset',-999))==m
                      and r.get('status')=='verified_bottom']
        solar_angle=(sum(float(r['solar_altitude_deg']) for r in valid_bottom)/len(valid_bottom)
                     if valid_bottom else None)
        if solar_angle is None:
            valid_face=[r for r in (verified_target_bands or [])
                        if int(r.get('minute_offset',-999))==m]
            solar_angle=(sum(float(r['solar_altitude_deg']) for r in valid_face)/len(valid_face)
                         if valid_face else None)
        result.append({'datetime':event_dt+timedelta(minutes=m),
            'minutes_from_event':m,'fraction':frac,'strict_fraction':frac,
            'possible_fraction':_clip((sum(values.values())+unknown_count)/len(zones)),
            'front_face_fraction':(partial_face/len(zones))/3.,
            'bottom_unresolved_weight_fraction':unknown_count/len(zones),
            'bottom_lit_zone_count':full_bottom,'face_only_zone_count':partial_face,
            'eligible_zone_count':len(zones),
            'dominant':None,
            'representative_solar_altitude_deg':solar_angle,
            'possible_dominant':None})
    return {'available':True,'potential_km':float(potential_km),
        'target_count':len(zones),'target_weight':sum(z['end_km']-z['start_km'] for z in zones),
        'eligible_zones':zones,'times':result,'direction':direction,
        'method':'horizontal_15km_zone_fraction_bottom_1_face_only_one_third',
        'caveat':'同一水平采样区只计一次：云底受光=1，仅已严格验证的云锋面受光=1/3，否则=0；未知另外记录。云顶受光当前没有独立严格验证记录，不能据此虚增1/3。'}


def apply_to_curve(curve, illumination, atmospheric_factor=1., color_fn=None,
                   aod_color_quality=1., target_cloud_cover_pct=None):
    """Render illuminated *cloud area* rather than just illuminated fraction.

    fraction is the fraction of eligible cloud targets that receive light.
    Cloud area = fraction * mid/high cloud cover within the potential range;
    thus a satellite edit that removes half the target clouds must change the
    area even if the fraction of remaining clouds illuminated stays constant.
    This is a 1D sampled-corridor proxy, not satellite-measured sky area.
    """
    if not isinstance(curve,dict) or not illumination.get('available'): return curve
    import colorsys
    by_time={p['datetime'].replace(second=0,microsecond=0):p for p in illumination['times']}
    original=curve.get('samples') or []
    peak_fraction=max((p['fraction'] for p in illumination['times']),default=0.)
    if target_cloud_cover_pct is None:
        # Backwards-compatible estimate if a caller has no main-line cover.
        # Fallback uses eligible horizontal reach only; do not count layers twice.
        reach=max(1e-9,float(illumination.get('potential_km') or 0.))
        target_cloud_cover_pct=100.0*float(illumination.get('target_weight') or 0.)/reach
    target_cloud_cover_pct=max(0.0,min(100.0,float(target_cloud_cover_pct)))
    for point in original:
        dt=point.get('datetime')
        row=by_time.get(dt.replace(second=0,microsecond=0)) if dt is not None else None
        active=(row is not None and (point.get('minutes_from_event',0)>=0 if
                illumination.get('direction')=='evening' else point.get('minutes_from_event',0)<=0))
        frac=row['fraction'] if active else 0.
        point['geometry_valid']=bool(frac>1e-5)
        # Use a consistent 0–100% sampled-corridor area proxy throughout the
        # timeline, peak selection, and the 5% foreground cutoff.
        absolute_area_pct=frac*target_cloud_cover_pct if active else 0.0
        point['tintable_cloud_area']=round(absolute_area_pct,6)
        # Per-minute color belongs to the full set of lit zones; do not let
        # one dominant cloud redefine the area, peak or tint hue.
        solar_alt=(row.get('representative_solar_altitude_deg') if row else None)
        point['cloud_color_source'] = ({'method':'all_verified_lit_zones',
                                        'representative_solar_altitude_deg':round(float(solar_alt),3)}
                                       if active and frac>0 and solar_alt is not None else None)
        if active and frac>0:
            point['brightness']=round(_clip(frac*atmospheric_factor,0.,1.)*100.,1)
            if color_fn and solar_alt is not None:
                base=color_fn(solar_alt)
                rr,gg,bb=[v/255. for v in base]
                h,l,s=colorsys.rgb_to_hls(rr,gg,bb)
                r,g,b=colorsys.hls_to_rgb(h,l,_clip(s*(0.08+0.92*aod_color_quality)))
                point['color']=(round(255*r),round(255*g),round(255*b))
        else:
            point['brightness']=0.
        # Two independent timeline layers. The foreground threshold is DISPLAY
        # ONLY; do not modify the official ratio, probability, scale or peak.
        # The strict per-target minute evidence is already included in row.fraction.
        point['strict_light_window']=bool(active and frac > 1e-8)
        point['colored_cloud_area_pct']=round(absolute_area_pct,3)
        point['illuminated_target_fraction_pct']=round(100.0*frac,3) if active else 0.0
        point['major_coloration_valid']=bool(point['strict_light_window'] and
                                              point['colored_cloud_area_pct'] >= 5.0)
        point['local_cloud_illumination_fraction']=round(frac,6)
        point['local_cloud_strict_fraction']=round(row['strict_fraction'],6) if row else 0.
        point['local_cloud_possible_fraction']=round(row['possible_fraction'],6) if row else 0.
        point['sunward_face_illumination_fraction']=round(row['front_face_fraction'],6) if row else 0.
        point['cloud_bottom_unresolved_fraction']=round(row['bottom_unresolved_weight_fraction'],6) if row else 0.
    # Preserve all actually verified per-minute rays. The 5% threshold above
    # controls only the vivid major-colour overlay; it must not erase weak
    # verified light, or disconnect the strict minute chronology.
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
    curve['geometry_gate_diagnostic']={'mode':'spherical_cloud_bottom_area_plus_independent_sunward_face',
        'front_face_peak_fraction':max((p.get('front_face_fraction',0.) for p in illumination['times']),default=0.),
        'bottom_area_unresolved_peak_fraction':max((p.get('bottom_unresolved_weight_fraction',0.) for p in illumination['times']),default=0.),
        'target_count':illumination['target_count'],'potential_range_km':illumination['potential_km'],
        'peak_fraction':peak_fraction,'target_cover_pct':round(target_cloud_cover_pct,3),'peak_colored_area_pct':round(max((p.get('colored_cloud_area_pct',0.) for p in original),default=0.),3),'strict_peak':max(p['strict_fraction'] for p in illumination['times']),
        'possible_peak':max(p['possible_fraction'] for p in illumination['times']),
        'caveat':illumination['caveat']}
    curve['note']='严格球面逐点云底受光为主，云锋面独立诊断；无部分云量透射，纵轴为抽样云底受光面积代理（非真实二维云面积）。'
    return curve
