# -*- coding: utf-8 -*-
"""Offline three-day chart of published overview values, using only HTML/CSS.

No SVG, JavaScript, HTTP requests, or forecast recomputation.  Some versions
of Streamlit's st.html sanitize inline SVG and leave the chart body blank.
"""
from __future__ import annotations

import html
import math
from datetime import date

MODELS = (('ECMWF IFS 0.25°', 'EC'), ('GFS Global', 'GFS'))


def _num(value):
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def color_for_score(score):
    """Continuous quality score palette (not actual cloud hue)."""
    anchors = ((0, (162, 169, 181)), (30, (193, 166, 164)),
               (50, (240, 181, 122)), (70, (237, 126, 116)),
               (90, (218, 84, 147)), (100, (157, 88, 201)))
    if score is None:
        return '#a9afba'
    score = max(0.0, min(100.0, float(score)))
    for (a, ca), (b, cb) in zip(anchors, anchors[1:]):
        if score <= b:
            t = (score-a)/(b-a)
            rgb = tuple(round(ca[i]*(1-t)+cb[i]*t) for i in range(3))
            return '#%02x%02x%02x' % rgb
    return '#9d58c9'


def _records_for(day, direction):
    if not isinstance(day, dict):
        return {}
    records = day.get(direction) or {}
    return records if isinstance(records, dict) else {}


def render_direction(days, direction, title):
    """Render one visual-scale bar per model per day."""
    days = list(days or [])[:3]
    bar_specs = (
        ('ECMWF IFS 0.25°', 'EC', 'visual_burn_scale', '视觉'),
        ('GFS Global', 'GFS', 'visual_burn_scale', '视觉'),
    )
    all_scales = []
    available = 0
    for day in days:
        records = _records_for(day, direction)
        for model, _short, key, _kind in bar_specs:
            item = records.get(model) or {}
            scale = _num(item.get(key))
            prob = _num(item.get('probability'))
            score = _num(item.get('color_score'))
            if scale is not None and prob is not None and score is not None:
                available += 1
                all_scales.append(max(0.0, scale))
    axis_top = max(10.0, max(all_scales, default=0.0))
    all_zero = bool(available) and not any(x > 0 for x in all_scales)
    gridlines = ''.join(
        f'<div style="position:absolute;left:0;right:0;bottom:{pct}%;border-top:1px solid rgba(73,91,121,.16);pointer-events:none"></div>'
        for pct in (0, 25, 50, 75, 100)
    )
    labels = ''.join(
        f'<div style="position:absolute;left:0;right:2px;bottom:calc({pct}% - 8px);text-align:right;font-size:11px;color:#566477;white-space:nowrap">{axis_top*pct/100:.0f}%</div>'
        for pct in (0, 25, 50, 75, 100)
    )
    columns = []
    for idx in range(3):
        day = days[idx] if idx < len(days) else {}
        raw_date = str(day.get('date') or '') if isinstance(day, dict) else ''
        try:
            label = date.fromisoformat(raw_date).strftime('%m/%d')
        except ValueError:
            label = '—'
        bars = []
        records = _records_for(day, direction)
        for model, short, key, kind in bar_specs:
            item = records.get(model) or {}
            if not isinstance(item, dict):
                item = {}
            scale = _num(item.get(key))
            probability = _num(item.get('probability'))
            score = _num(item.get('color_score'))
            tip_label = f'{short} {kind}规模'
            if None in (scale, probability, score):
                tip = html.escape(f'{label} {tip_label} | 数据缺失', quote=True)
                bars.append(f'<div title="{tip}" style="width:18%;max-width:18px;height:15px;border:1px dashed #9ca9b9;border-radius:4px 4px 0 0;background:rgba(100,116,139,.045);box-sizing:border-box"></div>')
                continue
            scale = max(0.0, float(scale))
            probability = max(0.0, min(100.0, float(probability)))
            score = max(0.0, min(100.0, float(score)))
            bar_height = min(100.0, scale / axis_top * 100.0)
            color = color_for_score(score)
            tip = html.escape(f'{short} | 火烧云规模·{kind} {scale:.1f}% | 常规概率 {probability:.0f}% | 色彩分 {score:.0f}', quote=True)
            if probability <= 0.0 or scale <= 0.0:
                bars.append(f'<div title="{tip}" style="width:18%;max-width:18px;height:0;flex:none;border:0;background:none"></div>')
            else:
                bars.append(f'<div title="{tip}" style="width:18%;max-width:18px;height:{bar_height:.4f}%;background:{color};opacity:{probability/100:.4f};border-radius:5px 5px 0 0;border:0;box-sizing:border-box"></div>')
        columns.append(
            '<div style="flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:stretch">'
            '<div style="height:192px;display:flex;justify-content:center;align-items:flex-end;gap:4px;position:relative;z-index:1">'
            + ''.join(bars) + '</div>'
            f'<div style="text-align:center;font-size:12px;color:#334155;margin-top:11px">{html.escape(label)}</div>'
            '</div>'
        )
    if all_zero:
        note = '本轮视觉规模均为 0%。'
    elif not available:
        note = '当前没有可用于绘图的完整指标；缺失数据以虚线标记。'
    else:
        note = '悬停可查看视觉规模、常规概率和色彩分。'
    return f"""<div style="background:rgba(255,255,255,.82);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,.78);border-radius:22px;padding:17px 20px 14px;box-shadow:0 8px 30px rgba(23,26,54,.09);margin:10px 0 20px;color:#27344a;font-family:-apple-system,BlinkMacSystemFont,'Microsoft YaHei',sans-serif">
      <div style="font-size:20px;font-weight:700;padding:2px 4px">{html.escape(title)}</div>
      <div style="font-size:12px;color:#526079;padding:4px 4px 0">柱高=视觉规模 · 不透明度=常规概率÷100 · 颜色=色彩分</div>
      <div style="display:flex;width:100%;margin-top:18px;align-items:stretch;gap:10px">
        <div style="width:42px;flex:none;position:relative;height:192px">{labels}</div>
        <div style="min-width:0;flex:1;position:relative">
          <div style="position:absolute;left:0;right:0;top:0;height:192px">{gridlines}</div>
          <div style="position:relative;display:flex;gap:4px">{''.join(columns)}</div>
        </div>
      </div>
      <div style="font-size:12px;color:#526079;text-align:center;margin-top:7px">ECMWF　GFS</div>
      <div style="font-size:11px;color:#64748b;padding:8px 4px 2px">{html.escape(note)}</div>
    </div>"""


def manual_records_to_days(records, expected_dates=()):
    """Normalize manual Actions records to the exact shape used by scheduled charts."""
    by_date = {str(day): {'date': str(day), 'morning': {}, 'evening': {}}
               for day in (expected_dates or ())}
    for row in records or ():
        if not isinstance(row, dict):
            continue
        day = str(row.get('date') or '')
        direction = row.get('direction')
        model = str(row.get('model') or '')
        if not day or direction not in ('morning', 'evening') or model not in {m for m, _ in MODELS}:
            continue
        target = by_date.setdefault(day, {'date': day, 'morning': {}, 'evening': {}})
        target[direction][model] = {
            'probability': row.get('probability'),
            'color_score': row.get('color_score'),
            'burn_scale': row.get('burn_scale'),
            'visual_burn_scale': row.get('visual_burn_scale'),
        }
    return [by_date[k] for k in sorted(by_date)]
