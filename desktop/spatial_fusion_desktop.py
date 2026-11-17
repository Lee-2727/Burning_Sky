# -*- coding: utf-8 -*-
"""Desktop-only opt-in OM / API hybrid ray-fusion experiment.

The production desktop application is unchanged.  This entry point adds a
local snapshot/replay harness around the existing shared engine and ray-fusion
module, with explicit provenance; it does not download OM in the baseline.
"""
from __future__ import annotations
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from copy import deepcopy
import threading
import queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine import FireCloudApp, tk, ttk, filedialog, messagebox
from burning_sky.spatial_ray_fusion import bind_horizontal_profiles
from desktop.spatial_v2_visual import (
    format_time, normalize_utc, source_summary, source_comparison_line,
    read_snapshot, render_map_figure, render_fusion_profile_figure, LAYERS,
)

ROOT = PROJECT_ROOT / 'desktop_spatial_debug'
SNAPSHOTS = ROOT / 'om_snapshots'
REPORTS = ROOT / 'reports'


def _canonical_utc(value):
    if isinstance(value, datetime):
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
                else value.astimezone(timezone.utc)).isoformat()
    return str(value)


def request_key(coords, distances, event_utc, model_id):
    """Exact coordinate/time/model key; stale forecast or wrong city not replayed."""
    payload = {'model':str(model_id), 'event_utc':_canonical_utc(event_utc),
               'coords':[[round(float(a),6),round(float(b),6)] for a,b in coords],
               'distances':[round(float(x),4) for x in distances]}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]


def fetch_desktop_horizontal(coords, distances, event_utc, model_id, *,
                            replay=False, cache_dir=SNAPSHOTS, fetcher=None):
    """Returns recorded *actual* OM samples or reads them live. No interpolation.

    Replay never calls a weather provider and requires an exact query key.
    """
    key=request_key(coords,distances,event_utc,model_id)
    path=Path(cache_dir)/(key+'.json')
    if replay:
        if not path.is_file():
            raise FileNotFoundError('当前模型/时间/坐标组合没有 OM 快照；不能用其他时间的云量替代')
        with path.open(encoding='utf-8') as fp: obj=json.load(fp)
        if obj.get('key')!=key or not isinstance(obj.get('data'),dict):
            raise ValueError('OM 快照与本次请求不匹配')
        out=obj['data']
        if len(out.get('samples') or [])!=len(coords):
            raise ValueError('OM 快照点数与请求不一致')
        return deepcopy(out), 'snapshot_replay',str(path)
    if fetcher is None:
        from burning_sky.spatial_om import fetch_horizontal_clouds
        fetcher=fetch_horizontal_clouds
    data=fetcher(coords,distances,event_utc,model_id)
    if len(data.get('samples') or []) !=len(coords):
        raise ValueError('OM 返回点数不匹配，拒绝存入快照')
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    with tmp.open('w',encoding='utf-8') as fp:
        json.dump({'key':key,'data':data,'created_at':datetime.now(timezone.utc).isoformat()},fp,ensure_ascii=False,indent=2,default=str)
    os.replace(tmp,path)
    return data,'om_live',str(path)


def fetch_national_cloud_snapshot(source, folder=SNAPSHOTS, *, fetcher=None):
    """Explicit-on-click national OM transfer; never called during normal scans.

    A completed national snapshot is immutable for the same model/run/valid.
    Repainting it uses the on-disk raster and does not contact Open-Meteo.
    """
    source=source or {}
    if not source.get('domain') or not source.get('reference_time') or not source.get('valid_time'):
        raise ValueError('全国读取前必须先完成一次有效 OM 空间云场查询')
    identity={key:source.get(key) for key in ('domain','reference_time','valid_time')}
    key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode('utf-8')).hexdigest()[:20]
    directory=Path(folder)
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/('national_'+key+'.json')
    if path.is_file():
        from desktop.spatial_v2_visual import read_snapshot
        old=read_snapshot(path)
        om=old.get('spatial_source') or {}
        if om.get('national_overlay') and all(om.get(k)==v for k,v in identity.items() if k!='domain') and om.get('domain')==identity['domain']:
            return path,False
    if fetcher is None:
        from burning_sky.spatial_om import fetch_national_clouds_from_source
        fetcher=fetch_national_clouds_from_source
    data=fetcher(source)
    view=data.get('grid_view') or {}
    if not view.get('layers') or not (data.get('spatial_source') or {}).get('national_overlay'):
        raise ValueError('全国云场返回缺少实际网格或覆盖标记；拒绝缓存')
    temp=path.with_suffix('.tmp')
    with temp.open('w',encoding='utf-8') as fp:
        json.dump({'key':key,'data':data,'created_at':datetime.now(timezone.utc).isoformat()},fp,ensure_ascii=False,default=str)
    os.replace(temp,path)
    return path,True


def attach_hybrid_profiles(api_pressure, horizontal):
    """Makes no changes to original API result if OM validity checks fail."""
    pressure=deepcopy(api_pressure)
    result=bind_horizontal_profiles(pressure.get('samples') or [],horizontal)
    if not result.get('attached'):
        return api_pressure,result
    return pressure,result


