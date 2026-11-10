"""Opt-in desktop experiment: 360-degree cloud-target survey.

Four batched Open-Meteo requests, 8 bearings x 2 distances. The result is
cloud occupancy ONLY; independent spherical illumination, surface visibility,
altitude-band raytracing and east-side 'reverse-burn' are deliberately *not*
asserted from this coarse survey. No writes to the main Burning Sky engine.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time
import urllib.parse
import urllib.request

from .api_rate_limit import limited_urlopen

HEADINGS = (0, 45, 90, 135, 180, 225, 270, 315)
RADII_KM = (60, 150)
FIELDS = ('cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high')
BASE_URL = 'https://api.open-meteo.com/v1/forecast'


def destination(lat: float, lon: float, bearing_deg: float, km: float) -> tuple[float, float]:
    """Spherical forward-geodesic; WGS84 mean-radius approximation."""
    phi1 = math.radians(lat)
    lam1 = math.radians(lon)
    theta = math.radians(bearing_deg)
    delta = km / 6371.0088
    phi2 = math.asin(math.sin(phi1) * math.cos(delta) +
                     math.cos(phi1) * math.sin(delta) * math.cos(theta))
    lam2 = lam1 + math.atan2(math.sin(theta) * math.sin(delta) * math.cos(phi1),
                             math.cos(delta) - math.sin(phi1) * math.sin(phi2))
    lon2 = (math.degrees(lam2) + 540) % 360 - 180
    return round(math.degrees(phi2), 5), round(lon2, 5)


def positions(lat, lon):
    return [{'bearing': b, 'radius_km': d, 'lat': destination(lat, lon, b, d)[0],
             'lon': destination(lat, lon, b, d)[1]} for b in HEADINGS for d in RADII_KM]


def _extract_location(raw):
    if isinstance(raw, dict):
        return raw
    raise ValueError('Open-Meteo响应不是字典')


def _value_at(payload, target_hour):
    hourly = payload.get('hourly') or {}
    times = hourly.get('time') or []
    if not times:
        return {f: None for f in FIELDS}
    # API uses Asia/Shanghai in this experiment; exact hour match, or closest
    # same-day time if a model omits a particular hourly sample.
    indices = [i for i, stamp in enumerate(times) if str(stamp).startswith(target_hour[:10])]
    if not indices:
        return {f: None for f in FIELDS}
    i = min(indices, key=lambda j: abs(int(str(times[j])[11:13]) - int(target_hour[11:13])))
    return {f: ((hourly.get(f) or [])[i] if len(hourly.get(f) or []) > i else None)
            for f in FIELDS}


def survey(lat: float, lon: float, day: str, model: str, hour: int,
           progress=None, fetch_json=None) -> dict:
    """Fetch 16 target points in batches of four. Returns request accounting.

    fetch_json(url) injection enables fully offline tests.
    """
    coords = positions(lat, lon)
    requested_at = time.strftime('%Y-%m-%d %H:%M:%S')
    hour = max(0, min(23, int(hour)))
    stamp = f'{day}T{hour:02d}:00'
    out = []
    count = 0
    started = time.monotonic()
    for offset in range(0, len(coords), 4):
        batch = coords[offset:offset + 4]
        params = {
            'latitude': ','.join(str(p['lat']) for p in batch),
            'longitude': ','.join(str(p['lon']) for p in batch),
            'start_date': day, 'end_date': day,
            'hourly': ','.join(FIELDS), 'timezone': 'Asia/Shanghai',
            'models': model,
        }
        url = BASE_URL + '?' + urllib.parse.urlencode(params, safe=',')
        if fetch_json is not None:
            raw = fetch_json(url)
        else:
            req = urllib.request.Request(url, headers={'User-Agent': 'BurningSky-Compass360-Experiment/1.0'})
            with limited_urlopen(req, timeout=35) as response:
                raw = json.loads(response.read().decode('utf-8'))
        count += 1
        records = [raw] if isinstance(raw, dict) else raw
        if not isinstance(records, list) or len(records) != len(batch):
            raise ValueError(f'响应返回{len(records) if isinstance(records,list) else "?"}个位置，预期{len(batch)}个')
        for point, item in zip(batch, records):
            values = _value_at(_extract_location(item), stamp)
            out.append({**point, **values})
        if progress:
            progress(len(out), len(coords), count)
    return {
        'schema_version': 1, 'kind': 'compass_cloud_survey_not_illumination',
        'date': day, 'hour': hour, 'model': model,
        'observer': {'latitude': lat, 'longitude': lon},
        'requested_at': requested_at, 'http_requests': count, 'target_points': len(coords),
        'elapsed_seconds': round(time.monotonic() - started, 2),
        'independent_spherical_light_verified': False,
        'rows': out,
        'caveat': '仅8方位×2距离中高低云量采样；未验证东侧云面的太阳光路，不代表反烧概率或可染色面积。',
    }


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


def _coarse_advantage(low, mid, high):
    """Experimental direction advantage score for display only.

    Positive: more middle/high cloud as potential target cloud.
    Negative: too much low cloud, which often blocks the scene.
    This is NOT the official Burning Sky probability or illumination score.
    """
    low = 0.0 if low is None else float(low)
    mid = 0.0 if mid is None else float(mid)
    high = 0.0 if high is None else float(high)
    score = 0.72 * high + 0.28 * mid - 0.60 * low
    if (high + mid) < 12:
        score *= 0.6
    return round(_clamp(score), 1)


def _mix_hex(a, b, t):
    t = max(0.0, min(1.0, float(t)))
    def _rgb(x):
        x = x.lstrip('#')
        return int(x[0:2],16), int(x[2:4],16), int(x[4:6],16)
    ar, ag, ab = _rgb(a)
    br, bg, bb = _rgb(b)
    r = int(round(ar + (br - ar) * t))
    g = int(round(ag + (bg - ag) * t))
    b = int(round(ab + (bb - ab) * t))
    return f'#{r:02x}{g:02x}{b:02x}'


def _advantage_color(score):
    score = _clamp(score)
    if score <= 50:
        return _mix_hex('#56606b', '#79a7d3', score / 50.0)
    return _mix_hex('#79a7d3', '#ff8a5b', (score - 50.0) / 50.0)




# Experimental sampled spherical ray screening, isolated from the forecast engine.
# Pressure-level cloud fractions and geopotential heights are used at actual
# positions along the solar bearing from each target, never copied from the
# observer's opposite-direction transect.
PRESSURES = (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100)
PRESSURE_FIELDS = tuple(f'{field}_{p}hPa' for p in PRESSURES for field in ('cloud_cover', 'geopotential_height'))
UPSTREAM_FRACTIONS = (0.18, 0.42, 0.68, 1.0)


def _batch_weather(points, day, model, fields, fetch_json=None, progress=None):
    out = []
    requests = 0
    for offset in range(0, len(points), 4):
        batch = points[offset:offset+4]
        params = {'latitude': ','.join(str(p['lat']) for p in batch),
                  'longitude': ','.join(str(p['lon']) for p in batch),
                  'start_date': day, 'end_date': day, 'hourly': ','.join(fields),
                  'timezone': 'Asia/Shanghai', 'models': model}
        url = BASE_URL + '?' + urllib.parse.urlencode(params, safe=',')
        if fetch_json is None:
            req = urllib.request.Request(url, headers={'User-Agent': 'BurningSky-360RayPilot/1.1'})
            with limited_urlopen(req, timeout=45) as response:
                raw = json.loads(response.read().decode('utf-8'))
        else:
            raw = fetch_json(url)
        records = [raw] if isinstance(raw, dict) else raw
        if not isinstance(records, list) or len(records) != len(batch):
            raise ValueError(f'位置响应数量不匹配：请求{len(batch)}，返回{len(records) if isinstance(records,list) else "未知"}')
        out.extend(records)
        requests += 1
        if progress:
            progress(len(out), len(points), requests)
    return out, requests


def _levels_at(payload, stamp):
    hourly = payload.get('hourly') or {}
    times = hourly.get('time') or []
    matches = [i for i, t in enumerate(times) if str(t).startswith(stamp[:10])]
    if not matches:
        return []
    i = min(matches, key=lambda j: abs(int(str(times[j])[11:13])-int(stamp[11:13])))
    levels = []
    for pressure in PRESSURES:
        cc = hourly.get(f'cloud_cover_{pressure}hPa') or []
        height = hourly.get(f'geopotential_height_{pressure}hPa') or []
        if i < len(cc) and i < len(height) and isinstance(cc[i], (float,int)) and isinstance(height[i],(float,int)):
            levels.append((float(height[i])/1000.0, float(cc[i])))
    return sorted(levels)


def _target_heights(levels):
    # Sample physically retrieved cloud layers with >= 5% cloud cover;
    # the coverage threshold selects potential targets, not blockers.
    items = [(h, cc) for h,cc in levels if 2 <= h <= 15 and cc >= 5]
    items.sort(key=lambda pair: pair[1], reverse=True)
    return [h for h,_ in items[:5]]


def _blocked_by_levels(ray_height_km, levels, threshold=25.0):
    # A pressure-level sample has a finite vertical resolution; avoid treating
    # that layer as a zero-thickness surface.
    if not levels:
        return None
    for i, (h, cc) in enumerate(levels):
        lower = (levels[i-1][0]+h)/2 if i else max(0.0,h-.35)
        upper = (h+levels[i+1][0])/2 if i+1<len(levels) else h+.35
        if lower <= ray_height_km <= upper and cc >= threshold:
            return True
    return False


def survey_illumination(lat, lon, day, model, hour, progress=None, fetch_json=None):
    """Independent 360-degree 3-D *sampled* solar-ray screening pilot.

    This samples four upstream locations per target. Missing pressure data and
    unsampled gaps are never represented as strictly clear paths. The output
    is deliberately not the official full-resolution Burning Sky forecast.
    """
    from datetime import datetime
    from .algorithm import solar_position
    from .spherical_raytrace import _ray_height_km, _earth_occluded
    start = time.monotonic()
    hour = max(0,min(23,int(hour)))
    stamp = f'{day}T{hour:02d}:00'
    dt = datetime.fromisoformat(stamp)
    targets = positions(lat,lon)
    # The experiment obtains real pressure-cloud layers at each target.
    raw_targets, n_target = _batch_weather(targets, day, model, PRESSURE_FIELDS+FIELDS, fetch_json,
                                          (lambda a,b,c: progress('目标云',a,b,c)) if progress else None)
    objects = []
    ray_points = []
    for idx,(target,raw) in enumerate(zip(targets,raw_targets)):
        levels = _levels_at(raw,stamp)
        coverage = _value_at(raw,stamp)
        altitude,azimuth = solar_position(dt,8,target['lat'],target['lon'])
        heights = _target_heights(levels)
        # Sample to 1.3 x geometry reach for the highest available target.
        # No hard 600/1000km cap. Very high clouds may extend > 1,000km.
        max_height = max(heights,default=8.0)
        theoretical_reach = 2*math.sqrt(2*6371.0088*max_height)
        required = 1.3*theoretical_reach
        objects.append({'target':target, 'clouds':coverage, 'levels':levels,
                        'target_heights_km':heights, 'sun_altitude_deg':altitude,
                        'sun_azimuth_deg':azimuth,'required_km':round(required,1),
                        'target_data_complete':len(levels)>=6})
        for frac in UPSTREAM_FRACTIONS:
            distance = round(required*frac,2)
            a,b = destination(target['lat'],target['lon'],azimuth,distance)
            ray_points.append({'lat':a,'lon':b,'distance_km':distance,'target_index':idx})
    raw_upstream, n_upstream = _batch_weather(ray_points, day, model, PRESSURE_FIELDS,fetch_json,
                                              (lambda a,b,c: progress('上游光路',a,b,c)) if progress else None)
    for idx,obj in enumerate(objects):
        target = obj['target']
        upstream=[]
        for off in range(len(UPSTREAM_FRACTIONS)):
            k=idx*len(UPSTREAM_FRACTIONS)+off
            upstream.append((ray_points[k]['distance_km'],_levels_at(raw_upstream[k],stamp)))
        heights=obj['target_heights_km']
        possible=0
        earth_blocked=0
        cloud_blocked=0
        unresolved=0
        tested=[]
        for h in heights:
            status='sampled_clear'
            for d,levels in upstream:
                if _earth_occluded(h,obj['sun_altitude_deg'],d):
                    status='earth_blocked'; break
                ray_h = _ray_height_km(h,obj['sun_altitude_deg'],d)
                blocked = _blocked_by_levels(ray_h, levels)
                if blocked is None:
                    status='missing_data'; break
                if blocked:
                    status='cloud_blocked'; break
            if status=='sampled_clear': possible+=1
            elif status=='earth_blocked': earth_blocked+=1
            elif status=='cloud_blocked': cloud_blocked+=1
            else: unresolved+=1
            tested.append({'target_height_km':round(h,2),'status':status})
        if not heights or not obj['target_data_complete']:
            state='insufficient_data'
        elif possible:
            state='sampled_light_candidate'
        elif unresolved:
            state='insufficient_data'
        else:
            state='sampled_blocked'
        obj.update({'state':state, 'tested_heights':tested,
                    'sampled_clear_targets':possible,'sampled_earth_blocked':earth_blocked,
                    'sampled_cloud_blocked':cloud_blocked,'unresolved':unresolved,
                    'sampled_light_fraction':round(100*possible/len(heights),1) if heights else None})
        obj.pop('levels',None)
    return {'schema_version':2, 'kind':'compass_sampled_spherical_illumination_pilot',
            'date':day,'hour':hour,'model':model,'observer':{'latitude':lat,'longitude':lon},
            'requested_at':time.strftime('%Y-%m-%d %H:%M:%S'),
            'http_requests':n_target+n_upstream,'target_requests':n_target,
            'upstream_requests':n_upstream,'target_points':len(targets),
            'upstream_points':len(ray_points),'elapsed_seconds':round(time.monotonic()-start,2),
            'independent_spherical_light_verified':False,
            'rows':[dict(o['target'],**o['clouds'],state=o['state'],
                         sun_altitude_deg=round(o['sun_altitude_deg'],2),
                         required_km=o['required_km'], tested_heights=o['tested_heights'],
                         sampled_clear_targets=o['sampled_clear_targets'],
                         sampled_light_fraction=o['sampled_light_fraction']) for o in objects],
            'caveat':'颜色基于真实云层高度及上游球面光路离散采样，不是完整连续云场严格验证；不能作为正式反烧概率。'}
def _sampled_row_strength(row):
    state = row.get('state', 'insufficient_data')
    if state != 'sampled_light_candidate':
        return 0.0
    frac = float(row.get('sampled_light_fraction') or 0.0)
    high = float(row.get('cloud_cover_high') or 0.0)
    mid = float(row.get('cloud_cover_mid') or 0.0)
    low = float(row.get('cloud_cover_low') or 0.0)
    score = 0.60 * frac + 0.30 * high + 0.15 * mid - 0.20 * low
    return _clamp(score)


def _predicted_burn_color(sun_altitude_deg, strength):
    """Use the same illustrative solar hue as the principal model.

    Grey out low-confidence candidate colours; no fabricated illumination.
    """
    from .cloud_color_palette import cloud_hex
    strength = _clamp(strength) / 100.0
    hue = cloud_hex(-8.0 if sun_altitude_deg is None else float(sun_altitude_deg))
    return _mix_hex('#394552', hue, 0.20 + 0.80 * strength)


def _heading_summaries(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(int(row['bearing']), []).append(row)
    out = []
    for bearing in HEADINGS:
        items = grouped.get(int(bearing), [])
        if not items:
            out.append({'bearing': bearing, 'strength': 0.0, 'high_cloud': None, 'color': '#65717b', 'state': 'insufficient_data'})
            continue
        weighted_strength = 0.0
        weight_sum = 0.0
        high_acc = 0.0
        high_weight = 0.0
        best = None
        best_s = -1.0
        blocked = 0
        unknown = 0
        for row in items:
            w = 0.42 if int(row.get('radius_km', 0)) == 60 else 0.58
            s = _sampled_row_strength(row)
            weighted_strength += s * w
            weight_sum += w
            if row.get('cloud_cover_high') is not None:
                high_acc += float(row.get('cloud_cover_high') or 0.0) * w
                high_weight += w
            if row.get('state') == 'sampled_blocked':
                blocked += 1
            elif row.get('state') != 'sampled_light_candidate':
                unknown += 1
            if s > best_s:
                best_s = s
                best = row
        strength = round(weighted_strength / weight_sum, 1) if weight_sum else 0.0
        high_cloud = round(high_acc / high_weight, 1) if high_weight else None
        if best and best.get('state') == 'sampled_light_candidate' and strength > 0:
            color = _predicted_burn_color(best.get('sun_altitude_deg'), strength)
            state = 'candidate'
        elif blocked == len(items):
            color = '#4c6179'
            state = 'blocked'
        else:
            color = '#65717b'
            state = 'insufficient_data'
        out.append({'bearing': bearing, 'strength': strength, 'high_cloud': high_cloud, 'color': color, 'state': state})
    return out


def _polar_xy(cx, cy, radius, bearing_deg):
    angle = math.radians(bearing_deg)
    return cx + radius * math.sin(angle), cy - radius * math.cos(angle)


def launch_desktop(owner, model_code, latitude, longitude, day,
                   event_hour=None, direction_label=None, model_label=None, event_time=None):
    """Small independent Tk window. No side effects on owner.direction_results."""
    import threading
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from datetime import date as date_type
    win = tk.Toplevel(owner)
    win.title(f'360°八方位受光 · {day} · {model_label or model_code} · {direction_label or "独立实验"}')
    win.geometry('800x820')
    panel = ttk.Frame(win, padding=12)
    panel.pack(fill='both', expand=True)
    ttk.Label(panel, text=(f'当前单次结果：{day} · {model_label or model_code} · {direction_label or "独立实验"}'
                           + (f' · 事件 {event_time}' if event_time else '')
                           + ' | 16目标 + 上游离散球面光路（约20批请求）；主概率/染色占比不变。'),
              wraplength=740).pack(anchor='w')
    hour_var = tk.IntVar(value=int(event_hour) if event_hour is not None else 17)
    line = ttk.Frame(panel)
    line.pack(anchor='w', pady=8)
    ttk.Label(line, text='目标时间（北京时间整点）:').pack(side='left')
    ttk.Combobox(line, textvariable=hour_var, values=list(range(24)), width=5, state='readonly').pack(side='left', padx=7)
    status = tk.StringVar(value='待开始；不会进行后台持续请求')
    ttk.Label(panel, textvariable=status).pack(anchor='w', pady=5)

    view_var = tk.StringVar(value='smooth' if direction_label else 'raw')
    view_bar = ttk.Frame(panel)
    view_bar.pack(anchor='w', pady=(0, 6))
    ttk.Label(view_bar, text='视图:').pack(side='left')
    ttk.Radiobutton(view_bar, text='原始采样视图', value='raw', variable=view_var).pack(side='left', padx=(6, 10))
    ttk.Radiobutton(view_bar, text='平滑八方位视图', value='smooth', variable=view_var).pack(side='left')

    sky_canvas = tk.Canvas(panel, width=390, height=390, background='#112234', highlightthickness=0)
    sky_canvas.pack(anchor='center', pady=7)
    text = tk.Text(panel, height=12, width=96)
    text.pack(fill='both', expand=True)
    current = {'value': None}

    def draw_raw_view(result):
        sky_canvas.delete('all')
        cx, cy = 195, 185
        for radius, label in ((70, '60 km'), (140, '150 km')):
            sky_canvas.create_oval(cx-radius,cy-radius,cx+radius,cy+radius, outline='#57738a', dash=(3,4))
            sky_canvas.create_text(cx+radius+9,cy-10,text=label,fill='#aec6d8',font=('Arial',8),anchor='w')
        for row in result['rows']:
            ring = 65 if int(row['radius_km']) == 60 else 132
            x, y = _polar_xy(cx, cy, ring, row['bearing'])
            state = row.get('state', 'insufficient_data')
            high = row.get('cloud_cover_high')
            cloud = '?' if high is None else str(int(round(float(high))))
            fraction = row.get('sampled_light_fraction')
            if state == 'sampled_light_candidate':
                strength = _sampled_row_strength(row)
                fill = _predicted_burn_color(row.get('sun_altitude_deg'), strength)
                tag = '候选' + str(int(round(float(fraction or 0)))) + '%'
            elif state == 'sampled_blocked':
                fill = '#4c6179'
                tag = '遮挡'
            else:
                fill = '#65717b'
                tag = '缺数据'
            sky_canvas.create_oval(x-17,y-17,x+17,y+17,fill=fill,outline='#d6e3ea',width=1)
            sky_canvas.create_text(x,y-1,text=cloud,fill='#ffffff',font=('Arial',10,'bold'))
            sky_canvas.create_text(x,y+23,text=tag,fill='#dce8ef',font=('Arial',7))
        for az, label in ((0,'北'),(90,'东'),(180,'南'),(270,'西')):
            x, y = _polar_xy(cx, cy, 165, az)
            sky_canvas.create_text(x,y,text=label,fill='#e4e7e9',font=('Arial',11,'bold'))
        legend_x, legend_y = 22, 340
        sky_canvas.create_text(legend_x, legend_y-12, text='颜色=受光候选与预测颜色  数字=高云%', anchor='w', fill='#c9d8e3', font=('Arial',8))
        for i, (label, color) in enumerate((('遮挡', '#4c6179'), ('橙红', '#ff7a45'), ('粉紫', '#b884ff'))):
            x0 = legend_x + i * 62
            sky_canvas.create_oval(x0, legend_y, x0+16, legend_y+16, fill=color, outline='#d6e3ea')
            sky_canvas.create_text(x0+22, legend_y+8, text=label, anchor='w', fill='#dce8ef', font=('Arial',8))
        sky_canvas.create_oval(cx-5,cy-5,cx+5,cy+5,fill='#ffcc81',outline='')
        sky_canvas.create_text(cx,cy+18,text='观测点',fill='#fff',font=('Arial',9))

    def draw_smooth_view(result):
        sky_canvas.delete('all')
        cx, cy = 195, 185
        base_r, amp_r = 16.0, 126.0
        summaries = _heading_summaries(result['rows'])
        # Prefer a complete 8-direction radar-like chart: closed polygon,
        # octagonal guides, sector colour fill, and a visible sun direction.
        def ring_points(radius):
            pts = []
            for az in HEADINGS:
                x, y = _polar_xy(cx, cy, radius, az)
                pts.extend((x, y))
            return pts
        # Octagonal grid
        for pct in (25, 50, 75, 100):
            r = base_r + amp_r * pct / 100.0
            sky_canvas.create_polygon(*ring_points(r), outline='#6b7990', fill='', width=1)
        for az in HEADINGS:
            x, y = _polar_xy(cx, cy, base_r + amp_r, az)
            sky_canvas.create_line(cx, cy, x, y, fill='#6b7990', width=1)
        # Draw coloured sectors using consecutive vertices; this gives a
        # visually complete chart without pretending intermediate angles were
        # sampled independently.
        vertices = []
        sector_colors = []
        for i, q in enumerate(summaries):
            strength = float(q['strength'] or 0.0)
            r = base_r + amp_r * strength / 100.0
            x, y = _polar_xy(cx, cy, r, q['bearing'])
            vertices.append((x, y, q))
        n = len(vertices)
        for i in range(n):
            q1 = vertices[i][2]
            q2 = vertices[(i + 1) % n][2]
            if q1['state'] == 'candidate' and q2['state'] == 'candidate':
                fill = _mix_hex(q1['color'], q2['color'], 0.5)
            elif q1['state'] == 'candidate' or q2['state'] == 'candidate':
                cand = q1 if q1['state'] == 'candidate' else q2
                fill = _mix_hex('#5c6471', cand['color'], 0.45)
            elif q1['state'] == 'blocked' and q2['state'] == 'blocked':
                fill = '#4c6179'
            else:
                fill = '#626f80'
            sector_colors.append(fill)
            x1, y1, _ = vertices[i]
            x2, y2, _ = vertices[(i + 1) % n]
            sky_canvas.create_polygon(cx, cy, x1, y1, x2, y2, fill=fill, outline='')
        # Closed main polygon outline
        poly_coords = []
        for x, y, _ in vertices:
            poly_coords.extend((x, y))
        sky_canvas.create_polygon(*poly_coords, outline='#ffcf6d', fill='', width=2)
        # Source points and cloud labels
        for x, y, q in vertices:
            sky_canvas.create_oval(x-3, y-3, x+3, y+3, fill='#fff7dd', outline='')
            high = q['high_cloud']
            label_r = max((((x-cx)**2 + (y-cy)**2) ** 0.5) + 18, 48)
            lx, ly = _polar_xy(cx, cy, label_r, q['bearing'])
            sky_canvas.create_text(lx, ly, text=('?' if high is None else str(int(round(float(high))))), fill='#ffffff', font=('Arial', 9, 'bold'))
        # Sun direction dashed line and icon, based on the first sampled row.
        sun_az = None
        if result.get('rows'):
            sun_az = result['rows'][0].get('sun_azimuth_deg')
        if sun_az is not None:
            line_r = base_r + amp_r + 30
            x2, y2 = _polar_xy(cx, cy, line_r, sun_az)
            sky_canvas.create_line(cx, cy, x2, y2, fill='#ffcf33', width=2, dash=(6, 4))
            sx, sy = _polar_xy(cx, cy, line_r + 18, sun_az)
            sky_canvas.create_oval(sx-7, sy-7, sx+7, sy+7, fill='#ffcf33', outline='')
            sky_canvas.create_text(sx, sy, text='日', fill='#5a4300', font=('Arial', 8, 'bold'))
        # Direction labels
        for az, label in ((0,'北'),(45,'东北'),(90,'东'),(135,'东南'),(180,'南'),(225,'西南'),(270,'西'),(315,'西北')):
            x, y = _polar_xy(cx, cy, base_r + amp_r + 20, az)
            sky_canvas.create_text(x, y, text=label, fill='#e4e7e9', font=('Arial', 10, 'bold'))
        # Center marker and legend
        sky_canvas.create_oval(cx-4, cy-4, cx+4, cy+4, fill='#ffcc81', outline='')
        sky_canvas.create_text(20, 336, text='闭合多边形=8方位实验受光优势  颜色=预测云色  数字=高云%', anchor='w', fill='#d7e2ea', font=('Arial', 8))
        sky_canvas.create_text(20, 353, text='虚线=太阳方向；图形为补全显示，未采样中间角度不视为独立严格验证', anchor='w', fill='#b9c7d3', font=('Arial', 8))
    def redraw_current(*_args):
        if not current['value']:
            sky_canvas.delete('all')
            return
        if view_var.get() == 'smooth':
            draw_smooth_view(current['value'])
        else:
            draw_raw_view(current['value'])

    view_var.trace_add('write', redraw_current)

    def show_report(result):
        current['value'] = result
        redraw_current()
        text.delete('1.0', 'end')
        text.insert('end', '实验结果：云量+真实气压层高度+上游离散球面追踪（尚非严格连续光路）\n\n')
        text.insert('end', f"HTTP请求：{result['http_requests']}；目标点：{result['target_points']}；耗时：{result['elapsed_seconds']} 秒\n")
        text.insert('end', '方位°  距离km   低云%   中云%   高云%  受光筛选\n')
        for rec in result['rows']:
            adv = f"候选{rec.get('sampled_light_fraction')}%" if rec.get('state')=='sampled_light_candidate' else ('遮挡' if rec.get('state')=='sampled_blocked' else '缺测')
            base = '    '.join(f"{rec.get(k) if rec.get(k) is not None else '缺测':>4}" for k in FIELDS)
            text.insert('end', f"{rec['bearing']:>3}       {rec['radius_km']:>3}       {base}   {adv!s:>7}\n")
        text.insert('end', '\n' + result['caveat'] + '\n')
        status.set('完成；可切换原始采样视图/平滑八方位视图，主预报未修改')

    def start():
        btn.configure(state='disabled')
        status.set('正在查询16个目标云气压层及64个上游光路点（预计20批请求）…')
        def progress(stage, done, total, count):
            try:
                win.after(0, lambda: status.set(f'{stage}：已采样 {done}/{total} 点；本阶段HTTP请求 {count}'))
            except tk.TclError:
                pass
        def worker():
            try:
                result = survey_illumination(float(latitude), float(longitude), str(day), model_code, hour_var.get(), progress)
                win.after(0, lambda: show_report(result))
            except Exception as exc:
                msg = str(exc)
                win.after(0, lambda: status.set('查询失败：' + msg[:180]))
            finally:
                try:
                    win.after(0, lambda: btn.configure(state='normal'))
                except tk.TclError:
                    pass
        threading.Thread(target=worker, daemon=True).start()

    def export():
        if not current['value']:
            messagebox.showinfo('尚无结果', '请先进行一次实验查询', parent=win)
            return
        target = filedialog.asksaveasfilename(parent=win, defaultextension='.json',
                        initialfile=f'Compass360_Experimental_{day}.json', filetypes=[('JSON', '*.json')])
        if target:
            Path(target).write_text(json.dumps(current['value'], ensure_ascii=False, indent=2), encoding='utf-8')
            messagebox.showinfo('已保存', target, parent=win)

    tools = ttk.Frame(panel)
    tools.pack(fill='x', pady=10)
    btn = ttk.Button(tools, text='计算当前单次结果的360°受光图' if direction_label else '运行16目标球面受光实验', command=start)
    btn.pack(side='left')
    ttk.Button(tools, text='保存实验JSON', command=export).pack(side='left', padx=12)
    return win
