# -*- coding: utf-8 -*-
"""Independent one-direction advanced satellite evidence editor.

Two 24-hour snapshots per identity: unaltered baseline and latest accepted
correction. Never promote infrared palette categories to measured cloud cover.
"""
import os
import sys
import io
import json
import time
import math
import copy
import hashlib
from pathlib import Path
from datetime import datetime, date, timedelta

os.environ['BURNING_SKY_HEADLESS'] = '1'
os.environ['BURNING_SKY_REQUEST_PROFILE'] = 'interactive'
import streamlit as st
import pandas as pd
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
import engine
from burning_sky.satellite_calibration import CITIES, _fit_affine, _avg_patch, compare_samples, _patch_evidence
from burning_sky.satellite_auto_vision import recognize_cities, auto_cloud_references
from burning_sky.spherical_raytrace import destination
from burning_sky.web_color_curve import draw_dynamic_color_curve
from burning_sky.profile_image import render_profile
from burning_sky.api_rate_limit import set_request_profile, reset_request_profile, cooldown_remaining

st.set_page_config(page_title='进阶分析 | Burning Sky',page_icon='🛰️',layout='wide')
password = os.getenv('APP_PASSWORD','')
try: password = st.secrets.get('APP_PASSWORD',password)
except Exception: pass
if password and not st.session_state.get('authenticated'):
    pw=st.text_input('请输入 Burning Sky 访问密码',type='password')
    if st.button('登录') and pw==password:
        st.session_state['authenticated']=True
        st.rerun()
    st.stop()

CACHE = ROOT/'data'/'advanced_analysis_cache'
TTL_SECONDS = 24*3600
MODELS={'EC':'ECMWF IFS 0.25°','GFS':'GFS Global'}
DIRS={'朝霞':'morning','晚霞':'evening'}

def _key(place,lat,lon,elev,day,model,direction):
    raw=json.dumps([place,round(lat,5),round(lon,5),round(elev,1),day,model,direction],ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:26]

def _prune():
    CACHE.mkdir(parents=True,exist_ok=True)
    now=time.time()
    for p in list(CACHE.glob('*.json'))+list(CACHE.glob('*.jpg')):
        try:
            if now-p.stat().st_mtime>TTL_SECONDS:p.unlink()
        except OSError:pass

def _write(name,payload):
    _prune()
    path=CACHE/f'{name}.json'
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(engine.FireCloudApp._json_safe(payload),ensure_ascii=False,default=str),'utf-8')
    os.replace(tmp,path)

def _load(name):
    _prune()
    path=CACHE/f'{name}.json'
    if not path.exists():return None
    try: return json.loads(path.read_text('utf-8'))
    except (ValueError,OSError):return None

class _V:
    def __init__(self,v):self.v=str(v)
    def get(self):return self.v
    def set(self,v):self.v=str(v)
class _Label:
    def configure(self,**kwargs):pass
class Runner(engine.FireCloudApp):
    def _set_main_progress(self,*args,**kwargs):pass
    def _set_text(self,*args,**kwargs):pass
    def _set_judgment_text(self,*args,**kwargs):pass
    def _draw_peak_color_swatch(self,*args,**kwargs):pass
    def _draw_colorbar(self,*args,**kwargs):pass

def _runner(place,lat,lon,elev,day,model):
    r=object.__new__(Runner)
    values={'place':place,'date':day,'lat':str(lat),'lon':str(lon),'elevation':str(elev),
            'weather_model':MODELS[model],'cloud_base_mode':'自动剖面推算',
            'morning_cloud_base_km':'2.0','evening_cloud_base_km':'2.0',
            'morning_aod':'','evening_aod':'','aod_source':'CAMS'}
    r.vars={k:_V(v) for k,v in values.items()}
    r.direction_results={};r.last_report='';r.last_fetch_time=None
    r.aod_manual_override=False;r._setting_aod_programmatically=False
    r.web_aod_value=None;r.web_aod_valid_time=None;r.aod_path_values=[]
    r.cloud_path_samples=[];r.cloud_path_result=None;r.fetch_in_progress=False
    r.after=lambda *a,**k:None
    for name in ('morning_summary','evening_summary','morning_score_label','evening_score_label',
                 'morning_upper_label','evening_upper_label','morning_color_score_label',
                 'evening_color_score_label','morning_judgment_label','evening_judgment_label',
                 'morning_rainbow_label','evening_rainbow_label'):
        setattr(r,name,_Label())
    r.morning_aod_status_var=_V('');r.evening_aod_status_var=_V('')
    return r

