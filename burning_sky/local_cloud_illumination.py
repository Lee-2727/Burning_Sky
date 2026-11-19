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


def _convex_hull(points):
    points=sorted(set(points))
    if len(points)<=2:
        return points
    def cross(o,a,b):
        return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    lower=[]
    for point in points:
        while len(lower)>=2 and cross(lower[-2],lower[-1],point)<=1e-14:
            lower.pop()
        lower.append(point)
    upper=[]
    for point in reversed(points):
        while len(upper)>=2 and cross(upper[-2],upper[-1],point)<=1e-14:
            upper.pop()
        upper.append(point)
    return lower[:-1]+upper[:-1]


def _apparent_patch_solid_angle(start_km, end_km, bottom_km, top_km,
                                transverse_width_km):
    """Solid angle of a rectangular cloud patch, in steradians.

    The centreline profile does not provide a measured cross-track cloud
    boundary. The patch width is therefore the 15-km sampling width. Its
    vertices are projected to (azimuth, sin(apparent elevation)); polygon area
    in this coordinate system is solid angle. Spherical-Earth elevation is
    used so distant clouds are not projected with a flat-Earth horizon.
    """
    from .sky_six import apparent_elevation
    a=max(0.0,float(start_km)); b=max(a,float(end_km))
    bottom=max(0.0,float(bottom_km)); top=max(bottom,float(top_km))
    half=max(0.0,float(transverse_width_km))/2.0
    if b<=a or top<=bottom or half<=0:
        return 0.0
    points=[]
    for x in (a,b):
        for y in (-half,0.0,half):
            horizontal=math.hypot(x,y)
            azimuth=math.atan2(y,x) if horizontal>1e-12 else 0.0
            for height in (bottom,top):
                elevation=apparent_elevation(horizontal,height)
                points.append((azimuth,math.sin(math.radians(elevation))))
    hull=_convex_hull(points)
    if len(hull)<3:
        return 0.0
    area=abs(sum(hull[i][0]*hull[(i+1)%len(hull)][1]-
                 hull[(i+1)%len(hull)][0]*hull[i][1]
                 for i in range(len(hull)))*0.5)
    return max(0.0,float(area))


def _unique_vertical_targets(cloud_cells):
    """Select one lowest meaningful mid/high target per horizontal column.

    Upper stacked layers remain in the complete cloud field for ray blocking;
    they are not additional target clouds or additional visual-area entries.
    """
    from .sky_six import _exposed_cloud_target_cells
    all_targets=[dict(c) for c in (cloud_cells or [])
                 if float(c.get('top_km',0) or 0)>1.5
                 and float(c.get('cloud_cover',0) or 0)>0]
    support=[c for c in all_targets if float(c.get('cloud_cover',0) or 0)>=25.0]
    return _exposed_cloud_target_cells(all_targets,support)


