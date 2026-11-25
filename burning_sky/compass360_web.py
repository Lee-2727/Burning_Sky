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


def _event_utc(event):
    """Desktop engine stores an Asia/Shanghai wall clock when tz is absent."""
    from datetime import datetime, timedelta, timezone
    if isinstance(event, str):
        event = datetime.fromisoformat(event)
    if not isinstance(event, datetime):
        raise ValueError('缺少有效的朝霞/晚霞事件时刻')
    if event.tzinfo is None:
        return (event - timedelta(hours=8)).replace(tzinfo=timezone.utc)
    return event.astimezone(timezone.utc)


def _event_local_naive(event):
    """Normalize cached or live event_dt to the local naive datetime expected
    by solar_position(..., utc_offset=8). JSON-restored results store datetimes
    as strings, while live results may still hold datetime objects.
    """
    from datetime import datetime, timedelta, timezone
    if isinstance(event, str):
        event = datetime.fromisoformat(event)
    if not isinstance(event, datetime):
        raise ValueError('缺少有效的朝霞/晚霞事件时刻')
    if event.tzinfo is not None:
        event = event.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    return event


def _region_bounds(lat, lon, radius_km=900):
    """Observer-centered rectangle with extra room for 700 km upstream rays."""
    import math
    dlat = radius_km / 110.5
    conservative_lat = min(83., abs(float(lat)) + dlat)
    dlon = min(50., radius_km / max(20., 110.5*math.cos(math.radians(conservative_lat))))
    return (max(-180.,lon-dlon), min(180.,lon+dlon),
            max(-85.,lat-dlat), min(85.,lat+dlat))


def _matching_cached_national(model_id, event_utc, expected_run=None):
    """Use a separately cached manual nationwide cloud map only if time matches."""
    from datetime import timedelta, timezone
    from .web_national_map import MODELS, load_cached
    from .spatial_ray_fusion import _utc
    local_date = event_utc.astimezone(timezone(timedelta(hours=8))).date()
    for display, mid in MODELS.items():
        if mid != model_id:
            continue
        payload = load_cached(display,local_date)
        if not payload:
            continue
        data=payload.get('data') or {}
        info=data.get('spatial_source') or {}
        if info.get('time_mosaic'):
            continue  # Three-zone nationwide composite is NOT one event-time cloud field
        valid=_utc(info.get('valid_time'))
        run=_utc(info.get('reference_time'))
        requested_run=_utc(expected_run)
        if requested_run is not None and run != requested_run:
            continue
        if valid is not None and abs((valid-event_utc).total_seconds()) <= 5400:
            return data
    return None


def _fetch_regional_compass_cloud(model_id, event_utc, lat, lon, expected_run=None):
    """Fetch one regional OM raster; NO additional point/pressure HTTP batches.

    Cloud-occupancy compass remains a 2-D candidate display and does NOT
    replace the established 3-D single-direction ray verification.
    """
    from .spatial_om import resolve_valid_file, fetch_national_clouds_from_source
    cached = _matching_cached_national(model_id, event_utc, expected_run=expected_run)
    if cached:
        return cached, '复用了此前的全国云图缓存'
    ref=resolve_valid_file(model_id,event_utc)
    from .spatial_ray_fusion import _utc
    if _utc(expected_run) is not None and _utc(ref['reference_time']) != _utc(expected_run):
        raise ValueError('OM 最新起报与本次单次结果采用的空间云场起报不同；拒绝混用，请重新运行单次查询')
    source={'domain':ref['model_domain'], 'reference_time':ref['reference_time'],
            'valid_time':ref['valid_time'],
            'available_variables':sorted(ref['variables'])}
    return fetch_national_clouds_from_source(
        source,bounds=_region_bounds(lat,lon)), '已按需读取一次区域 OM 空间云场（可能包含多次分块传输）'


def _compass_brief(candidate):
    sectors=[s for s in (candidate.get('sectors') or [])
             if isinstance(s.get('relative_score'),(int,float))]
    if not sectors:
        return '八方位的上游云场覆盖不足，暂无可比较方向；缺测不等于0。'
    strongest=max(sectors,key=lambda s:float(s['relative_score']))
    suffix=('已取得原始严格光路的时间/高度参考。' if candidate.get('strict_source_rays')
            else '原始严格光路尚无有效种子；当前仅显示 OM 几何候选。')
    return (f"当前最高：{strongest['name']} · {strongest.get('view_type','候选')}"
            f"（相对指数 {strongest['relative_score']:.1f}，非发生概率）。"+suffix)


