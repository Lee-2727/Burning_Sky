# -*- coding: utf-8 -*-
"""Spherical-Earth cloud-layer ray tracing for local diagnostic use.

Cloud cover is treated as a grid-cell obstruction proxy, not as a resolved 3-D cloud.
"""
from __future__ import annotations
import math
import os
import json
from datetime import timedelta
from .algorithm import solar_position

EARTH_RADIUS_KM = 6371.0088
LAYER_BANDS_KM = {
    "low": (0.0, 2.0, "cloud_cover_low"),
    "mid": (2.0, 6.0, "cloud_cover_mid"),
    "high": (6.0, 12.0, "cloud_cover_high"),
}

def destination(lat_deg, lon_deg, bearing_deg, distance_km):
    """Great-circle destination from origin, bearing clockwise from north."""
    p1, l1, br, d = map(math.radians, (lat_deg, lon_deg, bearing_deg)), None, None, None
    # unpack explicitly (the tuple conversion above is intentionally avoided for clarity)
    p1 = math.radians(lat_deg); l1 = math.radians(lon_deg); br = math.radians(bearing_deg)
    delta = distance_km / EARTH_RADIUS_KM
    p2 = math.asin(math.sin(p1)*math.cos(delta)+math.cos(p1)*math.sin(delta)*math.cos(br))
    l2 = l1 + math.atan2(math.sin(br)*math.sin(delta)*math.cos(p1), math.cos(delta)-math.sin(p1)*math.sin(p2))
    return math.degrees(p2), ((math.degrees(l2)+540)%360)-180

def _ray_height_km(target_base_km, solar_alt_deg, distance_km):
    """Exact straight solar ray expressed in a *surface-arc-length* section.

    distance_km = R * central_angle, NOT distance travelled along the ray.
    Height is relative to the reference sphere, using the same geocentric
    datum at every distance. Atmospheric refraction is excluded.
    """
    theta = float(distance_km) / EARTH_RADIUS_KM
    alpha = math.radians(float(solar_alt_deg))
    denominator = math.cos(theta + alpha)
    if denominator <= 0:
        return float('inf')
    return ((EARTH_RADIUS_KM + float(target_base_km)) *
            math.cos(alpha) / denominator - EARTH_RADIUS_KM)


def _earth_occluded(target_base_km, solar_alt_deg, surface_distance_km):
    """True if any segment between the target and this arc position enters Earth."""
    alpha = math.radians(float(solar_alt_deg))
    theta = float(surface_distance_km) / EARTH_RADIUS_KM
    # On the outward ray the closest point to Earth occurs at theta=-alpha
    # for negative alpha; the exact tangent height is r0*cos(alpha)-R.
    if alpha >= 0:
        return False
    min_theta = min(theta, -alpha)
    return _ray_height_km(target_base_km, solar_alt_deg,
                          EARTH_RADIUS_KM * min_theta) < -1e-8