class SpatialDesktopMixin:
    """Overrides just the pressure acquisition hook of the shared engine."""
    def _fetch_cloud_base_profiles(self, coords, distances, sunset_utc, model_id,
                                  local_ground_elevation_m=None):
        pressure=super()._fetch_cloud_base_profiles(
            coords,distances,sunset_utc,model_id,
            local_ground_elevation_m=local_ground_elevation_m)
        if not bool(getattr(self,'_spatial_enabled_snapshot',False)):
            return pressure
        self.spatial_fetch_log=getattr(self,'spatial_fetch_log',[])
        try:
            horizontal,mode,snapshot_path=fetch_desktop_horizontal(
                coords,distances,sunset_utc,model_id,
                replay=bool(getattr(self,'_spatial_replay_snapshot',False)),
                cache_dir=getattr(self,'spatial_cache_dir',SNAPSHOTS),
                fetcher=getattr(self,'spatial_fetcher',None))
            hybrid,binding=attach_hybrid_profiles(pressure,horizontal)
            info=horizontal.get('spatial_source') or {}
            accepted=int(binding.get('attached') or 0)
            self.spatial_fetch_log.append({
                'status':'spatial_horizontal_used_vertical_api' if accepted else 'point_api_fallback',
                'model':model_id,'distance_count':len(distances),
                'bound_points':accepted,'run':info.get('reference_time'),
                'valid':info.get('valid_time'),'fields':info.get('fields'),
                'cells_read':info.get('cells_read'), 'layer_origin':info.get('layer_origin'),
                'evidence':info.get('evidence'), 'source_mode':mode,
                'snapshot_file':snapshot_path, 'reason':binding.get('reason'),
                'warning':'OM 与 API 起报时次是否一致尚未核实；二维覆盖率不代表光学不透明度',
            })
            return hybrid
        except Exception as exc:
            self.spatial_fetch_log.append({
                'status':'point_api_fallback','model':model_id,
                'distance_count':len(distances),
                'reason':f'{type(exc).__name__}: {str(exc)[:250]}',
                'source_mode':'snapshot_replay' if getattr(self,'_spatial_replay_snapshot',False) else 'om_live'})
            return pressure


def _safe_value(x):
    if isinstance(x,datetime): return x.isoformat()
    if isinstance(x,dict): return {str(k):_safe_value(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)): return [_safe_value(v) for v in x]
    if isinstance(x,(str,int,float,bool)) or x is None: return x
    return str(x)


def collect_report(app):
    results={}
    selection=getattr(app,'_spatial_requested_direction','both')
    for direction,r in (getattr(app,'direction_results',{}) or {}).items():
        if selection in ('morning','evening') and direction!=selection:
            continue
        inferred=r.get('inferred') or {}
        results[direction]={
            'date':str(app.vars['date'].get()) if 'date' in app.vars else None,
            'model':r.get('query_model_id'),
            'api_model_metadata':r.get('model_metadata_signature'),
            'api_profile_valid_times':sorted({str(p.get('time')) for p in (r.get('profiles') or []) if p.get('time')})[:20],
            'event_local_time':str(r.get('event_dt')) if r.get('event_dt') is not None else None,
            'origin':r.get('origin'),
            'azimuth':r.get('azimuth'),
            'regular_probability':inferred.get('regular_score'),
            'color_score':inferred.get('color_score'),
            'tintable_ratio':inferred.get('tintable_ratio'),
            'burn_scale_index':inferred.get('burn_scale_index'),
            'burn_scale_label':inferred.get('burn_scale_label'),
            'strict_primary_path_diagnostic':inferred.get('strict_primary_path_diagnostic'),
            'off_axis_resampling':inferred.get('off_axis_resampling'),
        }
    return _safe_value({
        'mode':'OM_HORIZONTAL_FUSION_DESKTOP' if getattr(app,'_spatial_enabled_snapshot',False) else 'BASELINE_POINT_API',
        'local_om_replay':bool(getattr(app,'_spatial_replay_snapshot',False)),
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'location':app.vars['place'].get() if 'place' in app.vars else '',
        'query_direction':getattr(app,'_spatial_requested_direction','both'),
        'spatial_threshold_experimental':85.0,
        'report_format_version':2,
        'explanation':'85% is a new OM-versus-pressure inconsistency screen, not the old sky ray threshold',
        'source_log':getattr(app,'spatial_fetch_log',[]),
        'directions':results,
    })



# Desktop report comparison helpers are deliberately independent of Tk widgets.
# They also support the V1 report files already saved on disk.
def _report_mode(raw):
    value = str(raw or '').strip().upper()
    if value == 'BASELINE_POINT_API':
        return 'baseline'
    if value in ('OM_HORIZONTAL_FUSION', 'OM_HORIZONTAL_FUSION_DESKTOP'):
        return 'om'
    return None


def _report_rows(folder):
    """Yield concrete report/direction pairs without trusting GUI current selections."""
    found=[]
    for path in Path(folder).glob('desktop_*.json') if Path(folder).is_dir() else ():
        try:
            report=json.loads(path.read_text(encoding='utf-8'))
            mode=_report_mode(report.get('mode'))
            if mode is None or not isinstance(report.get('directions'),dict):
                continue
            stamp=str(report.get('generated_at') or path.stat().st_mtime)
            for direction,row in report['directions'].items():
                if direction not in ('morning','evening') or not isinstance(row,dict):
                    continue
                key=(str(report.get('location') or '').strip(),
                     str(row.get('date') or '').strip(),
                     str(row.get('model') or '').strip(),direction)
                if not all(key):
                    continue
                found.append({'key':key,'mode':mode,'stamp':stamp,
                              'path':path,'report':report,'row':row})
        except (OSError,ValueError,TypeError,AttributeError):
            continue
    return sorted(found,key=lambda x:x['stamp'],reverse=True)


