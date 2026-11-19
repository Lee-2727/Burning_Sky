# -*- coding: utf-8 -*-
"""Burning Sky Lite: independent Streamlit entry, shared forecast engine.

No weather query is triggered by the three-day overview. Only one of morning or
night is calculated per form submission. Screenshot calibration is automatic
and fail-closed; the screenshot itself is not rendered back to the user.
"""
from __future__ import annotations
import hashlib
import json
import os
import io
import zipfile
from pathlib import Path
from datetime import timedelta
import sys

# Set before engine import (the engine has an optional Tk desktop interface).
os.environ['BURNING_SKY_HEADLESS'] = '1'
os.environ['BURNING_SKY_REQUEST_PROFILE'] = 'interactive'  # Lite single-mode queries use the interactive budget; published overview is read-only
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

import pandas as pd
import streamlit as st
from burning_sky.place_excel_paths import find_place_workbook
from burning_sky.lite_forecast import run_single, verdict, source_time, event_time, clouds, number
from burning_sky.lite_satellite import calibrate_auto, MAX_UPLOAD_BYTES
from burning_sky.warm_web_theme import apply_warm_theme, render_hero
from burning_sky.advanced_bridge import identity as advanced_identity, read as archive_read, write as archive_write
from burning_sky.interactive_result_cache import load as load_interactive_cache, save as save_interactive_cache
from burning_sky.interactive_result_cache import prune_previous_days as _prune_interactive_cache
from burning_sky.advanced_bridge import prune_daily_cache as _prune_satellite_cache
_prune_interactive_cache()
_prune_satellite_cache()
from lite.overview_data import get_overview, china_today
from lite.bar_cards import CSS as CHART_CSS, render as render_bars

# Visible build marker: if the hosted Lite still shows five dates, it is running old code.
LITE_OVERVIEW_UI_BUILD = '3D-GEOMETRIC-VISUAL-20261119-R4'
LITE_SINGLE_ENGINE_BUILD = 'SHARED-ENGINE-CLOUD-BOTTOM-EVENT-GEOMETRIC-VISUAL-ANGLE-DAILY-CACHE-20261120-R5-NO-CONTINUOUS-CLOUD'

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

/* One-line model verdict: reuse the Lite frosted-glass lead-card language,
   but keep it compact so it reads as the conclusion beneath each model. */
.bs-lite-model-summary{
  min-width:0;margin:10px 0 4px;padding:13px 16px;
  background:var(--lite-card-strong);border:1px solid var(--lite-line);
  border-radius:18px;color:var(--lite-ink);overflow-wrap:anywhere;
  backdrop-filter:blur(20px) saturate(150%);
  -webkit-backdrop-filter:blur(20px) saturate(150%);
  box-shadow:0 10px 26px rgba(8,10,20,.18);
}
.bs-lite-model-summary .label{
  font-size:13px;font-weight:680;letter-spacing:.02em;
  color:var(--lite-faint);margin-bottom:6px;
}
.bs-lite-model-summary .text{
  font-size:14px;line-height:1.5;font-weight:600;color:var(--lite-ink);
}

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



