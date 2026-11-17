# -*- coding: utf-8 -*-
"""Manual Pro China OM atlas: three local dawn/dusk times, one cached composite.

This is a coarse, two-dimensional *relative candidate index*, not a calibrated
fire-cloud probability. Clicking Query fetches three China raster time slices;
redrawing the chart does not perform network I/O. Existing forecast pathways
and the Pro scheduled five-day workflow are never invoked here.
"""
from __future__ import annotations
from datetime import datetime, date, time as dt_time, timezone, timedelta
from pathlib import Path
import json
import math
import os

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'data'/'spatial_maps'
MODELS={'EC 0.25°':'ecmwf_ifs025','GFS 0.25°':'ncep_gfs_global'}
CLOUD_LAYERS=['低云','中云','高云','分层最大覆盖（非总云量）']
# Only representative model read times are discretized; each rendered cell
# still uses the solar geometry for its own latitude and longitude.
REGIONS=(
    {'name':'西部','lon_min':73., 'lon_max':97.5, 'reference_lat':38.,'reference_lon':88.},
    {'name':'中部','lon_min':97.5,'lon_max':114., 'reference_lat':34.,'reference_lon':107.},
    {'name':'东部','lon_min':114.,'lon_max':136.01,'reference_lat':31.,'reference_lon':121.},
)
BJ=timezone(timedelta(hours=8))


def _path(model, day, direction='evening'):
    if model not in MODELS: raise ValueError('不支持的云场模型')
    if direction not in ('morning','evening'): raise ValueError('朝霞/晚霞方向无效')
    return CACHE/f'{MODELS[model]}_{day.isoformat()}_{direction}.json'


def _local_event(day,latitude,longitude,direction):
    """Find sunrise/sunset (-0.833° apparent altitude), Beijing local date."""
    from burning_sky.algorithm import solar_position
    if direction not in ('morning','evening'):raise ValueError('无效的太阳事件方向')
    base=datetime.combine(day,dt_time(0,0))
    prev=None
    for minute in range(0,1441,10):
        when=base+timedelta(minutes=minute)
        alt,_=solar_position(when,8.,latitude,longitude)
        current=alt+0.833
        if prev is not None:
            crossed=(direction=='morning' and prev[1]<0<=current) or (direction=='evening' and prev[1]>=0>current)
            if crossed:
                # Linear interpolation across the 10-minute interval. Enough
                # to select the model's nearest *published* valid time.
                fraction=max(0.,min(1.,-prev[1]/(current-prev[1]))) if current!=prev[1] else 0.
                return base+timedelta(minutes=prev[0]+10*fraction)
        prev=(minute,current)
    raise ValueError('该区域当天未找到日出/日落时刻')


def _json_clean(value):
    import numpy as np
    if isinstance(value,dict): return {str(k):_json_clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_json_clean(v) for v in value]
    if isinstance(value,(np.integer,)):return int(value)
    if isinstance(value,(np.floating,)):value=float(value)
    if isinstance(value,(float,)) and not math.isfinite(value):return None
    if isinstance(value,np.ndarray):return _json_clean(value.tolist())
    return value


def _combine_regions(zones,direction):
    """Produce an honest zonal cloud mosaic and region-time candidate scores.

    Never interpolate OM cloud pixels across time. Region boundaries may remain
    visible: the three rasters are genuinely different valid times.
    """
    import numpy as np
    from desktop.spatial_area_analysis import national_candidate
    from desktop.spatial_v2_visual import _layer_grid
    if not zones: raise ValueError('没有可合成的云场')
    first=zones[0]['data']
    lat,lon,_=_layer_grid(first,'低云')
    output={key:np.full(lat.shape,np.nan,dtype=float) for key in ('cloud_cover_low','cloud_cover_mid','cloud_cover_high')}
    score=np.full(lat.shape,np.nan,dtype=float)
    evaluated=[]
    for zone in zones:
        data=zone['data'];region=zone['region']
        rlat,rlon,_=_layer_grid(data,'低云')
        if rlat.shape!=lat.shape or not np.allclose(rlat,lat,equal_nan=True) or not np.allclose(rlon,lon,equal_nan=True):
            raise ValueError('区域云场网格位置不同，不能强行拼接')
        zone_mask=(lon>=region['lon_min'])&(lon<region['lon_max'])
        for label,key in (('低云','cloud_cover_low'),('中云','cloud_cover_mid'),('高云','cloud_cover_high')):
            _,_,arr=_layer_grid(data,label)
            output[key][zone_mask]=arr[zone_mask]
        result=national_candidate(data,direction=direction,max_cells=3600,
                                  longitude_bounds=(region['lon_min'],region['lon_max']),latitude_bounds=(17.5,54.5))
        score[zone_mask]=result['score'][zone_mask]
        evaluated.append({'name':region['name'],'in_twilight_cells':result['in_twilight_cells'],
                          'evaluated_cells':result['evaluated_cells'],'stride':result['stride'],
                          'earth_blocked_cells':result.get('earth_blocked_cells',0),
                          'unknown_ray_cells':result.get('unknown_ray_cells',0)})
    source=first.get('spatial_source') or {}
    merged={'samples':[],'grid_view':{'latitude':lat.tolist(),'longitude':lon.tolist(),
             'layers':{k:v.tolist() for k,v in output.items()},'shape':list(lat.shape)},
            'spatial_source':{'domain':source.get('domain'),
                              'reference_time':source.get('reference_time'),
                              'valid_time':None, 'layer_origin':source.get('layer_origin'),
                              'time_mosaic':True}}
    return merged,score, evaluated


