"""Pressure-level cloud-layer reconstruction with explicit provenance.

Model pressure-level cloud cover and humidity are model predictions, NOT observations.
Never invent missing cloud or clear-sky evidence; inferred boundaries remain labelled.
"""
import math


def _finite(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def analyze_vertical_layers(levels, cloud_threshold=25.0, rh_threshold=85.0):
    """Analyze on every call. Interpolate *only* bounded missing heights.

    Returns candidate cloud layers, plus per-level source flags. Unknown humidity/
    cloud cover stays unknown; no fabricated cloud or clear-sky evidence.
    """
    rows = []
    for item in levels:
        row = dict(item)
        height = _finite(row.get('height_m_asl'))
        cc = _finite(row.get('cloud_cover'))
        rh = _finite(row.get('relative_humidity'))
        row['height_m_asl'] = height
        row['cloud_cover'] = cc
        row['relative_humidity'] = rh
        row['height_source'] = row.get('height_source') or ('model_geopotential' if height is not None else 'missing')
        row['cloud_evidence_source'] = ('model_cloud_cover' if cc is not None else
                                        'humidity_proxy' if rh is not None else 'missing')
        row['cloud_state'] = ('candidate' if (cc is not None and cc >= cloud_threshold) or
                              (rh is not None and rh >= rh_threshold) else
                              'unknown' if cc is None and rh is None else 'not_detected')
        rows.append(row)
    # Use pressure ordering for missing-height interpolation, only when bracketed.
    rows.sort(key=lambda r: -(_finite(r.get('pressure_hpa')) or 0.0))
    for i, row in enumerate(rows):
        if row['height_m_asl'] is not None:
            continue
        before = next((r for r in reversed(rows[:i]) if r['height_m_asl'] is not None), None)
        after = next((r for r in rows[i+1:] if r['height_m_asl'] is not None), None)
        p = _finite(row.get('pressure_hpa'))
        if not before or not after or not p or p <= 0:
            continue
        p0 = _finite(before.get('pressure_hpa'))
        p1 = _finite(after.get('pressure_hpa'))
        if not p0 or not p1 or p0 == p1:
            continue
        t = (math.log(p) - math.log(p0))/(math.log(p1)-math.log(p0))
        if 0 <= t <= 1:
            row['height_m_asl'] = before['height_m_asl'] + t*(after['height_m_asl']-before['height_m_asl'])
            row['height_source'] = 'bounded_log_pressure_interpolation'
    valid = sorted((r for r in rows if r['height_m_asl'] is not None),key=lambda r:r['height_m_asl'])
    layers=[]; active=[]
    for row in valid + [{'cloud_state':'not_detected'}]:
        if row['cloud_state'] == 'candidate':
            active.append(row)
        elif active:
            layers.append({'base_m_asl':active[0]['height_m_asl'],
                           'top_m_asl':active[-1]['height_m_asl'],
                           'vertical_samples':len(active),
                           'boundary_quality':'pressure_level_bracket_only',
                           'inferred':any(r['height_source']=='bounded_log_pressure_interpolation' or
                                          r['cloud_evidence_source']=='humidity_proxy' for r in active),
                           'evidence_sources':sorted(set(r['cloud_evidence_source'] for r in active))})
            active=[]
    return {'levels':rows,'layers':layers,'missing_levels':sum(r['height_m_asl'] is None for r in rows),
            'inferred_height_levels':sum(r['height_source']=='bounded_log_pressure_interpolation' for r in rows),
            'note':'模式气压层候选云层，不是实测云底云顶；未检测到不代表无云。'}