def profile_cloud_cells(profiles, threshold=25.0, blur_horizontal_km=10.0, blur_vertical_km=0.2):
    """Use EXACTLY the cell rectangles drawn by the Tkinter pressure-profile view.

    These are coarse grid-cell proxies, not resolved physical cloud boundaries.
    """
    valid = sorted((p for p in (profiles or []) if p.get('levels') and
                    isinstance(p.get('distance_km'), (int, float))),
                   key=lambda p: float(p['distance_km']))
    # Mild anisotropic Gaussian smoothing of *cloud cover*, not of cloud geometry.
    # Include neighbouring clear samples so boundaries can soften in both directions.
    observations = [
        (float(p['distance_km']), float(lv['height_agl_km']),
         float(lv['cloud_cover']))
        for p in valid for lv in p['levels']
        if isinstance(lv.get('height_agl_km'), (int, float))
        and isinstance(lv.get('cloud_cover'), (int, float))
    ]
    def softened_cover(d, h, original):
        if blur_horizontal_km <= 0 or blur_vertical_km <= 0:
            return original
        weighted = total = 0.0
        for xd, xh, cover in observations:
            dx = abs(xd-d) / blur_horizontal_km
            dz = abs(xh-h) / blur_vertical_km
            if dx > 3 or dz > 3:
                continue
            w = math.exp(-0.5 * (dx*dx + dz*dz))
            total += w*cover
            weighted += w
        return total/weighted if weighted > 1e-12 else original
    # Exact manual intervals are authoritative; the display-cell midpoint is
    # not an artificial cloud boundary. Disable blur for edited sections so a
    # neighbouring cloudy sample cannot bleed into an explicitly clear zone.
    intervals = next((p.get('manual_override_intervals') for p in valid
                      if p.get('manual_override_intervals')), [])
    if intervals:
        blur_horizontal_km = 0.0
        blur_vertical_km = 0.0
    out = []
    distances = [float(p['distance_km']) for p in valid]
    for i, prof in enumerate(valid):
        d = distances[i]
        left = (distances[i-1] + d)/2 if i else max(0., d-(distances[1]-d)/2 if len(distances)>1 else 0.)
        right = (d+distances[i+1])/2 if i+1<len(distances) else (d+(d-distances[i-1])/2 if i else d)
        levels = sorted((lv for lv in prof['levels'] if isinstance(lv.get('height_agl_km'), (int,float))),
                        key=lambda lv:lv['height_agl_km'])
        for j, lv in enumerate(levels):
            h=float(lv['height_agl_km']); cc=lv.get('cloud_cover');rh=lv.get('relative_humidity')
            # Humidity is retained as a diagnostic, but does not create an
            # opaque solid wall by itself when measured cloud cover is available.
            effective = softened_cover(d, h, float(cc)) if isinstance(cc, (int,float)) else None
            if effective is None:
                continue
            bottom=(float(levels[j-1]['height_agl_km'])+h)/2 if j else max(0.,h-.35)
            top=(h+float(levels[j+1]['height_agl_km']))/2 if j+1<len(levels) else h+.35
            # Split each display cell at every manual interval boundary.
            # Outside the edit, retain the original cloud cover; inside it,
            # use the exact override, independently of sampling-grid centres.
            boundaries=sorted(set([left,right]+[float(x) for row in intervals for x in row[:2]
                                            if left < float(x) < right]))
            for x,y in zip(boundaries,boundaries[1:]):
                mid=(x+y)/2
                segment_cover=float(lv.get('_original_cloud_cover',cc) or 0) if intervals else effective
                if intervals:
                    for a,b,low,med,high in intervals:
                        if float(a)<=mid<float(b):
                            if h<2: value=low
                            elif h<6: value=med
                            else: value=high
                            if value is not None: segment_cover=float(value)
                if segment_cover < threshold: continue
                out.append({'start_km':x,'end_km':y,'bottom_km':bottom,'top_km':top,
                            'cloud_cover':segment_cover,'raw_cloud_cover':cc,'sample_distance_km':d,'pressure_hpa':lv.get('pressure_hpa'),
                            'reconstructed_cloud':bool(lv.get('reconstructed_cloud'))})
    return out


def _cell_intersection(target_base_km, solar_alt_deg, cell, end_distance_km=None):
    """Check a continuous spherical ray against a rectangular display cloud cell."""
    a,b=cell['start_km'],cell['end_km']
    if end_distance_km is not None: b=min(b,float(end_distance_km))
    if b<=0 or b<a:return False
    # The ray has at most one minimum in this range; include it in range extrema.
    critical=max(a,min(b,-math.radians(solar_alt_deg)*EARTH_RADIUS_KM))
    hs=[_ray_height_km(target_base_km,solar_alt_deg,d) for d in (a,b,critical)]
    return min(hs)<=cell['top_km'] and max(hs)>=cell['bottom_km']


