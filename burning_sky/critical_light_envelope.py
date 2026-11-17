# -*- coding: utf-8 -*-
"""Experimental spherical critical-ray envelope; does NOT alter production scores.

Distances are Earth surface arc kilometres from observer towards the Sun.
Cloud cells are uncertain model proxies, not opaque observed 3D clouds.
"""
import math

R = 6371.0088


def ray_height(target_height_km, solar_alt_deg, upstream_arc_km):
    alpha = math.radians(solar_alt_deg)
    denominator = math.cos(alpha + upstream_arc_km / R)
    if denominator <= 0:
        return float('inf')
    return (R + target_height_km) * math.cos(alpha) / denominator - R


def inverse_target_height(cloud_height_km, solar_alt_deg, upstream_arc_km):
    alpha = math.radians(solar_alt_deg)
    return (R + cloud_height_km) * math.cos(alpha + upstream_arc_km / R) / math.cos(alpha) - R


def merge_intervals(intervals):
    merged = []
    for lo, hi in sorted((float(a), float(b)) for a, b in intervals if b >= a):
        if merged and lo <= merged[-1][1] + 1e-9:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return merged


def subtract_intervals(base, forbidden):
    remaining = []
    for low, high in base:
        cursor = low
        for a, b in merge_intervals(forbidden):
            if b <= cursor or a >= high:
                continue
            if a > cursor:
                remaining.append((cursor, min(a, high)))
            cursor = max(cursor, b)
            if cursor >= high:
                break
        if cursor < high:
            remaining.append((cursor, high))
    return remaining


def cloud_shadow_interval(cell, target_face_km, solar_alt_deg):
    """Exact target-face height interval whose rays intersect a cloud rectangle.

    The ray is evaluated continuously over the cell's upstream arc interval.
    Endpoints plus the spherical ray's stationary point suffice; no ray stepping.
    """
    start = max(float(cell['start_km']), target_face_km + 1e-7)
    end = float(cell['end_km'])
    if end <= start:
        return None
    a, b = start - target_face_km, end - target_face_km
    critical = -math.radians(solar_alt_deg) * R
    distances = [a, b]
    if a < critical < b:
        distances.append(critical)
    # Inverse target height is monotone in cloud height; extrema at boundaries.
    bottom = min(inverse_target_height(float(cell['bottom_km']), solar_alt_deg, d)
                 for d in distances)
    top = max(inverse_target_height(float(cell['top_km']), solar_alt_deg, d)
              for d in distances)
    return bottom, top


def analyze_target_face(target_cell, cells, solar_alt_deg, max_distance_km=None):
    """Return possible illuminated face bands and separate upstream obstructions.

    The target is illuminated from the Sun-facing (larger-distance) side.
    Its own cloud cell is never counted as an upstream obstruction.
    Other cells are only considered if their interior is upstream of this face.
    This is a geometry proxy, not a measured illuminated cloud fraction.
    """
    face = float(target_cell['end_km'])
    band = [(float(target_cell['bottom_km']), float(target_cell['top_km']))]
    if band[0][1] <= band[0][0]:
        return {'target_face_km': face, 'possible_face_height_bands_km': [], 'error': 'empty target'}
    if max_distance_km is None:
        max_distance_km = max((float(c['end_km']) for c in cells), default=face)
    max_distance_km = max(face, float(max_distance_km))
    forbidden = []
    sources = []
    for c in cells:
        if c is target_cell or float(c['end_km']) <= face + 1e-7:
            continue
        clipped = dict(c)
        clipped['end_km'] = min(float(c['end_km']), max_distance_km)
        interval = cloud_shadow_interval(clipped, face, solar_alt_deg)
        if interval is not None:
            forbidden.append(interval)
            sources.append({'start_km': float(c['start_km']), 'end_km': clipped['end_km'],
                            'shadow_height_band_km': interval,
                            'cloud_cover': c.get('cloud_cover')})
    alpha = math.radians(solar_alt_deg)
    tangent = min(max_distance_km - face, max(0., -alpha * R))
    earth_floor = inverse_target_height(0., solar_alt_deg, tangent)
    forbidden.append((-1e6, earth_floor))
    possible = subtract_intervals(band, forbidden)
    width = band[0][1] - band[0][0]
    return {'target_face_km': face, 'target_height_band_km': band[0],
            'possible_face_height_bands_km': possible,
            'possible_face_height_fraction_proxy': sum(b-a for a,b in possible) / width,
            'earth_min_target_height_km': earth_floor,
            'upstream_shadow_sources': sources,
            'warning': 'All cloud rectangles are uncertain coverage proxies; not confirmed opaque blockers or measured illuminated area.'}
