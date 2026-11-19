# -*- coding: utf-8 -*-
"""Web cloud-profile renderer using the desktop Matplotlib diagnostic style.

Rendering is display-only: it does not change cloud geometry, ray validation,
or target weights. Verified bottom rays, reverse-burn face evidence, geometric
candidates and blocked bottom samples retain separate visual semantics.
"""
from __future__ import annotations

import io
import math
from PIL import Image


def _setup_chinese_fonts():
    from matplotlib import rcParams, font_manager
    known = {x.name for x in font_manager.fontManager.ttflist}
    preferred = ('Microsoft YaHei', 'Noto Sans CJK SC', 'Noto Sans CJK JP',
                 'SimHei', 'AR PL UMing CN', 'DejaVu Sans')
    rcParams['font.sans-serif'] = [x for x in preferred if x in known] or ['DejaVu Sans']
    rcParams['axes.unicode_minus'] = False


def render_profile(result, width=1600, height=900):
    """Return a desktop-style distance/height cross-section as a PIL image.

    Verified bottom rays are grouped by their 15-km target zone and rendered
    with thin, translucent LineCollections, matching the desktop fusion plot.
    This avoids the opaque purple wall created by drawing hundreds of thick
    individual Pillow lines. Upper stacked cloud cells remain in the cloud
    obstruction/profile display, but the ray engine no longer treats them as
    independent targets.
    """
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.patches import Rectangle
    from matplotlib.collections import LineCollection
    from matplotlib import colormaps
    from burning_sky.spherical_raytrace import profile_cloud_cells, _earth_occluded, _ray_height_km

    _setup_chinese_fonts()
    samples = sorted((result.get('samples') or []), key=lambda x: float(x.get('distance_km') or 0))
    raw_profiles = result.get('profiles') or []
    pmap = {p.get('distance_km'): p for p in raw_profiles if isinstance(p, dict)}
    profiles = []
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
    all_verified = [r for r in (inferred.get('verified_primary_ray_traces') or [])
                    if isinstance(r, dict)
                    and isinstance(r.get('solar_altitude_deg'), (int, float))
                    and isinstance(r.get('target_height_km'), (int, float))]
    verified_bottom = [r for r in all_verified
                       if r.get('surface') == 'cloud_bottom'
                       or r.get('status') == 'verified_cloud_bottom_spherical']
    verified_faces = [r for r in all_verified
                      if r.get('status') == 'verified_sunward_face_full_sampled_corridor']
    candidates = [r for r in (inferred.get('spherical_face_plot_traces') or [])
                  if isinstance(r, dict) and all(isinstance(r.get(k), (int, float))
                  for k in ('target_face_km', 'target_height_km', 'solar_altitude_deg'))]

    target = next((float(r['target_height_km']) for r in verified_bottom), None)
    if target is None:
        target = next((float(r['target_height_km']) for r in verified_faces), None)
    if target is None:
        target = next((float(p['estimated_base_km']) for p in profiles
                       if float(p.get('distance_km') or 0) == 0 and
                       isinstance(p.get('estimated_base_km'), (int, float))), 4.0)

    cells = profile_cloud_cells(profiles, threshold=25.0)
    xmax = max(100.0, max(float(p.get('distance_km') or 0) for p in profiles))
    ymax = max(18.0, min(24.0, max((float(c.get('top_km') or 0) for c in cells), default=12.0) + 1.0))

    fig = Figure(figsize=(float(width) / 100.0, float(height) / 100.0), dpi=100, facecolor='#f8fafc')
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor('#fbfcfe')

    # Actual pressure-level cloud cells. They remain as blockers even when an
    # upper vertically stacked cell is omitted from the target-ray sweep.
    for cell in cells:
        try:
            x0, x1 = float(cell['start_km']), float(cell['end_km'])
            y0, y1 = max(0.0, float(cell['bottom_km'])), float(cell['top_km'])
            if x1 <= x0 or y1 <= y0 or y0 > ymax:
                continue
            cover = max(0.0, min(100.0, float(cell.get('cloud_cover', 50))))
            alpha = max(.12, min(.78, .12 + .0062 * cover))
            ax.add_patch(Rectangle((x0, y0), x1-x0, y1-y0,
                                   facecolor='#536071', alpha=alpha,
                                   edgecolor='#d4dbe4', linewidth=.38, zorder=2))
        except (KeyError, TypeError, ValueError):
            continue

    # Bottom samples: red only for verified cloud-bottom illumination.
    bottom_rows = inferred.get('verified_bottom_sample_traces') or []
    lit_segments = set()
    marker_rows = {}
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
            lit_segments.add((max(float(key[0]), pos-width_km/2),
                              min(float(key[1]), pos+width_km/2), base_km))
        elif status in ('cloud_blocked', 'earth_or_source_horizon') or status.startswith('unresolved'):
            marker_rows.setdefault(status, []).append((pos, base_km))
    for a, b, base in sorted(lit_segments):
        if b > a:
            ax.plot([a, b], [base, base], color='#ef4444', linewidth=3.0,
                    solid_capstyle='butt', zorder=7)
    for status, points in marker_rows.items():
        color = '#e2893b' if status == 'cloud_blocked' else (
            '#94a3b8' if status.startswith('unresolved') else '#64748b')
        dedup = sorted(set((round(x, 2), round(y, 3)) for x, y in points))
        if dedup:
            ax.scatter([p[0] for p in dedup], [p[1] for p in dedup],
                       marker='x', s=25, linewidths=1.0, c=color, zorder=8,
                       label={'cloud_blocked': '云层遮挡采样点',
                              'earth_or_source_horizon': '地球/光源地平线限制',
                              }.get(status, '未验证采样点'))

    # Strict verified bottom rays: every ray is retained, but drawn as thin,
    # translucent region-colored segments (desktop LineCollection style).
    groups = {}
    for ray in verified_bottom:
        try:
            origin = float(ray.get('target_bottom_position_km', ray.get('target_face_km')))
            h0 = float(ray['target_height_km'])
            alt = float(ray['solar_altitude_deg'])
            far = min(xmax, float(ray.get('source_check_km') or xmax))
            if not (0 <= origin < far and 0 < h0 <= ymax):
                continue
            ds = np.linspace(origin, far, 120)
            hs = [(_ray_height_km(h0, alt, float(d-origin))
                   if not _earth_occluded(h0, alt, float(d-origin)) else np.nan) for d in ds]
            groups.setdefault(round(origin / 15.0), []).append(
                np.column_stack((ds, np.asarray(hs, dtype=float))))
        except (TypeError, ValueError, KeyError):
            continue
    palette = colormaps['plasma']
    for gid, segments in sorted(groups.items()):
        hue = palette((gid * 0.6180339887498949) % 1.0)
        ax.add_collection(LineCollection(segments, colors=[hue], alpha=.25,
                                         linewidths=.95, zorder=5))
    if verified_bottom:
        ax.plot([], [], color='#7434ac', lw=1.3, alpha=.65,
                label='严格验证云底光线（按15 km区域着色）')

    # Reverse-burn face paths are shown separately and never enter bottom area.
    for ray in verified_faces:
        try:
            face = float(ray['target_face_km'])
            h0 = float(ray['target_height_km'])
            alt = float(ray['solar_altitude_deg'])
            far = min(xmax, float(ray.get('source_check_km') or xmax))
            if not (0 <= face < far and 0 < h0 <= ymax):
                continue
            ds = np.linspace(face, far, 100)
            hs = [(_ray_height_km(h0, alt, float(d-face))
                   if not _earth_occluded(h0, alt, float(d-face)) else np.nan) for d in ds]
            ax.plot(ds, hs, color='#9b78c8', linestyle=(0, (3, 3)),
                    linewidth=.8, alpha=.28, zorder=4)
        except (TypeError, ValueError, KeyError):
            continue
    if verified_faces:
        ax.plot([], [], color='#9b78c8', ls=(0, (3, 3)), lw=1,
                label='严格验证云锋面（反烧专用，不计入常规占比）')

    # Geometric candidates remain faint and are deliberately downsampled.
    candidate_stride = max(1, math.ceil(len(candidates) / 24))
    for ray in candidates[::candidate_stride][:24]:
        try:
            face = float(ray['target_face_km'])
            h0 = float(ray['target_height_km'])
            alt = float(ray['solar_altitude_deg'])
            if not (0 <= face < xmax and 0 < h0 <= ymax):
                continue
            ds = np.linspace(face, xmax, 100)
            hs = [(_ray_height_km(h0, alt, float(d-face))
                   if not _earth_occluded(h0, alt, float(d-face)) else np.nan) for d in ds]
            ax.plot(ds, hs, color='#c084d8', linestyle=(0, (2, 3)),
                    linewidth=.7, alpha=.18, zorder=3)
        except (TypeError, ValueError, KeyError):
            continue
    if candidates:
        ax.plot([], [], color='#c084d8', ls=(0, (2, 3)), lw=.8,
                label='球面几何候选（未验证）')

    # Conflict examples are diagnostics, not automatically opaque blockers.
    diag = inferred.get('strict_primary_path_diagnostic') or {}
    conflict = diag.get('horizontal_conflict_examples') or []
    cx, cy = [], []
    for row in conflict[:24]:
        try:
            x, y = float(row['distance_km']), float(row['ray_height_km'])
            if 0 <= x <= xmax and 0 <= y <= ymax:
                cx.append(x); cy.append(y)
        except (KeyError, TypeError, ValueError):
            continue
    if cx:
        ax.scatter(cx, cy, marker='x', s=38, linewidths=1.5,
                   c='#f97316', label='OM/垂直数据冲突示例', zorder=9)

    if 0 <= target <= ymax:
        ax.scatter([0], [target], s=35, c='#111827', edgecolors='white',
                   linewidths=.8, zorder=10, label=f'目标云底 {target:.2f} km')
    boundaries = result.get('boundaries') or {}
    for key, color, label in (('fire_cloud_range_km', '#0f766e', '霞云距'),
                              ('cloud_edge_distance_km', '#b45309', '云边距')):
        value = boundaries.get(key)
        if isinstance(value, (int, float)):
            ax.axvline(float(value), color=color, linewidth=1.1, alpha=.85,
                       label=f'{label} {float(value):.0f} km')

    ax.set_xlim(0, xmax); ax.set_ylim(0, ymax)
    ax.set_xlabel('沿太阳方位距离（km）')
    ax.set_ylabel('相对地面高度（km）')
    ax.grid(color='#d1d5db', alpha=.36, linewidth=.55)
    ax.tick_params(colors='#475569', labelsize=8)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#64748b'); ax.spines['bottom'].set_color('#64748b')

    place = str(result.get('place') or '')
    model = str(result.get('query_model_label') or result.get('query_model_id') or '')
    direction = result.get('direction')
    title = '朝霞' if direction == 'morning' else '晚霞' if direction == 'evening' else '火烧云'
    ax.set_title(f'{place} {model}｜{title}严格球面光路 · 云层剖面',
                 loc='left', fontsize=11, color='#1e293b', pad=10)
    stack_diag = inferred.get('stacked_target_diagnostic') or {}
    stack_removed_km = float(stack_diag.get('removed_stacked_horizontal_km') or 0.0)
    ax.text(1.0, 1.015,
            f'云底光线：{len(verified_bottom)}  ·  反烧云锋面：{len(verified_faces)}  ·  云底采样：{len(bottom_rows)}  ·  叠层上部目标剔除：{stack_removed_km:.0f} km',
            transform=ax.transAxes, ha='right', va='bottom', fontsize=7.1, color='#64748b')
    # Cloud-cover grayscale colorbar, as in the desktop diagnostic.
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize
    sm = ScalarMappable(norm=Normalize(0, 100), cmap='Greys')
    cb = fig.colorbar(sm, ax=ax, fraction=.025, pad=.025)
    cb.set_label('云量 %', fontsize=8); cb.ax.tick_params(labelsize=7)
    ax.legend(loc='upper left', fontsize=7.2, framealpha=.9, ncol=2)
    fig.subplots_adjust(left=.09, right=.93, bottom=.12, top=.88)

    buf = io.BytesIO()
    canvas.print_png(buf)
    buf.seek(0)
    with Image.open(buf) as rendered:
        return rendered.convert('RGB').copy()
