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
    """Height above spherical surface along ray from local target toward sun.

    The target is at radius R + base. Ray direction is the local solar direction;
    positive distance follows the sun-facing azimuth. A ray below the local horizon
    is not considered a direct-light path.
    """
    alt = math.radians(solar_alt_deg)
    r0 = EARTH_RADIUS_KM + target_base_km
    # Local tangent-plane components; exact spherical radial distance along the ray.
    radial = r0 + distance_km * math.sin(alt)
    tangential = distance_km * math.cos(alt)
    return math.sqrt(radial*radial+tangential*tangential)-EARTH_RADIUS_KM

def trace_at_time(samples, target_base_km, dt_local, utc_offset_hours, origin_lat, origin_lon,
                  max_distance_km=None, step_km=5.0, cloud_threshold_pct=25.0):
    """Trace one real solar position and test sampled cloud-layer intersections."""
    alt, az = solar_position(dt_local, utc_offset_hours, origin_lat, origin_lon)
    ordered = sorted((s for s in samples if isinstance(s.get('distance_km'), (int,float))),
                     key=lambda s: float(s['distance_km']))
    if max_distance_km is not None:
        ordered = [s for s in ordered if float(s['distance_km']) <= max_distance_km]
    path=[]; first_block=None
    # Negative solar altitude is essential for dawn/dusk illumination of elevated clouds.
    # Reject only when the ray actually intersects the spherical Earth.
    for s in ordered:
        d=float(s['distance_km']); h=_ray_height_km(target_base_km,alt,d)
        lat=s.get('lat'); lon=s.get('lon')
        if lat is None or lon is None:
            lat,lon=destination(origin_lat,origin_lon,az,d)
        layers=[]; blocked=False; reasons=[]; layer_data_complete=True
        # Minimum ray radius between target and this sample catches Earth
        # intersection even if the discrete endpoints are above terrain.
        angle=math.radians(alt); r0=EARTH_RADIUS_KM+target_base_km
        closest=max(0.0,min(d,-r0*math.sin(angle)))
        min_h=math.sqrt(max(0.0,r0*r0+closest*closest+2*r0*closest*math.sin(angle)))-EARTH_RADIUS_KM
        if min_h < 0:
            blocked=True; reasons.append("球面光路穿过地球曲面")
        # The local target cloud is the object being illuminated, not an upstream blocker.
        if d <= 0.01:
            path.append({"distance_km":d,"lat":lat,"lon":lon,"ray_height_km":h,
                "cloud_cover_low":s.get("cloud_cover_low"),"cloud_cover_mid":s.get("cloud_cover_mid"),
                "cloud_cover_high":s.get("cloud_cover_high"),"intersected_layers":[],
                "blocked":False,"layer_data_complete":True,"reason":"本地目标云点，不作为上游遮挡物"})
            continue
        for name,(lo,hi,key) in LAYER_BANDS_KM.items():
            cover=s.get(key)
            if not isinstance(cover,(int,float)):
                layer_data_complete=False
                continue
            # A sampled layer blocks only when the ray's altitude intersects its approximate band.
            if lo <= h <= hi and float(cover) >= cloud_threshold_pct:
                blocked=True; layers.append(name); reasons.append(f"{name}云量{float(cover):.0f}%≥{cloud_threshold_pct:g}%且光线高度{h:.2f}km落入{lo:g}-{hi:g}km层")
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
    return {"time":dt_local.isoformat(),"solar_altitude_deg":alt,"solar_azimuth_deg":az,
            "target_cloud_base_km":target_base_km,"status":status,
            "first_block":first_block,"path":path,
            "checked_to_km":max((float(row["distance_km"]) for row in path),default=0),"upstream_unverified":True,"model_note":"仅确认已有采样距离内的遮挡状态；采样终点以远至太阳未覆盖，不能宣称全光路无遮挡。"}

def scan_window(samples, target_base_km, event_dt, utc_offset_hours, lat, lon,
                before_minutes=30, after_minutes=30, step_minutes=1, max_distance_km=None):
    """Minute-resolved local solar-position scan around sunrise/sunset event."""
    results=[]
    for m in range(-before_minutes, after_minutes+1, step_minutes):
        dt=event_dt+timedelta(minutes=m)
        results.append(trace_at_time(samples,target_base_km,dt,utc_offset_hours,lat,lon,max_distance_km))
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
        rows=sorted(result.get('path', []), key=lambda r:r['distance_km'])
        if len(rows)<3: continue
        clear=[]
        for r in rows:
            h=r.get('ray_height_km')
            if not isinstance(h,(int,float)) or h<0 or not r.get('layer_data_complete'):
                clear.append(False);continue
            ok=True
            for name,(lo,hi,key) in LAYER_BANDS_KM.items():
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
