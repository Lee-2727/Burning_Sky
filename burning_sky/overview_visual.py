# -*- coding: utf-8 -*-
"""Five-day probability/quality/scale visual; no weather calls."""
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
    """Continuously interpolate between score anchors; never imply actual cloud hue."""
    anchors = ((0, (162, 169, 181)), (30, (193, 166, 164)),
               (50, (240, 181, 122)), (70, (237, 126, 116)),
               (90, (218, 84, 147)), (100, (157, 88, 201)))
    if score is None:
        return '#a9afba'
    score = max(0.0, min(100.0, float(score)))
    for (a, ca), (b, cb) in zip(anchors, anchors[1:]):
        if score <= b:
            t = (score - a) / (b - a)
            rgb = tuple(round(ca[i] * (1-t) + cb[i] * t) for i in range(3))
            return '#%02x%02x%02x' % rgb
    return '#9d58c9'


def render_direction(days, direction, title):
    """HTML SVG panel: white translucent card, two bars for each of five dates.

    Column height=burn_scale; column opacity=probability; color=color_score.
    Missing data have an outlined placeholder, never a fabricated value.
    """
    days = list(days or [])[:5]
    vals = []
    for day in days:
        dat = day.get(direction, {}) if isinstance(day, dict) else {}
        for model, _ in MODELS:
            record = dat.get(model) or {}
            if isinstance(record, dict):
                scale = _num(record.get('burn_scale'))
                if scale is not None:
                    vals.append(max(0.0,scale))
    # smallest allowed axis is 10%; actual largest bar determines max otherwise.
    axis_top = max(10.0, max(vals, default=0.0))
    width, left, right, top, bottom = 810, 52, 18, 38, 224
    chart_h = bottom - top
    step = (width-left-right)/5
    lines = []
    for tick in (0, .25, .5, .75, 1):
        y = bottom - chart_h*tick
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#64748b" stroke-opacity=".17"/>')
        lines.append(f'<text x="{left-9}" y="{y+4:.1f}" font-size="12" fill="#435066" text-anchor="end">{axis_top*tick:.0f}%</text>')
    for i in range(5):
        day = days[i] if i<len(days) else {}
        raw_date = str(day.get('date') or '') if isinstance(day,dict) else ''
        try:
            label = date.fromisoformat(raw_date).strftime('%m/%d')
        except ValueError:
            label = '—'
        x_base = left+step*(i+.5)
        lines.append(f'<text x="{x_base:.1f}" y="{bottom+28}" font-size="13" fill="#334155" text-anchor="middle">{html.escape(label)}</text>')
        dat=day.get(direction,{}) if isinstance(day,dict) else {}
        for j,(model, short) in enumerate(MODELS):
            item=dat.get(model) or {}
            if not isinstance(item,dict): item={}
            scale=_num(item.get('burn_scale'))
            prob=_num(item.get('probability'))
            score=_num(item.get('color_score'))
            x=x_base + (-21 if j==0 else 5)
            if scale is None or prob is None or score is None:
                lines.append(f'<rect x="{x:.1f}" y="{bottom-14}" width="16" height="14" rx="3" fill="none" stroke="#a4acb9" stroke-dasharray="2,3"/>')
                continue
            scale=max(0.0,scale)
            prob=max(0.0,min(100.0,prob))
            # completely transparent for 0%; retain outline for visibility/zero distinction
            height=chart_h*min(1.0,scale/axis_top)
            if scale>0: height=max(1.2,height)
            y=bottom-height
            color=color_for_score(score)
            tooltip=html.escape(f'{label} {short} | 规模 {scale:.1f}% | 概率 {prob:.0f}% | 色彩分 {score:.0f}',quote=True)
            lines.append(f'<g><title>{tooltip}</title><rect x="{x:.1f}" y="{y:.1f}" width="16" height="{height:.1f}" rx="3" fill="{color}" fill-opacity="{prob/100:.3f}" stroke="{color}" stroke-opacity=".55" stroke-width="1.3"/></g>')
    legend='<text x="310" y="274" font-size="12" fill="#435066">每组左侧：EC　　右侧：GFS</text>'
    desc = '柱高=规模 · 透明度=概率 · 颜色=色彩分（并非真实云色）'
    svg = f'<svg viewBox="0 0 {width} 295" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{html.escape(title)}未来五日EC与GFS规模柱状图">'+''.join(lines)+legend+'</svg>'
    return f"""<div style="background:rgba(255,255,255,.82);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,.78);border-radius:22px;padding:15px 16px 10px;box-shadow:0 8px 30px rgba(23,26,54,.09);margin:10px 0 20px;color:#27344a;font-family:-apple-system,BlinkMacSystemFont,Microsoft YaHei,sans-serif">
      <div style="font-size:19px;font-weight:700;padding:3px 8px">{html.escape(title)}</div>
      <div style="font-size:12px;color:#526079;padding:3px 8px">{desc}</div>
      <div style="width:100%;overflow-x:auto">{svg}</div>
      <div style="font-size:11px;color:#64748b;padding:0 8px 6px">鼠标悬停可查看各项数值；缺失数据使用虚线标记</div>
    </div>"""
