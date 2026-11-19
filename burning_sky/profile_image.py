"""Web cloud-profile renderer matching the desktop diagnostic drawing style.

The renderer is display-only. Strict paths, geometric candidates, blocked
bottom samples and reconstructed cloud layers retain separate visual semantics.
"""
import json
import math
import os
from PIL import Image, ImageDraw, ImageFont
from .spherical_raytrace import profile_cloud_cells, _earth_occluded, _ray_height_km
from .profile_geometry import sample_ray_curve


def _font(size, bold=False):
    candidates = (['/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
                   '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                   '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf'] if bold else
                  ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                   '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                   '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'])
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _dashed_line(draw, xy, fill, width=2, dash=8, gap=5):
    if len(xy) < 2:
        return
    for a, b in zip(xy, xy[1:]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        length = math.hypot(dx, dy)
        if length < 1:
            continue
        ux, uy = dx/length, dy/length
        pos = 0.0
        while pos < length:
            end = min(length, pos+dash)
            draw.line((a[0]+ux*pos, a[1]+uy*pos,
                       a[0]+ux*end, a[1]+uy*end), fill=fill, width=width)
            pos += dash+gap


def render_profile(result, width=1600, height=900):
    """Render a desktop-style distance/height cross-section for Streamlit."""
    samples = sorted((result.get('samples') or []), key=lambda x: float(x.get('distance_km') or 0))
    raw_profiles = result.get('profiles') or []
    pmap = {p.get('distance_km'): p for p in raw_profiles if isinstance(p, dict)}
    profiles = []
    # Match the desktop's display-only fallback: use pressure layers when they
    # contain cloud evidence; otherwise show low/mid/high forecast bands.
    for sm in samples:
        prof = pmap.get(sm.get('distance_km'), {}) or {}
        levels = list(prof.get('levels') or [])
        pressure_has_cloud = any(isinstance(lv.get('cloud_cover'), (int, float))
                                 and lv['cloud_cover'] >= 25 for lv in levels)
        layer_has_cloud = any(isinstance(sm.get(k), (int, float)) and sm[k] >= 10
                              for k in ('cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high'))
        if levels and (pressure_has_cloud or not layer_has_cloud):
            profiles.append({**prof, 'levels': levels})
            continue
        fallback = []
        for key, h, pressure in (('cloud_cover_low', 1.0, 850),
                                 ('cloud_cover_mid', 4.5, 600),
                                 ('cloud_cover_high', 8.5, 300)):
            amount = sm.get(key)
            if isinstance(amount, (int, float)):
                fallback.append({'height_agl_km': h, 'cloud_cover': amount,
                                 'relative_humidity': None, 'pressure_hpa': pressure,
                                 'cloudy_candidate': amount >= 25})
        if fallback:
            profiles.append({'distance_km': sm.get('distance_km'),
                             'estimated_base_km': None,
                             'status': 'display_fallback_layer_bands',
                             'levels': fallback, 'fallback_layer_bands': True})
    profiles = sorted((p for p in profiles if p.get('levels') and
                       isinstance(p.get('distance_km'), (int, float))),
                      key=lambda p: float(p['distance_km']))
    if not profiles:
        return None

    inferred = result.get('inferred') or {}
    ray_records = []
    debug_path = inferred.get('spherical_raytrace_debug_json')
    if debug_path and os.path.isfile(debug_path):
        try:
            with open(debug_path, encoding='utf-8') as f:
                ray_records = json.load(f).get('results', [])
        except (OSError, ValueError, TypeError):
            ray_records = []

    all_verified = [r for r in (inferred.get('verified_primary_ray_traces') or [])
                    if isinstance(r, dict) and isinstance(r.get('solar_altitude_deg'), (int, float))
                    and isinstance(r.get('target_height_km'), (int, float))]
    # Standard backlit fire-cloud rays must originate at cloud bottoms.
    # Verified sunward faces are shown separately as reverse-burn evidence.
    verified_bottom = [r for r in all_verified
                       if r.get('surface') == 'cloud_bottom'
                       or r.get('status') == 'verified_cloud_bottom_spherical']
    verified_faces = [r for r in all_verified
                      if r.get('status') == 'verified_sunward_face_full_sampled_corridor']
    target = next((float(r['target_height_km']) for r in verified_bottom), None)
    if target is None:
        target = next((float(r['target_height_km']) for r in verified_faces), None)
    if target is None:
        target = next((float(r['target_cloud_base_km']) for r in ray_records
                       if isinstance(r.get('target_cloud_base_km'), (int, float))), None)
    if target is None:
        target = next((float(p['estimated_base_km']) for p in profiles
                       if float(p.get('distance_km') or 0) == 0 and
                       isinstance(p.get('estimated_base_km'), (int, float))), None)
    if target is None:
        target = 4.0

    left, right, top, bottom = 92, 118, 58, 125
    plot_w, plot_h = width-left-right, height-top-bottom
    distances = [float(p['distance_km']) for p in profiles]
    max_dist = max(distances) if distances else 1.0
    plot_dist = max_dist + ((distances[-1]-distances[-2])/2 if len(distances)>1 else 0)
    all_h = [float(lv.get('height_agl_km')) for p in profiles for lv in p.get('levels', [])
             if isinstance(lv.get('height_agl_km'), (int, float))]
    max_h = min(22.0, max(8.0, target+4.0, math.ceil(max(all_h or [10.0]))))
    def X(v): return left + (float(v)/plot_dist)*plot_w if plot_dist else left
    def Y(v): return top + plot_h - max(0.0, min(max_h, float(v)))/max_h*plot_h

    im = Image.new('RGB', (width, height), '#fbfcfe')
    d = ImageDraw.Draw(im)
    title_font, axis_font = _font(25, True), _font(16)
    small_font, legend_font = _font(13), _font(14)
    direction = result.get('direction')
    title = ('朝霞路径云层精细剖面' if direction == 'morning' else
             '晚霞路径云层精细剖面' if direction == 'evening' else
             '严格球面光路 · 云层剖面')
    model = str(result.get('query_model_label') or result.get('query_model_id') or '')
    d.text((left, 15), title, font=title_font, fill='#1e293b')
    d.text((left, 42), f"{result.get('place','')}  {model}  ｜横轴：沿太阳方位距离（km）｜纵轴：相对地面高度（km）",
           font=small_font, fill='#64748b')

    # Grid and axes, as in the desktop Tk diagnostic.
    for h in range(0, int(max_h)+1, 2):
        yy = Y(h)
        d.line((left, yy, left+plot_w, yy), fill='#e3e8ef', width=1)
        d.text((left-12, yy-8), f'{h} km', font=small_font, fill='#334155', anchor='ra')
    tick_step = 25 if max_dist <= 250 else (50 if max_dist <= 500 else 100)
    ticks = list(range(0, int(max_dist)+1, tick_step))
    if not ticks or ticks[-1] < max_dist:
        ticks.append(int(max_dist))
    for dist in ticks:
        xx = X(dist)
        d.line((xx, top, xx, top+plot_h), fill='#edf0f4', width=1)
        d.text((xx, top+plot_h+12), str(dist), font=small_font, fill='#334155', anchor='mt')
    d.line((left, top, left, top+plot_h), fill='#475569', width=2)
    d.line((left, top+plot_h, left+plot_w, top+plot_h), fill='#475569', width=2)
    d.text((left+plot_w/2, height-21), '沿太阳方位距离（km）', font=axis_font,
           fill='#1f2937', anchor='mt')
    ylab = Image.new('RGBA', (140, 26), (0,0,0,0))
    ImageDraw.Draw(ylab).text((4, 3), '相对高度（km）', font=small_font, fill='#1f2937')
    ylab = ylab.rotate(90, expand=True)
    im.paste(ylab, (18, int(top+plot_h/2-ylab.height/2)), ylab)

    # Draw the same cloud cells used by the geometric ray tracer.
    cells = profile_cloud_cells(profiles, threshold=25.0, blur_horizontal_km=0., blur_vertical_km=0.)
    for cell in cells:
        if cell['top_km'] <= 0 or cell['bottom_km'] > max_h:
            continue
        cc = cell.get('cloud_cover')
        if not isinstance(cc, (int, float)):
            cc = 50.0
        gray = int(250-205*max(0, min(100, float(cc)))/100)
        x0, x1 = X(cell['start_km']), X(cell['end_km'])
        y0, y1 = Y(min(max_h, cell['top_km'])), Y(max(0, cell['bottom_km']))
        d.rectangle((x0, y0, x1, y1), fill=(gray, gray, gray), outline='#ffffff', width=1)
        if cell.get('reconstructed_cloud'):
            _dashed_line(d, [(x0,y0),(x1,y0),(x1,y1),(x0,y1),(x0,y0)], '#e11d48', 2, 7, 5)

    # Estimated bases are markers only; never substitute them for cloud layers.
    for prof in profiles:
        base = prof.get('estimated_base_km')
        if isinstance(base, (int, float)) and 0 <= base <= max_h:
            xx, yy = X(prof['distance_km']), Y(base)
            d.line((xx-4, yy-4, xx+4, yy+4), fill='#d83c3c', width=2)
            d.line((xx+4, yy-4, xx-4, yy+4), fill='#d83c3c', width=2)

    # Verified bottom samples: red horizontal strip is the ONLY official
    # primary-view illuminated surface. Blocked/unresolved samples remain distinct.
    bottom_rows = inferred.get('verified_bottom_sample_traces') or []
    lit_segments = set()
    seen_markers = set()
    for row in bottom_rows:
        key = row.get('target_key') or []
        if len(key) < 4:
            continue
        try:
            pos = float(row['position_km'])
            width_km = float(row.get('area_width_km') or 0.0)
            base_km = float(key[2])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        status = str(row.get('status', ''))
        if status == 'verified_bottom' and width_km > 0:
            seg = (max(float(key[0]), pos-width_km/2), min(float(key[1]), pos+width_km/2), base_km)
            lit_segments.add(seg)
        elif status in ('cloud_blocked', 'earth_or_source_horizon') or status.startswith('unresolved'):
            marker_key = (round(pos,2), round(base_km,3), status)
            if marker_key in seen_markers:
                continue
            seen_markers.add(marker_key)
            xx, yy = X(pos), Y(base_km)
            color = '#e2893b' if status == 'cloud_blocked' else ('#a8b0c6' if status.startswith('unresolved') else '#64748b')
            d.line((xx-5, yy-5, xx+5, yy+5), fill=color, width=2)
            d.line((xx+5, yy-5, xx-5, yy+5), fill=color, width=2)
    for start_km, end_km, base_km in sorted(lit_segments):
        if end_km > start_km and 0 <= base_km <= max_h:
            d.line((X(start_km), Y(base_km), X(end_km), Y(base_km)), fill='#ef4444', width=5)

    # Draw all final verified rays at their own cloud target, not a guessed
    # shared cloud base. Grouping is display-only; no ray is promoted by it.
    strict_count = 0
    for ray in verified_bottom:
        start = ray.get('target_bottom_position_km', ray.get('target_face_km'))
        try:
            start, h0, alt = float(start), float(ray['target_height_km']), float(ray['solar_altitude_deg'])
            far = min(plot_dist, float(ray.get('source_check_km') or plot_dist))
        except (TypeError, ValueError):
            continue
        if not (0 <= start < far and 0 < h0 <= max_h):
            continue
        pts=[]
        for j in range(401):
            dist=start+(far-start)*j/400
            if _earth_occluded(h0, alt, dist-start):
                if len(pts)>1: d.line(pts, fill='#7434ac', width=3)
                pts=[]; continue
            h=_ray_height_km(h0,alt,dist-start)
            if math.isfinite(h) and 0 <= h <= max_h:
                pts.append((X(dist),Y(h)))
            else:
                if len(pts)>1: d.line(pts, fill='#7434ac', width=3)
                pts=[]
        if len(pts)>1:
            d.line(pts, fill='#7434ac', width=3)
        strict_count += 1

    face_verified_count = 0
    for ray in verified_faces:
        try:
            face, h0, alt = float(ray['target_face_km']), float(ray['target_height_km']), float(ray['solar_altitude_deg'])
            far = min(plot_dist, float(ray.get('source_check_km') or plot_dist))
        except (TypeError, ValueError):
            continue
        if not (0 <= face < far and 0 < h0 <= max_h):
            continue
        pts=[]
        for j in range(301):
            dist=face+(far-face)*j/300
            if _earth_occluded(h0,alt,dist-face):
                break
            h=_ray_height_km(h0,alt,dist-face)
            if math.isfinite(h) and 0 <= h <= max_h:
                pts.append((X(dist),Y(h)))
        _dashed_line(d,pts,'#8b5cf6',2,5,5)
        face_verified_count += 1

    # Geometric face traces are candidates only: dashed/dotted, never the red
    # official underside marks. This preserves the desktop distinction.
    candidates = [r for r in (inferred.get('spherical_face_plot_traces') or [])
                  if isinstance(r, dict) and all(isinstance(r.get(k),(int,float))
                  for k in ('target_face_km','target_height_km','solar_altitude_deg'))]
    for ray in candidates[::max(1, math.ceil(len(candidates)/28))][:28]:
        face, h0, alt = float(ray['target_face_km']), float(ray['target_height_km']), float(ray['solar_altitude_deg'])
        if not 0 <= face < plot_dist or not 0 < h0 <= max_h:
            continue
        pts=[]
        for j in range(181):
            dist=face+(plot_dist-face)*j/180
            if _earth_occluded(h0,alt,dist-face):
                break
            h=_ray_height_km(h0,alt,dist-face)
            if math.isfinite(h) and 0 <= h <= max_h:
                pts.append((X(dist),Y(h)))
        _dashed_line(d,pts,'#b16ad4',2,4,5)

    # If the legacy debug file has contiguous clear-path minutes, shade only
    # between the outer boundary rays of each run, matching desktop behavior.
    ordered = sorted((r for r in ray_records if isinstance(r.get('solar_altitude_deg'),(int,float))
                      and isinstance(r.get('time'),str)), key=lambda r:r['time'])
    runs=[]; current=[]
    from datetime import datetime as _dt
    for ray in ordered:
        if ray.get('status') != 'clear_path':
            if len(current)>=2: runs.append(current)
            current=[]; continue
        if current:
            try:
                gap=(_dt.fromisoformat(ray['time'])-_dt.fromisoformat(current[-1]['time'])).total_seconds()
            except (ValueError,TypeError): gap=float('inf')
            if not 1 <= gap <= 61:
                if len(current)>=2: runs.append(current)
                current=[]
        current.append(ray)
    if len(current)>=2:runs.append(current)
    for group in runs:
        alts=[float(r['solar_altitude_deg']) for r in group]
        lo,hi=min(alts),max(alts)
        if hi-lo<1e-8: continue
        for col in range(1,181):
            dist=plot_dist*col/181
            if _earth_occluded(target,lo,dist) or _earth_occluded(target,hi,dist):
                continue
            h1,h2=_ray_height_km(target,lo,dist),_ray_height_km(target,hi,dist)
            if not (math.isfinite(h1) and math.isfinite(h2)): continue
            low,high=max(0.,min(h1,h2)),min(max_h,max(h1,h2))
            if high<=low: continue
            xx=X(dist)
            for k in range(1,7):
                yy=Y(low+(high-low)*k/7)
                d.ellipse((xx-1,yy-1,xx+1,yy+1),fill='#bb398d')

    # OM horizontal/vertical conflicts remain orange crosses at their real nodes.
    gate = inferred.get('strict_primary_path_diagnostic') or {}
    for row in (gate.get('horizontal_conflict_examples') or [])[:24]:
        try: xx, yy = X(float(row['distance_km'])), Y(float(row['ray_height_km']))
        except (KeyError,TypeError,ValueError): continue
        d.line((xx-6,yy-6,xx+6,yy+6),fill='#ec770f',width=3)
        d.line((xx+6,yy-6,xx-6,yy+6),fill='#ec770f',width=3)

    # Target base and range boundaries.
    if 0 <= target <= max_h:
        xx, yy = X(0), Y(target)
        d.ellipse((xx-6,yy-6,xx+6,yy+6),fill='#101820',outline='white',width=2)
        d.text((xx+12,yy-18),f'目标云底 {target:.2f} km',font=small_font,fill='#111827')
    boundaries = result.get('boundaries') or {}
    for key, color, label, yoff in (('fire_cloud_range_km','#0f766e','霞云距',8),
                                    ('cloud_edge_distance_km','#b45309','云边距',26)):
        dist=boundaries.get(key)
        if isinstance(dist,(int,float)):
            xx=X(dist)
            d.line((xx,top,xx,top+plot_h),fill=color,width=2)
            d.text((min(left+plot_w-115,max(left+5,xx+5)),top+yoff),
                   f'{label} {dist:.0f}km',font=small_font,fill=color)

    # Cloud-cover grayscale legend and concise key.
    cbx, cby, cbh = width-70, top+38, max(100, plot_h-90)
    for k in range(100):
        gray=int(250-205*k/100)
        d.line((cbx,cby+k*cbh/100,cbx,cby+(k+1)*cbh/100+1),fill=(gray,gray,gray),width=14)
    d.text((cbx-8,cby-20),'云量 %',font=small_font,fill='#334155')
    for v in (0,25,50,75,100):
        d.text((cbx+12,cby+v*cbh/100-7),str(v),font=small_font,fill='#334155')
    legend_rows=[(height-91,[('#7434ac','严格验证云底光线'),
                               ('#8b5cf6','严格验证云锋面（反烧专用）'),
                               ('#b16ad4','几何候选光线')]),
                  (height-65,[('#ef4444','已受光云底采样带'),
                               ('#e2893b','云层遮挡采样点')])]
    for legend_y, items in legend_rows:
        lx=left
        for color,label in items:
            d.line((lx,legend_y+8,lx+22,legend_y+8),fill=color,width=4)
            d.text((lx+29,legend_y),label,font=small_font,fill='#334155')
            lx += int(width*.29)
    status=f'云底光线：{strict_count}   ·   已验证云锋面（反烧专用）：{face_verified_count}   ·   云底采样：{len(bottom_rows)}'
    d.text((left+plot_w,15),status,font=small_font,fill='#64748b',anchor='ra')
    return im
