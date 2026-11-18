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
    """Return Streamlit st.html-compatible markup (CSS divs, no SVG).

    The maximum of the scale axis is the tallest bar, with a minimum of 10%.
    Each date displays two adjacent bars, EC then GFS. The bar's height maps
    scale, raw probability/100 fill opacity, and hue maps color score.
    """
    days = list(days or [])[:3]
    all_scales = []
    available = 0
    for day in days:
        records = _records_for(day, direction)
        for model, _ in MODELS:
            item = records.get(model) or {}
            if not isinstance(item, dict):
                continue
            scale = _num(item.get('burn_scale'))
            prob = _num(item.get('probability'))
            score = _num(item.get('color_score'))
            if scale is not None and prob is not None and score is not None:
                available += 1
                all_scales.append(max(0.0, scale))
    axis_top = max(10.0, max(all_scales, default=0.0))
    all_zero = bool(available) and not any(x > 0 for x in all_scales)

    # No SVG: st.html historically sanitized SVG out of otherwise valid cards.
    gridlines = ''.join(
        f'<div style="position:absolute;left:0;right:0;bottom:{pct}%;'
        f'border-top:1px solid rgba(73,91,121,.16);pointer-events:none"></div>'
        for pct in (0, 25, 50, 75, 100)
    )
    labels = ''.join(
        f'<div style="position:absolute;left:0;right:2px;bottom:calc({pct}% - 8px);'
        f'text-align:right;font-size:11px;color:#566477;white-space:nowrap">'
        f'{axis_top*pct/100:.0f}%</div>'
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
        for model, short in MODELS:
            item = records.get(model) or {}
            if not isinstance(item, dict):
                item = {}
            scale = _num(item.get('burn_scale'))
            probability = _num(item.get('probability'))
            score = _num(item.get('color_score'))
            if None in (scale, probability, score):
                tip = html.escape(f'{label} {short} | 数据缺失', quote=True)
                bars.append(
                    f'<div title="{tip}" style="width:23%;max-width:24px;height:15px;'
                    f'border:1px dashed #9ca9b9;border-radius:4px 4px 0 0;'
                    f'background:rgba(100,116,139,.045);box-sizing:border-box"></div>'
                )
                continue
            scale = max(0.0, float(scale))
            probability = max(0.0, min(100.0, float(probability)))
            score = max(0.0, min(100.0, float(score)))
            bar_height = min(100.0, scale / axis_top * 100.0)
            # Preserve exact scale and opacity: do not create fake minimum-height bars.
            color = color_for_score(score)
            tip = html.escape(
                f'{label} {short} | 常规概率 {probability:.0f}% | 色彩分 {score:.0f} | 火烧云规模 {scale:.1f}%',
                quote=True
            )
            if probability <= 0.0 or scale <= 0.0:
                # Zero means EMPTY: no stroke, baseline or opacity floor.
                # Retain an invisible slot to keep left EC / right GFS positions fixed.
                bars.append(
                    f'<div title="{tip}" style="width:23%;max-width:24px;'
                    f'height:0;flex:none;border:0;background:none"></div>'
                )
            else:
                # Color hue = score; bar height = scale; raw alpha = probability / 100.
                # No independent border/halo or visual opacity floor.
                bars.append(
                    f'<div title="{tip}" style="width:23%;max-width:24px;'
                    f'height:{bar_height:.4f}%;background:{color};opacity:{probability/100:.4f};'
                    f'border-radius:5px 5px 0 0;border:0;box-sizing:border-box"></div>'
                )
        columns.append(
            '<div style="flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:stretch">'
            '<div style="height:192px;display:flex;justify-content:center;align-items:flex-end;'
            'gap:7px;position:relative;z-index:1">'+''.join(bars)+'</div>'
            f'<div style="text-align:center;font-size:12px;color:#334155;margin-top:11px">{html.escape(label)}</div>'
            '</div>'
        )
    if all_zero:
        note = '本轮两模式火烧云规模均为0%，柱形按规则不绘制；不是图表加载失败。'
    elif not available:
        note = '当前没有可用于绘图的完整指标；缺失数据以虚线标记。'
    else:
        note = '鼠标悬停可查看每根柱子的概率、色彩分和规模；缺失数据以虚线标记。'

    return f'''<div style="background:rgba(255,255,255,.82);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,.78);border-radius:22px;padding:17px 20px 14px;box-shadow:0 8px 30px rgba(23,26,54,.09);margin:10px 0 20px;color:#27344a;font-family:-apple-system,BlinkMacSystemFont,'Microsoft YaHei',sans-serif">
      <div style="font-size:20px;font-weight:700;padding:2px 4px">{html.escape(title)}</div>
      <div style="font-size:12px;color:#526079;padding:4px 4px 0">柱高=规模 · 不透明度=原始概率÷100 · 颜色=色彩分（并非真实云色） · 0%不绘制</div>
      <div style="display:flex;width:100%;margin-top:18px;align-items:stretch;gap:10px">
        <div style="width:42px;flex:none;position:relative;height:192px">{labels}</div>
        <div style="min-width:0;flex:1;position:relative">
          <div style="position:absolute;left:0;right:0;top:0;height:192px">{gridlines}</div>
          <div style="position:relative;display:flex;gap:4px">{''.join(columns)}</div>
        </div>
      </div>
      <div style="font-size:12px;color:#526079;text-align:center;margin-top:7px">各日期左侧 EC · 右侧 GFS</div>
      <div style="font-size:11px;color:#64748b;padding:8px 4px 2px">{html.escape(note)}</div>
    </div>'''