def _compute(config,direction,cache=None,overrides=None,extra=None, saved_rainbow=None):
    r=_runner(config['place'],config['lat'],config['lon'],config['elev'],config['date'],config['model'])
    prior=engine._CASE_REQUEST_CONTEXT
    request_cache = cache if isinstance(cache,dict) else {}
    context={'mode':'capture','cache':request_cache}
    if overrides:context['cloud_overrides']=overrides
    if extra:context['satellite_extra_distances']=extra
    if overrides:context['satellite_recompute']=True
    if saved_rainbow:context['saved_rainbow']=saved_rainbow
    token=set_request_profile("satellite_advanced")
    try:
        engine._CASE_REQUEST_CONTEXT=context
        engine.messagebox.last_error=None
        r._fetch_weather_task(direction,raise_errors=True,notify_success=False)
        if engine.messagebox.last_error:raise RuntimeError(str(engine.messagebox.last_error))
        result=r.direction_results.get(direction)
        if not result or not result.get('inferred'):raise RuntimeError('分析没有返回完整结果')
        return result,request_cache
    finally:
        engine._CASE_REQUEST_CONTEXT=prior
        reset_request_profile(token)

def _fig(result,direction):
    curve=result.get('dynamic_light_curve') or {}
    return draw_dynamic_color_curve(curve.get('samples') or [],curve.get('peak'),direction)

def _summary(result):
    inf=result.get('inferred') or {}
    return { '常规概率':inf.get('regular_score'), '色彩分':inf.get('color_score'),
             '可染色云占比':inf.get('tintable_ratio'), '火烧云规模':inf.get('burn_scale_index') }

def _show(result,direction,label):
    st.subheader(label)
    summary=_summary(result)
    cols=st.columns(4)
    for col,(name,v) in zip(cols,summary.items()):
        col.metric(name,'—' if v is None else f'{v}'+('' if name=='色彩分' else '%'))
    st.caption(result.get('summary') or '')
    st.image(_fig(result,direction),use_container_width=True)
    with st.expander('球面受光剖面图',expanded=False):
        try:st.image(render_profile(result),use_container_width=True)
        except Exception as ex:st.warning(str(ex))
    with st.expander('路径云量采样',expanded=False):
        rows=result.get('samples') or []
        cols=['distance_km','cloud_cover_low','cloud_cover_mid','cloud_cover_high','manual_cloud_override']
        st.dataframe([{col:r.get(col) for col in cols} for r in rows],use_container_width=True)

def _ref_inputs(image,w,h,auto_marks=None):
    st.caption(f'原始截图尺寸：{w}×{h}。地标和云区参考位置使用原始图片像素坐标；需要至少3个地标，推荐4–6个。')
    defaults=([{'城市':m['city'],'x':int(round(m['x'])),'y':int(round(m['y']))}
               for m in (auto_marks or [])] or
              [{'城市':'上海','x':None,'y':None},
               {'城市':'南京','x':None,'y':None},
               {'城市':'杭州','x':None,'y':None},
               {'城市':'合肥','x':None,'y':None}])
    marks=st.data_editor(pd.DataFrame(defaults),num_rows='dynamic',hide_index=True,
                         column_config={'城市':st.column_config.SelectboxColumn('城市',options=list(CITIES))},
                         key='adv_landmarks',use_container_width=True)
    landmarks=[]
    for _,row in marks.iterrows():
        name=str(row['城市'])
        if name in CITIES and pd.notna(row['x']) and pd.notna(row['y']):
            x,y=float(row['x']),float(row['y'])
            if 0<=x<w and 0<=y<h:landmarks.append((name,x,y))
    if len(landmarks)<3 or len({n for n,_,_ in landmarks})!=len(landmarks):
        st.warning('请核准至少3个不同地标的像素坐标；表格中的坐标只是填写示例，不是对当前截图的实际识别。')
        return [], None
    # Annotated image preview to help checking coordinates.
    annotated=image.copy();draw=ImageDraw.Draw(annotated)
    for name,x,y in landmarks:
        draw.ellipse((x-6,y-6,x+6,y+6),outline='yellow',width=4)
        draw.text((x+9,y-9),name,fill='yellow')
    st.image(annotated,caption='地标校准预览：黄圈应准确落在地图对应城市位置',use_container_width=True)
    try:
        _,quality=_fit_affine(landmarks)
        st.caption(f"配准RMS {quality['rms_px']} 像素 · 最大残差 {quality['max_px']} 像素")
        if len(landmarks)>=4 and quality['rms_px']>18:
            st.error('地标误差超过18像素，请调整坐标后再分析。')
            return [],quality
    except Exception as e:
        st.error('配准失败：'+str(e));return [],None
    return landmarks,quality

