"""Opt-in 360-degree sampled illumination map, shared by two Streamlit frontends.

This is deliberately isolated from baseline Burning Sky inference. Unlike the
old lateral-clear-gap compass, it works without a recognized traditional gap.
"""
from __future__ import annotations

import math
from datetime import datetime
from PIL import Image, ImageDraw

from .experimental_compass360 import (
    HEADINGS, survey_illumination, _heading_summaries, _polar_xy,
)
from .sunset_compass import _font


def render_compass(result, label='', size=760):
    """Render 8 measured bearings as a continuous closed *display* polygon."""
    im = Image.new('RGB', (size, size), '#f8fafc')
    dr = ImageDraw.Draw(im, 'RGBA')
    cx = cy = size // 2
    radius = size * .345
    summaries = _heading_summaries(result.get('rows') or [])
    def xy(az, r):
        return _polar_xy(cx, cy, r, az)
    for fraction in (.25, .5, .75, 1.):
        pts = [xy(b, radius*fraction) for b in HEADINGS]
        dr.line(pts + pts[:1], fill=(190, 205, 222, 215), width=2)
    for b, name in zip(HEADINGS, ('北','东北','东','东南','南','西南','西','西北')):
        dr.line((cx, cy, *xy(b, radius)), fill=(190,205,222,190), width=2)
        dr.text(xy(b,radius+size*.050), name, font=_font(max(15,int(size*.024))),
                fill='#334155', anchor='mm')
    # a central zero-advantage stays at the center; no positive baseline.
    points = [xy(row['bearing'], radius*max(0,min(100,float(row['strength'])))/100) for row in summaries]
    for i in range(8):
        a, b = summaries[i], summaries[(i+1)%8]
        p, q = points[i], points[(i+1)%8]
        if a['state']=='candidate' and b['state']=='candidate':
            color = _hex_blend(a['color'],b['color'],.5)
            alpha=145
        elif a['state']=='candidate' or b['state']=='candidate':
            c=a if a['state']=='candidate' else b
            color=_hex_blend('#8190a4',c['color'],.4)
            alpha=83
        else:
            color='#748398'
            alpha=65
        dr.polygon(((cx,cy),p,q),fill=(*_hex_rgb(color),alpha))
    dr.line(points+points[:1],fill=(249,183,82,255),width=4,joint='curve')
    # show cloud % at each measured bearing; not at interpolated bearings.
    for row,(x,y) in zip(summaries,points):
        dr.ellipse((x-4,y-4,x+4,y+4),fill=(255,252,220,255))
        rr=max(53, math.hypot(x-cx,y-cy)+24)
        tx,ty=xy(row['bearing'],rr)
        high=row.get('high_cloud')
        label_num='?' if high is None else str(int(round(float(high))))
        dr.text((tx,ty),label_num,font=_font(max(12,int(size*.022))),fill='#24354a',anchor='mm')
    rows=result.get('rows') or []
    az=rows[0].get('sun_azimuth_deg') if rows else None
    if az is not None:
        az=float(az)
        for i in range(18):
            if i%2==0:
                dr.line((*xy(az,radius*(i/18)),*xy(az,radius*((i+1)/18))),fill=(224,161,14,255),width=4)
        x,y=xy(az,radius+16)
        dr.ellipse((x-8,y-8,x+8,y+8),fill=(254,204,49,255))
    dr.ellipse((cx-6,cy-6,cx+6,cy+6),fill=(26,42,62,255))
    head=label or f"{result.get('date','')} {result.get('model','')} {result.get('hour','')}时"
    dr.text((26,20),head,font=_font(max(15,int(size*.026))),fill='#26374a')
    dr.text((26,size-46),'凸出=实验受光候选优势  色彩=模型示意云色  数字=高云量%',font=_font(max(11,int(size*.016))),fill='#52657b')
    dr.text((26,size-22),'未验证方向不补作已受光；无传统云洞也可采样',font=_font(max(11,int(size*.015))),fill='#63758a')
    return im


def _hex_rgb(value):
    value=value.lstrip('#')
    return tuple(int(value[i:i+2],16) for i in (0,2,4))


def _hex_blend(a,b,t):
    ca,cb=_hex_rgb(a),_hex_rgb(b)
    return '#%02x%02x%02x' % tuple(int(round(x+(y-x)*t)) for x,y in zip(ca,cb))


def render_web_panel(st, engine, result, meta, direction_key, replay_only=False):
    """No network until explicit click. Session cache is specific to full inputs."""
    origin=result.get('origin')
    event=result.get('event_dt')
    if isinstance(event,str):
        try: event=datetime.fromisoformat(event)
        except ValueError: event=None
    model_label=str(meta.get('model') or '')
    key='compass360_illumination_v2_'+str((meta.get('date'),model_label,
                                         meta.get('lat'),meta.get('lon'),direction_key,
                                         str(event), result.get('azimuth')))
    day=str(meta.get('date') or (event.date().isoformat() if event else ''))
    name='朝霞' if direction_key in ('morning','朝霞') else '晚霞'
    st.caption(f'当前单次结果：{day} · {model_label} · {name}。16目标气压层 + 64个上游光路采样点；只按需请求，预计20批。')
    saved=st.session_state.get(key)
    if saved:
        st.image(render_compass(saved,f'{day} · {model_label} · {name}'),use_container_width=True)
        st.caption(f"实际HTTP请求：{saved.get('http_requests','?')}；耗时：{saved.get('elapsed_seconds','?')}秒。圆内颜色代表离散光路候选，不是正式概率。")
    if replay_only and not saved:
        st.caption('当前为离线案例回放，未保存360°受光采样数据；不会联网生成新图。')
    if st.button('生成/刷新360°受光补全图（约20批请求）',key=key+'_run',disabled=bool(replay_only)):
        if not origin or event is None:
            st.warning('缺少当前查询的观测坐标或事件时间。')
            return
        try:
            model_id=engine._weather_model_id_from_label(model_label)
            if model_id not in ('ecmwf_ifs025','ncep_gfs_global'):
                st.warning('360°受光实验目前只支持EC/GFS。')
                return
            with st.spinner('正在按统一限速规则采样目标云和上游光路……'):
                data=survey_illumination(float(origin[0]),float(origin[1]),day,model_id,int(event.hour))
            st.session_state[key]=data
            st.image(render_compass(data,f'{day} · {model_label} · {name}'),use_container_width=True)
            st.success(f"已完成 {data['target_points']} 个目标、{data['upstream_points']} 个上游采样点；HTTP {data['http_requests']}批。")
        except Exception as exc:
            st.error('360°采样失败：'+str(exc)[:280])
    st.caption('实验说明：当前仅对所采样方向进行离散球面光路筛选；轮廓闭合与扇区色彩是可视化，不表示中间方位经过独立严格验证。')