def find_latest_report_comparison(folder):
    """Latest compatible baseline/OM pair, or a precise explanation of missing pairs.

    Pair keys include place, date, actual model ID, and direction. We do not
    conflate a saved evening query with morning or EC with GFS.
    """
    found=_report_rows(folder)
    pairs={}
    for record in found:
        pair=pairs.setdefault(record['key'],{})
        pair.setdefault(record['mode'],record)
    ready=[(key,pair) for key,pair in pairs.items() if 'baseline' in pair and 'om' in pair]
    if not ready:
        b=sum(row['mode']=='baseline' for row in found)
        m=sum(row['mode']=='om' for row in found)
        details=[]
        for row in found[:8]:
            a,bdate,model,direction=row['key']
            details.append('%s | %s | %s | %s | %s' % (
                '融合' if row['mode']=='om' else '原版',a,bdate,model,
                '朝霞' if direction=='morning' else '晚霞'))
        return None,('现有有效原版记录 %s 条，融合记录 %s 条，但没有找到地点、日期、模型、方向全部一致的一组。\n' % (b,m) +
                     ('已保存记录：\n'+'\n'.join(details) if details else '暂未发现可识别的诊断 JSON。'))
    # Select the latest completed pair, and collect all available directions
    # from that same city/date/model if both are matched.
    best_key,best_pair=max(ready,key=lambda x:max(r['stamp'] for r in x[1].values()))
    core=best_key[:3]
    group={key[3]:pair for key,pair in ready if key[:3]==core}
    return {'location':core[0],'date':core[1],'model':core[2],
            'directions':group,'latest':best_key[3]},None


def format_report_comparison(result):
    lines=['Burning Sky · Desktop 原版 / OM 融合对比',
           '地点：%s  |  日期：%s  |  模型：%s'%(
               result['location'],result['date'],result['model']),
           '='*64]
    base_reports={str(pair['baseline']['path']):pair['baseline']['report'] for pair in result['directions'].values()}
    fusion_reports={str(pair['om']['path']):pair['om']['report'] for pair in result['directions'].values()}
    b_report=max(base_reports.values(),key=lambda x:str(x.get('generated_at','')))
    f_report=max(fusion_reports.values(),key=lambda x:str(x.get('generated_at','')))
    b_time=source_summary(b_report)
    f_time=source_summary(f_report)
    lines += ['', '【模型时间与来源（时间全部注明 UTC / 北京时间）】',
              '原版 · 逐点 API 最新模型元数据起报：'+format_time(b_time['api_metadata_run']),
              '融合 · 逐点 API 最新模型元数据起报：'+format_time(f_time['api_metadata_run']),
              '融合 · OM 空间清单起报：'+('；'.join(format_time(x) for x in f_time['om_runs']) if f_time['om_runs'] else '未记录'),
              '融合 · OM 云场有效时刻：'+('；'.join(format_time(x) for x in f_time['om_valids']) if f_time['om_valids'] else '未记录'),
              source_comparison_line(b_report,f_report)]
    def fmt(value):
        return '—' if value is None else str(value)
    def measured(row,key):
        return row.get(key)
    for direction in ('morning','evening'):
        pair=result['directions'].get(direction)
        if not pair:
            continue
        old,new=pair['baseline']['row'],pair['om']['row']
        lines += ['', '【%s】'%('朝霞' if direction=='morning' else '晚霞')]
        for mode_name, report_row in [('原版',old),('融合',new)]:
            valid_times=report_row.get('api_profile_valid_times') or []
            lines.append('  %s · API 气压层有效时次：%s'%(
                mode_name,' / '.join(str(x) for x in valid_times[:5]) if valid_times else '旧报告未记录'))
            lines.append('  %s · 查询事件本地时间：%s'%(mode_name,report_row.get('event_local_time') or '未记录'))
        for key,label,unit in (
            ('regular_probability','常规概率','%'),
            ('color_score','色彩分','分'),
            ('tintable_ratio','可染色云占比','%'),
            ('burn_scale_index','火烧云规模','%')):
            a,b=measured(old,key),measured(new,key)
            text='  %-9s %s → %s'%(label,fmt(a),fmt(b))
            if isinstance(a,(int,float)) and isinstance(b,(int,float)) and not isinstance(a,bool) and not isinstance(b,bool):
                text+='   (变化 %+0.2f%s)'%(b-a,unit)
            lines.append(text)
        a=old.get('strict_primary_path_diagnostic') or {}
        d=new.get('strict_primary_path_diagnostic') or {}
        lines.append('  严格光路：%s → %s'%(fmt(a.get('status')),fmt(d.get('status'))))
        for field,label in (
            ('horizontal_ray_checks','候选光路检查'),
            ('horizontal_evaluated_nodes','网格节点检查'),
            ('horizontal_conflict_count','水平/垂直冲突'),
            ('horizontal_target_conflict_count','目标云冲突')):
            lines.append('  %s：%s'%(label,fmt(d.get(field))))
        lines.append('  原版 JSON：%s'%pair['baseline']['path'].name)
        lines.append('  融合 JSON：%s'%pair['om']['path'].name)
        # Source-level audit: these values follow DIFFERENT calculation chains.
        new_area=new.get('tintable_ratio');old_area=old.get('tintable_ratio')
        same_p=(old.get('regular_probability')==new.get('regular_probability'))
        same_c=(old.get('color_score')==new.get('color_score'))
        if (isinstance(new_area,(int,float)) and isinstance(old_area,(int,float))
            and new_area+1e-8<old_area):
            lines.append('  【评分链审查】OM 已减少严格受光面积。')
            if same_p:
                lines.append('    常规概率未变化：旧概率评分未连续接入 OM 冲突数量/面积；只受严格光路是否最终通过的门控影响。')
                lines.append('    这属于当前算法结构，不能据此推断 62%/86% 已经适当校准。')
            if same_c:
                lines.append('    色彩分未变化：颜色属于条件色彩表现指标，不乘染色面积；尚未直接纳入 OM 空间透光衰减。')
                lines.append('    颜色不变可能有定义上的合理性，但不等于火烧云整体强度不变。')
        if d.get('horizontal_conflict_count',0):
            lines.append('  【冲突统计】>=85% 是新实验阈值；与云量本身的真实单束光透过率不能画等号。')
    # Avoid counting same OM report twice when the report contains both directions.
    om_reports={str(pair['om']['path']):pair['om']['report'] for pair in result['directions'].values()}
    logs=[row for report in om_reports.values() for row in (report.get('source_log') or []) if isinstance(row,dict)]
    success=sum(x.get('status')=='spatial_horizontal_used_vertical_api' and int(x.get('bound_points') or 0)>0 for x in logs)
    fallback=sum(x.get('status')=='point_api_fallback' for x in logs)
    lines += ['', '空间数据源：OM 绑定 %s 批，回退原 API %s 批。'%(success,fallback)]
    derived=sum(x.get('layer_origin')=='pressure_layer_proxy' and x.get('status')=='spatial_horizontal_used_vertical_api' for x in logs)
    if derived:lines.append('其中 GFS 气压层云量重构 %s 批（不同于原生低中高云；按气压层最大值近似）。'%derived)
    if fallback:
        reasons=list(dict.fromkeys(str(x.get('reason')) for x in logs if x.get('status')=='point_api_fallback' and x.get('reason')))
        for reason in reasons[:4]:lines.append('空间回退原因：'+reason[:230])
    if success==0:
        lines.append('注意：融合报告未记录成功的 OM 绑定，无法证明本次结果由空间云场改变。')
    if fallback:
        lines += ['', '【OM 回退原因（读取现有诊断 JSON，无需重新请求天气）】']
        domain_map={'ncep_gfs_global':'ncep_gfs025', 'ecmwf_ifs025':'ecmwf_ifs025'}
        grouped={}
        for row in logs:
            if row.get('status')!='point_api_fallback':
                continue
            model=str(row.get('model') or '未记录')
            domain=domain_map.get(model,'未明确映射')
            reason=str(row.get('reason') or '旧报告未记录异常细节')
            key=(model,domain,reason)
            group=grouped.setdefault(key,{'count':0,'distances':[]})
            group['count']+=1
            if row.get('distance_count') is not None:
                group['distances'].append(str(row['distance_count']))
        for (model,domain,reason),details in grouped.items():
            lines.append('  模型 %s → OM %s，失败 %d 批（每批采样点数 %s）'%(model,domain,details['count'],
                          '/'.join(details['distances']) if details['distances'] else '未记录'))
            lines.append('    失败原因：%s'%reason)
        if result['model']=='ncep_gfs_global':
            lines.append('  说明：GFS Global 是可混合分辨率的 API 名称，而 OM ncep_gfs025 是独立 0.25° 网格；即使读取成功，也不能假设两者起报与云场完全相同。')
    lines += ['', '说明：两次运行的 API/OM 模型起报是否完全一致尚未证实；',
              '      结果差异不能直接全部归因于 OM。',
              '      85% 仍只是 V1 的实验性冲突阈值，不是物理通光阈值。']
    return '\n'.join(lines)