def _read_offline_case_zip(blob):
    """Read a saved case ZIP in memory; never extract files or contact APIs."""
    if not isinstance(blob, (bytes, bytearray)) or len(blob) > 100 * 1024 * 1024:
        raise ValueError('案例 ZIP 无效或超过 100 MB')
    required = {'analysis_state.json', 'request_cache.json', 'case_manifest.json'}
    try:
        with zipfile.ZipFile(io.BytesIO(blob), 'r') as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError('ZIP 完整性检查失败：' + bad)
            names = set(archive.namelist())
            if not required.issubset(names):
                raise ValueError('案例 ZIP 缺少：' + '、'.join(sorted(required - names)))
            manifest = json.loads(archive.read('case_manifest.json').decode('utf-8'))
            analysis = json.loads(archive.read('analysis_state.json').decode('utf-8'))
            cache = json.loads(archive.read('request_cache.json').decode('utf-8'))
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('无法读取案例 ZIP：' + str(exc)) from exc
    if not isinstance(cache, dict) or not cache:
        raise ValueError('案例中的 request_cache.json 为空或格式错误')
    hashes = manifest.get('file_sha256') or {}
    for name in ('analysis_state.json', 'request_cache.json'):
        expected = hashes.get(name)
        if expected:
            with zipfile.ZipFile(io.BytesIO(blob), 'r') as archive:
                actual = hashlib.sha256(archive.read(name)).hexdigest()
            if actual != expected:
                raise ValueError(name + ' SHA256 校验失败，文件可能损坏')
    source = manifest.get('source_case') or {}
    params = ((manifest.get('effective_parameters') or {}).get('request_inputs') or {})
    place = str(source.get('place') or analysis.get('place') or params.get('place') or '').strip()
    day = str(source.get('date') or analysis.get('date') or params.get('date') or '').strip()
    model_code = str(source.get('model_code') or analysis.get('model_code') or '').upper()
    model_label = 'GFS Global' if model_code == 'GFS' else ('ECMWF IFS 0.25°' if model_code == 'EC' else '')
    lat = source.get('latitude', analysis.get('latitude', params.get('lat')))
    lon = source.get('longitude', analysis.get('longitude', params.get('lon')))
    elevation = source.get('elevation_m', analysis.get('elevation_m', params.get('elevation', 0)))
    directions = analysis.get('direction_results') or {}
    valid_directions = [d for d in ('morning', 'evening') if isinstance(directions.get(d), dict)]
    if not place or not day or not model_label or lat is None or lon is None or not valid_directions:
        raise ValueError('案例元数据不完整：需要地点、日期、EC/GFS 模式、坐标和方向结果')
    return {'manifest': manifest, 'analysis': analysis, 'cache': cache, 'params': params,
            'place': place, 'day': day, 'model_code': model_code, 'model_label': model_label,
            'lat': float(lat), 'lon': float(lon), 'elevation': float(elevation or 0),
            'directions': valid_directions}


def _offline_metric_row(label, inferred):
    def _fmt(key):
        value = inferred.get(key)
        try:
            return f'{float(value):.2f}'
        except (TypeError, ValueError):
            return '—'
    return {'来源': label, '常规概率': _fmt('regular_score'),
            '色彩分': _fmt('color_score'),
            '几何规模': _fmt('geometric_burn_scale_index'),
            '视觉规模': _fmt('visual_burn_scale_index'),
            '几何可染色占比': _fmt('geometric_tintable_ratio'),
            '视觉可染色占比': _fmt('visual_tintable_ratio')}


def _display_metric(name,value,suffix=''):
    return '—' if number(value) is None else f'{number(value):.0f}{suffix}'


def _triad(result):
    inf=result.get('inferred') or {}
    return (inf.get('regular_score'),inf.get('color_score'),inf.get('visual_burn_scale_index'))


def _visual_result(result, fetched_at=None, direction='evening'):
    inferred=result.get('inferred') or {}
    st.markdown('<div class="bs-lite-lead">'+__import__('html').escape(verdict(result))+'</div>',unsafe_allow_html=True)
    c1,c2,c3=st.columns(3)
    vals=_triad(result)
    for col,name,val,suffix in zip((c1,c2,c3),('常规概率','色彩分','视觉规模'),vals,('%','/100','%')):
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
    # Expose the same verified-angle evidence used by the shared Pro engine;
    # Lite must not silently look like an older colour-score implementation.
    angle_diag=inferred.get('color_angle_diagnostic') or {}
    with st.expander('色彩评分依据',expanded=False):
        note=str(inferred.get('color_score_note') or '暂无评分说明')
        st.caption(note)
        reasons=inferred.get('color_score_reasons') or []
        if reasons:
            for reason in reasons:
                st.write('• '+str(reason))
        if angle_diag:
            st.caption(
                f"严格验证受光角度：{angle_diag.get('min_deg','—')}°～"
                f"{angle_diag.get('max_deg','—')}°；跨度 {angle_diag.get('span_deg','—')}°；"
                f"有效分钟 {angle_diag.get('verified_minutes','—')}；"
                f"角度系数 {angle_diag.get('factor','—')}"
            )
        else:
            st.caption('本次没有可用于色彩加权的严格验证太阳角度；不会用未验证角度补分。')