def trace_at_time(samples, target_base_km, dt_local, utc_offset_hours, origin_lat, origin_lon,
                  max_distance_km=None, step_km=5.0, cloud_threshold_pct=25.0, profiles=None):
    """Trace one real solar position and test sampled cloud-layer intersections."""
    alt, az = solar_position(dt_local, utc_offset_hours, origin_lat, origin_lon)
    ordered = sorted((s for s in samples if isinstance(s.get('distance_km'), (int,float))),
                     key=lambda s: float(s['distance_km']))
    if profiles:
        # Only assess the span covered by pressure profiles; outside is unverified,
        # not a fabricated cloud-free or cloud-blocked zone.
        profile_limit = max((float(p['distance_km']) for p in profiles if p.get('levels')), default=0.)
        ordered = [s for s in ordered if float(s['distance_km']) <= profile_limit]
    if max_distance_km is not None:
        ordered = [s for s in ordered if float(s['distance_km']) <= max_distance_km]
    # Profile-mode blockage is evaluated against the same cloud rectangles as GUI.
    # No invented 2-6km / 6-12km solid bands when pressure profiles exist.
    cells = profile_cloud_cells(profiles, cloud_threshold_pct) if profiles else []
    use_profiles = bool(profiles)
    path=[]; first_block=None
    # Negative solar altitude is essential for dawn/dusk illumination of elevated clouds.
    # Reject only when the ray actually intersects the spherical Earth.
    for s in ordered:
        d=float(s['distance_km']); h=_ray_height_km(target_base_km,alt,d)
        lat=s.get('lat'); lon=s.get('lon')
        if lat is None or lon is None:
            lat,lon=destination(origin_lat,origin_lon,az,d)
        layers=[]; blocked=False; reasons=[]; layer_data_complete=True
        # Earth occultation is checked along the entire arc, not just endpoints.
        theta_min = min(d / EARTH_RADIUS_KM, max(0.0, -math.radians(alt)))
        min_h = _ray_height_km(target_base_km, alt, theta_min * EARTH_RADIUS_KM)
        if _earth_occluded(target_base_km, alt, d):
            blocked=True; reasons.append("球面光路穿过地球曲面")
        # The local target cloud is the object being illuminated, not an upstream blocker.
        if d <= 0.01:
            path.append({"distance_km":d,"lat":lat,"lon":lon,"ray_height_km":h,
                "cloud_cover_low":s.get("cloud_cover_low"),"cloud_cover_mid":s.get("cloud_cover_mid"),
                "cloud_cover_high":s.get("cloud_cover_high"),"intersected_layers":[],
                "blocked":False,"layer_data_complete":True,"reason":"本地目标云点，不作为上游遮挡物"})
            continue
        for name,(lo,hi,key) in ({} if use_profiles else LAYER_BANDS_KM).items():
            cover=s.get(key)
            if not isinstance(cover,(int,float)):
                layer_data_complete=False
                continue
            # A sampled layer blocks only when the ray's altitude intersects its approximate band.
            if lo <= h <= hi and float(cover) >= cloud_threshold_pct:
                blocked=True; layers.append(name); reasons.append(f"{name}云量{float(cover):.0f}%≥{cloud_threshold_pct:g}%且光线高度{h:.2f}km落入{lo:g}-{hi:g}km层")
        if use_profiles:
            # A profile gap is not evidence of clear air when the profile is missing.
            # Profile cells are checked continuously, not only at sample points.
            for cell in cells:
                if cell['end_km'] > 0 and cell['start_km'] <= d and _cell_intersection(target_base_km,alt,cell,end_distance_km=d):
                    blocked=True
                    layers.append('pressure_profile')
                    reasons.append(f"气压层云块 {cell['sample_distance_km']:g}km / {cell['bottom_km']:.2f}-{cell['top_km']:.2f}km")
                    break
            layer_data_complete = bool(cells) and all(
                any(abs(float(p.get('distance_km',-1))-float(smp['distance_km']))<.01 and p.get('levels')
                    for p in profiles)
                for smp in ordered if float(smp['distance_km']) <= d)
        row={"distance_km":d,"lat":lat,"lon":lon,"ray_height_km":h,
             "cloud_cover_low":s.get('cloud_cover_low'),"cloud_cover_mid":s.get('cloud_cover_mid'),
             "cloud_cover_high":s.get('cloud_cover_high'),"intersected_layers":layers,
             "blocked":blocked,"layer_data_complete":layer_data_complete,
             "local_solar_azimuth_deg":az,"local_solar_altitude_deg":alt,
             "minimum_ray_height_km":min_h,
             "reason":"；".join(reasons) if reasons else "未命中达到阈值的云层高度带"}
        path.append(row)
        if blocked and first_block is None: first_block=row
    complete_path=bool(path) and all(r.get("layer_data_complete") for r in path)
    status = "blocked" if first_block else ("clear_path" if complete_path else "unresolved_data")
    return {"geometry_source":"pressure_profile_cells" if use_profiles else "fixed_layer_bands", "time":dt_local.isoformat(),"solar_altitude_deg":alt,"solar_azimuth_deg":az,
            "target_cloud_base_km":target_base_km,"status":status,
            "first_block":first_block,"path":path,
            "checked_to_km":max((float(row["distance_km"]) for row in path),default=0),"upstream_unverified":True,"model_note":"仅确认已有采样距离内的遮挡状态；采样终点以远至太阳未覆盖，不能宣称全光路无遮挡。"}