def render_web_panel(st, engine, result, meta, direction_key, replay_only=False):
    """Display OM-based 8-sector candidate advantage, never the old 20-batch sampler."""
    from datetime import datetime
    from desktop.spatial_area_analysis import render_compass_figure
    from .compass_illuminated_sectors import compass_from_verified_rays
    origin = result.get('origin')
    event = result.get('event_dt')
    model_label = str(meta.get('model') or '')
    day = str(meta.get('date') or '')
    name='朝霞' if direction_key in ('morning','朝霞') else '晚霞'
    model_id=engine._weather_model_id_from_label(model_label)
    if model_id == 'ncep_gfs_global':
        st.info('GFS 的 OM 水平云场已停用，不再生成基于该云场的八方位候选图；GFS 单次结果仅使用原生 API 气压层。')
        return
    if model_id != 'ecmwf_ifs025':
        st.caption('OM 八方位候选优势目前仅对 EC 开放。')
        return
    if not origin or event is None:
        st.caption('本次结果缺少地理坐标或事件时间，不能生成八方位图。')
        return
    lat,lon=float(origin[0]),float(origin[1])
    # Keep UTC for matching the OM valid time, but pass a local naive datetime
    # into the solar-angle routine. Cached JSON restores event_dt as a string.
    event_local=_event_local_naive(event)
    event_utc=_event_utc(event)
    observed_runs={str(row['spatial_horizontal_run']) for row in result.get('profiles',[])
                   if isinstance(row,dict) and row.get('spatial_horizontal_run')}
    expected_run=next(iter(observed_runs)) if len(observed_runs)==1 else None
    traces=(result.get('inferred') or {}).get('verified_primary_ray_traces') or []
    # Include ray evidence in the result key: do not display a radar created
    # from another version of the single-direction strict verification.
    ray_signature=tuple((r.get('minute_offset'), r.get('target_height_km'),r.get('target_face_km'))
                        for r in traces if isinstance(r,dict))
    cache_key='om_compass_gap_weight_v3_'+str((model_id,day,name,lat,lon,event_utc.isoformat(),expected_run,ray_signature))
    state=st.session_state.get(cache_key)
    st.caption('朝霞只看日出前、晚霞只看日落后。OM 云洞相对太阳方位偏南/偏北决定光照优势，'
               '再按对应方向中高云量加权；迎日侧少云、背日侧多云时分析反烧候选。')
    if state:
        fig=render_compass_figure(state['candidate'])
        st.pyplot(fig,use_container_width=True)
        st.caption(_compass_brief(state['candidate']))
        st.caption('云图来源：'+state['source']+'；OM run: '+str(state['candidate'].get('run'))+
                   '；valid: '+str(state['candidate'].get('valid')))
    elif replay_only:
        st.caption('离线案例回放未含区域 OM 栅格；不自动访问网络。')
    if st.button('生成 / 刷新受光云八方位（不请求旧20批点位API）',
                 key=cache_key+'_run',disabled=bool(replay_only)):
        try:
            with st.spinner('正在复用或按需读取区域 OM 云场；一次网格读取可能包含多次 HTTP 分块……'):
                data,source=_fetch_regional_compass_cloud(model_id,event_utc,lat,lon,expected_run=expected_run)
                c=compass_from_verified_rays(data,lat,lon,event_local, direction_key,
                                             verified_rays=traces)
            st.session_state[cache_key]={'candidate':c,'source':source}
            fig=render_compass_figure(c)
            st.pyplot(fig,use_container_width=True)
            st.caption(_compass_brief(c))
            st.success('已完成：OM 云洞偏移×中高云八方位。'+source)
        except Exception as exc:
            st.error('OM 八方位生成失败，未改动单次预报：'+str(exc)[:350])
    st.caption('金线长度=云洞南北偏移光照优势×对应中高云量的相对指数（非发生概率）；虚线=太阳。'
               '反太阳侧凸起仅代表反烧候选，不能冒充严格受光。'
               '缺测不可视为0，本图不回写正式概率或色彩分。')
