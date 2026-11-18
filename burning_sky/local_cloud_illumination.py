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
                       potential_km, minutes=30, samples_per_face=32,
                       verified_target_bands=None, verified_target_bottom_points=None):
    if not profiles or not event_dt or potential_km is None or potential_km <= 0:
        return {'available': False, 'reason': '缺少剖面、时间或有效霞云距', 'times': []}
    # No smoothing across exact user-edited clear corridors.
    cells=profile_cloud_cells(profiles,threshold=0.000001,blur_horizontal_km=0.,blur_vertical_km=0.)
    blocker_cells=profile_cloud_cells(profiles,threshold=25.0,blur_horizontal_km=0.,blur_vertical_km=0.)
    # Explicit verified face bands: missing entries mean NO validated light.
    # The legacy 25% cloud threshold is retained only for obstruction testing,
    # not for deciding whether thin target clouds exist or count as area.
    verified_index={}
    if verified_target_bands is not None:
        for record in verified_target_bands:
            key=(int(record['minute_offset']), tuple(round(float(x),6) for x in record['target_key']))
            verified_index.setdefault(key,[]).append(tuple(record['height_band_km']))
    # A face-height band does not quantify the illuminated CLOUD BOTTOM.
    # The strict scanner now sends distinct verified underside area strips.
    bottom_index={}
    if verified_target_bottom_points is not None:
        for record in verified_target_bottom_points:
            key=(int(record['minute_offset']),tuple(round(float(x),6) for x in record['target_key']))
            bottom_index.setdefault(key,[]).append(record)
    event_az=solar_position(event_dt,utc_offset,lat,lon)[1]
    limit=float(potential_km)
    # Keep the original cell identity.  In the previous integrated build the
    # target was copied to a new dict, so `c is target` was always false and
    # the target cloud was incorrectly treated as its own upstream blocker.
    # Use the original continuous-face experiment's threshold/height selection.
    targets=[]
    for cell in cells:
        a=max(0.,float(cell['start_km'])); b=float(cell['end_km'])
        if a>=limit-1e-7 or b<=a or float(cell['top_km'])<=1.5 or float(cell['cloud_cover'])<=0.:
            continue
        low=float(cell['bottom_km']); high=float(cell['top_km'])
        if high<=low: continue
        # Preserve the original upstream obstacle; clip only the target face.
        # A cell crossing potential_km is partly a valid local cloud target.
        target=dict(cell)
        target['_original_cell']=cell
        target['end_km']=min(b,limit)
        target['target_weight']=(target['end_km']-a)*_clip(float(cell['cloud_cover'])/100.)
        targets.append(target)
    den=sum(t['target_weight'] for t in targets)
    if den<=0: return {'available':False,'reason':'霞云距内没有满足连续云面实验条件的中高云剖面','times':[]}
    result=[]
    for m in range(-minutes,1) if direction=='morning' else range(0,minutes+1):
        dt=event_dt+timedelta(minutes=m)
        # Cloud targets stay at the original geographic sampled axis.
        az=event_az
        numerator=0.; strict_numerator=0.; possible_numerator=0.
        face_numerator=0.; unknown_bottom_weight=0.
        biggest=None; biggest_possible=None
        colored_solar_alt_weight=0.0; colored_solar_alt_sum=0.0
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
            analysis=analyze_target_face(target,[c for c in blocker_cells if float(c['end_km'])>face+1e-7],solar_alt)
            fraction=_clip(analysis.get('possible_face_height_fraction_proxy',0.))
            if verified_target_bands is not None:
                target_key=tuple(round(float(target[k]),6) for k in
                                 ('start_km','end_km','bottom_km','top_km'))
                bands=verified_index.get((m,target_key),[])
                # Union the actual height intervals (a target may have several).
                bounded=sorted((max(a,float(lo)),min(b,float(hi))) for lo,hi in bands
                               if min(b,float(hi))>max(a,float(lo)))
                total=0.; last=None
                for lo,hi in bounded:
                    if last is None: last=[lo,hi]
                    elif lo<=last[1]: last[1]=max(last[1],hi)
                    else:
                        total+=last[1]-last[0];last=[lo,hi]
                if last is not None: total+=last[1]-last[0]
                # Verified full-corridor target band is the hard gate; local
                # analytic interval can only reduce it, never create light.
                fraction=min(fraction,_clip(total/max(1e-12,b-a)))
            # Keep face illumination as a SEPARATE diagnostic.  Never use
            # its vertical band fraction as the cloud bottom's area fraction.
            face_fraction=fraction
            # Cloud colour must follow the SUN AT THE ACTUALLY ILLUMINATED
            # UNDERSIDE POSITION.  The old version assigned the far-face Sun
            # angle to every strip, making local clouds jump to unrelated hues.
            effective_solar_alt=solar_alt
            if verified_target_bottom_points is not None:
                target_key=tuple(round(float(target[k]),6) for k in
                                 ('start_km','end_km','bottom_km','top_km'))
                points=bottom_index.get((m,target_key),[])
                # Strict cloud-bottom observations carry the real horizontal
                # interval they represent. Never normalize by a fixed count.
                total_width=max(1e-12,float(target['end_km'])-float(target['start_km']))
                verified_points=[p for p in points if p['status']=='verified_bottom']
                lit_width=sum(float(p.get('area_width_km',0.)) for p in verified_points)
                unknown_width=sum(float(p.get('area_width_km',0.))
                                  for p in points if p['status'].startswith('unresolved'))
                if verified_points and lit_width>0:
                    effective_solar_alt=sum(float(p['solar_altitude_deg'])*float(p.get('area_width_km',0.))
                                            for p in verified_points)/lit_width
                # Missing samples remain unknown rather than silently dark.
                represented=sum(float(p.get('area_width_km',0.)) for p in points)
                unknown_width+=max(0.,total_width-represented)
                fraction=_clip(lit_width/total_width)
                unresolved_fraction=_clip(unknown_width/total_width)
                unknown_bottom_weight+=target['target_weight']*unresolved_fraction
            strict_fraction=fraction
            face_numerator+=target['target_weight']*face_fraction
            # Diagnostic upper bound: only fully overcast upstream cells are
            # assumed to be definite opaque obstacles; not a probability.
            hard=[c for c in blocker_cells if float(c['end_km'])>face+1e-7 and float(c['cloud_cover'])>=99.999]
            possible_fraction=_clip(analyze_target_face(target,hard,solar_alt).get('possible_face_height_fraction_proxy',0.))
            if verified_target_bands is not None:
                possible_fraction=min(possible_fraction,face_fraction)
            if verified_target_bottom_points is not None:
                # Underside upper bound: unresolved strips MAY be illuminated.
                # Sun-facing height bands are a separate physical surface.
                possible_fraction=_clip(fraction+unresolved_fraction)
            possible_dominance=target['target_weight']*possible_fraction
            if biggest_possible is None or possible_dominance>biggest_possible['dominance']:
                biggest_possible={'dominance':possible_dominance,'face_km':face,
                                  'solar_altitude_deg':solar_alt,'height_km':(a,b),
                                  'illumination_fraction':possible_fraction}
            w=target['target_weight']; numerator+=w*fraction
            strict_numerator+=w*strict_fraction; possible_numerator+=w*possible_fraction
            dominance=w*fraction
            if dominance>0:
                colored_solar_alt_sum+=dominance*effective_solar_alt
                colored_solar_alt_weight+=dominance
            if biggest is None or dominance>biggest['dominance']:
                biggest={'dominance':dominance,'face_km':face,'solar_altitude_deg':effective_solar_alt,
                         'height_km':(a,b),'illumination_fraction':fraction,
                         'cloud_cover_pct':100*target['cloud_cover']/100 if False else target['cloud_cover']}
        frac=_clip(numerator/den); lower=_clip(strict_numerator/den); upper=_clip(possible_numerator/den)
        result.append({'datetime':dt,'minutes_from_event':m,'fraction':frac,
                       'strict_fraction':lower,'possible_fraction':upper,
                       'front_face_fraction':_clip(face_numerator/den),
                       'bottom_unresolved_weight_fraction':_clip(unknown_bottom_weight/den),
                       'dominant':biggest if biggest and biggest['dominance']>1e-9 else None,
                       'representative_solar_altitude_deg':(colored_solar_alt_sum/colored_solar_alt_weight if colored_solar_alt_weight>0 else None),
                       'possible_dominant':biggest_possible if biggest_possible and biggest_possible['dominance']>1e-9 else None})
    return {'available':True,'potential_km':limit,'target_count':len(targets),
            'target_weight':den,'times':result,'method':'spherical_bottom_surface_area_strict_target_minute_gate',
            'caveat':'云底水平面积条带逐点严格球面受光；云锋面单独诊断不代替云底；25%云量遮挡假设；离线剖面非实测二维云面积'}


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
        # The target weights are distance(km) times cover(0-1), accumulated
        # across eligible altitude bands; clamp overlapping bands at 100%.
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
        dominant=row.get('dominant') if row else None
        # Passive diagnostics: trace which target cloud supplied the minute's
        # hue. Does not change probability, tint area, or RGB computation.
        if dominant and active:
            point['cloud_color_source'] = {
                'target_face_km': round(float(dominant['face_km']), 2),
                'height_band_km': [round(float(v), 2) for v in dominant['height_km']],
                'target_solar_altitude_deg': round(float(dominant['solar_altitude_deg']), 3),
                'target_illuminated_area_strip_fraction': round(float(dominant['illumination_fraction']), 4),
            }
        else:
            point['cloud_color_source'] = None
        if dominant and active:
            # Brightness represents the dominant illuminated cloud's light,
            # NOT the total colored-cloud area. Atmospheric transmission
            # is an independent brightness/colour adjustment.
            point['brightness']=round(_clip(dominant['illumination_fraction']*atmospheric_factor,0.,1.)*100.,1)
            if color_fn:
                base=color_fn(row.get('representative_solar_altitude_deg') if row.get('representative_solar_altitude_deg') is not None else dominant['solar_altitude_deg'])
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