class DesktopSpatialApp(SpatialDesktopMixin, FireCloudApp):
    def __init__(self):
        self._spatial_enabled_snapshot=False
        self._spatial_replay_snapshot=False
        self.spatial_cache_dir=SNAPSHOTS
        self.spatial_fetch_log=[]
        self.spatial_fetcher=None
        self._national_result_queue=queue.Queue()
        self._national_busy=False
        super().__init__()
        self.title('Burning Sky · Desktop OM 空间光路融合试验（不影响 Pro/Lite）')

    def _build_ui(self):
        super()._build_ui()
        # Insert a compact always-visible experimental control in the header.
        container=self.nametowidget(self.main_scroll_canvas.itemcget(self._main_outer_window,'window'))
        header=container.winfo_children()[0]
        panel=ttk.Frame(container,style='Card.TFrame',padding=10)
        panel.pack(fill='x',before=header,pady=(0,9))
        self.spatial_toggle=tk.BooleanVar(value=False)
        self.spatial_replay=tk.BooleanVar(value=False)
        ttk.Checkbutton(panel,text='开启 OM 二维云场 + API 垂直剖面融合（实验）',
                        variable=self.spatial_toggle).pack(anchor='w')
        ttk.Checkbutton(panel,text='只重放本地已保存的 OM 快照（不联网请求 OM）',
                        variable=self.spatial_replay).pack(anchor='w')
        self.spatial_message=tk.StringVar(value='默认关闭：与原桌面算法一致；开启后只在 OM 数据有效时参与光路复核。')
        ttk.Label(panel,textvariable=self.spatial_message,style='Card.TLabel',wraplength=850).pack(anchor='w',pady=3)
        buttons=ttk.Frame(panel,style='Card.TFrame');buttons.pack(fill='x',pady=3)
        ttk.Button(buttons,text='保存空间光路诊断 JSON',command=self.export_spatial_report).pack(side='left',padx=(0,8))
        ttk.Button(buttons,text='查看本次冲突概要',command=self.show_spatial_summary).pack(side='left',padx=(0,8))
        ttk.Button(buttons,text='比较最近原版/融合报告',command=self.compare_last_reports).pack(side='left')
        drawbuttons=ttk.Frame(panel,style='Card.TFrame');drawbuttons.pack(fill='x',pady=(5,3))
        ttk.Button(drawbuttons,text='绘制缓存 OM 云场俯视图',command=self.show_cached_cloud_map).pack(side='left',padx=(0,9))
        ttk.Button(drawbuttons,text='读取全国 OM 云场（手动）',command=self.request_national_cloud_map).pack(side='left',padx=(0,9))
        ttk.Button(drawbuttons,text='融合朝霞剖面',command=lambda:self.show_fusion_profile('morning')).pack(side='left',padx=(0,9))
        ttk.Button(drawbuttons,text='融合晚霞剖面',command=lambda:self.show_fusion_profile('evening')).pack(side='left')
        map_tools=ttk.Frame(panel,style='Card.TFrame');map_tools.pack(fill='x',pady=(4,3))
        ttk.Button(map_tools,text='准备省市底图（一次）',command=self.prepare_china_basemap).pack(side='left',padx=(0,8))
        ttk.Button(map_tools,text='八方位朝霞（离线）',command=lambda:self.show_cloud_compass('morning')).pack(side='left',padx=(0,8))
        ttk.Button(map_tools,text='八方位晚霞（离线）',command=lambda:self.show_cloud_compass('evening')).pack(side='left',padx=(0,8))
        map_row2=ttk.Frame(panel,style='Card.TFrame');map_row2.pack(fill='x',pady=(0,3))
        ttk.Button(map_row2,text='全国朝霞候选区（离线）',command=lambda:self.show_national_candidate('morning')).pack(side='left',padx=(0,8))
        ttk.Button(map_row2,text='全国晚霞候选区（离线）',command=lambda:self.show_national_candidate('evening')).pack(side='left')
        ttk.Label(panel,text='绘制按钮完全离线；首次「读取全国」会产生 OM 文件网络传输，之后同起报/有效时刻复用缓存。GFS 分层可为气压层重构，非卫星云图。',
                  style='Card.TLabel',wraplength=850).pack(anchor='w')
        ttk.Label(panel,text='建议相同日期、模型分别关闭/开启融合查询；需要离线复测时复用原案例 API 缓存 + OM 快照。',
                  style='Card.TLabel',wraplength=850).pack(anchor='w')

    def fetch_weather(self,direction_key='both'):
        # Called from Tk main thread, snapshot flags before background work.
        self._spatial_requested_direction=direction_key
        self._spatial_enabled_snapshot=bool(self.spatial_toggle.get())
        self._spatial_replay_snapshot=bool(self.spatial_replay.get())
        self.spatial_fetch_log=[]
        self.spatial_message.set('运行中：空间融合='+('开启' if self._spatial_enabled_snapshot else '关闭') +
                                 ('；OM 本地快照重放' if self._spatial_replay_snapshot else '；OM 联网读取'))
        return super().fetch_weather(direction_key)

    def _finish_fetch_weather(self,preserve_status=False):
        super()._finish_fetch_weather(preserve_status=preserve_status)
        successes=sum(x.get('status')=='spatial_horizontal_used_vertical_api' for x in self.spatial_fetch_log)
        misses=sum(x.get('status')=='point_api_fallback' for x in self.spatial_fetch_log)
        status=('已完成：OM 融合 %s 批；回退 API %s 批。' % (successes,misses)) if self._spatial_enabled_snapshot else '已完成原版对照，不使用 OM 数据。'
        if self._spatial_enabled_snapshot and misses:
            failures=[row.get('reason') or '无错误详情' for row in self.spatial_fetch_log if row.get('status')=='point_api_fallback']
            if failures:
                status+=' 最近回退原因：'+str(failures[-1])[:180]
        self.spatial_message.set(status)
        try:
            REPORTS.mkdir(parents=True,exist_ok=True)
            dest=REPORTS/('desktop_%s_%s.json'%(
                'om' if self._spatial_enabled_snapshot else 'baseline',
                datetime.now().strftime('%Y%m%d_%H%M%S_%f')))
            with dest.open('w',encoding='utf-8') as f:
                json.dump(collect_report(self),f,ensure_ascii=False,indent=2)
        except (OSError,TypeError,ValueError):
            pass

    def export_spatial_report(self):
        try:
            dest=filedialog.asksaveasfilename(
                title='保存空间光路诊断',defaultextension='.json',
                filetypes=[('JSON files','*.json')],initialfile='OM_光路诊断.json')
            if dest:
                Path(dest).write_text(json.dumps(collect_report(self),ensure_ascii=False,indent=2),encoding='utf-8')
                messagebox.showinfo('导出成功',f'已保存：{dest}')
        except Exception as exc:
            messagebox.showerror('导出失败',str(exc))

    def compare_last_reports(self):
        """Show the most recent compatible on-disk pair in a scrollable Tk window."""
        try:
            result,reason=find_latest_report_comparison(REPORTS)
            if result is None:
                messagebox.showinfo('未找到可比对的一组报告',reason)
                return
            content=format_report_comparison(result)
            # Keep a text copy, but do not require a separate reader to see it.
            REPORTS.mkdir(parents=True,exist_ok=True)
            filename='compare_%s.txt'%datetime.now().strftime('%Y%m%d_%H%M%S')
            save_to=REPORTS/filename
            save_to.write_text(content,encoding='utf-8')
            window=tk.Toplevel(self)
            window.title('Burning Sky · 原版 / 空间融合对照')
            window.geometry('820x620')
            window.minsize(520,360)
            frame=ttk.Frame(window,padding=12)
            frame.pack(fill='both',expand=True)
            ttk.Label(frame,text='自动匹配最近一组同地点、日期、模型、方向的结果',
                      font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=(0,8))
            area=ttk.Frame(frame)
            area.pack(fill='both',expand=True)
            scrollbar=ttk.Scrollbar(area,orient='vertical')
            scrollbar.pack(side='right',fill='y')
            output=tk.Text(area,wrap='word',font=('Consolas',11),
                           yscrollcommand=scrollbar.set,relief='flat',padx=12,pady=12)
            output.pack(side='left',fill='both',expand=True)
            scrollbar.configure(command=output.yview)
            output.insert('1.0',content)
            output.configure(state='disabled')
            ttk.Label(frame,text='文本报告已保存至：'+str(save_to),wraplength=780).pack(anchor='w',pady=(8,4))
            ttk.Button(frame,text='关闭',command=window.destroy).pack(anchor='e')
            window.transient(self)
            window.lift()
            window.focus_force()
        except Exception as exc:
            messagebox.showerror('对比报告打开失败', '%s: %s'%(type(exc).__name__,exc))

    def _open_figure_window(self, title, figure, *, note=''):
        """Open a saved-data-only matplotlib canvas with PNG export."""
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        win=tk.Toplevel(self)
        win.title(title)
        win.geometry('1080x750')
        win.minsize(670,430)
        frame=ttk.Frame(win,padding=8);frame.pack(fill='both',expand=True)
        if note:
            ttk.Label(frame,text=note,wraplength=1020).pack(anchor='w',pady=(0,5))
        canvas=FigureCanvasTkAgg(figure,master=frame)
        canvas.get_tk_widget().pack(fill='both',expand=True)
        canvas.draw_idle()
        row=ttk.Frame(frame);row.pack(fill='x')
        def save():
            path=filedialog.asksaveasfilename(defaultextension='.png',filetypes=[('PNG 图片','*.png')])
            if path:
                try:figure.savefig(path,dpi=160,facecolor=figure.get_facecolor())
                except Exception as exc:messagebox.showerror('保存失败',str(exc))
        ttk.Button(row,text='保存 PNG（不联网）',command=save).pack(side='right',padx=5)
        ttk.Button(row,text='关闭',command=win.destroy).pack(side='right',padx=5)
        win.transient(self)
        win.lift()
        return win

    def _existing_snapshot_files(self):
        """Prefer newest OM report, but accept V1 cache snapshots and moves."""
        candidates=[]
        for report_file in sorted(REPORTS.glob('desktop_om_*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                report=json.loads(report_file.read_text(encoding='utf-8'))
                for row in report.get('source_log') or []:
                    if not isinstance(row,dict):continue
                    raw=row.get('snapshot_file')
                    if raw:
                        fname=Path(raw).name
                        source=Path(raw)
                        if not source.is_file():source=Path(self.spatial_cache_dir)/fname
                        if source.is_file():candidates.append(source)
            except (ValueError,TypeError,OSError):pass
        candidates.extend(sorted(Path(self.spatial_cache_dir).glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True))
        seen=set();unique=[]
        for x in candidates:
            key=str(x.resolve())
            if key not in seen:seen.add(key);unique.append(x)
        return unique[:80]

    def _latest_horizontal_source(self):
        for path in self._existing_snapshot_files():
            try:
                data=read_snapshot(path)
                source=data.get('spatial_source') or {}
                if source.get('domain') and source.get('reference_time') and source.get('valid_time') and not source.get('national_overlay'):
                    return source
            except (OSError,ValueError,TypeError):continue
        return None

    def request_national_cloud_map(self):
        """The only action that may fetch previously uncached nationwide OM."""
        if self._national_busy:
            messagebox.showinfo('全国云场','正在读取同一批全国云场，请查看状态栏。')
            return
        src=self._latest_horizontal_source()
        if src is None:
            messagebox.showinfo('全国云场','先开启融合完成一次成功的 EC/GFS 空间查询，再手动获取该起报与有效时刻的全国云场。')
            return
        self._national_busy=True
        self.spatial_message.set('正在获取全国网格（首次可能增加 OM 网络读取，保留原有 API 查询结果不变）…')
        def worker():
            try:
                path,downloaded=fetch_national_cloud_snapshot(src,folder=self.spatial_cache_dir)
                self._national_result_queue.put(('ok',str(path),downloaded))
            except Exception as exc:
                self._national_result_queue.put(('error',f'{type(exc).__name__}: {str(exc)}',False))
        threading.Thread(target=worker,daemon=True,name='NationalOMRead').start()
        self.after(150,self._poll_national_cloud)

    def _poll_national_cloud(self):
        try:status,detail,downloaded=self._national_result_queue.get_nowait()
        except queue.Empty:
            self.after(150,self._poll_national_cloud)
            return
        self._national_busy=False
        if status=='ok':
            self.spatial_message.set('全国云场已缓存：'+Path(detail).name+('（本次读取了新数据）' if downloaded else '（已有缓存，未联网）'))
            self.show_cached_cloud_map()
        else:
            self.spatial_message.set('全国 OM 读取失败，原预测结果未修改：'+detail[:160])
            messagebox.showwarning('全国云场读取失败',detail)

    def show_cached_cloud_map(self):
        """The draw action never requests .om or any other weather API."""
        try:
            paths=self._existing_snapshot_files()
            if not paths:
                messagebox.showinfo('没有 OM 本地快照','请先开启融合查询，成功读取并保存 .om 云场后再绘图；绘图本身不会联网。')
                return
            snapshot=[]
            for path in paths:
                try:
                    data=read_snapshot(path)
                    info=data.get('spatial_source') or {}
                    grid=data.get('grid_view') or {}
                    label=f"{info.get('domain','未知')} | valid {str(info.get('valid_time','?'))[:16]} | " + (
                        f"{grid.get('shape',[])} 网格" if grid.get('layers') else f"{len(data.get('samples') or [])} 离散点（旧快照）")
                    snapshot.append((label,path,data))
                except (OSError,ValueError,TypeError):pass
            if not snapshot:
                messagebox.showinfo('快照不可用','找到文件但无法读取有效云数据，请查看快照 JSON。')
                return
            # Compose matching batches as a tiled mosaic. Never mix model runs.
            groups={}
            for label,path,data in snapshot:
                info=data.get('spatial_source') or {}
                key=(info.get('domain'),info.get('reference_time'),info.get('valid_time'))
                groups.setdefault(key,[]).append((label,path,data))
            mosaics=[]
            for key,group in groups.items():
                if len(group)>1:
                    mosaic_label=f'拼接 {len(group)} 批 | {key[0]} | valid {str(key[2])[:16]}'
                    mosaics.append((mosaic_label,[item[1] for item in group],[item[2] for item in group]))
            snapshot=mosaics+snapshot
            win=tk.Toplevel(self);win.title('OM 本地云场俯视图 · 完全离线')
            win.geometry('1050x770');win.minsize(680,460)
            toolbar=ttk.Frame(win,padding=7);toolbar.pack(fill='x')
            ttk.Label(toolbar,text='批次').pack(side='left')
            select=tk.StringVar(value='0')
            picks=[f"{i+1}. {label}" for i,(label,_,_) in enumerate(snapshot)]
            choose=ttk.Combobox(toolbar,values=picks,width=67,state='readonly')
            choose.current(0);choose.pack(side='left',padx=5,fill='x',expand=True)
            layer=tk.StringVar(value='中云')
            view_choice=tk.StringVar(value='全国固定底图')
            ttk.Label(toolbar,text='视角').pack(side='left',padx=(7,2))
            view_picker=ttk.Combobox(toolbar,textvariable=view_choice,values=['全国固定底图','局部网格范围'],width=14,state='readonly')
            view_picker.pack(side='left',padx=(0,4))
            layer_picker=ttk.Combobox(toolbar,textvariable=layer,values=list(LAYERS),width=22,state='readonly')
            layer_picker.pack(side='left',padx=5)
            figure_holder=ttk.Frame(win);figure_holder.pack(fill='both',expand=True)
            status=tk.StringVar(value='绘图直接读取磁盘缓存，不会产生新的天气 API 请求。')
            ttk.Label(win,textvariable=status,wraplength=1020).pack(anchor='w',padx=9,pady=4)
            row=ttk.Frame(win,padding=7);row.pack(fill='x')
            state={'figure':None,'canvas':None,'data':None}
            def redraw(_ev=None):
                idx=choose.current()
                if idx<0:return
                try:
                    _,path,data=snapshot[idx]
                    fig=render_map_figure(data,layer.get(),nationwide=view_choice.get()=='全国固定底图')
                    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
                    if state['canvas'] is not None:
                        state['canvas'].get_tk_widget().destroy()
                    state['canvas']=FigureCanvasTkAgg(fig,master=figure_holder)
                    state['canvas'].get_tk_widget().pack(fill='both',expand=True)
                    state['canvas'].draw_idle()
                    state.update({'figure':fig,'data':data})
                    is_national=any((d.get('spatial_source') or {}).get('national_overlay') for d in (data if isinstance(data,list) else [data]))
                    status.set('来自本地快照：'+(f'{len(path)} 个相同时次的文件' if isinstance(path,list) else str(path))+'；'+ ('包含已实际读取的全国网格。' if is_national else '只有局部读取范围，全国其他区域空白≠晴空。'))
                except Exception as exc:
                    status.set('无法绘制：'+str(exc))
            def save():
                fig=state.get('figure')
                if fig is None:return
                filename=filedialog.asksaveasfilename(defaultextension='.png',filetypes=[('PNG 图片','*.png')])
                if filename:
                    try:fig.savefig(filename,dpi=160,facecolor=fig.get_facecolor())
                    except Exception as exc:messagebox.showerror('保存失败',str(exc))
            choose.bind('<<ComboboxSelected>>',redraw)
            view_picker.bind('<<ComboboxSelected>>',redraw)
            layer_picker.bind('<<ComboboxSelected>>',redraw)
            ttk.Button(row,text='重绘',command=redraw).pack(side='left')
            ttk.Button(row,text='保存 PNG',command=save).pack(side='right',padx=5)
            ttk.Button(row,text='关闭',command=win.destroy).pack(side='right',padx=5)
            win.after(80,redraw)
            win.transient(self);win.lift()
        except Exception as exc:
            messagebox.showerror('离线云场绘制失败',f'{type(exc).__name__}: {exc}')

    def _matched_om_run(self, model):
        for row in reversed(getattr(self,'spatial_fetch_log',[]) or []):
            if row.get('model')==model and row.get('bound_points'):
                return row.get('run')
        return None

    def prepare_china_basemap(self):
        """Download Natural Earth province boundaries only on explicit click."""
        from desktop.china_admin_basemap import CACHE,prepare_admin_basemap
        if CACHE.exists():
            messagebox.showinfo('省市底图','省界底图已缓存到 '+str(CACHE)+'。\n重绘云图即可看到省界。')
            return
        self.spatial_message.set('正在一次性获取 Natural Earth 省级边界（不是天气请求）…')
        def worker():
            try:
                path,count,downloaded=prepare_admin_basemap()
                msg=f'已缓存 {count} 个省级行政单元，位置：{path}。今后只从磁盘读取。'
                self.after(0,lambda:self._basemap_download_result(True,msg))
            except Exception as exc:
                err=f'{type(exc).__name__}: {str(exc)[:320]}'
                self.after(0,lambda:self._basemap_download_result(False,err))
        threading.Thread(target=worker,daemon=True,name='ChinaAdminBasemap').start()

    def _basemap_download_result(self,success,text):
        self.spatial_message.set(text)
        if success:messagebox.showinfo('省市底图已就绪',text)
        else:messagebox.showwarning('省界底图下载失败',text+'\n可手动把来源 GeoJSON 放到 desktop_spatial_debug/map_basemap/china_admin1_reference.geojson；没有省界时仍显示海岸线和省会城市。')

    def _latest_national_snapshot(self):
        # Prefer a full nationwide cache; never pretend a partial local tile is China-wide.
        paths=list(self.spatial_cache_dir.glob('national_*.json')) if self.spatial_cache_dir.exists() else []
        if not paths:raise ValueError('尚无完整全国 OM 快照，请先点击「读取全国 OM 云场（手动）」。')
        paths.sort(key=lambda p:p.stat().st_mtime,reverse=True)
        # Do not show an EC cloud map after the user just queried GFS, or vice versa.
        model_ids={row.get('query_model_id') for row in (getattr(self,'direction_results',{}) or {}).values() if isinstance(row,dict)}
        domains={'ncep_gfs_global':'ncep_gfs025','ecmwf_ifs025':'ecmwf_ifs025'}
        wanted={domains[x] for x in model_ids if x in domains}
        for path in paths:
            try:
                data=read_snapshot(path)
                source=data.get('spatial_source') or {}
                if source.get('national_overlay') and (data.get('grid_view') or {}).get('layers') and (not wanted or source.get('domain') in wanted):
                    return data
            except (ValueError,TypeError,OSError):continue
        raise ValueError('没有完整有效的全国云场快照。')

    def show_cloud_compass(self,direction='evening'):
        """Reuse same-event OM raster and strict-ray seeds; never resample weather."""
        try:
            from desktop.spatial_area_analysis import render_compass_figure
            from burning_sky.compass_illuminated_sectors import compass_from_verified_rays
            from burning_sky.compass360_web import _event_utc
            from burning_sky.spatial_ray_fusion import _utc
            data=self._latest_national_snapshot()
            rows=getattr(self,'direction_results',{}) or {}
            row=rows.get(direction) or {}
            origin=row.get('origin')
            if not (isinstance(origin,(list,tuple)) and len(origin)>=2):
                raise ValueError('请先完成当前方向的单次查询，以取得对应的观测点和严格光路。')
            event=row.get('event_dt')
            if event is None:
                raise ValueError('当前单次结果缺少事件时间，不能与OM有效时次核对。')
            from datetime import datetime
            if isinstance(event,str): event=datetime.fromisoformat(event)
            info=data.get('spatial_source') or {}
            valid=_utc(info.get('valid_time'))
            if (valid is None or
                abs((valid-_event_utc(event)).total_seconds())>5400 or
                info.get('time_mosaic')):
                raise ValueError('本地全国云场不属于当前朝/晚霞的有效时次；请读取同一时刻的OM快照。')
            runs={str(p['spatial_horizontal_run']) for p in row.get('profiles',[])
                  if isinstance(p,dict) and p.get('spatial_horizontal_run')}
            if len(runs)==1 and _utc(next(iter(runs)))!=_utc(info.get('reference_time')):
                raise ValueError('OM云场起报与当前严格光路使用的空间云场不一致，拒绝混用。')
            result=compass_from_verified_rays(
                data,float(origin[0]),float(origin[1]),event,direction,
                verified_rays=(row.get('inferred') or {}).get('verified_primary_ray_traces') or [])
            fig=render_compass_figure(result)
            self._open_figure_window('八方位 · 云洞偏移与中高云加权',fig,note=result['note'])
        except Exception as exc:
            messagebox.showerror('八方位分析失败',str(exc))

    def show_national_candidate(self,direction):
        try:
            from desktop.spatial_area_analysis import national_candidate,render_national_candidate_figure
            data=self._latest_national_snapshot()
            result=national_candidate(data,direction=direction)
            if result['evaluated_cells']<1:
                messagebox.showwarning('全国候选区','当前 OM 快照有效时刻不覆盖足够的'+('朝霞' if direction=='morning' else '晚霞')+'晨昏窗口，或上游 700 km 超出已缓存区域。\n请使用相应有效时刻的全国快照。')
                return
            fig=render_national_candidate_figure(result)
            self._open_figure_window(('朝霞' if direction=='morning' else '晚霞')+' · 全国候选区域',fig,note=result['note'])
        except Exception as exc:messagebox.showerror('全国候选区分析失败',str(exc))

    def show_fusion_profile(self, direction_key):
        """Fused section highlights evidence conflicts without fabricating rays."""
        result=(getattr(self,'direction_results',{}) or {}).get(direction_key)
        if not result:
            messagebox.showinfo('没有剖面数据','请先查询该方向，再打开融合剖面。')
            return
        diag=(result.get('inferred') or {}).get('strict_primary_path_diagnostic') or {}
        if not diag.get('horizontal_fusion_enabled'):
            messagebox.showinfo('尚未启用融合复核','当前方向没有实际执行二维 OM 光路复核；将打开原版剖面。')
            return super().show_path_cloud_profile(direction_key)
        try:
            meta=result.get('model_metadata_signature') or {}
            name=self.vars['place'].get() if 'place' in self.vars else ''
            fig=render_fusion_profile_figure(result,model=result.get('query_model_id'),origin_name=name,
                                             om_run=self._matched_om_run(result.get('query_model_id')),
                                             api_run=meta.get('initialisation'))
            self._open_figure_window(('朝霞' if direction_key=='morning' else '晚霞')+' · OM 融合剖面',fig,
                note='紫实线：融合后仍通过的严格光路；虚线：未验证候选；橙叉：水平/垂直证据冲突（仅示例，不能解释成确定遮挡）。')
        except Exception as exc:messagebox.showerror('融合剖面绘制失败',f'{type(exc).__name__}: {exc}')

    def show_path_cloud_profile(self, direction_key):
        """Existing GUI profile buttons now open fused evidence when applicable."""
        row=(getattr(self,'direction_results',{}) or {}).get(direction_key) or {}
        diag=(row.get('inferred') or {}).get('strict_primary_path_diagnostic') or {}
        if diag.get('horizontal_fusion_enabled'):
            return self.show_fusion_profile(direction_key)
        return super().show_path_cloud_profile(direction_key)

    def show_spatial_summary(self):
        out=[]
        for direction,result in (getattr(self,'direction_results',{}) or {}).items():
            d=(result.get('inferred') or {}).get('strict_primary_path_diagnostic') or {}
            out.append('%s: %s; 候选 %s; 节点 %s; 水平冲突 %s; 目标冲突 %s'%(
                '朝霞' if direction=='morning' else '晚霞',
                d.get('status','暂无'),d.get('horizontal_ray_checks',0),
                d.get('horizontal_evaluated_nodes',0),
                d.get('horizontal_conflict_count',0),
                d.get('horizontal_target_conflict_count',0)))
        if self.spatial_fetch_log:
            out.append('数据源最近一批：%s'%self.spatial_fetch_log[-1].get('status','?'))
            for idx,row in enumerate(self.spatial_fetch_log,1):
                if row.get('status')=='point_api_fallback':
                    out.append('第 %d 批（%s，%s 点）回退：%s'%(idx,row.get('model') or '未知模型',
                               row.get('distance_count') or '?',row.get('reason') or '未记录原因'))
        messagebox.showinfo('空间融合诊断','\n'.join(out) or '尚无查询结果')


def main():
    DesktopSpatialApp().mainloop()

if __name__=='__main__': main()
