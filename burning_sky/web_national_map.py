# -*- coding: utf-8 -*-
"""Manual Pro nationwide OM cloud map with fixed geography and replaceable snapshot.

This is a regional *candidate index*, not the single-site statistical probability.
Drawing or switching layers never requests Open-Meteo data. A second explicit
query overwrites the selected stored snapshot atomically.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from datetime import datetime, time, timezone, timedelta

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'data'/'spatial_maps'
LAYER_LABELS=['低云','中云','高云','分层最大覆盖（非总云量）','朝霞潜力（相对）','晚霞潜力（相对）']
MODELS={'EC 0.25°':'ecmwf_ifs025','GFS 0.25°':'ncep_gfs_global'}


def _path(model, day):
    if model not in MODELS:raise ValueError('不支持的云场模型')
    return CACHE/f'{MODELS[model]}_{day.isoformat()}.json'


def fetch_and_store(model,day,local_hour=17):
    from .spatial_om import resolve_valid_file,fetch_national_clouds_from_source
    if model not in MODELS:raise ValueError('不支持的气象模型')
    # Explicit single valid-time map, not a nationwide all-day forecast.
    event=datetime.combine(day,time(hour=local_hour),tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
    resolved=resolve_valid_file(MODELS[model],event)
    source={'domain':resolved['model_domain'], 'reference_time':resolved['reference_time'],
            'valid_time':resolved['valid_time'], 'available_variables':sorted(resolved['variables'])}
    result=fetch_national_clouds_from_source(source)
    # Do not store stale/manually guessed grid payload as a completed result.
    grid=result.get('grid_view') or {}
    if not grid.get('latitude') or not grid.get('layers'):
        raise ValueError('全国云场缺少有效网格，拒绝替换旧缓存')
    payload={'schema_version':1,'model':model,'day':day.isoformat(),
             'local_hour':local_hour,'generated_at':datetime.now(timezone.utc).isoformat(),
             'data':result}
    dest=_path(model,day);dest.parent.mkdir(parents=True,exist_ok=True)
    tmp=dest.with_suffix('.tmp')
    # Missing OM pixels are NULL, never clear sky; JSON cannot store NaN.
    def clean(x):
        import math
        if isinstance(x,float) and not math.isfinite(x):return None
        if isinstance(x,dict):return {k:clean(v) for k,v in x.items()}
        if isinstance(x,(list,tuple)):return [clean(v) for v in x]
        return x
    tmp.write_text(json.dumps(clean(payload),ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')
    os.replace(tmp,dest)
    return payload


def load_cached(model,day):
    path=_path(model,day)
    if not path.is_file():return None
    return json.loads(path.read_text('utf-8'))


def _render_potential_map(data,direction):
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.colors import LinearSegmentedColormap
    from scipy.ndimage import gaussian_filter
    from desktop.spatial_area_analysis import national_candidate
    from desktop.spatial_v2_visual import _draw_china_basemap,_draw_admin_overlay,_setup_chinese_fonts
    _setup_chinese_fonts()
    result=national_candidate(data,direction=direction,max_cells=2400)
    score=np.asarray(result['score'],dtype=float)
    valid=np.isfinite(score)
    # Keep unknown cells unknown: image smoothing is DISPLAY ONLY.
    filled=np.where(valid,score,0.)
    numer=gaussian_filter(filled,sigma=1.3,mode='nearest')
    denom=gaussian_filter(valid.astype(float),sigma=1.3,mode='nearest')
    softened=np.divide(numer,denom,out=np.full_like(numer,np.nan),where=denom>0.12)
    softened=np.where(denom>0.12,softened,np.nan)
    lat=np.asarray(result['latitude'],dtype=float)
    lon=np.asarray(result['longitude'],dtype=float)
    fig=Figure(figsize=(12.2,7.4),dpi=105,facecolor='#fafaf9');ax=fig.add_subplot(111)
    _draw_china_basemap(ax)
    red=LinearSegmentedColormap.from_list('burning_potential_red',
        [(0.0,(0.86,0.17,0.20,0.0)),(0.2,(0.86,0.17,0.20,0.20)),
         (0.6,(0.86,0.17,0.20,0.57)),(1.0,(0.82,0.12,0.16,0.92))])
    ax.pcolormesh(lon,lat,np.ma.masked_invalid(softened),cmap=red,
                  shading='nearest',vmin=0,vmax=100,zorder=3)
    _draw_admin_overlay(ax)
    ax.set(xlim=(73,136),ylim=(17.5,54.5),xlabel='经度 °E',ylabel='纬度 °N')
    ax.set_aspect('equal');ax.grid(alpha=.16)
    source=data.get('spatial_source') or {}
    kind='朝霞' if direction=='morning' else '晚霞'
    ax.set_title(f'{kind} · 全国相对潜力（仅一个有效时次）\n{source.get("domain")} | '
                 f'valid {source.get("valid_time")} | run {source.get("reference_time")}',loc='left',fontsize=11)
    fig.text(.085,.025,'颜色越深，候选潜力越高；平滑仅为视觉效果。空白=未验证，不是0%；非统计发生概率。',fontsize=9)
    fig.subplots_adjust(left=.07,right=.98,top=.85,bottom=.12)
    return fig,result


def render_national_map_page(st):
    st.title('🗺️ 全国朝霞 / 晚霞云场地图')
    st.caption('固定行政区划底图 + 动态 EC/GFS 云场；独立于 Pro 单次与五日后台任务。仅点击“查询并覆盖”才请求新数据。')
    c1,c2,c3=st.columns([1.2,1,1])
    with c1:model=st.selectbox('气象模型',list(MODELS),key='map_model')
    with c2:day=st.date_input('日期',key='map_date')
    with c3:hour=st.selectbox('北京时间参考时刻',[6,7,17,18,19],index=2,key='map_hour')
    st.caption('这是单个有效时刻的全国空间云场。全国各地日出日落并不同步；候选热区不等于全天统计概率。')
    left,right=st.columns([1,1])
    with left:refresh=st.button('查询 / 覆盖当前云场',type='primary',use_container_width=True,key='map_refresh')
    with right:
        from desktop.china_admin_basemap import CACHE as ADMIN_CACHE
        STATIC_ADMIN=ROOT/'assets'/'maps'/'china_admin1_reference.geojson'
        if not ADMIN_CACHE.exists() and not STATIC_ADMIN.exists():
            if st.button('准备省界静态底图（一次）',use_container_width=True):
                try:
                    from desktop.china_admin_basemap import prepare_admin_basemap
                    path,n,created=prepare_admin_basemap(dest=STATIC_ADMIN)
                    st.success(f'准备了 {n} 个省级参考单元；该运行环境可复用：{path.name}')
                except Exception as e:st.error('准备失败：'+str(e))
        else:st.caption('省界静态底图已缓存，可离线重复绘制')
        if STATIC_ADMIN.is_file():
            with st.expander('保存底图到 GitHub 后可跨部署长期复用'):
                st.download_button('下载行政区划底图 GeoJSON',STATIC_ADMIN.read_bytes(),
                    file_name='china_admin1_reference.geojson',mime='application/geo+json')
                st.caption('把文件上传到仓库 assets/maps/ 目录。下载只针对静态地理资源，不包含天气数据。')
    if refresh:
        try:
            with st.spinner('正在读取 Open-Meteo 全国 OM 云场；失败不会覆盖已有地图……'):
                payload=fetch_and_store(model,day,hour)
            st.session_state['map_latest_payload']=payload
            st.success('云场已读取并覆盖本地同模型/同日期缓存')
        except Exception as exc:st.error('OM 查询失败，原图未改变：'+str(exc))
    payload=st.session_state.get('map_latest_payload')
    if not isinstance(payload,dict) or payload.get('model')!=model or payload.get('day')!=day.isoformat() or payload.get('local_hour')!=hour:
        payload=load_cached(model,day)
        if payload is not None and payload.get('local_hour')!=hour:payload=None
    if payload is None:
        st.info('当前条件暂无已缓存云图。点击“查询 / 覆盖当前云场”后，切换图层不再联网。')
        return
    data=payload['data'];info=data.get('spatial_source') or {}
    st.caption(f'OM model: {info.get("domain")} · run: {info.get("reference_time")} · valid: {info.get("valid_time")} '
               f'· 层来源: {info.get("layer_origin") or info.get("evidence") or "模型数据"}')
    layer=st.radio('图层',LAYER_LABELS,horizontal=True,key='map_layer')
    try:
        if layer in LAYER_LABELS[:4]:
            from desktop.spatial_v2_visual import render_map_figure
            fig=render_map_figure(data,layer,nationwide=True)
        else:
            fig,_=_render_potential_map(data,'morning' if layer.startswith('朝霞') else 'evening')
        st.pyplot(fig,use_container_width=True)
        import matplotlib.pyplot as plt
        plt.close(fig)
    except Exception as exc:
        st.error('地图绘制失败：'+str(exc))
    st.caption('Lite 五日仍只读取 Pro 发布的数据；全国地图目前为 Pro 手动查询，未加入自动任务。')