def fetch_and_store(model,day,direction='evening'):
    """Single click: three region-specific model times; atomic cache overwrite."""
    from . import spatial_om
    if model not in MODELS: raise ValueError('不支持的气象模型')
    if direction not in ('morning','evening'):raise ValueError('无效的朝霞晚霞方向')
    # Lock all three OM files to one completed model run from one manifest.
    import time
    domain=spatial_om.ALLOWED_MODELS[MODELS[model]]
    metadata=spatial_om._get_metadata_cached(domain,int(time.time()//600))
    zones=[];run=None
    snapshots_by_valid={}  # avoids downloading the same OM file twice within one click
    for region in REGIONS:
        event=_local_event(day,region['reference_lat'],region['reference_lon'],direction)
        resolved=spatial_om.resolve_valid_file(MODELS[model],event.replace(tzinfo=BJ).astimezone(timezone.utc),metadata=metadata)
        if run is None:run=resolved['reference_time']
        elif run!=resolved['reference_time']:raise ValueError('三个分区的模型起报不一致，拒绝拼接')
        src={'domain':resolved['model_domain'],'reference_time':resolved['reference_time'],
             'valid_time':resolved['valid_time'],'available_variables':sorted(resolved['variables'])}
        snapshot_key=(src['domain'],src['reference_time'],src['valid_time'])
        if snapshot_key not in snapshots_by_valid:
            snapshots_by_valid[snapshot_key]=spatial_om.fetch_national_clouds_from_source(
                src, bounds=(65.,144.,12.,60.))
        grid=snapshots_by_valid[snapshot_key]
        if not (grid.get('grid_view') or {}).get('layers'):
            raise ValueError(region['name']+' 云场缺失，保留已有地图')
        zones.append({'region':dict(region),'event_local':event.isoformat(),
                      'reference_time':resolved['reference_time'],
                      'valid_time':resolved['valid_time'],
                      'delta_minutes':round(resolved['delta_seconds']/60,1),'data':grid})
    merged,score, stats=_combine_regions(zones,direction)
    payload={'schema_version':2,'algorithm_version':2,'model':model,'day':day.isoformat(),'direction':direction,
             'generated_at':datetime.now(timezone.utc).isoformat(),
             'data':merged,'score':score.tolist(), 'region_stats':stats,
             'zones':[{k:v for k,v in z.items() if k!='data'} for z in zones],
             'note':'东中西分时 OM；各网格仅日出前/日落后，逐高云目标进行700 km球面高度分层粗筛；仅为相对候选潜力。'}
    dest=_path(model,day,direction);dest.parent.mkdir(parents=True,exist_ok=True)
    tmp=dest.with_suffix('.tmp')
    tmp.write_text(json.dumps(_json_clean(payload),ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')
    os.replace(tmp,dest)
    return payload


def load_cached(model,day,direction='evening'):
    path=_path(model,day,direction)
    if not path.is_file():return None
    raw=json.loads(path.read_text(encoding='utf-8'))
    return raw if raw.get('schema_version')==2 and raw.get('algorithm_version')==2 and raw.get('direction')==direction else None


def _render_potential_map(payload):
    """A soft red-only overlay; no artificial precision or spurious filled voids."""
    import numpy as np
    from scipy.ndimage import gaussian_filter,distance_transform_edt
    from matplotlib.figure import Figure
    from matplotlib.colors import LinearSegmentedColormap
    from desktop.spatial_v2_visual import _draw_china_basemap,_draw_admin_overlay,_setup_chinese_fonts
    _setup_chinese_fonts()
    grid=payload['data']['grid_view']
    lat=np.asarray(grid['latitude'],dtype=float);lon=np.asarray(grid['longitude'],dtype=float)
    score=np.asarray(payload['score'],dtype=float)
    valid=np.isfinite(score)
    if score.shape!=lat.shape:raise ValueError('热区分数与云场网格不匹配')
    # Smoothing ONLY on valid candidate support. The old fixed 0.12 mask
    # could erase *all* cells after coarsening with strides >=4.
    stride=max(1,max((x.get('stride',1) for x in payload.get('region_stats') or []),default=1))
    sigma=max(1.1,0.53*stride)
    numerator=gaussian_filter(np.where(valid,score,0.),sigma=sigma,mode='constant')
    mass=gaussian_filter(valid.astype(float),sigma=sigma,mode='constant')
    softened=np.divide(numerator,mass,out=np.full_like(numerator,np.nan),where=mass>0.015)
    if valid.any():
        distance=distance_transform_edt(~valid)
        softened=np.where(distance<=max(2.,stride*1.7),softened,np.nan)
    softened=np.where(softened>0,softened,np.nan)
    # Use the same cached province polygons as the fixed base map; no extra
    # network request. Without official cached shapes, do not invent borders.
    try:
        from desktop.china_admin_basemap import CACHE as ADMIN_CACHE,boundary_segments
        from matplotlib.path import Path as GeoPath
        source=ROOT/'assets'/'maps'/'china_admin1_reference.geojson'
        outlines=boundary_segments(source if source.exists() else ADMIN_CACHE)
        if outlines:
            xy=np.column_stack((lon.ravel(),lat.ravel()))
            land=np.zeros(xy.shape[0],dtype=bool)
            for boundary in outlines:
                if len(boundary)>=4:
                    land |= GeoPath(np.asarray(boundary)[:,:2]).contains_points(xy)
            softened=np.where(land.reshape(lat.shape),softened,np.nan)
    except (OSError,ValueError,ImportError):
        pass  # chart still draws without an admin mask if the cache is missing
    fig=Figure(figsize=(12.2,7.4),dpi=105,facecolor='#fafaf9');ax=fig.add_subplot(111)
    _draw_china_basemap(ax)
    red=LinearSegmentedColormap.from_list('bs_relative_red',[
        (0.,(.90,.23,.22,0.)),(.20,(.89,.20,.20,.17)),
        (.50,(.87,.17,.18,.49)),(1.,(.76,.08,.12,.94))])
    if np.isfinite(softened).any():
        ax.pcolormesh(lon,lat,np.ma.masked_invalid(softened),cmap=red,
                      shading='nearest',vmin=0,vmax=100,zorder=3,rasterized=True)
    _draw_admin_overlay(ax)
    name='朝霞' if payload['direction']=='morning' else '晚霞'
    ax.set(xlim=(73,136),ylim=(17.5,54.5),xlabel='经度 °E',ylabel='纬度 °N')
    ax.set_aspect('equal');ax.grid(alpha=.13)
    ax.set_title(f'{name} · 东中西分时全国相对潜力 | {payload["day"]}\n{payload["model"]}  ·  三分区日出日落云场合成',loc='left',fontsize=11)
    fig.text(.085,.023,'红色为分层球面粗筛后的相对潜力（非发生概率）；缺测不代表 0%；平滑仅用于视觉呈现。',fontsize=9)
    fig.subplots_adjust(left=.07,right=.98,top=.86,bottom=.11)
    return fig


def _render_cloud_mosaic(payload,layer):
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable
    from desktop.spatial_v2_visual import _layer_grid,_draw_china_basemap,_draw_admin_overlay,_setup_chinese_fonts
    _setup_chinese_fonts()
    lat,lon,arr=_layer_grid(payload['data'],layer)
    fig=Figure(figsize=(12.2,7.4),dpi=105,facecolor='#fafaf9');ax=fig.add_subplot(111)
    _draw_china_basemap(ax)
    cmap={'低云':'Blues','中云':'Purples','高云':'Greys','分层最大覆盖（非总云量）':'Greys'}[layer]
    ax.pcolormesh(lon,lat,np.ma.masked_invalid(arr),cmap=cmap,shading='nearest',vmin=0,vmax=100,zorder=3,rasterized=True)
    _draw_admin_overlay(ax)
    fig.colorbar(ScalarMappable(norm=Normalize(0,100),cmap=cmap),ax=ax,shrink=.79,pad=.015,label='模型网格云量 (%)')
    ax.set(xlim=(73,136),ylim=(17.5,54.5),xlabel='经度 °E',ylabel='纬度 °N')
    ax.set_aspect('equal');ax.grid(alpha=.13)
    kind='朝霞' if payload['direction']=='morning' else '晚霞'
    ax.set_title(f'{kind}时段 · 全国{layer} · 东中西分时云场合成\n{payload["model"]} | {payload["day"]}',loc='left',fontsize=11)
    fig.text(.08,.02,'各分区使用不同 OM 有效时次；分区交界可能有接缝。空白=缺测，不是晴空。',fontsize=9)
    fig.subplots_adjust(left=.07,right=.88,top=.86,bottom=.11)
    return fig


def render_national_map_page(st):
    st.title('🗺️ 全国朝霞 / 晚霞云场地图')
    st.caption('静态省界底图 + 动态 EC/GFS 分区云场；按东中西三地的实际日出日落时刻选择模型快照，再合成全国候选热区。')
    c1,c2,c3=st.columns([1.2,1,1])
    with c1:model=st.selectbox('气象模型',list(MODELS),key='map_model')
    with c2:day=st.date_input('日期',key='map_date')
    with c3:direction_label=st.radio('查看类型',['晚霞','朝霞'],horizontal=True,key='map_direction')
    direction='morning' if direction_label=='朝霞' else 'evening'
    st.caption('查询会读取东/中/西约 3 个不同预报有效时次的全国云场，可能有多次远程分块传输。切换图层只用本地缓存。')
    left,right=st.columns([1,1])
    with left:refresh=st.button('查询 / 覆盖当前朝晚霞云场',type='primary',use_container_width=True,key='map_refresh')
    with right:
        from desktop.china_admin_basemap import CACHE as ADMIN_CACHE
        STATIC_ADMIN=ROOT/'assets'/'maps'/'china_admin1_reference.geojson'
        if not ADMIN_CACHE.exists() and not STATIC_ADMIN.exists():
            if st.button('准备省界静态底图（一次）',use_container_width=True):
                try:
                    from desktop.china_admin_basemap import prepare_admin_basemap
                    path,n,created=prepare_admin_basemap(dest=STATIC_ADMIN)
                    st.success(f'已准备 {n} 个省级参考单元：{path.name}')
                except Exception as e: st.error('准备失败：'+str(e))
        else:st.caption('省界静态底图可离线复用')
        if STATIC_ADMIN.is_file():
            with st.expander('把行政区划底图保存到 GitHub，以便跨部署复用'):
                st.download_button('下载省界底图 GeoJSON',STATIC_ADMIN.read_bytes(),
                    file_name='china_admin1_reference.geojson',mime='application/geo+json')
    if refresh:
        try:
            with st.spinner('正在按东/中/西当地日出日落分别读取云场、重建候选热区；失败不会覆盖旧缓存……'):
                payload=fetch_and_store(model,day,direction)
            st.session_state['map_latest_payload']=payload
            st.success('三个分区云场已合成，当前条件缓存已覆盖。')
        except Exception as exc:st.error('OM 查询失败，原图未改变：'+str(exc))
    payload=st.session_state.get('map_latest_payload')
    if not isinstance(payload,dict) or any((payload.get('model')!=model,
            payload.get('day')!=day.isoformat(),payload.get('direction')!=direction)):
        payload=load_cached(model,day,direction)
    if payload is None:
        st.info('该模型、日期和朝晚霞类型没有已完成云图。点击查询后可离线切换图层。')
        return
    for z in payload.get('zones',[]):
        event=z.get('event_local','')
        valid=z.get('valid_time','')
        region=z['region']['name']
        st.caption(f'{region}：参考日出/落 {event[11:16]} 北京时间 · OM valid {valid} · run {z.get("reference_time")} · 时差约 {z.get("delta_minutes")} 分钟')
    layers=CLOUD_LAYERS+[direction_label+'潜力（相对）']
    layer=st.radio('图层',layers,index=len(layers)-1,horizontal=True,key='map_layer_display')
    try:
        fig=_render_cloud_mosaic(payload,layer) if layer in CLOUD_LAYERS else _render_potential_map(payload)
        st.pyplot(fig,use_container_width=True)
        import matplotlib.pyplot as plt
        plt.close(fig)
    except Exception as exc:st.error('地图绘制失败：'+str(exc))
    stats=payload.get('region_stats') or []
    st.caption(' | '.join(f'{r["name"]}粗筛 {r["evaluated_cells"]} / 晨昏 {r["in_twilight_cells"]} / 不确定 {r.get("unknown_ray_cells",0)}' for r in stats))
    st.caption('红色为 OM 二维云场 + 700 km 球面分层粗筛的相对候选指数，并非统计概率或完整三维验证；缺少上游数据时不当作晴空。')