def scan_window(samples, target_base_km, event_dt, utc_offset_hours, lat, lon,
                before_minutes=30, after_minutes=30, step_minutes=1, max_distance_km=None, profiles=None, direction=None):
    """Minute-resolved local solar-position scan around sunrise/sunset event."""
    results=[]
    # Fire-cloud forecast: sunrise BEFORE the event; sunset AFTER the event only.
    if direction == 'morning': after_minutes=0
    elif direction == 'evening': before_minutes=0
    for m in range(-before_minutes, after_minutes+1, step_minutes):
        dt=event_dt+timedelta(minutes=m)
        results.append(trace_at_time(samples,target_base_km,dt,utc_offset_hours,lat,lon,max_distance_km,profiles=profiles))
    valid=results
    windows=[]; start=None; prev=None
    for r in valid:
        clear=r['status']=='clear_path'
        if clear and start is None: start=r['time']
        if not clear and start is not None:
            windows.append({"start":start,"end":prev}); start=None
        if not clear and start is None: pass
        prev=r['time']
    if start is not None: windows.append({"start":start,"end":prev})
    return {"scan_count":len(results),"results":results,"candidate_clear_windows":windows,
            "model_note":"clear_path仅代表已采样路径未命中遮挡阈值；不代表直至太阳无遮挡。"}