st.title('🛰️ 实况分析 · 进阶分析')
st.caption('独立于普通单次查询及自动/手动多模概览。基础单次与最新卫星修正各保存24小时，可覆盖重算；不会修改普通概览。')
with st.sidebar:
    st.page_link('app.py',label='返回 Burning Sky 单次分析',icon='🌇')
    st.header('进阶条件')
    place=st.text_input('地点','上海')
    from burning_sky.place_excel_paths import find_place_workbook
    try:
        df=pd.read_excel(find_place_workbook(),sheet_name='地名坐标表')
        if '城市名称' in df.columns:
            options=[str(x) for x in df['城市名称'].dropna().tolist() if place.strip() in str(x)][:20]
            if options:
                chosen=st.selectbox('地点库匹配',options)
                row=df[df['城市名称'].astype(str)==chosen].iloc[0]
                st.caption('已匹配地点；请检查下方坐标。')
                place=chosen
                chosen_lat=float(row.get('纬度',31.2304))
                chosen_lon=float(row.get('经度',121.4737))
                chosen_elev=float(row.get('海拔（m）',5.0))
            else:
                chosen_lat,chosen_lon,chosen_elev=31.2304,121.4737,5.0
        else:
            chosen_lat,chosen_lon,chosen_elev=31.2304,121.4737,5.0
    except Exception:
        chosen_lat,chosen_lon,chosen_elev=31.2304,121.4737,5.0
    lat=st.number_input('纬度',value=chosen_lat,format='%.5f')
    lon=st.number_input('经度',value=chosen_lon,format='%.5f')
    elev=st.number_input('海拔（米）',value=chosen_elev)
    day=st.date_input('预报日期',value=date.today())
    model=st.selectbox('模式',list(MODELS))
    direction_label=st.radio('方向',list(DIRS),horizontal=True)
    direction=DIRS[direction_label]
config={'place':place,'lat':lat,'lon':lon,'elev':elev,'date':day.isoformat(),'model':model,'direction':direction}
ident=_key(place,lat,lon,elev,day.isoformat(),model,direction)
base_key=ident+'_base'; corrected_key=ident+'_corrected'
partial_key=ident+'_supplement_cache'
base=_load(base_key); corrected=_load(corrected_key)
remaining=cooldown_remaining()
if remaining:
    st.warning(f'Open-Meteo 冷却中，约剩余 {remaining} 秒。已保存的预测仍可查看；期间不要重复点击重算。')
st.caption('卫星进阶查询使用专用慢速档：真实HTTP请求至少间隔8秒，最多每分钟7次。缓存命中不占额度；遇到429时暂停，保留成功补采。')

col1,col2,col3=st.columns([1,1,2])
run_new=col1.button('计算/覆盖基础单次',type='primary',use_container_width=True,disabled=remaining>0)
if col2.button('删除当前进阶缓存',use_container_width=True):
    for key in (base_key,corrected_key,partial_key):
        (CACHE/f'{key}.json').unlink(missing_ok=True)
    (CACHE/f'{corrected_key}.jpg').unlink(missing_ok=True)
    st.rerun()