def projection_zone_illumination(cloud_cells, potential_km, bottom_rows, face_rows,
                                 minute_offsets, step_km=15.0, include_faces=False):
    """One count per horizontal mid/high cloud zone, independent of layers.

    Standard sunrise/sunset is bottom-only: a strictly verified cloud bottom
    contributes 1; side/top/front-face evidence contributes zero. A vertical
    column is represented by its one lowest meaningful target cloud. The
    visual weight is the apparent solid angle of that target patch, estimated
    from its distance, vertical extent, cloud cover, and a 15-km cross-track
    width. It is a view-angle proxy, not a reconstructed 3-D cloud footprint.
    """
    limit=float(potential_km)
    if limit<=0:
        return {'zones':[], 'minute_weights':{}, 'unknown_counts':{},
                'event_geometric_fraction':0.0,'event_visual_fraction':0.0}
    targets,stacking_diag=_unique_vertical_targets(cloud_cells)
    targets=[c for c in targets if float(c.get('top_km',0) or 0)>1.5
             and float(c.get('start_km',0) or 0)<limit-1e-7]
    count=max(1,int(math.ceil(limit/step_km-1e-10)))
    zones=[]
    for i in range(count):
        lo=i*step_km; hi=min(limit,(i+1)*step_km)
        if hi<=lo:
            continue
        pieces=[]
        covered_area_km=0.0
        weighted_center=weighted_bottom=weighted_top=weight_sum=0.0
        for cell in targets:
            a=max(lo,float(cell['start_km']))
            b=min(hi,limit,float(cell['end_km']))
            if b<=a+1e-8:
                continue
            cover=_clip(float(cell.get('cloud_cover',0) or 0)/100.0)
            if cover<=0:
                continue
            length=b-a
            area_weight=length*cover
            pieces.append({'start_km':a,'end_km':b,
                'bottom_km':float(cell['bottom_km']),'top_km':float(cell['top_km']),
                'cloud_cover_pct':round(cover*100.0,3)})
            covered_area_km+=area_weight
            weighted_center+=((a+b)*0.5)*area_weight
            weighted_bottom+=float(cell['bottom_km'])*area_weight
            weighted_top+=float(cell['top_km'])*area_weight
            weight_sum+=area_weight
        if pieces and weight_sum>0:
            # Convert fractional cloud occupancy to a compact equivalent patch:
            # its horizontal footprint area equals (zone area × cloud fraction).
            # This makes a sparse cloud physically smaller instead of applying a
            # second arbitrary distance multiplier to its angular area.
            zone_span=hi-lo
            coverage_fraction=_clip(covered_area_km/max(zone_span,1e-9))
            linear_scale=math.sqrt(coverage_fraction)
            center=weighted_center/weight_sum
            radial_length=zone_span*linear_scale
            radial_a=max(lo,center-radial_length/2.0)
            radial_b=min(hi,center+radial_length/2.0)
            transverse_width=step_km*linear_scale
            mean_bottom=weighted_bottom/weight_sum
            mean_top=weighted_top/weight_sum
            visual_weight=_apparent_patch_solid_angle(
                radial_a,radial_b,mean_bottom,mean_top,transverse_width)
            zones.append({'index':i,'start_km':lo,'end_km':hi,
                'target_pieces':pieces,'target_cover_pct':round(coverage_fraction*100.0,3),
                'visual_weight_sr':visual_weight,
                'visual_weight_deg2':visual_weight*(180.0/math.pi)**2,
                'visual_patch_radial_km':[round(radial_a,4),round(radial_b,4)],
                'visual_patch_transverse_width_km':round(transverse_width,4),
                'visual_patch_bottom_top_km':[round(mean_bottom,4),round(mean_top,4)]})
    weights={}; unknown_counts={}; bottom_lit_counts={}; face_only_counts={}
    for m in minute_offsets:
        values={z['index']:0.0 for z in zones}
        unresolved=set(); face_zones=set()
        for row in face_rows or ():
            if int(row.get('minute_offset',-999)) != m:continue
            key=row.get('target_key') or ()
            if len(key)<4 or not row.get('height_band_km'):continue
            if float(row['height_band_km'][1])<=float(row['height_band_km'][0]):continue
            lo,hi=float(key[0]),float(key[1])
            for zone in zones:
                if lo<zone['end_km']-1e-7 and hi>zone['start_km']+1e-7:
                    face_zones.add(zone['index'])
                    if include_faces:
                        values[zone['index']]=max(values[zone['index']],1.0/3.0)
        for row in bottom_rows or ():
            if int(row.get('minute_offset',-999)) != m:continue
            pos=float(row.get('position_km',-999))
            status=str(row.get('status',''))
            key=row.get('target_key') or ()
            for zone in zones:
                in_zone=(zone['start_km']-1e-7<=pos<zone['end_km']-1e-7 or
                         (abs(pos-limit)<1e-7 and abs(zone['end_km']-limit)<1e-7))
                if not in_zone:continue
                if len(key)>=2 and not (float(key[0])<zone['end_km'] and
                                         float(key[1])>zone['start_km']):
                    continue
                if status=='verified_bottom':
                    values[zone['index']]=1.0
                elif status.startswith('unresolved'):
                    unresolved.add(zone['index'])
        weights[m]=values
        unknown_counts[m]=len([i for i in unresolved if values[i]<1.0])
        bottom_lit_counts[m]=sum(1 for v in values.values() if v>=1.0-1e-9)
        face_only_counts[m]=len([i for i in face_zones if values.get(i,0.0)<1.0-1e-9])

    # Event-wide accumulation: a horizontal zone counts once if its cloud
    # bottom was strictly verified at ANY minute in the complete dawn/dusk
    # window. This is intentionally separate from the per-minute peak.
    ever_values={z['index']:0.0 for z in zones}
    ever_unknown=set()
    for m,values in weights.items():
        for idx,value in values.items():
            ever_values[idx]=max(ever_values.get(idx,0.0),float(value))
        for idx in range(len(zones)):
            if unknown_counts.get(m,0):
                # The exact unresolved IDs are retained below in the per-zone
                # state; unknown_counts remains the compact minute diagnostic.
                pass
    # Rebuild the unresolved-zone union directly from source rows so that a
    # zone unknown at one time is not silently described as a proven zero.
    for row in bottom_rows or ():
        if str(row.get('status','')).startswith('unresolved'):
            try:
                pos=float(row.get('position_km',-999))
            except (TypeError,ValueError):
                continue
            for zone in zones:
                if zone['start_km']-1e-7<=pos<zone['end_km']-1e-7:
                    ever_unknown.add(zone['index'])
    lit_ids={i for i,v in ever_values.items() if v>1e-8}
    total_visual=sum(float(z.get('visual_weight_sr',0.0)) for z in zones)
    lit_visual=sum(float(z.get('visual_weight_sr',0.0))*ever_values.get(z['index'],0.0)
                   for z in zones)
    event_geometric=len(lit_ids)/len(zones) if zones else 0.0
    event_visual=lit_visual/total_visual if total_visual>1e-15 else event_geometric
    event_possible_ids=lit_ids | ever_unknown
    event_possible_visual=sum(float(z.get('visual_weight_sr',0.0))
                               for z in zones if z['index'] in event_possible_ids)
    event_possible_geometric=len(event_possible_ids)/len(zones) if zones else 0.0
    event_possible_visual=event_possible_visual/total_visual if total_visual>1e-15 else event_possible_geometric
    return {'zones':zones,'minute_weights':weights,'unknown_counts':unknown_counts,
            'bottom_lit_counts':bottom_lit_counts,'face_only_counts':face_only_counts,
            'stacking_diagnostic':stacking_diag,'event_zone_values':ever_values,
            'event_lit_zone_count':len(lit_ids),'event_unknown_zone_count':len(ever_unknown-lit_ids),
            'event_geometric_fraction':_clip(event_geometric),
            'event_visual_fraction':_clip(event_visual),
            'event_possible_geometric_fraction':_clip(event_possible_geometric),
            'event_possible_visual_fraction':_clip(event_possible_visual),
            'visual_total_solid_angle_sr':total_visual,
            'visual_lit_solid_angle_sr':lit_visual,
            'include_faces':bool(include_faces)}