def render_schematic_map(scan_result, output_path, title="球面光线追踪示意图"):
    """Render a lightweight lat/lon-grid schematic without external map tiles."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return False
    rows=[]
    for result in scan_result.get('results',[]):
        rows.extend(result.get('path',[]))
    if not rows: return False
    lats=[float(r['lat']) for r in rows if r.get('lat') is not None]
    lons=[float(r['lon']) for r in rows if r.get('lon') is not None]
    if not lats or not lons:return False
    w,h=1200,760; pad=70
    im=Image.new('RGB',(w,h),(248,250,252)); dr=ImageDraw.Draw(im)
    try: font=ImageFont.truetype('msyh.ttc',18); small=ImageFont.truetype('msyh.ttc',13)
    except Exception: font=small=None
    lo1,lo2=min(lons),max(lons); la1,la2=min(lats),max(lats)
    if abs(lo2-lo1)<.05:lo1-=.05;lo2+=.05
    if abs(la2-la1)<.05:la1-=.05;la2+=.05
    lo1-=.35;lo2+=.35;la1-=.35;la2+=.35
    def xy(lon,lat):return (pad+(lon-lo1)/(lo2-lo1)*(w-2*pad),h-pad-(lat-la1)/(la2-la1)*(h-2*pad))
    # Bundled Natural Earth generalized country/coastline boundaries, offline.
    # This is geographic context, NOT an EC/GFS gridded cloud field.
    try:
        world_path=os.path.join(os.path.dirname(__file__),'world_coast_simplified.json')
        with open(world_path,encoding='utf-8') as geo_file: shapes=json.load(geo_file)
        for shape in shapes:
            polys=shape['coordinates'] if shape['type']=='MultiPolygon' else [shape['coordinates']]
            for polygon in polys:
                ring=polygon[0]
                if not ring:continue
                minx=min(q[0] for q in ring);maxx=max(q[0] for q in ring)
                miny=min(q[1] for q in ring);maxy=max(q[1] for q in ring)
                if maxx<lo1 or minx>lo2 or maxy<la1 or miny>la2:continue
                pts=[xy(q[0],q[1]) for q in ring]
                if len(pts)>2:dr.line(pts,fill=(173,186,180),width=2,joint='curve')
    except Exception:pass
    # Clouds are rendered as translucent sample footprints, not an invented 2-D forecast grid.
    try:
        from PIL import Image as _Image
        cloud_layer=_Image.new('RGBA',(w,h),(0,0,0,0))
        cloud_draw=ImageDraw.Draw(cloud_layer)
        samples={}
        for r in rows:samples[round(float(r['distance_km']),2)]=r
        for r in samples.values():
            x,y=xy(float(r['lon']),float(r['lat']))
            for j,(key,clr) in enumerate((('cloud_cover_low',(45,130,220)),('cloud_cover_mid',(150,85,205)),('cloud_cover_high',(240,150,50)))):
                cover=r.get(key)
                if not isinstance(cover,(int,float)) or cover<=0:continue
                rad=8+min(100,float(cover))*.22
                ox=(j-1)*9
                cloud_draw.ellipse((x+ox-rad,y-rad,x+ox+rad,y+rad),fill=(*clr,int(min(175,35+cover*1.3))))
        im=Image.alpha_composite(im.convert('RGBA'),cloud_layer).convert('RGB');dr=ImageDraw.Draw(im)
    except Exception:pass
    # geographic graticule
    for i in range(7):
        x=pad+i*(w-2*pad)/6; y=pad+i*(h-2*pad)/6
        dr.line((x,pad,x,h-pad),fill=(220,226,232),width=1); dr.line((pad,y,w-pad,y),fill=(220,226,232),width=1)
    # plot path for each minute; colored by status and show first blockage
    for result in scan_result.get('results',[]):
        path=result.get('path',[])
        pts=[xy(float(r['lon']),float(r['lat'])) for r in path]
        if len(pts)>1:
            clr=(34,139,94) if result.get('status')=='clear_path' else (210,70,60)
            dr.line(pts,fill=clr,width=2)
        fb=result.get('first_block')
        if fb:
            x,y=xy(float(fb['lon']),float(fb['lat'])); dr.ellipse((x-5,y-5,x+5,y+5),fill=(190,35,35),outline='white')
    # layer cloud-cover dots at sample locations
    uniq={}
    for r in rows: uniq[(round(float(r['lat']),4),round(float(r['lon']),4))]=r
    palette={'cloud_cover_low':(85,155,230),'cloud_cover_mid':(165,105,210),'cloud_cover_high':(230,145,55)}
    for (lat,lon),r in uniq.items():
        x,y=xy(lon,lat)
        vals=[(k,c) for k,c in palette.items() if isinstance(r.get(k),(int,float))]
        if vals:
            k,c=max(vals,key=lambda kv:float(r[kv[0]])); cover=max(0,min(100,float(r[k])))
            radius=3+cover/18
            dr.ellipse((x-radius,y-radius,x+radius,y+radius),fill=c,outline=(255,255,255))
    eq=equivalent_gaps(scan_result)
    if eq:
        best=max(eq,key=lambda g:g['size_km'])
        first=scan_result['results'][0]['path']
        ordered=sorted(first,key=lambda x:x['distance_km'])
        def near(d):return min(ordered,key=lambda r:abs(r['distance_km']-d))
        a,b=near(best['start_km']),near(best['end_km'])
        dr.line((xy(a['lon'],a['lat']),xy(b['lon'],b['lat'])),fill=(25,205,95),width=9)
        dr.text((pad,46),f"Equivalent gap: {best['start_km']:.0f}-{best['end_km']:.0f} km / size {best['size_km']:.0f} km",fill=(0,95,55),font=small)
    dr.text((pad,18),title,fill=(20,35,50),font=font)
    dr.text((pad,h-35),"Natural Earth 地理轮廓 + 离散采样云量足迹（非 Windy 连续云图）；线条为光路投影",fill=(65,75,85),font=small)
    im.save(output_path); return True


def equivalent_gaps(scan_result, min_gap_km=15.0, cloud_threshold_pct=25.0):
    """Conservative spatial equivalent: contiguous sampled intervals transparent
    at a verified clear time, not the entire 0..reach ray.

    The proxy is not a unique inversion of a three-dimensional cloud hole.
    """
    candidates=[]
    for result in scan_result.get('results', []):
        if result.get('status') != 'clear_path':
            continue
        profile_mode = result.get('geometry_source') == 'pressure_profile_cells'
        rows=sorted(result.get('path', []), key=lambda r:r['distance_km'])
        if len(rows)<3: continue
        clear=[]
        for r in rows:
            h=r.get('ray_height_km')
            if not isinstance(h,(int,float)) or h<0 or not r.get('layer_data_complete'):
                clear.append(False);continue
            ok=True
            for name,(lo,hi,key) in ({} if profile_mode else LAYER_BANDS_KM).items():
                cover=r.get(key)
                if lo<=h<=hi and (not isinstance(cover,(int,float)) or cover>=cloud_threshold_pct):
                    ok=False
            clear.append(ok and not r.get('blocked'))
        i=1
        while i<len(rows):
            if not clear[i]:
                i+=1;continue
            start=i
            while i+1<len(rows) and clear[i+1]:i+=1
            end=i
            i+=1
            # No extrapolation beyond the first/last sampled locations.
            a=float(rows[start]['distance_km']);b=float(rows[end]['distance_km'])
            if b-a>=min_gap_km:
                candidates.append({'start_km':round(a,1),'end_km':round(b,1),
                    'size_km':round(b-a,1),'source':'spherical_raytrace_equivalent',
                    'solar_altitude_deg':result['solar_altitude_deg'],
                    'time':result['time'],'equivalence_note':'逐点球面光路高度带的保守空间代理；不是精确云洞边界'})
    # Same spatial segment at multiple times is one candidate; retain longest.
    unique={}
    for g in candidates:
        key=(g['start_km'],g['end_km'])
        if key not in unique:unique[key]=g
    return sorted(unique.values(),key=lambda g:(-g['size_km'],g['start_km']))
