# -*- coding: utf-8 -*-
"""Burning Sky Lite: independent Streamlit entry, shared forecast engine.

No weather query is triggered by the five-day overview. Only one of morning or
night is calculated per form submission. Screenshot calibration is automatic
and fail-closed; the screenshot itself is not rendered back to the user.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys

# Set before engine import (the engine has an optional Tk desktop interface).
os.environ['BURNING_SKY_HEADLESS'] = '1'
os.environ['BURNING_SKY_REQUEST_PROFILE'] = 'manual'
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

import pandas as pd
import streamlit as st
from burning_sky.place_excel_paths import find_place_workbook
from burning_sky.lite_forecast import run_single, verdict, source_time, event_time, clouds, number
from burning_sky.lite_satellite import calibrate, MAX_UPLOAD_BYTES
from burning_sky.web_color_curve import draw_dynamic_color_curve
from burning_sky.warm_web_theme import apply_warm_theme, render_hero
from burning_sky.advanced_bridge import identity as advanced_identity, read as archive_read, write as archive_write
from lite.overview_data import get_overview, china_today
from lite.bar_cards import CSS as CHART_CSS, render as render_bars

st.set_page_config(page_title='Burning Skies Lite', page_icon='🌅', layout='wide', initial_sidebar_state='collapsed')

# Optional independent password; Lite does not read Pro credentials.
try:
    _lite_password = str(st.secrets.get('LITE_APP_PASSWORD',''))
except (OSError,RuntimeError,KeyError):
    _lite_password = ''
_lite_password = _lite_password or os.environ.get('LITE_APP_PASSWORD','')
if _lite_password and not st.session_state.get('lite_authenticated'):
    st.title('Burning Skies Lite')
    entered=st.text_input('访问密码', type='password')
    if st.button('进入'):
        import hmac
        if hmac.compare_digest(entered,_lite_password):
            st.session_state['lite_authenticated']=True
            st.rerun()
        else:
            st.error('密码不正确')
    st.stop()

# Configure GitHub read-only source in the same way as the Pro app.
try:
    _repo = st.secrets.get('BURNING_SKY_GITHUB_REPOSITORY')
    if _repo and 'BURNING_SKY_GITHUB_REPOSITORY' not in os.environ:
        os.environ['BURNING_SKY_GITHUB_REPOSITORY']=str(_repo)
    _branch = st.secrets.get('BURNING_SKY_GITHUB_BRANCH')
    if _branch and 'BURNING_SKY_GITHUB_BRANCH' not in os.environ:
        os.environ['BURNING_SKY_GITHUB_BRANCH']=str(_branch)
except (OSError,RuntimeError,KeyError):
    pass

# Shared engine is imported, not copied. The Pro UI is never imported.
import engine

apply_warm_theme()
st.markdown(CHART_CSS,unsafe_allow_html=True)
st.markdown('''<style>
/* ===== Burning Sky Lite · refined glass surface (independent of Pro theme) ===== */
[data-testid="stSidebar"]{display:none}

:root{
  --lite-ink:#F4F6FB; --lite-dim:rgba(244,246,251,.72); --lite-faint:rgba(244,246,251,.46);
  --lite-line:rgba(255,255,255,.12); --lite-hairline:rgba(255,255,255,.08);
  --lite-card:rgba(255,255,255,.07); --lite-card-strong:rgba(255,255,255,.10);
}

/* Page rhythm */
[data-testid="stMainBlockContainer"]{padding-top:1.6rem;padding-bottom:3.5rem}
.block-container{gap:.9rem}

/* Typography: calm Apple-style hierarchy, no heavy display weights */
h1,h2,h3,h4{letter-spacing:-.015em}
h3{font-weight:660 !important;font-size:1.18rem !important}
h4{font-weight:620 !important;font-size:1.02rem !important}
.stMarkdown p, .stMarkdown li{color:var(--lite-dim) !important;font-weight:450}
[data-testid="stCaptionContainer"], .stCaption{color:var(--lite-faint) !important;font-size:.78rem;letter-spacing:.01em}
hr{border-color:var(--lite-hairline) !important;margin:1.4rem 0}

/* Verdict lead card: frosted glass over the dusk sky, not an opaque white tile */
.bs-lite-lead{
  background:var(--lite-card-strong);
  border:1px solid var(--lite-line);
  border-radius:22px;
  padding:18px 22px;
  color:var(--lite-ink);
  font-size:1.16rem;
  font-weight:620;
  letter-spacing:-.01em;
  line-height:1.5;
  backdrop-filter:blur(22px) saturate(150%);
  -webkit-backdrop-filter:blur(22px) saturate(150%);
  box-shadow:0 14px 34px rgba(8,10,20,.22);
  margin:14px 0 20px;
}
.bs-lite-version{color:var(--lite-faint);font-size:12px;margin:8px 0}

/* Hero caption: soften the heavy title weight against the photo */
.bs-hero{border-radius:24px;border:1px solid var(--lite-line);box-shadow:0 18px 46px rgba(8,10,20,.30)}
.bs-hero-cap .t{font-size:20px !important;font-weight:680 !important;letter-spacing:-.015em;text-shadow:0 2px 14px rgba(8,10,20,.45)}
.bs-hero-cap .s{font-size:12.5px !important;color:var(--lite-dim) !important;text-shadow:0 1px 8px rgba(8,10,20,.4)}

/* Metric cards: unified glass, airy padding, refined numerals */
[data-testid="stMetric"]{
  background:var(--lite-card);
  border:1px solid var(--lite-line);
  border-radius:18px;
  padding:14px 16px;
  backdrop-filter:blur(18px) saturate(150%);
  -webkit-backdrop-filter:blur(18px) saturate(150%);
  box-shadow:0 8px 22px rgba(8,10,20,.16);
}
[data-testid="stMetricLabel"]{color:var(--lite-faint) !important;font-size:.78rem;font-weight:500;letter-spacing:.02em}
[data-testid="stMetricValue"]{color:var(--lite-ink) !important;font-weight:650 !important;font-size:1.62rem !important;letter-spacing:-.02em}

/* Segmented navigation pill */
.stButtonGroup, [data-testid="stSegmentedControl"]{border-radius:14px}
.stButtonGroup button[role="tab"], .stButtonGroup button[role="radio"]{
  background:rgba(255,255,255,.06) !important;
  border:1px solid var(--lite-line) !important;
  color:var(--lite-dim) !important;
  border-radius:11px !important;
  font-weight:550 !important;
}
.stButtonGroup button[aria-checked="true"]{
  background:rgba(255,255,255,.15) !important;
  border-color:var(--bs-accent) !important;
  color:var(--lite-ink) !important;
}

/* Form controls: soft glass surfaces, hairline borders */
[data-testid="stSelectbox"] > div, [data-testid="stDateInput"] > div,
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
[data-baseweb="select"] > div, [data-baseweb="datepicker"] > div{
  background-color:rgba(255,255,255,.08) !important;
  border-color:var(--lite-line) !important;
  border-radius:12px !important;
}
[data-testid="stSelectbox"] > div:focus-within, [data-testid="stDateInput"] > div:focus-within,
[data-testid="stTextInput"] input:focus, [data-testid="stNumberInput"] input:focus{
  border-color:var(--bs-accent) !important;
  box-shadow:0 0 0 3px rgba(255,255,255,.08) !important;
}

/* Buttons */
.stButton > button, [data-testid="stFormSubmitButton"] > button, [data-testid="stBaseButton-secondary"]{
  border-radius:13px !important;
  border:1px solid var(--lite-line) !important;
  background:rgba(255,255,255,.09) !important;
  color:var(--lite-ink) !important;
  font-weight:560 !important;
  letter-spacing:.01em;
  backdrop-filter:blur(12px);
  transition:background .16s ease,border-color .16s ease,box-shadow .16s ease;
}
.stButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover{
  background:rgba(255,255,255,.15) !important;
  border-color:var(--bs-accent) !important;
}
button[kind="primary"], [data-testid="stBaseButton-primary"]{
  border-radius:14px !important;
  font-weight:600 !important;
  box-shadow:0 8px 22px rgba(8,10,20,.26);
}

/* File uploader drop zone */
[data-testid="stFileUploader"] section{
  background:var(--lite-card) !important;
  border:1px dashed var(--lite-line) !important;
  border-radius:16px !important;
}
[data-testid="stFileUploader"] section:hover{border-color:var(--bs-accent) !important}

/* Expander / panels */
[data-testid="stExpander"]{
  background:var(--lite-card) !important;
  border:1px solid var(--lite-line) !important;
  border-radius:18px !important;
  backdrop-filter:blur(20px) saturate(150%);
  -webkit-backdrop-filter:blur(20px) saturate(150%);
}
[data-testid="stExpander"] details summary{font-weight:600;color:var(--lite-ink)}

/* Data table */
[data-testid="stDataFrame"], [data-testid="stTable"]{
  border:1px solid var(--lite-line) !important;
  border-radius:16px !important;
  overflow:hidden;
}

/* Alerts */
[data-testid="stAlert"], .stAlertContainer{
  background:var(--lite-card) !important;
  border:1px solid var(--lite-line) !important;
  border-radius:15px !important;
}

@media(max-width:550px){
  .bs-lite-lead{font-size:1rem;padding:14px 16px;border-radius:18px}
  [data-testid="stMetric"]{padding:11px 12px}
  [data-testid="stMetricValue"]{font-size:1.38rem !important}
  .bs-hero-cap .t{font-size:17px !important}
}
</style>''',unsafe_allow_html=True)

@st.cache_data(show_spinner=False)
def _places():
    file=Path(find_place_workbook())
    if not file.is_file():
        file=ROOT/'lite'/'cities_fallback.xlsx'
    frame=pd.read_excel(file,sheet_name='地名坐标表')
    expected={'城市名称','纬度','经度','海拔（m）'}
    if not expected.issubset(frame.columns):
        raise ValueError('地名坐标表字段不完整')
    frame=frame.dropna(subset=['城市名称','纬度','经度','海拔（m）']).copy()
    frame['城市名称']=frame['城市名称'].astype(str).str.strip()
    frame=frame[frame['城市名称'].ne('')].drop_duplicates('城市名称',keep='first')
    return {str(row['城市名称']):(float(row['纬度']),float(row['经度']),float(row['海拔（m）'])) for _,row in frame.iterrows()}


@st.cache_data(ttl=75,show_spinner=False)
def _published(city):
    return get_overview(city)


def _display_metric(name,value,suffix=''):
    return '—' if number(value) is None else f'{number(value):.0f}{suffix}'


def _triad(result):
    inf=result.get('inferred') or {}
    return (inf.get('regular_score'),inf.get('color_score'),inf.get('burn_scale_index'))


def _visual_result(result, fetched_at=None, direction='evening'):
    inferred=result.get('inferred') or {}
    st.markdown('<div class="bs-lite-lead">'+__import__('html').escape(verdict(result))+'</div>',unsafe_allow_html=True)
    c1,c2,c3=st.columns(3)
    vals=_triad(result)
    for col,name,val,suffix in zip((c1,c2,c3),('常规概率','色彩分','火烧云规模'),vals,('%','/100','%')):
        col.metric(name,_display_metric(name,val,suffix))
    st.divider()
    d1,d2=st.columns(2)
    with d1:
        st.caption('模型数据时间')
        st.markdown('**'+__import__('html').escape(source_time(result,fetched_at))+'**')
        st.caption('事件时间')
        st.markdown('**'+__import__('html').escape(event_time(result))+'**')
    with d2:
        st.caption('云量 · 低 / 中 / 高')
        lo,mid,hi=clouds(result)
        st.markdown('**'+' / '.join(_display_metric('',x,'%') for x in (lo,mid,hi))+'**')
        st.caption('上限概率')
        st.markdown('**'+_display_metric('',inferred.get('upper_score'),'%')+'**')
    st.markdown('#### 云色时序')
    curve=result.get('dynamic_light_curve') or {}
    # The Lite variant keeps the real per-minute geometry, color and time axis,
    # but removes the long scientific legend below the original Pro figure.
    timeline=draw_dynamic_color_curve(curve.get('samples') or [],curve.get('peak'),direction)
    st.image(timeline.crop((0,0,timeline.width,429)),use_container_width=True)


def _version_table(before,after):
    st.markdown('#### 校准前后')
    c1,c2=st.columns(2)
    for col,label,result in ((c1,'校准前',before),(c2,'校准后',after)):
        a,b,c=_triad(result)
        with col:
            st.markdown('**'+label+'**')
            st.caption('常规概率 · 色彩分 · 规模')
            st.markdown(f'**{_display_metric("",a,"%")}　/　{_display_metric("",b)}　/　{_display_metric("",c,"%") }**')


def _calibration_id(state, engine_module):
    meta=state['meta']
    config={'place':meta['place'],'lat':meta['lat'],'lon':meta['lon'],'elev':meta['elev'],
            'date':meta['date'],'model':'GFS' if 'GFS' in meta['model_label'] else 'EC',
            'direction':meta['direction']}
    baseline=engine_module.FireCloudApp._json_safe(state['result'])
    fingerprint=hashlib.sha256(json.dumps(baseline,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()[:16]
    return advanced_identity(config)+'_'+fingerprint

render_hero('Burning Skies Lite','朝霞 · 晚霞')
nav=st.segmented_control('查看', ['未来五日','单次预测'],default='未来五日',key='lite_nav',label_visibility='collapsed')

if nav=='未来五日':
    # Read only completed public overview files. Never dispatch GitHub Actions.
    city_list=['合肥']
    _cities=ROOT/'overview_cities.txt'
    if _cities.is_file():
        extra=[s.split(',')[0].strip() for s in _cities.read_text('utf-8-sig').splitlines()
               if s.strip() and not s.lstrip().startswith('#')]
        city_list=list(dict.fromkeys([x for x in extra if x])) or city_list
    city=st.selectbox('地点',city_list,key='lite_overview_city') if len(city_list)>1 else city_list[0]
    with st.spinner('读取五日概览…'):
        overview=_published(city)
    if not overview['has_data']:
        st.info('当前尚无可显示的五日预报。')
    else:
        for direction,label in (('morning','朝霞'),('evening','晚霞')):
            chart=render_bars(overview['days'],direction,label)
            if hasattr(st,'html'):
                st.html(chart)
            else:
                st.markdown(chart,unsafe_allow_html=True)
        # Entire overview is read-only, and its detailed table is optional.
        with st.expander('查看全部数据',expanded=False):
            rows=[]
            for day in overview['days']:
                for direction,name in (('morning','朝霞'),('evening','晚霞')):
                    for model,short in (('ECMWF IFS 0.25°','EC'),('GFS Global','GFS')):
                        record=(day.get(direction) or {}).get(model) or {}
                        rows.append({'日期':day['date'],'时段':name,'模式':short,
                            '常规概率':_display_metric('',record.get('probability'),'%'),
                            '色彩分':_display_metric('',record.get('color_score')),
                            '火烧云规模':_display_metric('',record.get('burn_scale'),'%')})
            st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
        if overview.get('updated_at'):
            st.caption('更新：'+str(overview['updated_at']))

else:
    try:
        cities=_places()
    except (OSError, ValueError, KeyError) as ex:
        st.error('地点库读取失败：'+str(ex))
        st.stop()
    if not cities:
        st.error('当前地点库为空')
        st.stop()
    names=sorted(cities.keys(),key=lambda x:(x!='合肥',x))
    with st.form('lite_query_form'):
        a,b,c=st.columns([2,2,2])
        with a:
            place=st.selectbox('地点',names,index=names.index('合肥') if '合肥' in names else 0)
        with b:
            day=st.date_input('日期',china_today(),min_value=china_today(),
                              max_value=china_today()+__import__('datetime').timedelta(days=15))
        with c:
            model=st.selectbox('模式',['ECMWF IFS 0.25°','GFS Global'])
        left,right=st.columns(2)
        morning=left.form_submit_button('查询朝霞',use_container_width=True)
        evening=right.form_submit_button('查询晚霞',use_container_width=True)

    if morning or evening:
        lat,lon,elevation=cities[place]
        direction='morning' if morning else 'evening'
        st.session_state.pop('lite_current',None)
        st.session_state.pop('lite_version',None)
        st.session_state.pop('lite_next_version',None)
        for _old_key in list(st.session_state):
            if str(_old_key).startswith('lite_sat_upload_'):
                st.session_state.pop(_old_key,None)
        with st.spinner('正在计算'+('朝霞' if morning else '晚霞')+'…'):
            try:
                state=run_single(engine,place=place,lat=lat,lon=lon,elevation=elevation,
                                 day=day.isoformat(),model_label=model,direction=direction)
                state['calibration']=None
                st.session_state['lite_current']=state
            except Exception as ex:
                st.error('查询失败：'+str(ex))

    state=st.session_state.get('lite_current')
    if state:
        meta=state['meta']
        original=state['result']
        corrected=(state.get('calibration') or {}).get('result') if (state.get('calibration') or {}).get('changed') else None
        st.markdown(f"**{meta['place']} · {meta['date']} · {'朝霞' if meta['direction']=='morning' else '晚霞'} · {meta['model_label']}**")
        if corrected:
            _version_table(original,corrected)
            # Streamlit forbids modifying a widget's session_state AFTER that
            # widget was instantiated in this very rerun. Apply the result of
            # the previous calibration BEFORE rendering segmented_control.
            if st.session_state.pop('lite_next_version',None)=='校准后':
                st.session_state['lite_version']='校准后'
            if 'lite_version' in st.session_state:
                selected=st.segmented_control('结果版本',['原始预测','校准后'],key='lite_version')
            else:
                selected=st.segmented_control('结果版本',['原始预测','校准后'],default='校准后',key='lite_version')
            displayed=corrected if selected=='校准后' else original
        else:
            displayed=original
        _visual_result(displayed,state.get('fetched_at'),meta['direction'])
        st.divider()
        st.markdown('#### 卫星校准')
        uploaded=st.file_uploader('上传对应时刻的卫星云图截图',type=['png','jpg','jpeg','webp'],
                                  key='lite_sat_upload_'+_calibration_id(state,engine))
        run_calibration=st.button('一键校准',type='primary',disabled=uploaded is None,
                                  use_container_width=True,key='lite_sat_go')
        if run_calibration and uploaded:
            raw=uploaded.getvalue()
            if len(raw)>MAX_UPLOAD_BYTES:
                st.error('图片不能超过12MB')
            else:
                uploaded_sig=hashlib.sha256(raw).hexdigest()
                calibration_id=_calibration_id(state,engine)
                if state.get('calibration_signature')==uploaded_sig and state.get('calibration'):
                    st.info('当前截图已经校准，无需重复计算。')
                else:
                    checkpoint_key=calibration_id+'_lite_supplement'
                    previous=archive_read(checkpoint_key) or {}
                    recover=(previous.get('request_cache') or {}) if previous.get('screenshot_sha')==uploaded_sig else {}
                    with st.spinner('正在自动识别并校准…'):
                        try:
                            outcome=calibrate(original,state.get('cache') or {},meta,raw,recover_cache=recover)
                            state['calibration']=outcome
                            state['calibration_signature']=uploaded_sig
                            st.session_state['lite_current']=state
                            st.session_state['lite_next_version']='校准后' if outcome['changed'] else '原始预测'
                            archive_write(checkpoint_key,{'screenshot_sha':uploaded_sig,'request_cache':outcome.get('cache') or {}})
                            st.rerun()
                        except Exception as ex:
                            partial=getattr(ex,'partial_cache',None)
                            if partial:
                                archive_write(checkpoint_key,{'screenshot_sha':uploaded_sig,'request_cache':engine.FireCloudApp._json_safe(partial)})
                            st.error('未能完成校准：'+str(ex))
        if state.get('calibration'):
            st.caption(state['calibration'].get('note') or '')