def _version_table(before,after):
    st.markdown('#### 校准前后')
    c1,c2=st.columns(2)
    for col,label,result in ((c1,'校准前',before),(c2,'校准后',after)):
        a,b,visual=_triad(result)
        with col:
            st.markdown('**'+label+'**')
            st.caption('常规概率 · 色彩分 · 视觉规模')
            st.markdown(f'**{_display_metric("",a,"%")}　/　{_display_metric("",b)}　/　{_display_metric("",visual,"%") }**')


def _calibration_id(state, engine_module):
    meta=state['meta']
    config={'place':meta['place'],'lat':meta['lat'],'lon':meta['lon'],'elev':meta['elev'],
            'date':meta['date'],'model':'GFS' if 'GFS' in meta['model_label'] else 'EC',
            'direction':meta['direction']}
    baseline=engine_module.FireCloudApp._json_safe(state['result'])
    fingerprint=hashlib.sha256(json.dumps(baseline,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()[:16]
    return advanced_identity(config)+'_'+fingerprint

render_hero('Burning Skies Lite','朝霞 · 晚霞')
nav=st.segmented_control('查看', ['未来三日','单次预测'],default='未来三日',key='lite_nav',label_visibility='collapsed')

if nav=='未来三日':
    # Read only completed public overview files. Never dispatch GitHub Actions.
    city_list=['合肥']
    _cities=ROOT/'overview_cities.txt'
    if _cities.is_file():
        extra=[s.split(',')[0].strip() for s in _cities.read_text('utf-8-sig').splitlines()
               if s.strip() and not s.lstrip().startswith('#')]
        city_list=list(dict.fromkeys([x for x in extra if x])) or city_list
    city=st.selectbox('地点',city_list,key='lite_overview_city') if len(city_list)>1 else city_list[0]
    if st.button('刷新已发布数据', key='lite_overview_refresh'):
        _published.clear()
        st.rerun()
    with st.spinner('读取三日概览…'):
        overview=_published(city)
    # UI-level guard: published JSON may still contain five historical days.
    # Keep exactly today's three-day window independent of source age/order.
    _today=china_today()
    _dates=[(_today+timedelta(days=i)).isoformat() for i in range(3)]
    _by_date={str(day.get('date')):day for day in (overview.get('days') or [])
              if isinstance(day,dict)}
    display_days=[_by_date.get(d,{'date':d,'morning':{},'evening':{}}) for d in _dates]
    st.caption(f'Lite 三日显示版 · {LITE_OVERVIEW_UI_BUILD} · 展示 {_dates[0]} 至 {_dates[-1]}')
    counts=overview.get('model_counts') or {}
    st.caption(f"发布数据：{overview.get('source_name','未知')} · "
               f"有效 {overview.get('valid_count',0)}/12 项 · "
               f"EC {counts.get('ECMWF IFS 0.25°',0)}/6 · "
               f"GFS {counts.get('GFS Global',0)}/6 · "
               f"视觉规模 {overview.get('visual_valid_count',0)}/12")
    _pub = overview.get('publication') or {}
    if _pub.get('state') == 'partial':
        _stale = _pub.get('retained_old_keys') or []
        _missing = _pub.get('missing_keys') or []
        st.warning(f'本轮部分更新已发布：本轮有效 {_pub.get("valid_in_batch", "?")}/12 项；'
                   f'沿用旧结果 {len(_stale)} 项，缺失 {len(_missing)} 项。'
                   '旧结果仅用于相同日期/模式/朝晚霞，不代表刚刚计算成功。')
    if overview.get('checkpoint_filled'):
        st.info(f"已从当日检查点补齐 {overview['checkpoint_filled']} 项，未覆盖已发布结果。")
    if not overview['has_data']:
        st.info('当前尚无可显示的三日预报。')
    else:
        for direction,label in (('morning','朝霞'),('evening','晚霞')):
            chart=render_bars(display_days,direction,label)
            if hasattr(st,'html'):
                st.html(chart)
            else:
                st.markdown(chart,unsafe_allow_html=True)
        # Entire overview is read-only, and its detailed table is optional.
        with st.expander('查看全部数据',expanded=False):
            rows=[]
            for day in display_days:
                for direction,name in (('morning','朝霞'),('evening','晚霞')):
                    for model,short in (('ECMWF IFS 0.25°','EC'),('GFS Global','GFS')):
                        record=(day.get(direction) or {}).get(model) or {}
                        rows.append({'日期':day['date'],'时段':name,'模式':short,
                            '常规概率':_display_metric('',record.get('probability'),'%'),
                            '色彩分':_display_metric('',record.get('color_score')),
                            '几何规模':_display_metric('',record.get('burn_scale'),'%'),
                            '视觉规模':_display_metric('',record.get('visual_burn_scale'),'%')})
            st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
            st.caption('说明：柱子上的 0 表示有效零值；— 表示没有已完成的数据。')
        if overview.get('updated_at'):
            st.caption('更新：'+str(overview['updated_at']))

else:
    st.markdown('#### 离线案例重放')
    st.caption('上传 Pro / Lite 保存的案例 ZIP。只读取 ZIP 内 request_cache.json；缺少请求时立即报错，绝不联网补请求。')
    offline_zip = st.file_uploader('加载案例 ZIP（离线，不请求天气 API）', type=['zip'],
                                   key='lite_offline_case_zip')
    offline_go = st.button('读取缓存并离线重算', type='secondary',
                           disabled=offline_zip is None, use_container_width=True,
                           key='lite_offline_replay_go')
    if offline_go and offline_zip is not None:
        try:
            offline_case = _read_offline_case_zip(offline_zip.getvalue())
            if len(offline_case['directions']) > 1:
                replay_direction = st.selectbox('重放方向', offline_case['directions'],
                    format_func=lambda x: '朝霞' if x == 'morning' else '晚霞',
                    key='lite_offline_replay_direction')
            else:
                replay_direction = offline_case['directions'][0]
            params = dict(offline_case['params'])
            with st.spinner('正在使用 ZIP 内缓存离线重算（网络请求已禁用）…'):
                replay = run_single(engine, place=offline_case['place'],
                    lat=offline_case['lat'], lon=offline_case['lon'],
                    elevation=offline_case['elevation'], day=offline_case['day'],
                    model_label=offline_case['model_label'], direction=replay_direction,
                    request_cache=offline_case['cache'], replay_values=params)
            pro_record = (offline_case['analysis'].get('direction_results') or {}).get(replay_direction) or {}
            pro_inf = pro_record.get('inferred') or {}
            lite_inf = (replay.get('result') or {}).get('inferred') or {}
            st.session_state['lite_offline_replay_result'] = {
                'case': offline_case, 'direction': replay_direction,
                'replay': replay, 'pro_inferred': pro_inf,
                'pro_summary': pro_record.get('summary', '')}
            st.success(f"离线重算完成：{offline_case['place']} · {offline_case['day']} · "
                       f"{offline_case['model_code']} · {'朝霞' if replay_direction == 'morning' else '晚霞'}；"
                       f"使用缓存请求 {len(replay.get('cache_used_urls') or [])}/{len(offline_case['cache'])} 条。")
        except Exception as ex:
            st.error('离线重算失败（未发起网络请求）：' + str(ex))
    offline_result = st.session_state.get('lite_offline_replay_result')
    if offline_result:
        _case = offline_result['case']
        _dir = offline_result['direction']
        _lite_record = offline_result['replay']['result']
        _lite_inf = (_lite_record.get('inferred') or {})
        st.markdown(f"**离线对照：{_case['place']} · {_case['day']} · {_case['model_code']} · "
                    f"{'朝霞' if _dir == 'morning' else '晚霞'}**")
        _rows = [_offline_metric_row('Pro 存档', offline_result['pro_inferred']),
                 _offline_metric_row('Lite 离线重算', _lite_inf)]
        st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True)
        st.caption('请求缓存使用数：'+str(len(offline_result['replay'].get('cache_used_urls') or []))+
                   ' / '+str(len(_case['cache']))+'；未使用的缓存请求不会触发 API。')
        with st.expander('查看 Lite 离线重算结论', expanded=True):
            st.write(verdict(_lite_record))
            st.caption(source_time(_lite_record, offline_result['replay'].get('fetched_at')))
        if st.button('清除离线对照结果', key='lite_clear_offline_replay'):
            st.session_state.pop('lite_offline_replay_result', None)
            st.rerun()
    st.divider()
    try:
        cities=_places()
    except (OSError, ValueError, KeyError) as ex:
        st.error('地点库读取失败：'+str(ex))
        st.stop()
    if not cities:
        st.error('当前地点库为空')
        st.stop()
    names=sorted(cities.keys(),key=lambda x:(x!='合肥',x))
    a,b,c=st.columns([2,2,2])
    with a:
        place=st.selectbox('地点',names,index=names.index('合肥') if '合肥' in names else 0,key='lite_single_place')
    with b:
        day=st.date_input('日期',china_today(),min_value=china_today(),
                          max_value=china_today()+__import__('datetime').timedelta(days=15),key='lite_single_day')
    with c:
        model=st.selectbox('模式',['ECMWF IFS 0.25°','GFS Global','EC&GFS'],index=0,key='lite_single_model')
    direction_label=st.segmented_control('时段',['朝霞','晚霞'],
        default='晚霞' if st.session_state.get('lite_last_direction')=='evening' else '朝霞',
        key='lite_direction_choice')
    direction='morning' if direction_label=='朝霞' else 'evening'
    st.session_state['lite_last_direction']=direction
    run_query=st.button('查询 / 更新当前时段',type='primary',use_container_width=True,key='lite_query_current_direction')

    lat,lon,elevation=cities[place]
    if run_query:
        st.session_state.pop('lite_current',None)
        st.session_state.pop('lite_model_states',None)
        st.session_state.pop('lite_version',None)
        st.session_state.pop('lite_next_version',None)
        for _old_key in list(st.session_state):
            if str(_old_key).startswith('lite_sat_upload_'):
                st.session_state.pop(_old_key,None)
        model_codes=('EC','GFS') if model=='EC&GFS' else (('EC',) if model=='ECMWF IFS 0.25°' else ('GFS',))
        labels={'EC':'ECMWF IFS 0.25°','GFS':'GFS Global'}
        model_states={}
        for code in model_codes:
            with st.spinner(f'正在依次计算{labels[code]} {"朝霞" if direction=="morning" else "晚霞"}…'):
                try:
                    state=run_single(engine,place=place,lat=lat,lon=lon,elevation=elevation,
                                     day=day.isoformat(),model_label=labels[code],direction=direction)
                    state['calibration']=None
                    state['from_cache']=False
                    save_interactive_cache(place=place,lat=lat,lon=lon,elevation=elevation,
                        forecast_date=day.isoformat(),model=code,direction=direction,
                        result=state['result'],request_cache=state.get('cache') or {},
                        meta=state.get('meta') or {},values=state.get('values') or {'cloud_base_mode':'自动剖面推算','aod_source':'CAMS'},
                        spatial_source_log=state.get('spatial_source_log') or [],fetched_at=state.get('fetched_at'),
                        to_safe=engine.FireCloudApp._json_safe)
                    model_states[code]=state
                except Exception as ex:
                    st.error(f'{labels[code]} 查询失败：{ex}')
        st.session_state['lite_model_states']=model_states
        if model_states:
            active='EC' if 'EC' in model_states else 'GFS'
            st.session_state['lite_current']=model_states[active]
            st.session_state['lite_active_model']=active

    # Model/date/location changes restore the latest successful result from this
    # China-local day. No provider call is made just to switch display modes.
    if direction in ('morning','evening') and not run_query:
        codes=('EC','GFS') if model=='EC&GFS' else (('EC',) if model=='ECMWF IFS 0.25°' else ('GFS',))
        loaded={}
        for code in codes:
            rec=load_interactive_cache(place=place,lat=lat,lon=lon,elevation=elevation,
                forecast_date=day.isoformat(),model=code,direction=direction)
            if rec:
                meta=dict(rec.get('meta') or {})
                meta.update({'place':rec['place'],'lat':rec['lat'],'lon':rec['lon'],
                    'elev':rec['elevation'],'date':rec['date'],'model_label':rec['model_label'],
                    'direction':rec['direction']})
                loaded[code]={'result':rec['result'],'cache':rec.get('request_cache') or {},
                    'meta':meta,'values':rec.get('values') or {},'fetched_at':rec.get('fetched_at'),
                    'spatial_source_log':rec.get('spatial_source_log') or [],'calibration':None,'from_cache':True}
                # Restore the latest same-day satellite correction if it belongs
                # to this exact immutable baseline.
                try:
                    _base_key=_calibration_id(loaded[code],engine)
                    _supplement=archive_read(_base_key+'_lite_supplement')
                    if _supplement:
                        loaded[code]['supplement_cache']=_supplement.get('request_cache') or {}
                    ck=_base_key+'_lite_corrected'
                    corrected_record=archive_read(ck)
                    if corrected_record and corrected_record.get('baseline_sha')==hashlib.sha256(
                        json.dumps(engine.FireCloudApp._json_safe(rec['result']),ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest():
                        loaded[code]['calibration']=corrected_record.get('outcome')
                except Exception:
                    pass
        if loaded:
            st.session_state['lite_model_states']=loaded
            if model=='EC&GFS':
                # The comparison view no longer has a detail-model switch.
                # Use the model with a restored satellite correction for the
                # calibration status panel; otherwise EC is the stable default.
                active=next((code for code in ('EC','GFS')
                             if loaded.get(code,{}).get('calibration')),None)
                if active not in loaded:
                    active='EC' if 'EC' in loaded else 'GFS'
            else:
                active='EC' if model=='ECMWF IFS 0.25°' else 'GFS'
            st.session_state['lite_current']=loaded.get(active) or next(iter(loaded.values()))
            st.session_state['lite_active_model']=active
        elif st.session_state.get('lite_current'):
            current=st.session_state['lite_current']
            cm=current.get('meta') or {}
            current_code='EC' if 'ECMWF' in str(cm.get('model_label','')) else 'GFS'
            if (cm.get('place')!=place or cm.get('date')!=day.isoformat()
                    or cm.get('direction')!=direction or current_code not in codes):
                st.session_state.pop('lite_current',None)
                st.session_state.pop('lite_model_states',None)

    model_states=st.session_state.get('lite_model_states') or {}
    state=st.session_state.get('lite_current')
    if model=='EC&GFS' and model_states:
        st.markdown('#### EC / GFS 当日结果')
        with st.container(key='lite-model-comparison'):
            cols=st.columns(2,gap='large')
            for col,code in zip(cols,('EC','GFS')):
                with col:
                    st.markdown(f'### {code}')
                    candidate=model_states.get(code)
                    if candidate:
                        _corrected=(candidate.get('calibration') or {}).get('result') if (candidate.get('calibration') or {}).get('changed') else None
                        _shown_candidate=_corrected or candidate['result']
                        inf=_shown_candidate.get('inferred') or {}
                        # Restore the original Lite glass metric-card appearance;
                        # only the verbose detail switch is removed.
                        st.metric('常规概率',_display_metric('',inf.get('regular_score'),'%'))
                        st.metric('色彩分',_display_metric('',inf.get('color_score')))
                        st.metric('视觉规模',_display_metric('',inf.get('visual_burn_scale_index'),'%'))
                        _reason=__import__('html').escape(verdict(_shown_candidate))
                        st.markdown('<div class=\"bs-lite-model-summary\"><div class=\"label\">结论</div>'
                                    '<div class=\"text\">'+_reason+'</div></div>',unsafe_allow_html=True)
                        st.caption('模型数据时间：'+source_time(candidate['result'],candidate.get('fetched_at')))
                    else:
                        st.info('暂无当日完整缓存；需要查询后才会显示。')
        available=[x for x in ('EC','GFS') if x in model_states]
        _active=st.session_state.get('lite_active_model')
        active=_active if _active in available else ('EC' if 'EC' in available else available[0])
        st.session_state['lite_active_model']=active
        state=model_states.get(active)
        if state:
            st.session_state['lite_current']=state
    elif model in ('ECMWF IFS 0.25°','GFS Global') and model_states:
        active='EC' if model=='ECMWF IFS 0.25°' else 'GFS'
        state=model_states.get(active) or state
        if state: st.session_state['lite_current']=state

    if state:
        meta=state['meta']
        original=state['result']
        corrected=(state.get('calibration') or {}).get('result') if (state.get('calibration') or {}).get('changed') else None
        _display_model_label='EC&GFS 双模式' if model=='EC&GFS' else meta['model_label']
        st.markdown(f"**{meta['place']} · {meta['date']} · {'朝霞' if meta['direction']=='morning' else '晚霞'} · {_display_model_label}**")
        st.caption('Lite 单次计算：'+LITE_SINGLE_ENGINE_BUILD+' · EC/GFS 共用云底受光与视觉规模算法' + (' · 已恢复当日缓存，未重新请求天气 API' if state.get('from_cache') else ''))
        if model!='EC&GFS':
            if corrected:
                _version_table(original,corrected)
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
        upload_identity=hashlib.sha256(json.dumps([meta['place'],meta['lat'],meta['lon'],meta['elev'],meta['date'],meta['direction']],ensure_ascii=False).encode()).hexdigest()[:18]
        uploaded=st.file_uploader('上传对应时刻的卫星云图截图',type=['png','jpg','jpeg','webp'],
                                  key='lite_sat_upload_'+upload_identity)
        run_calibration=st.button('自动识别更接近卫星云图的模型并校准',type='primary',disabled=uploaded is None,
                                  use_container_width=True,key='lite_sat_go')
        if run_calibration and uploaded:
            raw=uploaded.getvalue()
            if len(raw)>MAX_UPLOAD_BYTES:
                st.error('图片不能超过12MB')
            else:
                uploaded_sig=hashlib.sha256(raw).hexdigest()
                candidate_states={}
                for code in ('EC','GFS'):
                    rec=load_interactive_cache(place=meta['place'],lat=meta['lat'],lon=meta['lon'],
                        elevation=meta['elev'],forecast_date=meta['date'],model=code,direction=meta['direction'])
                    if rec:
                        cm=dict(rec.get('meta') or {})
                        cm.update({'place':rec['place'],'lat':rec['lat'],'lon':rec['lon'],
                            'elev':rec['elevation'],'date':rec['date'],'model_label':rec['model_label'],
                            'direction':rec['direction']})
                        _candidate={'result':rec['result'],'cache':rec.get('request_cache') or {},'meta':cm}
                        try:
                            _supplement=archive_read(_calibration_id(_candidate,engine)+'_lite_supplement')
                            if _supplement:_candidate['supplement_cache']=_supplement.get('request_cache') or {}
                        except Exception:pass
                        candidate_states[code]=_candidate
                if not candidate_states:
                    st.error('没有可用的当日 EC/GFS 缓存，请先完成一次单次查询。')
                else:
                    with st.spinner('正在对比 EC/GFS 云带、云洞位置和卫星纹理，并自动选择基准…'):
                        try:
                            outcome=calibrate_auto(candidate_states,raw)
                            selected_model=outcome['selected_model']
                            chosen=candidate_states[selected_model]
                            chosen['calibration']=outcome
                            model_states[selected_model]=chosen
                            st.session_state['lite_model_states']=model_states
                            st.session_state['lite_current']=chosen
                            st.session_state['lite_active_model']=selected_model
                            baseline_sha=hashlib.sha256(json.dumps(engine.FireCloudApp._json_safe(chosen['result']),ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
                            corrected_key=_calibration_id(chosen,engine)+'_lite_corrected'
                            archive_write(corrected_key,{'baseline_sha':baseline_sha,'outcome':outcome,
                                'screenshot_sha':uploaded_sig,'saved_at':__import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')})
                            checkpoint_key=_calibration_id(chosen,engine)+'_lite_supplement'
                            archive_write(checkpoint_key,{'screenshot_sha':uploaded_sig,'request_cache':outcome.get('cache') or {}})
                            st.session_state['lite_next_version']='校准后' if outcome['changed'] else '原始预测'
                            st.success(f'自动选择 {selected_model} 作为校准基准。')
                            st.rerun()
                        except Exception as ex:
                            partial=getattr(ex,'partial_cache',None)
                            if partial:
                                archive_write(_calibration_id(state,engine)+'_lite_supplement',{'screenshot_sha':uploaded_sig,'request_cache':engine.FireCloudApp._json_safe(partial)})
                            st.error('未能完成校准：'+str(ex))
        if state.get('calibration'):
            st.caption(state['calibration'].get('note') or '')
            comparison=state['calibration'].get('model_comparison') or {}
            if comparison:
                st.caption('模型卫星匹配诊断（加权外观差异，越低越接近；仅为定性比较，不是卫星反演云量）：'+
                    '；'.join(f"{k} {v.get('score') if v.get('score') is not None else '不可评分'} / {v.get('points',0)}点" for k,v in comparison.items()))