def local_illumination(profiles, event_dt, direction, utc_offset, lat, lon,
                       potential_km, minutes=30, samples_per_face=32,
                       verified_target_bands=None, verified_target_bottom_points=None,
                       include_faces=False):
    if not profiles or not event_dt or potential_km is None or potential_km<=0:
        return {'available':False,'reason':'缺少剖面、时间或有效霞云距','times':[]}
    cells=profile_cloud_cells(profiles,threshold=0.000001,
                              blur_horizontal_km=0.,blur_vertical_km=0.)
    minute_offsets=list(range(-minutes,1) if direction=='morning' else range(0,minutes+1))
    projection=projection_zone_illumination(cells,potential_km,
        verified_target_bottom_points,verified_target_bands,minute_offsets,
        include_faces=include_faces)
    zones=projection['zones']
    if not zones:
        return {'available':False,'reason':'霞云距内没有满足条件的中高云采样区','times':[]}
    total_visual=sum(float(z.get('visual_weight_sr',0.0)) for z in zones)
    result=[]
    for m in minute_offsets:
        values=projection['minute_weights'][m]
        frac=_clip(sum(values.values())/len(zones))
        visual_lit=sum(float(z.get('visual_weight_sr',0.0))*values.get(z['index'],0.0)
                       for z in zones)
        visual_frac=_clip(visual_lit/total_visual) if total_visual>1e-15 else frac
        full_bottom=projection['bottom_lit_counts'].get(m,0)
        partial_face=projection['face_only_counts'].get(m,0)
        unknown_count=projection['unknown_counts'][m]
        valid_bottom=[r for r in (verified_target_bottom_points or [])
                      if int(r.get('minute_offset',-999))==m
                      and r.get('status')=='verified_bottom']
        solar_angle=(sum(float(r['solar_altitude_deg']) for r in valid_bottom)/len(valid_bottom)
                     if valid_bottom else None)
        if solar_angle is None and include_faces:
            valid_face=[r for r in (verified_target_bands or [])
                        if int(r.get('minute_offset',-999))==m]
            solar_angle=(sum(float(r['solar_altitude_deg']) for r in valid_face)/len(valid_face)
                         if valid_face else None)
        result.append({'datetime':event_dt+timedelta(minutes=m),
            'minutes_from_event':m,'fraction':frac,'strict_fraction':frac,
            'visual_fraction':visual_frac,
            'possible_fraction':_clip((sum(values.values())+unknown_count)/len(zones)),
            'front_face_fraction':(partial_face/len(zones))/3.,
            'bottom_unresolved_weight_fraction':unknown_count/len(zones),
            'bottom_lit_zone_count':full_bottom,'face_only_zone_count':partial_face,
            'face_contributes_to_fraction':bool(include_faces),
            'eligible_zone_count':len(zones),
            'visual_lit_solid_angle_sr':visual_lit,
            'visual_total_solid_angle_sr':total_visual,
            'dominant':None,'representative_solar_altitude_deg':solar_angle,
            'possible_dominant':None})
    return {'available':True,'potential_km':float(potential_km),
        'target_count':len(zones),'target_weight':sum(z['end_km']-z['start_km'] for z in zones),
        'eligible_zones':zones,'times':result,'direction':direction,
        'event_geometric_fraction':projection['event_geometric_fraction'],
        'event_visual_fraction':projection['event_visual_fraction'],
        'event_zone_values':projection.get('event_zone_values',{}),
        'event_possible_geometric_fraction':projection['event_possible_geometric_fraction'],
        'event_possible_visual_fraction':projection['event_possible_visual_fraction'],
        'event_lit_zone_count':projection['event_lit_zone_count'],
        'event_unknown_zone_count':projection['event_unknown_zone_count'],
        'visual_total_solid_angle_sr':projection['visual_total_solid_angle_sr'],
        'visual_lit_solid_angle_sr':projection['visual_lit_solid_angle_sr'],
        'stacking_diagnostic':projection.get('stacking_diagnostic'),
        'method':('horizontal_15km_zone_fraction_bottom_1_face_1third' if include_faces
                  else 'horizontal_15km_zone_fraction_bottom_only'),
        'visual_method':'spherical_projected_solid_angle_compact_equivalent_cloud_patch',
        'include_faces':bool(include_faces),
        'caveat':('同一水平采样区只计一次；标准逆光只接受云底严格受光；视觉权重按球面视角面积估算。'
                  '剖面只有一维，横向宽度暂按15km采样宽度近似；云量比例用于缩放等效云块的水平足迹，云层高度来自气压层网格。')}


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
        point['visual_illuminated_target_fraction_pct']=round(100.0*float(row.get('visual_fraction',0.0)),3) if active else 0.0
        point['visual_colored_cloud_area_pct']=round(float(row.get('visual_fraction',0.0))*target_cloud_cover_pct,3) if active else 0.0
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
    curve['geometry_gate_diagnostic']={'mode':('spherical_cloud_bottom_plus_face' if illumination.get('include_faces')
                                                   else 'spherical_cloud_bottom_only'),
        'front_face_peak_fraction':max((p.get('front_face_fraction',0.) for p in illumination['times']),default=0.),
        'bottom_area_unresolved_peak_fraction':max((p.get('bottom_unresolved_weight_fraction',0.) for p in illumination['times']),default=0.),
        'target_count':illumination['target_count'],'potential_range_km':illumination['potential_km'],
        'peak_fraction':peak_fraction,'target_cover_pct':round(target_cloud_cover_pct,3),'peak_colored_area_pct':round(max((p.get('colored_cloud_area_pct',0.) for p in original),default=0.),3),'strict_peak':max(p['strict_fraction'] for p in illumination['times']),
        'possible_peak':max(p['possible_fraction'] for p in illumination['times']),
        'caveat':illumination['caveat']}
    curve['note']=('严格球面逐点云底受光与云锋面分别计权；纵轴为抽样云底/云面受光面积代理（非真实二维云面积）。'
                   if illumination.get('include_faces') else
                   '常规火烧云仅按严格球面逐点云底受光计量；云锋面、侧面和顶部不参与可染色占比，纵轴为抽样云底受光面积代理（非真实二维云面积）。')
    return curve
