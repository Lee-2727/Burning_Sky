# -*- coding: utf-8 -*-
"""Summarize strictly validated sunward free-ray corridors.

These are 1-D distance projections of cloud-free RAYS, NOT observed
cloud holes in 2-D cloud cover.  The target's sunward face and the
validation boundary delimit the proven segment in the sampled corridor.
"""
from __future__ import annotations
import math


def summarize_verified_corridors(adaptive, *, limit=3):
    if not isinstance(adaptive, dict) or adaptive.get('status') != 'local_targets_verified':
        return None
    rows = adaptive.get('verified_ray_traces') or []
    output = []
    for ray in rows:
        try:
            face = float(ray['target_face_km'])
            boundary = float(ray['source_check_km'])
            t = str(ray.get('time') or '')
            h = float(ray['target_height_km'])
            if not all(map(math.isfinite, [face, boundary, h])) or boundary <= face:
                continue
            output.append({'start_km': face, 'end_km': boundary,
                           'size_km': boundary-face, 'time': t, 'height_km': h})
        except (ValueError, KeyError, TypeError):
            continue
    if not output:
        return None
    # Widest verified path, not the sum / union of overlapping minutes.
    output.sort(key=lambda x:(-x['size_km'], x['start_km'], x['time']))
    distinct = []
    for item in output:
        if not any(abs(item['start_km']-prev['start_km']) < 0.5 for prev in distinct):
            distinct.append(item)
        if len(distinct) >= limit:
            break
    return {'representative': output[0], 'distinct': distinct,
            'verified_minute_count': len(set(x['time'] for x in output)),
            'note': '等效无遮挡通道宽度为已验证太阳光线在采样轴上的投影长度；不等于气象云图的真实晴空云洞宽度。'}


def format_verified_corridors(summary):
    if not summary:
        return '存在严格验证光路，但缺少可提取的等效通道几何记录'
    r = summary['representative']
    primary = ('约 %.0f–%.0f km，等效宽度约 %.0f km' %
               (r['start_km'], r['end_km'], r['size_km']))
    others = summary.get('distinct', [])[1:]
    if others:
        primary += '；其他通道：' + '、'.join(
            '%.0f–%.0f km（%.0f km）' % (v['start_km'],v['end_km'],v['size_km'])
            for v in others)
    return primary + '（已验证光线沿采样轴的无遮挡投影，非真实晴空云洞）'