with col3:
    if base:st.success('基础预测缓存可用：'+str(base.get('saved_at','')))
    else:st.info('当前条件暂无有效基础缓存；请先计算。')
if run_new:
    try:
        with st.spinner('正在查询单次单模式，可能访问 Open-Meteo…'):
            result,cache=_compute(config,direction)
        payload={'config':config,'result':result,'request_cache':cache,'saved_at':datetime.now().isoformat()}
        _write(base_key,payload)
        (CACHE/f'{corrected_key}.json').unlink(missing_ok=True)
        (CACHE/f'{partial_key}.json').unlink(missing_ok=True)
        st.rerun()
    except Exception as exc:st.error('单次计算失败：'+str(exc))

if base:
    display_mode=st.radio('查看版本',['基础单次','最新卫星修正'] if corrected else ['基础单次'],horizontal=True)
    current=corrected if display_mode=='最新卫星修正' and corrected else base
    _show(current['result'],direction,display_mode)
    if corrected:
        if st.button('恢复基础结果（清空本次进阶修正）'):
            (CACHE/f'{corrected_key}.json').unlink(missing_ok=True)
            (CACHE/f'{corrected_key}.jpg').unlink(missing_ok=True)
            st.rerun()
        st.caption(f"最近修正观测时间：{corrected.get('observation_time','—')} · 最近修正时间：{corrected.get('saved_at','—')}。覆盖当前进阶修正，不覆盖基础单次。")
        st.subheader('修正前后对比')
        comparison=[]
        for k,v in _summary(base['result']).items():
            comparison.append({'指标':k,'原始':v,'修正':_summary(corrected['result']).get(k)})
        st.dataframe(comparison,hide_index=True,use_container_width=True)

    st.divider()
    st.subheader('上传卫星截图 · 云洞差异校准')
    screenshot=st.file_uploader('卫星图（建议保留地图地名、红外色标及时间）',type=['png','jpg','jpeg','webp'],key='advanced_screenshot')
    obs=st.text_input('卫星观测时间（北京时间）',value=f'{day.isoformat()} 17:00' if direction=='evening' else f'{day.isoformat()} 06:00')
    if screenshot:
        try:image=Image.open(io.BytesIO(screenshot.getvalue())).convert('RGB')
        except Exception as exc:
            st.error('截图损坏：'+str(exc));st.stop()
        st.image(image,caption='上传的卫星截图（自动识别结果只作为辅助证据）',use_container_width=True)
        # Only once per uploaded bytes; no OCR calls on slider/button reruns.
        upload_sig=hashlib.sha256(screenshot.getvalue()).hexdigest()
        vision_key='adv_vision_'+ident
        if st.session_state.get(vision_key,{}).get('file_sha')!=upload_sig:
            with st.spinner('自动识别城市地标与卫星图云区纹理…'):
                geo=recognize_cities(image)
                appearances=auto_cloud_references(image)
            st.session_state[vision_key]={'file_sha':upload_sig,'geo':geo,'appearance':appearances}
            st.session_state.pop('adv_candidates_'+ident,None)
            st.session_state.pop('adv_multiselect_'+ident,None)
            st.session_state.pop('adv_high_edits_'+ident,None)
            st.session_state.pop('adv_confirm_vision_'+ident,None)
            # Image-dependent widget state is reset only when a NEW image is uploaded.
            st.session_state.pop('adv_landmarks',None)
            for tag in ('cold','band','warm'):
                st.session_state.pop(tag+'x',None)
                st.session_state.pop(tag+'y',None)
        vision=st.session_state[vision_key]
        geo=vision['geo']; appearances=vision['appearance']
        st.markdown('**自动图像识别结果**')
        if geo['landmarks']:
            st.info(geo['message'])
            st.caption('OCR定位到 '+', '.join(m['city'] for m in geo['landmarks'])+
                       '；地图文字中心可能偏离真实城市中心，请检查黄圈。')
            q=geo.get('quality')
            if q:
                st.caption(f"自动配准候选：{len(geo['landmarks'])}地标 · RMS {q['rms_px']}px · 最大残差 {q['max_px']}px。低残差仍不代表地名标注就是精确城市中心。")
        else:
            st.warning(geo['message'])
        if len(appearances)<3:
            st.warning('未能可靠找到三种不同的图像外观区域，请展开下方工具手动设置。')
        # Pixel classes are palette appearance only, never measured cloud fraction.
        st.caption('自动云区：彩色冷云顶、亮灰云带、偏暗暖背景。它们不是0%/55%/90%实测云量。')
        preview=image.copy();pen=ImageDraw.Draw(preview)
        for m in geo['landmarks']:
            x,y=m['x'],m['y']
            pen.ellipse((x-8,y-8,x+8,y+8),outline='#facc15',width=4)
            pen.text((x+10,y),m['city'],fill='#facc15')
        for tag,display,color in (('cold','冷云','#00e5ff'),('band','云带','#ffe070'),('warm','暖区','#ff8787')):
            if tag in appearances:
                x=appearances[tag]['x'];y=appearances[tag]['y']
                pen.ellipse((x-12,y-12,x+12,y+12),outline=color,width=5)
                pen.text((x+15,y),display,fill=color)
        st.image(preview,caption='识别位置预览：黄色是城市文字参考位置，彩色圈是云区外观参考位置',use_container_width=True)
        # The original editors remain available for overrides, but are folded on mobile.
        with st.expander('人工核对 / 修改自动识别位置（识别错误时展开）', expanded=not bool(geo['landmarks'])):
            landmarks,quality=_ref_inputs(image,*image.size,auto_marks=geo['landmarks'])
            references={}
            cc=st.columns(3)
            for col,(tag,label) in zip(cc,[('cold','冷云顶厚云'),('band','灰白云带'),('warm','暖背景少云候选')]):
                with col:
                    seed=appearances.get(tag) or {'x':image.width//2,'y':image.height//2}
                    x=st.number_input(label+' x',min_value=0,max_value=image.width-1,value=int(seed['x']),key=tag+'x')
                    y=st.number_input(label+' y',min_value=0,max_value=image.height-1,value=int(seed['y']),key=tag+'y')
                    references[tag]=(x,y)
        # Never enable modification with <4 landmarks merely because three
        # affine points fit exactly (without an independent residual check).
        if landmarks and len(landmarks)<4:
            st.warning('自动识别地标不足4处：先补充或核对地标，再允许应用修正。')
        eligible=(len(landmarks)>=4 and quality is not None and quality['rms_px']<=18
                  and all(tag in references for tag in ('cold','band','warm')))
        if not eligible:
            st.caption('自动校准尚未通过校验；可以手动补充地标和三类参考区域。')
        confirmed=st.checkbox('我已核对城市地标位置与三类参考点（确认后才能分析差异）',key='adv_confirm_vision_'+ident)
        eligible=eligible and confirmed
        if st.button('分析截图与EC/GFS差异',disabled=not eligible,use_container_width=True):
            try:
                dt=datetime.strptime(obs,'%Y-%m-%d %H:%M')
                event=base['result'].get('event_dt')
                if event:
                    ev=datetime.fromisoformat(str(event).replace('Z','+00:00')).replace(tzinfo=None)
                    delta=abs((dt-ev).total_seconds())/60
                    if delta>120:st.warning(f'卫星图与预测事件相差约{delta:.0f}分钟；云系移动可能使校准失效。')
                rgb={name:_avg_patch(image,*xy,r=5) for name,xy in references.items()}
                if any(v is None for v in rgb.values()):raise ValueError('云区参考点过于靠近图像边界')
                rows=compare_samples(image,landmarks,rgb['cold'],rgb['warm'],base['result'],band_rgb=rgb['band'])
                st.session_state['adv_candidates_'+ident]=rows
                # Each new screenshot analysis starts with a clean selection. Old
                # distance selections must not silently carry into a new image.
                st.session_state.pop('adv_multiselect_'+ident, None)
                st.session_state.pop('adv_high_edits_'+ident, None)
                st.session_state['adv_screenshot_meta_'+ident]={'observation_time':obs,'quality':quality}
            except Exception as exc:st.error('截图差异分析失败：'+str(exc))
        candidates=st.session_state.get('adv_candidates_'+ident) or []
        if candidates:
            diffs=[r for r in candidates if '疑似' in r.get('judgement','') and r.get('suggested_high') is not None and r.get('confidence',0)>=.62]
            st.caption(f'共检查{len(candidates)}个路径点，发现{len(diffs)}个可审核候选。截图云色不是实测云量，仅提供建议修正。')
            if diffs:
                options={round(r['distance_km'],2):r for r in diffs}
                select_key='adv_multiselect_'+ident
                st.markdown('**批量审核卫星差异**')
                all_col,none_col=st.columns(2)
                if all_col.button(f'✅ 一键全选（{len(options)}项）',key='adv_select_all',
                                  use_container_width=True):
                    st.session_state[select_key]=list(options)
                    # Clear stale editor state when the selected rows change.
                    st.session_state.pop('adv_high_edits_'+ident,None)
                    st.rerun()
                if none_col.button('取消全部选择',key='adv_select_none',
                                   use_container_width=True):
                    st.session_state[select_key]=[]
                    st.session_state.pop('adv_high_edits_'+ident,None)
                    st.rerun()
                existing=st.session_state.get(select_key,[])
                st.session_state[select_key]=[k for k in existing if k in options]
                chosen=st.multiselect('选择要修正的路径距离（km）',list(options),
                    key=select_key)
                st.caption(f'已选择 {len(chosen)} / {len(options)} 项。全选仅勾选候选，不会自动写入修正；仍需下方确认。')
                chosen_rows=[options[k] for k in chosen]
                edits_df=pd.DataFrame([{'distance_km':r['distance_km'],
                           '建议高云%':r['suggested_high'],'采用高云%':r['suggested_high'],
                           '置信度':r['confidence'],'类型':r['judgement']} for r in chosen_rows])
                if not edits_df.empty:
                    edited=st.data_editor(edits_df,hide_index=True,use_container_width=True,key='adv_high_edits_'+ident,
                             disabled=['distance_km','建议高云%','置信度','类型'])
                else:edited=edits_df
                if st.button('确认修正并更新完整单次结果',type='primary',disabled=edited.empty or remaining>0):
                    try:
                        segments=[];extra=[]
                        # Non-overlapping 9.6-km intervals around distinct 10-km candidate positions.
                        accepted=[]
                        for _,r in edited.iterrows():
                            d=float(r['distance_km']);value=float(r['采用高云%'])
                            if not 0<=value<=100:raise ValueError('高云量需在0~100%')
                            accepted.append((d,value))
                        accepted.sort()
                        for i,(d,v) in enumerate(accepted):
                            gap=min([abs(d-other) for j,(other,_) in enumerate(accepted) if i!=j] or [10])
                            half=min(4.8,max(0.25,gap*0.45))
                            segments.append((max(0,d-half),d+half,None,None,v))
                            extra.extend([max(0,d-4),d,d+4])
                        # Keep previous confirmed regions unless a new revision
                        # overlaps them. The latest correction overwrites its area.
                        prior_edits=(corrected or {}).get('edits') or []
                        for old in prior_edits:
                            a,b,*_ = old
                            if not any(max(a,new[0])<min(b,new[1]) for new in segments):
                                segments.append(tuple(old))
                        segments.sort(key=lambda row:row[0])
                        for i in range(1,len(segments)):
                            if segments[i][0]<segments[i-1][1]:
                                raise ValueError('新旧校准区域存在重叠，请先清空进阶修正后重新选择。')
                        # Save newly downloaded request batches even if a later
                        # request hits HTTP 429. Never promote a partial result.
                        attempt_cache=copy.deepcopy(base.get('request_cache') or {})
                        if corrected:
                            attempt_cache.update(corrected.get('request_cache') or {})
                        checkpoint=_load(partial_key)
                        if checkpoint and checkpoint.get('config')==config:
                            attempt_cache.update(checkpoint.get('request_cache') or {})
                        try:
                            with st.spinner('慢速补采中：每次真实HTTP请求至少间隔8秒；失败会保存已完成数据…'):
                                new_result,updated_cache=_compute(config,direction,cache=attempt_cache,
                                        overrides=segments,extra=sorted(set(round(x,2) for x in extra))[:60],
                                        saved_rainbow=base['result'].get('rainbow'))
                        except Exception:
                            if attempt_cache:
                                _write(partial_key,{'config':config,'request_cache':attempt_cache,
                                                    'saved_at':datetime.now().isoformat()})
                            raise
                        (CACHE/f'{partial_key}.json').unlink(missing_ok=True)
                        _write(corrected_key,{'config':config,'result':new_result,'request_cache':updated_cache,
                            'edits':segments,'observation_time':obs,'saved_at':datetime.now().isoformat(),
                            'registration_quality':quality,'satellite_screenshot':f'{corrected_key}.jpg'})
                        # Keep the most recently confirmed satellite frame, and
                        # atomically overwrite earlier frames for this identity.
                        _prune()
                        tmp_image=CACHE/f'{corrected_key}.temp.jpg'
                        image.save(tmp_image,'JPEG',quality=83,optimize=True)
                        os.replace(tmp_image,CACHE/f'{corrected_key}.jpg')
                        st.success('卫星修正已完整重算，保留先前不重叠的修正区，并覆盖最新截图。')
                        st.rerun()
                    except Exception as exc:
                        st.error('修正重算失败，基础数据和先前修正保持不变：'+str(exc))
            with st.expander('全部截图分析记录',expanded=False):
                st.dataframe(candidates,use_container_width=True)

    st.divider()
    st.subheader('360°截图云场概览（不增加Open-Meteo请求）')
    st.caption('使用截图的二维纹理和已校准地图推断周边方位的云区证据；不代替16目标64上游点的球面受光实验。')
    if screenshot and st.session_state.get('adv_candidates_'+ident) and landmarks:
        try:
            from burning_sky.satellite_calibration import _fit_affine
            trans,_=_fit_affine(landmarks)
            rgbs={name:_avg_patch(image,*xy,r=5) for name,xy in references.items()}
            exams=[('厚云候选',rgbs['cold'],90),('云带候选',rgbs['band'],55),('少云候选',rgbs['warm'],10)]
            size=600;rad=235;cx=cy=300
            chart=Image.new('RGB',(size,size),'#f7f9fc');pen=ImageDraw.Draw(chart)
            headings=list(range(0,360,45))
            def pt(az,r):return cx+math.sin(math.radians(az))*r,cy-math.cos(math.radians(az))*r
            for frac in (.25,.5,.75,1):
                pen.polygon([pt(a,rad*frac) for a in headings],outline='#b9c9d7')
            data=[]
            for az in headings:
                cloud=[]
                for km in (60,150):
                    loc=destination(lat,lon,az,km);x,y=trans(*loc)
                    e=_patch_evidence(image,x,y,exams)
                    if e and e['confidence']>=.55:cloud.append({'厚云候选':85,'云带候选':50,'少云候选':15}.get(e['classification'],50))
                data.append(sum(cloud)/len(cloud) if cloud else None)
            for i,a in enumerate(headings):
                b=headings[(i+1)%8];v=data[i];w=data[(i+1)%8]
                if v is not None and w is not None:
                    p1=pt(a,rad*v/100);p2=pt(b,rad*w/100)
                    pen.polygon([(cx,cy),p1,p2],fill='#bdd1e8',outline='#5f82ab')
                x,y=pt(a,rad+25)
                pen.text((x-13,y-7),['北','东北','东','东南','南','西南','西','西北'][i],fill='#24344a')
                if v is not None:
                    x,y=pt(a,rad*v/100)
                    pen.ellipse((x-4,y-4,x+4,y+4),fill='#4468a7')
            st.image(chart,width=550)
            st.caption('半径表示示意的云覆盖视觉等级，不是云量实测值；未覆盖方向留空。')
        except Exception as exc:st.warning('截图360°展示失败：'+str(exc))
    else:
        st.info('完成截图校准和差异分析后，可显示截图覆盖范围内的八方向云场证据。')
else:
    st.info('请先运行当前地点/日期/模式/方向的基础单次分析。')
st.caption('缓存有效期：从最近一次保存起24小时，重算可覆盖。Streamlit Cloud本地磁盘不保证重启或重新部署后仍存在；持久化需外部存储。')
