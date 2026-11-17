# -*- coding: utf-8 -*-
"""Conservative coupling of OM horizontal cloud fields to vertical solar rays.

Horizontal cloud cover describes grid-cell areal occupancy, NOT guaranteed
opacity of any particular solar ray.  In this first integration it can VETO a
premature 'verified clear' result if high horizontal cover conflicts with an
apparently clear pressure-level path, but cannot itself prove a cloud-free ray
or invent cloud base/top heights.  Works on the actual minute-dependent beam
samples already collected by the existing 700-km scanner.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

LAYER_FIELDS = ('cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high')
# Deliberately overlapping broad altitude ranges: *plausibility* bands only.
# These are NOT claimed cloud-bottom / cloud-top measurements.
POSSIBLE_HEIGHT_KM = {
    'cloud_cover_low': (0.0, 3.5),
    'cloud_cover_mid': (2.0, 9.0),
    'cloud_cover_high': (5.0, 16.0),
}
CONFLICT_COVER_THRESHOLD = 85.0


def _utc(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str):
        s = value.strip().replace(' UTC', '+00:00').replace('Z', '+00:00')
        try:
            d = datetime.fromisoformat(s)
            return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def bind_horizontal_profiles(pressure_samples, spatial_horizontal, *, max_time_gap_seconds=5400):
    """Attach location-matched OM layers to pressure profiles; all-or-none.

    Spatial OM and API pressure fields may have different forecast cycles.
    We require the *same model ID at the caller* and bounded valid-time drift,
    but never claim their model run IDs are identical (API lacks row run IDs).
    """
    from .sky_six import _great_circle_km
    src = spatial_horizontal or {}
    rows = src.get('samples') or []
    info = src.get('spatial_source') or {}
    if len(pressure_samples) != len(rows) or not rows:
        return {'attached': 0, 'reason': 'spatial/pressure coordinate counts differ'}
    t_om = _utc(info.get('valid_time'))
    if t_om is None:
        return {'attached': 0, 'reason': 'missing OM valid time'}
    # Validate all rows before modifying any profile (no partial misleading data).
    checked = []
    for i, (p, row) in enumerate(zip(pressure_samples, rows)):
        p_lat, p_lon = p.get('lat'), p.get('lon')
        r_lat, r_lon = row.get('lat'), row.get('lon')
        try:
            pos_delta = _great_circle_km(float(p_lat),float(p_lon),float(r_lat),float(r_lon))
        except (TypeError, ValueError):
            return {'attached': 0, 'reason': f'missing coordinates at {i}'}
        if pos_delta > 1.0:
            return {'attached': 0, 'reason': f'coordinate mismatch at {i}: {pos_delta:.1f}km'}
        t_pressure = _utc(p.get('time'))
        if t_pressure is None or abs((t_pressure-t_om).total_seconds()) > max_time_gap_seconds:
            return {'attached': 0, 'reason': f'valid time mismatch at {i}'}
        try:
            values = {k: float(row[k]) for k in LAYER_FIELDS}
        except (KeyError, TypeError, ValueError):
            return {'attached': 0, 'reason': f'missing horizontal layers at {i}'}
        if any(not math.isfinite(v) or v < 0 or v > 100 for v in values.values()):
            return {'attached': 0, 'reason': f'invalid horizontal cover at {i}'}
        checked.append(values)
    for p, values in zip(pressure_samples, checked):
        p['spatial_horizontal_om'] = values
        p['spatial_horizontal_valid_time'] = info.get('valid_time')
        p['spatial_horizontal_run'] = info.get('reference_time')
        p['spatial_horizontal_time_uncertainty'] = True
    return {'attached': len(checked), 'valid_time': info.get('valid_time'),
            'reference_time': info.get('reference_time'),
            'note': 'OM horizontal and API pressure valid times near, run equality unverified'}


def check_horizontal_ray_consistency(ray_profiles, target_face_km, target_height_km,
                                     solar_altitude_deg, source_end_km):
    """Identify possible upstream blockers overlooked by vertical API sampling.

    Called only after legacy spherical/intersection checks found a candidate
    clear path. A horizontally cloudy 25km model pixel has uncertain vertical
    extent and occupancy: such a conflict makes the path UNVERIFIED, not BLOCKED.
    """
    from .spherical_raytrace import _ray_height_km
    tested = 0
    conflicts = []
    available = 0
    for row in ray_profiles:
        hdata = row.get('spatial_horizontal_om')
        if not isinstance(hdata, dict):
            continue
        try:
            d = float(row['distance_km'])
        except (KeyError, TypeError, ValueError):
            continue
        if not (target_face_km + 1.0 < d <= source_end_km):
            continue
        available += 1
        ray_h = _ray_height_km(target_height_km, solar_altitude_deg, d-target_face_km)
        if not math.isfinite(ray_h) or ray_h <= 0:
            continue
        tested += 1
        for layer, bounds in POSSIBLE_HEIGHT_KM.items():
            if not (bounds[0] <= ray_h <= bounds[1]):
                continue
            cover = hdata.get(layer)
            if not isinstance(cover,(int,float)) or not math.isfinite(cover):
                continue
            if cover < CONFLICT_COVER_THRESHOLD:
                continue
            # If a pressure level showed a cloud at comparable ray height,
            # the old strict obstruction test already catches its geometry.
            # Here we specifically track disagreements between the 2D model
            # and a vertically clear/weak pressure profile.
            pressure_corrob = any(
                isinstance(lv.get('height_agl_km'),(int,float))
                and isinstance(lv.get('cloud_cover'),(int,float))
                and abs(float(lv['height_agl_km'])-ray_h) <= 1.1
                and float(lv['cloud_cover']) >= 25.0
                for lv in (row.get('levels') or []))
            if not pressure_corrob:
                conflicts.append({'distance_km':round(d,1),
                    'ray_height_km':round(ray_h,2), 'layer':layer,
                    'spatial_cover_pct':round(cover,1),
                    'reason':'horizontal-heavy / vertical-not-confirmed'})
    return {'evaluated_nodes':tested, 'horizontal_nodes':available,
            'unresolved':bool(conflicts), 'conflicts':conflicts[:5]}


def check_horizontal_target_support(target_profile, target_height_km):
    """Reject *verification* if 2D map says the pressure-only target is absent.

    This does not prove the target cloud is absent; a grid-cell mean is not a
    cloud-object detector. A disagreement is explicitly *unresolved*.
    """
    hdata = target_profile.get('spatial_horizontal_om') if isinstance(target_profile,dict) else None
    if not isinstance(hdata,dict):
        return {'checked':False,'unresolved':False}
    plausible=[k for k, bounds in POSSIBLE_HEIGHT_KM.items()
               if bounds[0] <= target_height_km <= bounds[1]]
    vals=[hdata.get(k) for k in plausible]
    vals=[float(v) for v in vals if isinstance(v,(int,float)) and math.isfinite(v)]
    if not vals:
        return {'checked':False,'unresolved':False}
    return {'checked':True,'unresolved': max(vals)<5.,
            'max_horizontal_cover_pct':max(vals)}
