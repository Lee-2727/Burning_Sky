# -*- coding: utf-8 -*-
"""Read-only Desktop V2 cloud-map / fused-profile renderers.

Only on-disk OM snapshots / in-memory computed results are used here. No
network access and no changes to prediction logic. 2-D cover is not opacity.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import math
import json
from pathlib import Path

def _setup_chinese_fonts():
    from matplotlib import rcParams, font_manager
    known = {x.name for x in font_manager.fontManager.ttflist}
    preferred = ('Microsoft YaHei', 'Noto Sans CJK SC', 'Noto Sans CJK JP',
                 'SimHei', 'AR PL UMing CN', 'DejaVu Sans')
    rcParams['font.sans-serif'] = [x for x in preferred if x in known] or ['DejaVu Sans']
    rcParams['axes.unicode_minus'] = False

LAYERS = {
    '低云': 'cloud_cover_low',
    '中云': 'cloud_cover_mid',
    '高云': 'cloud_cover_high',
    '分层最大覆盖（非总云量）': 'max_layers',
}

def normalize_utc(value):
    if value in (None,''):
        return None
    if isinstance(value,(int,float)):
        if not math.isfinite(value):return None
        return datetime.fromtimestamp(value,tz=timezone.utc)
    if isinstance(value,datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value,str):
        try:
            result=datetime.fromisoformat(value.strip().replace('Z','+00:00'))
            return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
        except ValueError: return None
    return None

def format_time(value):
    dt=normalize_utc(value)
    if dt is None:
        return '未知 / 未记录'
    return dt.strftime('%Y-%m-%d %H:%M UTC')+' / '+dt.astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M 北京')

def source_summary(report):
    """Separate provider model metadata from observed OM manifest run.
    A matching *metadata timestamp* does NOT prove the point API is same run.
    """
    source = report.get('source_log') or []
    model_metas=[]
    for row in (report.get('directions') or {}).values():
        if isinstance(row,dict):
            meta=row.get('api_model_metadata') or {}
            value=normalize_utc(meta.get('initialisation'))
            if value:model_metas.append(value)
    runs=sorted({normalize_utc(row.get('run')) for row in source if isinstance(row,dict) and normalize_utc(row.get('run'))})
    valids=sorted({normalize_utc(row.get('valid')) for row in source if isinstance(row,dict) and normalize_utc(row.get('valid'))})
    return {'api_metadata_run': max(model_metas) if model_metas else None,
            'om_runs':runs,'om_valids':valids}

def source_comparison_line(baseline, fused):
    b=source_summary(baseline)
    f=source_summary(fused)
    b_run=b['api_metadata_run']
    f_run=f['api_metadata_run']
    om_runs=f['om_runs']
    if len(om_runs)>1:
        return '警告：本次融合有多个 OM 起报时间，批次不可直接当作同一快照。'
    if b_run is None or f_run is None or not om_runs:
        return '起报一致性无法判断：存在未记录的元数据或 OM 运行时次。'
    if not(b_run==f_run==om_runs[0]):
        return '警告：API 最新模型元数据与 OM 起报时次不一致；差异不应直接归因于算法。'
    return '元数据时间相同，但并不能证明逐点 API 的实际数据来自相同起报；需同源锁定验证。'

def read_snapshot(path):
    path=Path(path)
    raw=json.loads(path.read_text(encoding='utf-8'))
    data=raw.get('data')
    if not isinstance(data,dict) or not isinstance(data.get('samples'),list):
        raise ValueError('OM 快照没有有效采样数据')
    return data

def _layer_grid(data, layer):
    import numpy as np
    grid=data.get('grid_view') or {}
    layers=grid.get('layers') or {}
    selected=LAYERS[layer]
    if selected=='max_layers':
        arrs=[np.array(layers.get(k,[]),dtype=float) for k in LAYERS.values() if k!='max_layers']
        if not arrs or any(x.ndim!=2 or x.shape!=arrs[0].shape for x in arrs):
            raise ValueError('网格分层云量缺失或矩阵维度不一致')
        with np.errstate(all='ignore'):
            arr=np.nanmax(np.stack(arrs,axis=0),axis=0)
    else:
        arr=np.asarray(layers.get(selected,[]),dtype=float)
    if arr.ndim!=2 or not arr.size:
        raise ValueError('当前 OM 缓存没有可用二维栅格')
    if np.any((np.isfinite(arr)) & ((arr<0)|(arr>100))):
        raise ValueError('发现超出 0–100% 范围的云量，拒绝绘制')
    lat=np.asarray(grid.get('latitude',[]),dtype=float)
    lon=np.asarray(grid.get('longitude',[]),dtype=float)
    if lat.shape!=arr.shape or lon.shape!=arr.shape:
        raise ValueError('网格坐标与云量二维矩阵不匹配')
    return lat, lon, arr

def render_map_figure(data, layer='中云', *, nationwide=False):
    """Draw one or several already cached OM rectangles; never invent gaps.

    Multi-batch mosaics require identical model, run and valid time. Overlapping
    cells are merely drawn atop each other; missing surrounding area is blank.
    """
    import numpy as np
    from matplotlib.figure import Figure
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable
    _setup_chinese_fonts()
    datasets=list(data) if isinstance(data,(list,tuple)) else [data]
    if not datasets:raise ValueError('没有可绘制的快照')
    if layer not in LAYERS:raise ValueError('未知云层')
    info=datasets[0].get('spatial_source') or {}
    source_key=(info.get('domain'),info.get('reference_time'),info.get('valid_time'))
    for d in datasets:
        source=d.get('spatial_source') or {}
        if (source.get('domain'),source.get('reference_time'),source.get('valid_time'))!=source_key:
            raise ValueError('禁止拼接起报或有效时刻不同的 OM 网格')
    fig=Figure(figsize=(11.3,7.2),dpi=105,facecolor='#f7f9fc')
    ax=fig.add_subplot(111)
    if nationwide:
        _draw_china_basemap(ax)
    palette={
        '低云':'Blues','中云':'Purples','高云':'Greys',
        '分层最大覆盖（非总云量）':'Greys'}[layer]
    mappable=None;gridded_tiles=0;cells_total=0;cells_valid=0;old_point_count=0
    all_samples=[]
    for d in datasets:
        samples=d.get('samples') or []
        all_samples.extend(samples)
        gridded=bool((d.get('grid_view') or {}).get('layers'))
        if gridded:
            lat,lon,v=_layer_grid(d,layer)
            mask=np.ma.masked_invalid(v)
            if not mask.count(): continue
            if float(np.nanmax(lon))-float(np.nanmin(lon))>180:
                raise ValueError('当前跨日期变更线的网格无法直接放在平面地图')
            mappable=ax.pcolormesh(lon,lat,mask,cmap=palette,vmin=0,vmax=100,shading='nearest',
                                   edgecolors=(1,1,1,.20),linewidth=.18)
            gridded_tiles+=1;cells_total+=int(v.size);cells_valid+=int(mask.count())
        else:
            xs=[];ys=[];cs=[]
            for row in samples:
                try:
                    x,y=float(row['lon']),float(row['lat'])
                    if LAYERS[layer]=='max_layers':
                        values=[float(row[k]) for k in ('cloud_cover_low','cloud_cover_mid','cloud_cover_high')]
                        c=max(values)
                    else:c=float(row[LAYERS[layer]])
                    if not all(math.isfinite(vv) for vv in (x,y,c)) or not 0<=c<=100:continue
                    xs.append(x);ys.append(y);cs.append(c)
                except (KeyError,TypeError,ValueError):continue
            if cs:
                mappable=ax.scatter(xs,ys,c=cs,cmap=palette,vmin=0,vmax=100,s=95,
                                    edgecolors='white',linewidths=.6)
                old_point_count+=len(cs)
    if mappable is None: raise ValueError('没有可用云量（所有栅格缺测）')
    fig.colorbar(ScalarMappable(norm=Normalize(0,100),cmap=palette),ax=ax,
                 shrink=.83,pad=.025,label='模型网格云量 (%)')
    px=[];py=[]
    for r in all_samples:
        try:
            x,y=float(r['lon']),float(r['lat'])
            if math.isfinite(x) and math.isfinite(y):px.append(x);py.append(y)
        except (KeyError,ValueError,TypeError):continue
    if gridded_tiles and px:
        ax.scatter(px,py,s=10,c='#e45740',edgecolors='white',linewidths=.3,
                   label='实际采样点',zorder=10)
        ax.legend(loc='upper left',fontsize=9)
    summary=f'拼接 {len(datasets)} 批；原始网格 {cells_total} 个（有效 {cells_valid} 个）'
    if old_point_count: summary+=f'；旧快照离散点 {old_point_count} 个（未插值）'
    model=info.get('domain','未知模型')
    modes={str((d.get('spatial_source') or {}).get('layer_origin','')) for d in datasets}
    derived='GFS 气压层最大云量重构（非原生三层）' if 'pressure_layer_proxy' in modes else '模型原生分层云量'
    run=format_time(info.get('reference_time'))
    valid=format_time(info.get('valid_time'))
    ax.set_title(f'Open-Meteo 模型云场 · {model} · {layer} · {derived}\nrun: {run}\nvalid: {valid}',
                 fontsize=11,loc='left',pad=13)
    ax.set_xlabel('经度 (°E)');ax.set_ylabel('纬度 (°N)')
    if nationwide:
        _draw_admin_overlay(ax)
        ax.set_xlim(73,136);ax.set_ylim(17.5,54.5)
        ax.set_aspect('equal',adjustable='box')
    else:
        ax.set_aspect('auto')
    ax.grid(True,color='#cbd5e1',lw=.4,alpha=.35)
    fig.text(.105,.015,summary+'；空白表示未读取，不能视为晴空。仅原始网格，不是卫星实况图。',
             fontsize=9,color='#5c6776')
    fig.subplots_adjust(left=.11,right=.89,bottom=.15,top=.80)
    return fig

def _draw_china_basemap(ax):
    """Fixed geographical reference based on bundled simplified world borders.

    No web tiles, no disputed-area political annotations, no additional calls.
    This is a *cartographic outline* and cloud layers are separately masked.
    """
    import json
    import numpy as np
    from matplotlib.collections import LineCollection, PolyCollection
    from pathlib import Path
    source=Path(__file__).resolve().parents[1]/'burning_sky'/'world_coast_simplified.json'
    if not source.is_file():return
    try: geometries=json.loads(source.read_text(encoding='utf-8'))
    except (OSError,ValueError):return
    polys=[];lines=[]
    for item in geometries:
        typ=item.get('type');coordinates=item.get('coordinates') or []
        shapes=[coordinates] if typ=='Polygon' else coordinates if typ=='MultiPolygon' else []
        for shape in shapes:
            if not shape:continue
            outer=np.asarray(shape[0],dtype=float)
            if outer.ndim!=2 or outer.shape[1]<2 or not np.all(np.isfinite(outer)):continue
            x=outer[:,0];y=outer[:,1]
            if x.max()<72 or x.min()>137 or y.max()<17 or y.min()>55:continue
            if x.max()-x.min()>180:continue  # Date-line artefact
            polys.append(outer[:,:2]);lines.append(outer[:,:2])
    if polys:
        ax.add_collection(PolyCollection(polys,facecolors='#e7e9dc',edgecolors='none',alpha=.76,zorder=0))
        ax.add_collection(LineCollection(lines,colors='#788697',linewidths=.65,alpha=.73,zorder=1))
    ax.set_facecolor('#eaf1f7')


def _draw_admin_overlay(ax):
    """Overlay verified cached province borders/city reference markers *after* cloud."""
    import numpy as np
    import matplotlib.patheffects as pe
    from matplotlib.collections import LineCollection
    from desktop.china_admin_basemap import CACHE, CITIES, PROVINCE_LABELS, boundary_segments
    # Prefer a versioned project asset; fallback to the legacy desktop cache.
    static_geojson=Path(__file__).resolve().parents[1]/'assets'/'maps'/'china_admin1_reference.geojson'
    segments=boundary_segments(static_geojson if static_geojson.exists() else CACHE)
    if segments:
        ax.add_collection(LineCollection([np.asarray(x,dtype=float) for x in segments],
                         colors='#48586a',linewidths=.48,alpha=.80,zorder=11))
    for name,lon,lat in PROVINCE_LABELS:
        ax.text(lon,lat,name,ha='center',va='center',color='#435265',fontsize=7.4,
                alpha=.78,zorder=12,weight='medium',
                path_effects=[pe.withStroke(linewidth=1.4,foreground='white',alpha=.75)])
    # City positions are fixed WGS84 points, NOT administrative boundary guesses.
    for name,lon,lat in CITIES:
        if 73<=lon<=136 and 17.5<=lat<=54.5:
            ax.plot(lon,lat,'.',markersize=2.9,color='#354252',zorder=13)
            ax.annotate(name,(lon,lat),xytext=(2,3),textcoords='offset points',
                        color='#354252',fontsize=6,alpha=.9,zorder=14,
                        path_effects=[pe.withStroke(linewidth=1.1,foreground='white',alpha=.8)])
    if not segments:
        ax.text(.01,.01,'尚无缓存省界：点击「准备省界底图」一次下载；省会城市位置仅供参照',
                transform=ax.transAxes,fontsize=8,color='#6b7280',zorder=20,
                bbox={'facecolor':'white','alpha':.88,'edgecolor':'none'})


def render_fusion_profile_figure(result,*,model='未知',origin_name='',om_run=None,api_run=None):
    """Use actual saved ray candidates/verified paths and conflict exemplars.

    Candidate curves are dashed; verified-after-OM are solid. Conflict markers
    only represent recorded diagnostic examples, not all vetoes or opaque clouds.
    """
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle
    _setup_chinese_fonts()
    from burning_sky.spherical_raytrace import profile_cloud_cells, _ray_height_km, _earth_occluded
    import numpy as np
    fig=Figure(figsize=(11.6,6.9),dpi=110,facecolor='#f8fafc')
    ax=fig.add_subplot(111)
    inferred=result.get('inferred') or {}
    diag=inferred.get('strict_primary_path_diagnostic') or {}
    profs=result.get('profiles') or []
    valid=[p for p in profs if p.get('levels')]
    if not valid: raise ValueError('本次没有气压层剖面数据，无法绘制剖面')
    cells=profile_cloud_cells(valid,threshold=25.)
    xmax=max(100.,max(float(p.get('distance_km') or 0) for p in valid))
    ymax=18.
    for c in cells:
        try:
            bottom=max(0.,float(c['bottom_km']));top=float(c['top_km'])
            start=float(c['start_km']);end=float(c['end_km'])
            if top<=bottom or end<=start:continue
            a=max(.09,min(.85,float(c.get('cloud_cover',50))/100))
            ax.add_patch(Rectangle((start,bottom),end-start,top-bottom,facecolor='#536071',alpha=a,edgecolor='#cbd5e1',linewidth=.3))
            ymax=max(ymax,min(26.,top+1.5))
        except (KeyError,TypeError,ValueError):pass
    # Candidates from continuous face geometry are not verified.
    candidate=(inferred.get('spherical_face_plot_traces') or [])
    verified=(inferred.get('verified_primary_ray_traces') or [])
    def line_one(trace,*,strict):
        try:
            face=float(trace.get('target_bottom_position_km',trace['target_face_km'])) if strict else float(trace['target_face_km']);h0=float(trace['target_height_km']);alt=float(trace['solar_altitude_deg'])
            end=float(trace.get('source_check_km',xmax)) if strict else xmax
            end=min(xmax,end)
            if end<=face:return False
            ds=np.linspace(face,end,200)
            hs=[];poss=[]
            for d in ds:
                if _earth_occluded(h0,alt,max(0.,d-face)):
                    hs.append(float('nan'));continue
                h=_ray_height_km(h0,alt,d-face)
                hs.append(h if 0<=h<=ymax else float('nan'))
            ax.plot(ds,hs,color='#6926a6' if strict else '#b275ce',
                    linestyle='-' if strict else (0,(4,4)),linewidth=2 if strict else .8,
                    alpha=.82 if strict else .48,zorder=4)
            return True
        except (KeyError,ValueError,TypeError):return False
    for t in candidate[::max(1,len(candidate)//15)]: line_one(t,strict=False)
    for t in verified[::max(1,len(verified)//12)]: line_one(t,strict=True)
    examples=diag.get('horizontal_conflict_examples') or []
    xs=[];ys=[]
    for e in examples:
        try:
            x=float(e['distance_km']);y=float(e['ray_height_km'])
            if 0<=x<=xmax and 0<=y<=ymax:xs.append(x);ys.append(y)
        except (KeyError,ValueError,TypeError):pass
    if xs:ax.scatter(xs,ys,marker='x',s=68,linewidths=2.0,c='#f97316',label='OM 与垂直数据冲突示例',zorder=8)
    ax.plot([],[],color='#6926a6',lw=2,label='融合后仍已严格验证的光路')
    ax.plot([],[],color='#b275ce',ls='--',lw=1,label='仅球面几何候选（未验证）')
    ax.set(xlim=(0,xmax),ylim=(0,ymax),xlabel='沿原采样中心线距离 (km)',ylabel='相对地表高度 (km)')
    ax.grid(color='#d1d5db',alpha=.36)
    ax.legend(loc='upper left',fontsize=9)
    cloud_note=f"水平/垂直冲突 {diag.get('horizontal_conflict_count',0)} 次；图上仅显示 {len(xs)} 个可定位示例（最多保存 12 个）"
    ax.set_title(f"{origin_name} {model} | 融合后剖面复核 · {result.get('event_dt','')}\n{cloud_note}",loc='left',fontsize=11)
    fig.text(.09,.045,'run：OM '+format_time(om_run)+'    /    API 元数据 '+format_time(api_run),fontsize=9,color='#475569')
    fig.text(.09,.02,'橙色叉号 = 数据证据冲突，不代表确认遮挡；轴外光路节点无法精确投影到此一维图。',fontsize=9,color='#475569')
    fig.subplots_adjust(left=.09,right=.97,bottom=.17,top=.83)
    return fig
