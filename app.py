# -*- coding: utf-8 -*-
"""Streamlit web client for the shared Burning Sky v1.4.67 / Web V1.6.11 engine."""
from __future__ import annotations
from burning_sky.place_excel_paths import find_place_workbook

import os
# The Streamlit foreground query is interactive; GitHub Actions remains batch-limited.
os.environ["BURNING_SKY_REQUEST_PROFILE"] = "interactive"
# Streamlit 没有图形界面：禁止共用引擎弹出 Tk 对话框，否则无显示主机上会抛出
# "No master specified and tkinter is configured to not support default root"。
# 结果仍通过 engine.messagebox.last_error / last_info 读取。
os.environ["BURNING_SKY_HEADLESS"] = "1"
import re
import math
import io
import json
import html
import zipfile
import importlib.util
import sys
from datetime import date
from types import SimpleNamespace

import pandas as pd
import streamlit as st

# Load the sibling engine.py by absolute path. A plain `import engine` can
# resolve an unrelated installed module (or a stale module cached by Streamlit)
# on hosted deployments, leaving WEATHER_MODEL_CATALOG unavailable.
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_ENGINE_PATH = os.path.join(_APP_DIR, "engine.py")
# engine.py imports the sibling `burning_sky` package. Streamlit Cloud may
# execute app.py without adding the repository root to sys.path, so explicitly
# put the directory containing both engine.py and the package at the front.
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
_ENGINE_SPEC = importlib.util.spec_from_file_location("_burning_sky_runtime_engine", _ENGINE_PATH)
if _ENGINE_SPEC is None or _ENGINE_SPEC.loader is None:
    raise ImportError(f"无法加载同目录的 engine.py：{_ENGINE_PATH}")
engine = importlib.util.module_from_spec(_ENGINE_SPEC)
sys.modules[_ENGINE_SPEC.name] = engine
_ENGINE_SPEC.loader.exec_module(engine)
if not hasattr(engine, "WEATHER_MODEL_CATALOG"):
    raise ImportError(f"加载的引擎文件缺少 WEATHER_MODEL_CATALOG：{_ENGINE_PATH}")

st.set_page_config(page_title="Burning Sky", page_icon="🌇", layout="wide")

# Community-hosted apps may have a public URL. Configure APP_PASSWORD in Streamlit
# secrets or the environment to keep the interface restricted to the owner.
APP_PASSWORD = ""
try:
    APP_PASSWORD = str(st.secrets.get("APP_PASSWORD", ""))
except Exception:
    APP_PASSWORD = ""
APP_PASSWORD = APP_PASSWORD or os.environ.get("APP_PASSWORD", "")

if APP_PASSWORD:
    if st.session_state.get("authenticated") is not True:
        st.title("🌇 Burning Sky")
        password = st.text_input("访问密码", type="password")
        if st.button("进入", type="primary"):
            if password == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("密码不正确。")
        st.stop()

class _Var:
    def __init__(self, value=""):
        self.value = str(value)
    def get(self):
        return self.value
    def set(self, value):
        self.value = str(value)

class _DummyLabel:
    def configure(self, **kwargs):
        pass

class WebForecastEngine(engine.FireCloudApp):
    """Reuses v1.4.49 calculation and data-fetch methods without creating Tk."""
    def __init__(self, values):
        # Do not call the Tkinter constructor. The inherited forecast method only
        # needs its existing input variables, calculation helpers and result slots.
        self.vars = {k: _Var(v) for k, v in values.items()}
        self.direction_results = {}
        self.last_report = ""
        self.last_fetch_time = None
        self.aod_manual_override = False
        self._setting_aod_programmatically = False
        self.web_aod_value = None
        self.web_aod_valid_time = None
        self.aod_path_values = []
        self.cloud_path_samples = []
        self.cloud_path_result = None
        self.fetch_in_progress = False
        self.morning_summary = _DummyLabel()
        self.evening_summary = _DummyLabel()
        self.morning_score_label = _DummyLabel()
        self.evening_score_label = _DummyLabel()
        self.morning_upper_label = _DummyLabel()
        self.evening_upper_label = _DummyLabel()
        self.morning_color_score_label = _DummyLabel()
        self.evening_color_score_label = _DummyLabel()
        # The shared forecast method updates these Tk judgment widgets after
        # calculating results. The web client has no Tk UI, so provide harmless
        # placeholders to keep the shared calculation path compatible.
        self.morning_judgment_label = _DummyLabel()
        self.evening_judgment_label = _DummyLabel()
        self.morning_rainbow_label = _DummyLabel()
        self.evening_rainbow_label = _DummyLabel()
        self.morning_aod_status_var = _Var("")
        self.evening_aod_status_var = _Var("")
        self._error = None
    def after(self, delay_ms, callback=None, *args):
        """Tk compatibility: Streamlit renders results after computation, not via Tk callbacks."""
        return None
    def _set_main_progress(self, percent, status=None):
        pass
    @staticmethod
    def _set_text(widget, value):
        pass
    def _draw_peak_color_swatch(self, direction_key):
        pass
    def _draw_colorbar(self, direction_key):
        pass


from burning_sky.web_color_curve import draw_dynamic_color_curve as _draw_dynamic_color_curve

st.title("🌇 Burning Sky")
# 仅为“未来多日概览 / 手动查询多日概览”两个入口设置低饱和蓝色，不影响其他按钮。
st.markdown("""<style>
.st-key-open_multi_day_overview button {
    background: #718EAA !important;
    color: #FFFFFF !important;
    border: 1px solid #718EAA !important;
    font-weight: 600 !important;
}
.st-key-open_multi_day_overview button:hover {
    background: #627F9B !important;
    border-color: #627F9B !important;
    color: #FFFFFF !important;
}
.st-key-open_manual_overview button {
    background: #FFFFFF !important;
    color: #4A6480 !important;
    border: 1px solid #718EAA !important;
    font-weight: 600 !important;
}
.st-key-open_manual_overview button:hover {
    background: #EDF1F6 !important;
    border-color: #627F9B !important;
    color: #3C5468 !important;
}
</style>""", unsafe_allow_html=True)
with st.sidebar:
    if st.button("未来多日概览", use_container_width=True, key="open_multi_day_overview"):
        st.session_state["show_multi_day_overview"] = True
        st.session_state["show_manual_overview"] = False
        st.rerun()
    # 手动多日概览已从概览页独立出来，入口就放在“未来多日概览”按钮下方。
    if st.button("手动查询多日概览", use_container_width=True, key="open_manual_overview"):
        st.session_state["show_manual_overview"] = True
        st.session_state["show_multi_day_overview"] = False
        st.rerun()
    st.header("预报设置")
    place_query = st.text_input("地点名称（输入前几个字联想）", "合肥", key="place_query")
    # 地名联想优先读取 Burning Sky 根目录 Excel，兼容共享目录 Excel，不访问地理编码网络服务。
    import pandas as pd
    _place_df = pd.DataFrame()
    _xlsx_path = find_place_workbook()
    try:
        _place_df = pd.read_excel(_xlsx_path, sheet_name="地名坐标表")
    except Exception:
        pass
    def _norm_place(v):
        v = str(v or "").strip().replace(" ", "").replace("　", "")
        for suffix in ("市", "地区", "自治州", "自治县", "盟"):
            if v.endswith(suffix): v = v[:-len(suffix)]; break
        return v.casefold()
    _matches = []
    if not _place_df.empty and "城市名称" in _place_df.columns:
        _q = _norm_place(place_query)
        if _q:
            _names = [str(x) for x in _place_df["城市名称"].dropna().tolist()]
            _matches = [n for n in _names if _norm_place(n).startswith(_q) or _q in _norm_place(n)][:30]
    if _matches:
        _default = _matches.index(place_query) if place_query in _matches else 0
        place = st.selectbox("匹配地点（选择后自动读取坐标/海拔）", _matches, index=_default, key="place_match")
        _selected_rows = _place_df[_place_df["城市名称"].astype(str) == place]
        _selected = _selected_rows.iloc[0] if not _selected_rows.empty else None
    else:
        place = place_query.strip() or "未命名地点"
        _selected = None
        if place_query.strip(): st.caption("Excel 中暂无匹配项；可在本地 Excel 增加该地点，或手动输入坐标和海拔。")
    _default_lat = float(_selected["纬度"]) if _selected is not None and pd.notna(_selected.get("纬度")) else 31.8047
    _default_lon = float(_selected["经度"]) if _selected is not None and pd.notna(_selected.get("经度")) else 117.2214
    _default_elev = float(_selected["海拔（m）"]) if _selected is not None and pd.notna(_selected.get("海拔（m）")) else 0.0
    forecast_date = st.date_input("预报日期", value=date.today())
    lat = st.number_input("纬度", min_value=-90.0, max_value=90.0, value=_default_lat, format="%.4f")
    lon = st.number_input("经度", min_value=-180.0, max_value=180.0, value=_default_lon, format="%.4f")
    elevation = st.number_input("海拔（m，Excel 为近似城市中心值，可手动修改）", min_value=-500.0, max_value=9000.0, value=_default_elev, step=10.0)
    model_options = [item["label"] for item in engine.WEATHER_MODEL_CATALOG]
    model_label = st.selectbox("天气预报模式", model_options, index=0)
    st.caption("JMA MSM 已暂时停用；ECMWF (AI) 的部分云层分层由湿度场推算；CMA GRAPES 公共数据服务可能拥堵。接口不可用时会显示错误，不会静默切换模式。")
    cloud_base_mode = st.selectbox("云底来源", ["自动剖面推算", "手动指定"])
    st.caption("默认时区为 UTC+8。当前网页版本不自动识别任意地点的时区。")
    st.divider()
    aod_source = st.selectbox("AOD 数据源", ["CAMS", "CAMS（垂直分布）", "手动输入"], index=0, key="aod_source_selector",
                              help="CAMS 垂直分布使用相同的 AOD 数值，结合气压层湿度估计光学衰减；不额外请求 NASA")
    if aod_source == "CAMS（垂直分布）":
        st.caption("实验模式：CAMS 总 AOD 不变，使用气压层湿度估计垂直消光分布；主要对比色彩分和云色时序。")
    if aod_source == "手动输入":
        st.caption("手动 AOD（550 nm）：直接修改数值后点击查询")
        _manual_cols = st.columns(2)
        with _manual_cols[0]:
            st.number_input("朝霞 AOD", min_value=0.0, max_value=5.0, value=0.30, step=0.05, key="manual_morning_aod")
        with _manual_cols[1]:
            st.number_input("晚霞 AOD", min_value=0.0, max_value=5.0, value=0.30, step=0.05, key="manual_evening_aod")
    st.subheader("朝霞参数")
    morning_cloud_base = st.number_input("朝霞手动云底/回退值（km）", 0.1, 15.0, 2.0, 0.1)
    morning_aod_text = str(st.session_state.get("manual_morning_aod",0.30)) if aod_source == "手动输入" else ""
    st.subheader("晚霞参数")
    evening_cloud_base = st.number_input("晚霞手动云底/回退值（km）", 0.1, 15.0, 2.0, 0.1)
    evening_aod_text = str(st.session_state.get("manual_evening_aod",0.30)) if aod_source == "手动输入" else ""
    st.divider()
    # Case-library archive is separate from the temporary Streamlit session.
    from burning_sky import case_library as _case_library
    with st.expander("📚 网页案例库：保存、下载、删除", expanded=False):
        if not os.environ.get("BURNING_SKY_CASE_LIBRARY_DIR") and not _case_library.github_enabled():
            st.warning("当前案例库使用服务器本地目录；Streamlit Cloud 重新部署可能清空。永久存档请配置 BURNING_SKY_CASE_LIBRARY_DIR 为持久化挂载目录。")
        for _record in _case_library.list_cases():
            _cid = _record['id']
            st.caption(f"{_record.get('created_at')} · {_record.get('place')} · {_record.get('date')} · {_record.get('model')} · {_record.get('note','')}")
            _lc, _rc = st.columns([2, 1])
            with _lc:
                try:
                    _raw = _case_library.read(_cid)
                    st.download_button("下载已存案例ZIP", data=_raw, file_name=f"BurningSky_Archive_{_cid[:8]}.zip", mime="application/zip", key=f"case_dl_{_cid}")
                except Exception as _read_err:
                    st.error(f"案例读取失败：{_read_err}")
            with _rc:
                _confirm = st.checkbox("确认删除", key=f"case_confirm_{_cid}")
                if st.button("删除案例", key=f"case_del_{_cid}", disabled=not _confirm):
                    _case_library.delete(_cid)
                    st.rerun()

    st.subheader("案例缓存")
    case_upload = st.file_uploader("加载案例 ZIP（桌面端保存的案例文件夹压缩包）", type=["zip"], key="case_zip_upload")
    if case_upload is not None:
        try:
            with zipfile.ZipFile(case_upload) as _zf:
                _names = set(_zf.namelist())
                _state_name = next((n for n in _names if n.endswith("analysis_state.json")), None)
                _cache_name = next((n for n in _names if n.endswith("request_cache.json")), None)
                if not _state_name or not _cache_name:
                    st.session_state["case_bundle_state"] = None
                    st.session_state["case_bundle_cache"] = None
                    st.warning("该案例 ZIP 缺少 analysis_state.json 或 request_cache.json，不能用于缓存重算。")
                else:
                    st.session_state["case_bundle_state"] = json.loads(_zf.read(_state_name).decode("utf-8"))
                    st.session_state["case_bundle_cache"] = json.loads(_zf.read(_cache_name).decode("utf-8"))
                    from burning_sky.case_manifest import check_zip_manifest
                    _warnings = check_zip_manifest(_zf)
                    if _warnings:
                        st.warning("案例完整性提示：" + "；".join(_warnings[:4]))
                    st.success(f"案例已加载：{st.session_state['case_bundle_state'].get('date')} · {st.session_state['case_bundle_state'].get('model_code')}。")
        except Exception as _exc:
            st.error(f"案例 ZIP 读取失败：{_exc}")
    use_case_cache = st.checkbox("从案例读取数据（不请求天气 API）", value=False, key="use_case_cache")
    _case_state = st.session_state.get("case_bundle_state")
    if use_case_cache and not _case_state:
        st.warning("请先上传并加载包含原始请求缓存的案例 ZIP。")

    morning_col, evening_col = st.columns(2)
    with morning_col:
        run_morning = st.button("查询朝霞", use_container_width=True)
    with evening_col:
        run_evening = st.button("查询晚霞", use_container_width=True)
    run_all = st.button("查询 / 更新朝霞与晚霞", type="primary", use_container_width=True)
    rainbow_only_web = st.button("🌈 单独分析彩虹（不请求火烧云）",use_container_width=True)
    rainbow_web_period = st.radio("彩虹时段",["晚霞","朝霞"],horizontal=True,key="rainbow_web_period")
    st.caption('彩虹观察者：左侧查询地点（无需另填坐标）')
    rainbow_observer_submit = st.button('计算查询地点的虹弧覆盖（复用彩虹数据）',use_container_width=True)
    run = run_morning or run_evening or run_all
    if run:
        # A fire-cloud query replaces the standalone rainbow view. Keep the
        # underlying rainbow computation cache, but not its old UI panel.
        st.session_state.pop("rainbow_only_web_result", None)
        st.session_state.pop("rainbow_web_meta", None)
        st.session_state.pop("rainbow_observer_result", None)
        st.session_state["show_multi_day_overview"] = False
        # 在“手动查询多日概览”页面里点左侧“查询朝霞/查询晚霞”时，
        # 立即退出概览页，让本次单次查询结果在本轮就渲染出来。
        st.session_state["show_manual_overview"] = False
    query_direction = "morning" if run_morning else ("evening" if run_evening else "all")

# Right-side AOD controls mirror the sidebar selection; both directions use the
# same source, while manual numeric values remain direction-specific.
def _set_aod_source_from_right(widget_key):
    st.session_state["aod_source_selector"] = st.session_state[widget_key]

_right_aod_options = ["CAMS", "CAMS（垂直分布）", "手动输入"]
_right_aod_current = st.session_state.get("aod_source_selector", "CAMS")
_aod_right_cols = st.columns(2)
for _aod_direction, _aod_col in (("朝霞", _aod_right_cols[0]), ("晚霞", _aod_right_cols[1])):
    _right_key = "right_aod_" + ("morning" if _aod_direction == "朝霞" else "evening")
    st.session_state[_right_key] = _right_aod_current
    with _aod_col:
        st.selectbox(f"{_aod_direction} AOD 来源（与左侧同步）", _right_aod_options,
                     key=_right_key, on_change=_set_aod_source_from_right, args=(_right_key,))

# Rainbow-only execution: do not invoke the fire-cloud forecast task.
if rainbow_only_web:
    from datetime import datetime, timedelta
    from burning_sky.rainbow_visual import png_bytes, summarize
    _model_id=engine._weather_model_id_from_label(model_label)
    _finder=engine.find_sunset if rainbow_web_period=='晚霞' else engine.find_sunrise
    _event_dt,_sun_az=_finder(forecast_date,8,lat,lon)
    if _event_dt is None:
        st.error('无法计算太阳事件时间')
        st.stop()
    _event_utc=(_event_dt-timedelta(hours=8)).replace(tzinfo=None)
    _web_cache=st.session_state.get('case_bundle_cache') or {}
    if not use_case_cache:
        _web_cache=dict(_web_cache)
        _web_cache.update(st.session_state.get('rainbow_request_cache',{}))
    engine._CASE_REQUEST_CONTEXT={'mode':'replay' if use_case_cache else 'capture','cache':_web_cache}
    try:
        with st.spinner('分析彩虹雨区、光路、能见度及空气质量……'):
            _rainbow=WebForecastEngine._estimate_rainbow_opportunity(lat,lon,_sun_az,_event_utc,_model_id,
                minute_range=WebForecastEngine._rainbow_window(rainbow_web_period))
        st.session_state['rainbow_only_web_result']=_rainbow
        from burning_sky.rainbow_observer import evaluate_observer
        st.session_state['rainbow_observer_result']=evaluate_observer(_rainbow,lat,lon)
        st.session_state['rainbow_request_cache']=_web_cache
        st.session_state['rainbow_web_meta']=(forecast_date.isoformat(),model_label,round(lat,5),round(lon,5),rainbow_web_period)
    except Exception as _exc:
        st.error('独立彩虹分析失败：'+str(_exc))
    finally:
        engine._CASE_REQUEST_CONTEXT=None
    st.session_state['show_multi_day_overview']=False
    st.session_state['show_manual_overview']=False
if rainbow_observer_submit:
    from burning_sky.rainbow_observer import evaluate_observer
    try:
            _a,_b=float(lat),float(lon)
            _period=rainbow_web_period
            _finder=engine.find_sunset if _period=='晚霞' else engine.find_sunrise
            _dt,_az=_finder(forecast_date,8,lat,lon)
            from datetime import timedelta
            _utc=(_dt-timedelta(hours=8)).replace(tzinfo=None)
            _model=engine._weather_model_id_from_label(model_label)
            _key=WebForecastEngine._rainbow_cache_key(lat,lon,_utc,_model,
                WebForecastEngine._rainbow_window(_period))
            _regional=WebForecastEngine._RAINBOW_RESULT_CACHE.get(_key)
            if _regional is None:
                _regional=(st.session_state.get('rainbow_only_web_result') if
                    st.session_state.get('rainbow_web_meta')==(forecast_date.isoformat(),model_label,round(lat,5),round(lon,5),_period) else None)
            if _regional is None and st.session_state.get('rainbow_web_meta')==(forecast_date.isoformat(),model_label,round(lat,5),round(lon,5),'全天'):
                _regional=st.session_state.get('rainbow_only_web_result')
            if _regional is None:
                st.warning('尚无对应日期、模式和时段的区域彩虹分析，请先运行区域查询。')
            else:
                st.session_state['rainbow_observer_result']=evaluate_observer(_regional,_a,_b)
    except Exception as _exc:
        st.error('观察点虹弧计算失败：'+str(_exc))
if not run and st.session_state.get('rainbow_observer_result') is not None:
    _obs=st.session_state['rainbow_observer_result'];_best=_obs.get('best') or {}
    st.subheader('查询地点 · 彩虹锥与受光雨区求交（低分辨率估计）')
    _oc=st.columns(2)
    _oc[0].metric('主虹匹配指数',str(_best.get('primary_index',0))+'/100')
    _oc[1].metric('副虹匹配指数',str(_best.get('secondary_index',0))+'/100')
    st.caption('主虹采样支持弧长：'+str(_best.get('primary_sample_supported_arc_deg','—'))+'° / 理论地平线上方 '+str(_best.get('primary_theoretical_above_horizon_deg','—'))+'°；估计覆盖 '+str(_best.get('primary_estimated_coverage_pct','—'))+'%')
    st.caption('副虹采样支持弧长：'+str(_best.get('secondary_sample_supported_arc_deg','—'))+'°；估计覆盖 '+str(_best.get('secondary_estimated_coverage_pct','—'))+'%')
    st.caption('主虹方向：'+str(_best.get('primary_side_hint','未知'))+'；副虹方向：'+str(_best.get('secondary_side_hint','未知')))
    st.caption(_obs['completeness']+'。'+_obs['confidence']+'；无需额外API请求。')
    with st.expander('观察点逐时诊断'):
        st.json(_obs['timeline'])
def _web_summary_lines(summary):
    """Exactly five logical display rows, preserving the original engine text."""
    lines=str(summary or '').splitlines()
    if not lines:return []
    # Engine emits model timestamp, event, model/clouds, distances, base/AOD,
    # and an optional verdict. Keep model on the same row as color/cloud data.
    if len(lines)>=5 and lines[4].startswith('云底'):
        parts=lines[2].split('　|　',1)
        model=parts[0]
        clouds=parts[1] if len(parts)>1 else ''
        out=[lines[0],lines[1]+'　'+model,clouds,lines[3]+'　'+lines[4]]
        out.append('　'.join(x for x in lines[5:] if not x.startswith('视野内低云')) if len(lines)>5 else '特殊判定：无')
        out.extend(x for x in lines[5:] if x.startswith('视野内低云'))
        return out
    return lines


def _web_rainbow_panel(rainbow, compact=True, peak_color_rgb=None, sunset_context=False):
    from burning_sky.rainbow_visual import png_bytes, summarize, timeline_chart_bytes
    _cols=st.columns(4)
    for _c,_label,_value in zip(_cols,['主虹机会','主虹质量','副虹机会','副虹质量'],
                                  [rainbow.get('primary_index',0),rainbow.get('primary_quality',0),rainbow.get('secondary_index',0),rainbow.get('secondary_quality',0)]):
        _c.metric(_label,f'{_value}/100')
    _left,_right=st.columns([1,2],gap='small')
    with _left:
        st.image(png_bytes(rainbow,peak_color_rgb=peak_color_rgb,sunset_context=sunset_context),use_container_width=True,caption='虹弧示意（非实际完整度）')
    with _right:
        st.image(timeline_chart_bytes(rainbow),use_container_width=True,caption='彩虹时序图：柱高为主虹机会指数；颜色越深表示主虹质量越高；仅显示主虹 >30 的时刻')
    st.caption('彩虹机会：'+str(rainbow.get('label','暂不可估'))+' · '+str(rainbow.get('score','—'))+'/100')
    st.caption('；'.join(summarize(rainbow)['penalties']))
    if rainbow.get('rainbow_completeness'):
        st.caption('彩虹完整度：'+str(rainbow['rainbow_completeness']))


if not run and st.session_state.get('rainbow_only_web_result') is not None:
    from burning_sky.rainbow_visual import png_bytes, summarize
    _rb=st.session_state['rainbow_only_web_result']
    st.subheader('🌈 独立彩虹分析')
    _m=st.columns(4)
    for _c,_name,_value in zip(_m,['主虹概率式分值','主虹质量','副虹概率式分值','副虹质量'],
                               [_rb.get('primary_index',0),_rb.get('primary_quality',0),_rb.get('secondary_index',0),_rb.get('secondary_quality',0)]):
        _c.metric(_name,f'{_value}/100')
    _web_rainbow_panel(_rb)
    with st.expander('详细分析（各时段、采样点及系数）'):
        st.json([{k:v for k,v in r.items() if k!='details'} for r in _rb.get('timeline',[])])
        st.json(_rb.get('environment_sources',{}))
        st.json([{'time': r.get('time'), 'details': r.get('details', [])} for r in _rb.get('timeline', []) if isinstance(r, dict) and r.get('details')])
    st.caption('数字为未经历史校准的概率式评分，不是统计发生概率。')
    st.caption('火烧云规模：0% 不烧；大于0%且不足10% 微烧；10%–不足30% 小烧；30%–不足60% 中烧；60%–100% 大烧。')
    if rainbow_only_web: st.stop()

# 在“未来多日概览 / 手动查询多日概览”页面点击左侧任一查询按钮时，优先执行单次查询。
# 清除概览页面状态，避免 st.stop() 截断查询流程，也避免下次 rerun 又跳回概览。
if run and st.session_state.get("show_multi_day_overview"):
    st.session_state["show_multi_day_overview"] = False
if run and st.session_state.get("show_manual_overview"):
    st.session_state["show_manual_overview"] = False

# Read published overview JSON from GitHub as well as the deployment's local copy.
# Streamlit Cloud does not necessarily pull GitHub Actions data-only commits.
@st.cache_data(ttl=60, show_spinner=False)
def _published_overview_json(filename):
    import json, os, re, subprocess
    from urllib.parse import quote
    import requests
    root = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(root, "data", filename)
    local = None
    try:
        with open(local_path, encoding="utf-8") as f:
            local = json.load(f)
    except (OSError, ValueError):
        pass
    repo = os.environ.get("BURNING_SKY_GITHUB_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        try:
            remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root,
                                    capture_output=True, text=True, timeout=2)
            m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$", remote.stdout.strip())
            if m:
                repo = m.group(1)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if repo and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        branch = os.environ.get("BURNING_SKY_GITHUB_BRANCH", "main")
        url = f"https://raw.githubusercontent.com/{repo}/{quote(branch, safe='')}/data/{quote(filename)}"
        try:
            import time
            response = requests.get(url, params={"_": str(int(time.time() // 60))}, timeout=7,
                                    headers={"Cache-Control": "no-cache"})
            response.raise_for_status()
            published = response.json()
            if isinstance(published, dict):
                return published, "GitHub 最新发布文件"
        except (requests.RequestException, ValueError):
            pass
    return local, "部署本地文件（远端未取得）"


if st.session_state.get("show_multi_day_overview"):
    st.title("未来多日概览")
    if st.button("← 返回单次预报", key="back_to_single_forecast"):
        st.session_state["show_multi_day_overview"] = False
        st.rerun()
    @st.fragment(run_every="30s")
    def _render_multi_day_overview():
        _data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        # Background GitHub Actions status is published at model-batch boundaries.
        _status_path = os.path.join(_data_dir, "overview_status.json")
        try:
            import json as _status_json
            _update_status, _status_origin = _published_overview_json("overview_status.json")
            if not isinstance(_update_status, dict):
                raise FileNotFoundError("overview_status.json")
            _state = _update_status.get("state", "unknown")
            _completed_models = _update_status.get("completed_models", [])
            _model_total = _update_status.get("model_total", 5)
            _model_index = _update_status.get("model_index", 0)
            _current_model = _update_status.get("current_model")
            _active_direction = _update_status.get("direction_title", "朝霞/晚霞")
            _task_progress = _update_status.get("task_progress", 0)
            _task_total = _update_status.get("task_total", 30)
            if _state == "running":
                st.info(f"🔄 后台正在更新：{_active_direction} · 第 {_model_index}/{_model_total} 个模式 · {_current_model or '准备中'} · 总进度 {_task_progress}/{_task_total}。已完成模式：{', '.join(_completed_models) if _completed_models else '暂无'}。状态更新时间：{_update_status.get('updated_at', '未知')}。网页预报仍显示上一次完整发布的数据。")
            elif _state == "between_models":
                st.info(f"⏳ 多模概览正在准备下一个模式。已完成 {_model_index}/{_model_total} 个模式：{', '.join(_completed_models)}。状态更新时间：{_update_status.get('updated_at', '未知')}。")
            elif _state == "completed":
                st.success(f"✅ {_active_direction}本轮双模式三日数据已完整发布。完成时间：{_update_status.get('completed_at', _update_status.get('updated_at', '未知'))}。下次计划：{_update_status.get('next_update', '按定时任务执行')}。")
            elif _state == "failed":
                st.error(f"❌ {_active_direction}本轮更新失败（{_update_status.get('failure_count', 0)} 项）。已保留上一次完整发布的数据。原因：{_update_status.get('message', '请查看 GitHub Actions 日志')}。")
            else:
                st.caption(f"多模概览后台状态：{_state} · 状态更新时间：{_update_status.get('updated_at', '未知')} · 下次计划：{_update_status.get('next_update', '按定时任务执行')}。")
        except Exception:
            st.caption("后台更新状态尚未发布；等待 GitHub Actions 启动。计划时刻：北京时间每日 03:00、09:00、14:00 同时更新朝霞和晚霞。")
        _city_cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overview_cities.txt")
        _overview_cities = []
        try:
            with open(_city_cfg, "r", encoding="utf-8-sig") as _cf:
                _overview_cities = [line.split(",")[0].strip() for line in _cf if line.strip() and not line.lstrip().startswith("#")]
        except Exception:
            _overview_cities = ["合肥"]
        if not _overview_cities:
            _overview_cities = ["合肥"]
        _overview_city = st.selectbox("概览城市", _overview_cities, index=0, key="overview_city_select")
        _overview_path = os.path.join(_data_dir, f"overview_{_overview_city}.json")
        _overview = None
        try:
            import json as _json
            _city_snapshot = None
            _aggregate_snapshot = None
            _city_snapshot, _city_origin = _published_overview_json(f"overview_{_overview_city}.json")
            _aggregate, _aggregate_origin = _published_overview_json("overview.json")
            _cities = _aggregate.get("cities", {}) if isinstance(_aggregate, dict) else {}
            if isinstance(_cities, dict) and _overview_city in _cities:
                _aggregate_snapshot = _cities[_overview_city]
            elif _overview_city == "合肥" and isinstance(_aggregate, dict) and _aggregate.get("days"):
                _aggregate_snapshot = _aggregate

            def _snapshot_freshness(_snapshot):
                if not isinstance(_snapshot, dict):
                    return ""
                _times = _snapshot.get("direction_updated_at", {})
                return max([str(_snapshot.get("updated_at") or "")] +
                           [str(v or "") for v in _times.values()])

            # Both files are published by the updater. If one is stale or only
            # partially deployed, display whichever copy contains the newer result.
            _candidates = [(f"城市独立文件 · {_city_origin}", x) for x in (_city_snapshot,)
                           if isinstance(x, dict) and x.get("days")]
            _candidates += [(f"总览汇总文件 · {_aggregate_origin}", x) for x in (_aggregate_snapshot,)
                            if isinstance(x, dict) and x.get("days")]
            _overview_source, _overview = (max(_candidates, key=lambda item: _snapshot_freshness(item[1]))
                                           if _candidates else (None, None))
            if len(_candidates) > 1 and _overview is not None:
                import copy as _copy
                _overview = _copy.deepcopy(_overview)
                _merged_days = {d.get("date"): d for d in _overview.get("days", []) if isinstance(d, dict)}
                _direction_times = dict(_overview.get("direction_updated_at", {}))
                for _name, _candidate in _candidates:
                    _candidate_times = _candidate.get("direction_updated_at", {})
                    for _dir in ("morning", "evening"):
                        if str(_candidate_times.get(_dir) or "") > str(_direction_times.get(_dir) or ""):
                            for _day in _candidate.get("days", []):
                                _date = _day.get("date")
                                if _date in _merged_days:
                                    _merged_days[_date][_dir] = _copy.deepcopy(_day.get(_dir, {}))
                            _direction_times[_dir] = _candidate_times[_dir]
                _overview["direction_updated_at"] = _direction_times
                _overview_source += "（按朝/晚霞独立合并）"
        except Exception:
            _overview_source, _overview = None, None
        if not _overview or not _overview.get("days"):
            st.info(f"尚无{_overview_city}已保存的概览数据。后台定时更新完成后，这里会显示包含今天的未来三日、三种天气模式的朝霞与晚霞结果。")
        else:
            st.caption(f"固定地点：{_overview_city}（{_overview.get('latitude', '—')}, {_overview.get('longitude', '—')}） · 预报范围：包含今天的未来三日 · 三模式 · 数据来源：{_overview_source} · 数据更新时间：{_overview.get('updated_at', '未知')}。")
            _model_order = ["ECMWF IFS 0.25°", "GFS Global", "ICON Global"]
            _model_short = {"ECMWF IFS 0.25°": "EC", "GFS Global": "GFS", "ICON Global": "ICON", "CMA GRAPES Global": "CMA", "ECMWF (AI)": "EC (AI)"}
            _direction_times = _overview.get("direction_updated_at", {})
            for _direction, _direction_title in (("morning", "🌄 朝霞"), ("evening", "🌆 晚霞")):
                _direction_update_time = _direction_times.get(_direction, "尚未更新")
                st.subheader(f"{_direction_title}　·　更新时次：{_direction_update_time}")
                _date_cols = st.columns(3)
                for _col, _day in zip(_date_cols, _overview.get("days", [])):
                    with _col:
                        st.markdown(f"**{_day.get('date', '日期未知')}**")
                        _records = _day.get(_direction, {})
                        _rows = []
                        for _model in _model_order:
                            _v = _records.get(_model, {})
                            _prob = _v.get("probability")
                            _scale = _v.get("burn_scale")
                            _rows.append({
                                "模式": _model_short[_model],
                                "常规概率": (f"{_prob:.0f}%" if isinstance(_prob, (int, float)) else "—"),
                                "色彩分": (f"{_v['color_score']:.0f}" if isinstance(_v.get("color_score"), (int, float)) else "—"),
                                "火烧云规模": (f"{_scale:.0f}%" if isinstance(_scale, (int, float)) else "—"),
                            })
                        _df = pd.DataFrame(_rows)
                        def _fire_score_style(value):
                            try:
                                score = float(value)
                            except (TypeError, ValueError):
                                return ""
                            if score < 20:
                                color = "#D6B84C"  # 淡黄
                            elif score < 40:
                                color = "#C99A16"  # 金色
                            elif score < 60:
                                color = "#E47724"  # 橘色
                            elif score < 80:
                                color = "#D83A32"  # 红色
                            else:
                                color = "#A12A83"  # 紫红色
                            return f"color: {color}; font-weight: 700;"
                        st.dataframe(_df, use_container_width=True, hide_index=True)
    _render_multi_day_overview()

    st.stop()

# -----------------------------------------------------------------------------
# 手动查询多日概览：独立页面（入口在左侧栏“未来多日概览”按钮下方）。
# 这里只提交 GitHub Actions 后台任务并读取已发布结果，不在网页里跑 18 次预测。
# -----------------------------------------------------------------------------
_MANUAL_MODEL_ORDER = ["ECMWF IFS 0.25°", "GFS Global", "ICON Global"]
_MODEL_SHORT = {
    "ECMWF IFS 0.25°": "EC", "GFS Global": "GFS", "ICON Global": "ICON",
    "CMA GRAPES Global": "CMA", "ECMWF (AI)": "EC (AI)",
}
_ACTIVE_RUN_STATES = ("queued", "in_progress", "waiting", "pending", "requested")
_MANUAL_DISPATCH_COOLDOWN_SECONDS = 30


def _overview_score_color(value):
    """与自动多日概览相同的常规概率配色。"""
    try:
        score = float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return ""
    if score < 20:
        color = "#D6B84C"   # 淡黄
    elif score < 40:
        color = "#C99A16"   # 金色
    elif score < 60:
        color = "#E47724"   # 橘色
    elif score < 80:
        color = "#D83A32"   # 红色
    else:
        color = "#A12A83"   # 紫红色
    return f"color: {color}; font-weight: 700;"


def _overview_styled(frame, subset=None):
    """给概览表格上色；Styler 不可用时退回普通表格，绝不让展示失败。"""
    try:
        styler = getattr(frame.style, "map", None) or getattr(frame.style, "applymap", None)
        if styler is not None:
            return styler(_overview_score_color, subset=subset or ["常规概率"])
    except Exception:
        pass
    return frame


def _manual_direction_frame(records_by_model):
    """单个“日期 + 朝霞/晚霞”单元格：每种模式一行，缺失显示 —。"""
    rows = []
    for model in _MANUAL_MODEL_ORDER:
        record = records_by_model.get(model) or {}
        probability = record.get("probability")
        color_score = record.get("color_score")
        burn_scale = record.get("burn_scale")
        rows.append({
            "模式": _MODEL_SHORT.get(model, model),
            "常规概率": (f"{probability:.0f}%" if isinstance(probability, (int, float)) else "—"),
            "色彩分": (f"{color_score:.0f}" if isinstance(color_score, (int, float)) else "—"),
            "火烧云规模": (f"{burn_scale:.0f}%" if isinstance(burn_scale, (int, float)) else "—"),
        })
    return pd.DataFrame(rows)


def _manual_records_by_day(display):
    """records -> {日期: {'morning': {模式: 记录}, 'evening': {模式: 记录}}}"""
    by_day = {}
    for record in (display or {}).get("records", []) or []:
        if not isinstance(record, dict):
            continue
        day_key = str(record.get("date") or "")
        direction = record.get("direction")
        if not day_key or direction not in ("morning", "evening"):
            continue
        by_day.setdefault(day_key, {"morning": {}, "evening": {}}) \
              [direction][str(record.get("model"))] = record
    return by_day


def _render_manual_overview_result(display, expected_dates):
    """按 日期 → 朝霞/晚霞 → 模式 展示，与自动多日概览的表格布局保持一致。"""
    by_day = _manual_records_by_day(display)
    dates = [d for d in expected_dates]
    dates.extend(sorted(d for d in by_day if d not in dates))
    for day_key in dates:
        day_records = by_day.get(day_key) or {"morning": {}, "evening": {}}
        st.markdown(f"**{day_key}**")
        columns = st.columns(2)
        for column, (direction, title) in zip(columns, (("morning", "🌄 朝霞"), ("evening", "🌆 晚霞"))):
            with column:
                st.markdown(title)
                frame = _manual_direction_frame(day_records.get(direction, {}))
                try:
                    st.dataframe(_overview_styled(frame), use_container_width=True, hide_index=True)
                except Exception:
                    # 某些 Streamlit 版本不接受 Styler：退回普通表格，绝不让结果区报错。
                    st.dataframe(frame, use_container_width=True, hide_index=True)


if st.session_state.get("show_manual_overview"):
    st.title("手动查询多日概览")
    _nav_cols = st.columns([1.3, 1.3, 3.4])
    with _nav_cols[0]:
        if st.button("← 返回单次预报", key="back_from_manual_overview"):
            st.session_state["show_manual_overview"] = False
            st.rerun()
    with _nav_cols[1]:
        if st.button("未来多日概览 →", key="manual_to_overview"):
            st.session_state["show_manual_overview"] = False
            st.session_state["show_multi_day_overview"] = True
            st.rerun()
    st.caption("独立的 EC / GFS / ICON 三模式、三日、朝晚霞查询（共 18 项）。网页只提交后台任务和读取已发布结果，不在网页里联网计算。")
    st.caption("想马上看某一天的单次结果时，直接点击左侧『查询朝霞』或『查询晚霞』，网页会自动切到查询结果界面。")
    import hashlib as _mh
    from pathlib import Path as _MP
    from datetime import datetime as _MDT, timedelta as _MTD
    from zoneinfo import ZoneInfo as _MZone
    from urllib.parse import quote as _quote
    _mtz = _MZone("Asia/Shanghai")
    _today = _MDT.now(_mtz).date()
    _cache_dir = _MP(_APP_DIR) / "data" / "manual_overview_cache"
    try:
        _cache_dir.mkdir(parents=True, exist_ok=True)
        for _file in _cache_dir.glob("*.json"):
            try:
                _meta = json.loads(_file.read_text(encoding="utf-8"))
                if _MDT.fromisoformat(_meta["obtained_at"]).astimezone(_mtz).date() < _today - _MTD(days=1):
                    _file.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, TypeError):
                pass
    except OSError:
        # 只读部署（例如 Streamlit Cloud 的临时容器）没有写权限，缓存目录缺失不影响查询。
        pass
    with st.container(border=True):
        _mq = st.text_input("查询地点（独立 Excel 联想）", value="合肥", key="manual_overview_query")
        _names = []
        if not _place_df.empty and "城市名称" in _place_df.columns and _mq.strip():
            _names = [str(v) for v in _place_df["城市名称"].dropna().tolist()
                      if _norm_place(_mq) in _norm_place(v)][:30]
        _mplace = st.selectbox("匹配地点", _names, key="manual_overview_place") if _names else _mq.strip()
        _mrow = None
        if not _place_df.empty and "城市名称" in _place_df.columns:
            _match_rows = _place_df[_place_df["城市名称"].astype(str) == _mplace]
            if not _match_rows.empty: _mrow = _match_rows.iloc[0]
        def _mdefault(col, fallback):
            return float(_mrow[col]) if _mrow is not None and col in _mrow and pd.notna(_mrow[col]) else fallback
        _cols = st.columns(3)
        with _cols[0]:
            _mlat = st.number_input("纬度", -90.0, 90.0, _mdefault("纬度",31.8047), format="%.5f", key="m_lat_"+str(_mplace))
        with _cols[1]:
            _mlon = st.number_input("经度", -180.0, 180.0, _mdefault("经度",117.2214), format="%.5f", key="m_lon_"+str(_mplace))
        with _cols[2]:
            _melev = st.number_input("海拔（m）", -500.0, 9000.0, _mdefault("海拔（m）",0.0), key="m_elev_"+str(_mplace))
        _mdate = st.date_input("起始日期（含当天连续三日）", value=_today, key="manual_overview_start")
        _models = ["ECMWF IFS 0.25°", "GFS Global", "ICON Global"]
        _identity = {"place":_mplace, "lat":round(_mlat,5), "lon":round(_mlon,5),
                     "elev":round(_melev,1), "date":_mdate.isoformat(), "models":_models}
        _cache_id = _mh.sha256(json.dumps(_identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
        _cache_file = _cache_dir / (_cache_id + ".json")
        _cached = None
        if _cache_file.exists():
            try: _cached = json.loads(_cache_file.read_text(encoding="utf-8"))
            except (OSError, ValueError): pass
        if _cached:
            st.info("已自动读取缓存，数据获得时间："+str(_cached.get("obtained_at", "未知"))+"。点击查询才重新联网。")
    # GitHub Actions async manual overview: never run 18 forecasts inside Streamlit.
    import requests as _gh_requests
    import time as _gh_time
    import re as _gh_re
    import subprocess as _gh_subprocess
    def _gh_secret(name, default=""):
        try: return str(st.secrets.get(name, default))
        except Exception: return str(os.environ.get(name, default))
    _repo = _gh_secret("BURNING_SKY_GITHUB_REPOSITORY") or _gh_secret("GITHUB_REPOSITORY")
    if not _repo:
        try:
            _remote = _gh_subprocess.run(["git","remote","get-url","origin"],cwd=_APP_DIR,capture_output=True,text=True,timeout=2)
            _match = _gh_re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$",_remote.stdout.strip())
            if _match: _repo = _match.group(1)
        except (OSError, _gh_subprocess.TimeoutExpired): pass
    _branch = _gh_secret("BURNING_SKY_GITHUB_BRANCH", "main")
    _token = _gh_secret("BURNING_SKY_GITHUB_TOKEN")
    _configured = bool(_token and _gh_re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", _repo))
    _api_base = f"https://api.github.com/repos/{_repo}"
    _headers = {"Authorization":f"Bearer {_token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"}
    def _gh_call(method,path,**kwargs):
        resp = _gh_requests.request(method,_api_base+path,headers=_headers,timeout=12,**kwargs)
        if not resp.ok: raise RuntimeError(f"GitHub API HTTP {resp.status_code}: {resp.text[:220]}")
        return resp.json() if resp.content else None
    def _list_manual_runs(page_size=30):
        return _gh_call("GET","/actions/workflows/manual-overview.yml/runs",
                        params={"per_page":page_size}).get("workflow_runs",[])
    def _active_manual_runs(page_size=30):
        return [r for r in _list_manual_runs(page_size) if r.get("status") in _ACTIVE_RUN_STATES]
    _display = None  # Published result is optional; never read before assignment.
    _display_origin = "本地缓存"
    _active = []
    _latest = None
    _workflow_ready = False
    _workflow_error = ""
    if _configured:
        try:
            # Validate the workflow itself before listing runs. A 404 here can mean
            # missing workflow on the default branch, wrong repository, or denied access.
            _workflow = _gh_call("GET", "/actions/workflows/manual-overview.yml")
            _workflow_ready = bool(_workflow.get("id"))
            _runs = _list_manual_runs()
            _active = [r for r in _runs if r.get("status") in _ACTIVE_RUN_STATES]
            _matching = [r for r in _runs if _cache_id in (r.get("display_title") or "")]
            _latest = _matching[0] if _matching else None
        except Exception as _e:
            _workflow_error = str(_e)
            if "HTTP 404" in _workflow_error:
                st.error("GitHub 找不到手动概览工作流。请确认 .github/workflows/manual-overview.yml 已上传到仓库默认分支、Secrets 的仓库名正确，且 Token 有 Actions 读写权限。GitHub 404 也可能表示权限不足。")
            else:
                st.warning("无法查询后台任务："+_workflow_error)
    else:
        st.warning("需要在 Streamlit Secrets 设置 BURNING_SKY_GITHUB_TOKEN 和 BURNING_SKY_GITHUB_REPOSITORY 才能启动/停止后台任务。")

    # 同一个仓库里只允许一个手动多日概览任务：已有任务时阻止再次启动。
    _manual_busy = bool(_active)
    if _manual_busy:
        _busy_run = _active[0]
        st.warning("⏳ 已有一个手动多日概览任务正在进行："
                   f"{_busy_run.get('display_title','')} · {_busy_run.get('status')} · 运行编号 {_busy_run.get('run_number','?')}。"
                   "请等待上一个手动多日概览任务完成后再启动新的任务。")
    _btn_start,_btn_stop,_btn_refresh = st.columns([2,1,1])
    with _btn_start:
        _do_run = st.button("▶ 启动 GitHub 后台更新",type="primary",
                            disabled=not _configured or not _workflow_ready or _manual_busy,
                            key="manual_overview_submit",
                            help="已有一个手动多日概览任务进行时，启动会被阻止。")
    with _btn_stop:
        _do_stop = st.button("■ 停止任务",disabled=not _configured or not _workflow_ready or not _active,key="manual_overview_cancel")
    with _btn_refresh:
        _do_refresh = st.button("刷新状态",key="manual_overview_refresh")
    if _do_run and _workflow_ready:
        # 提交前再确认一次：既防止重复点击，也防止两个页面/两个用户同时启动。
        _blocked_reason = ""
        try:
            _last_dispatch = float(st.session_state.get("manual_overview_dispatch_at") or 0.0)
        except (TypeError, ValueError):
            _last_dispatch = 0.0
        if _last_dispatch and (_gh_time.time() - _last_dispatch) < _MANUAL_DISPATCH_COOLDOWN_SECONDS:
            _blocked_reason = "刚刚已提交过一次后台任务。请等待上一个手动多日概览任务完成后再启动新的任务。"
        else:
            try:
                if _active_manual_runs():
                    _blocked_reason = "请等待上一个手动多日概览任务完成后再启动新的任务。"
            except Exception as _guard_error:
                _blocked_reason = "无法确认后台任务状态，已阻止本次启动："+str(_guard_error)
        if _blocked_reason:
            st.error(_blocked_reason)
        else:
            try:
                _gh_call("POST","/actions/workflows/manual-overview.yml/dispatches",json={"ref":_branch,"inputs":{"place":_mplace,"lat":str(_mlat),"lon":str(_mlon),"elev":str(_melev),"date":_mdate.isoformat(),"request_id":_cache_id}})
                st.session_state["manual_overview_dispatch_at"] = _gh_time.time()
                st.success("已提交 GitHub Actions 后台任务，可以关闭网页。")
                st.rerun()
            except Exception as _e: st.error("启动失败："+str(_e))
    if _do_stop:
        try:
            # Only cancel the run started by this manual overview workflow.
            for _r in _active:
                _gh_call("POST",f"/actions/runs/{_r['id']}/cancel")
            st.info("已请求取消后台任务。GitHub 可能需要片刻才停止。")
            st.rerun()
        except Exception as _e: st.error("取消失败："+str(_e))
    if _active:
        _r = _active[0]
        st.info(f"GitHub Actions 运行中：{_r.get('display_title','')} · {_r.get('status')} · 运行编号 {_r.get('run_number','?')}")
        st.caption("关闭网页不影响后台运行。停止按钮会取消当前手动概览工作流的活动任务。")
    elif _latest:
        st.caption(f"最近一次相同条件的后台任务：{_latest.get('status')} / {_latest.get('conclusion') or '未结束'}")
    # GitHub raw data is authoritative after publication; the Streamlit instance may be stale.
    if _repo and _gh_re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",_repo):
        try:
            _url=f"https://raw.githubusercontent.com/{_repo}/{_quote(_branch,safe='')}/data/manual_overview_runs/{_cache_id}.json"
            _resp=_gh_requests.get(_url,params={"v":str(int(_gh_time.time()//30))},timeout=8,headers={"Cache-Control":"no-cache"})
            if _resp.ok:
                _published=_resp.json()
                if _published.get("identity")==_identity:
                    _display=_published
                    _display_origin="GitHub 已发布文件"
        except (_gh_requests.RequestException,ValueError): pass
    if _display is None: _display = _cached

    st.divider()
    st.subheader("查询结果")
    if not _display:
        st.info("本次条件还没有已发布的结果。启动后台任务后，每完成一项都会更新进度；也可以等待 GitHub Actions 发布后点击“刷新状态”。")
    else:
        _result_identity = _display.get("identity") or _identity
        try:
            _result_start = date.fromisoformat(str(_result_identity.get("date")))
        except (TypeError, ValueError):
            _result_start = _mdate
        _expected_dates = [(_result_start + _MTD(days=_i)).isoformat() for _i in range(3)]
        _state_labels = {"running":"后台计算中（结果会逐项补充）", "completed":"全部完成",
                         "partial":"部分完成（未完成项见失败详情）"}
        st.caption(f"来源：{_display_origin} · "
                   f"进度 {_display.get('completed', 0)}/{_display.get('total', 18)} · "
                   f"状态：{_state_labels.get(str(_display.get('state')), _display.get('state', '未知'))} · "
                   f"数据获得时间：{_display.get('obtained_at', '未知')}")
        st.caption("表格列与自动更新的多日概览一致：常规概率为未经实况校准的经验估计，火烧云规模为可着色中高云占比。")
        _render_manual_overview_result(_display, _expected_dates)
        if _display.get("errors"):
            with st.expander(f"失败详情（{len(_display['errors'])} 项）"):
                st.text("\n".join(_display["errors"]))
    st.stop()


if run:
    engine.messagebox.last_error = None
    engine.messagebox.last_info = None
    values = {
        "place": place,
        "date": forecast_date.isoformat(),
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "elevation": f"{elevation:.0f}",
        "weather_model": model_label,
        "cloud_base_mode": cloud_base_mode,
        "aod_source": aod_source,
        "morning_cloud_base_km": f"{morning_cloud_base:.2f}",
        "evening_cloud_base_km": f"{evening_cloud_base:.2f}",
        "morning_aod": morning_aod_text.strip(),
        "evening_aod": evening_aod_text.strip(),
    }
    runner = WebForecastEngine(values)
    _direction_text = "朝霞与晚霞" if query_direction == "all" else ("朝霞" if query_direction == "morning" else "晚霞")
    _query_dir = "both" if query_direction == "all" else query_direction
    _case_state = st.session_state.get("case_bundle_state")
    if use_case_cache:
        _expected_model = {"ecmwf_ifs025": "EC", "ncep_gfs_global": "GFS", "icon_global": "ICON", "ecmwf_aifs025_single": "AIFS", "cma_grapes_global": "CMA_GRAPES"}.get(engine._weather_model_id_from_label(model_label), "EC")
        _dirs = set((_case_state or {}).get("direction_results", {}).keys())
        _required_dirs = {"morning", "evening"} if _query_dir == "both" else {_query_dir}
        _missing_dirs = _required_dirs - _dirs
        if not _case_state or _case_state.get("date") != forecast_date.isoformat() or _case_state.get("model_code") != _expected_model or _missing_dirs:
            _why = []
            if not _case_state: _why.append("未加载案例")
            elif _case_state.get("date") != forecast_date.isoformat(): _why.append(f"案例日期为 {_case_state.get('date')}，当前为 {forecast_date.isoformat()}")
            if _case_state and _case_state.get("model_code") != _expected_model: _why.append(f"案例模式为 {_case_state.get('model_code')}，当前为 {_expected_model}")
            if _missing_dirs: _why.append("案例缺少方向：" + "、".join("朝霞" if x == "morning" else "晚霞" for x in _missing_dirs))
            st.error("案例数据不匹配，已停止查询：" + "；".join(_why) + "。请加载正确案例或取消缓存勾选。")
            st.stop()
        engine._CASE_REQUEST_CONTEXT = {"mode": "replay", "cache": st.session_state.get("case_bundle_cache") or {}}
    else:
        # A new online query captures newly requested data. Previously uploaded
        # case payloads are only for explicit offline replay, never online mode.
        _web_case_cache = {}
        engine._CASE_REQUEST_CONTEXT = {"mode": "capture", "cache": _web_case_cache}
    try:
        with st.spinner(f"{'正在读取案例缓存' if use_case_cache else '正在获取天气、云层剖面与 CAMS AOD'}，并计算{_direction_text}……"):
            # The shared engine now splits combined queries identically for
            # desktop and web, including merged rainbow results.
            runner._fetch_weather_task(_query_dir)
    finally:
        engine._CASE_REQUEST_CONTEXT = None
    if engine.messagebox.last_error:
        st.error(f"{engine.messagebox.last_error[0]}：{engine.messagebox.last_error[1]}")
    elif not runner.direction_results:
        st.error("没有获得预报结果。请检查日期、坐标、天气接口状态后重试。")
    else:
        _chosen_direction='evening' if query_direction=='evening' else 'morning'
        _rb_main=(getattr(runner,'rainbow_day_result',None) if query_direction=='all' else None) or (runner.direction_results.get(_chosen_direction) or {}).get('rainbow')
        # Fire-cloud query results are rendered below in the single-mode panel.
        # Do not activate the standalone rainbow panel as a side effect.
        import copy
        st.session_state['fire_cloud_original_results'] = copy.deepcopy(runner.direction_results)
        st.session_state['fire_cloud_replay_values'] = dict(values)
        st.session_state['fire_cloud_replay_cache'] = dict(_web_case_cache if not use_case_cache else (st.session_state.get('case_bundle_cache') or {}))
        st.session_state['cloud_manual_override_text'] = ''
        st.session_state["fire_cloud_last_results"] = runner.direction_results
        st.session_state["fire_cloud_last_report"] = runner.last_report
        st.session_state["fire_cloud_last_meta"] = {
            "place": place, "date": forecast_date.isoformat(), "model": model_label,
            "lat": lat, "lon": lon, "elevation": elevation, "fetched_at": runner.last_fetch_time,
            "query_direction": query_direction,
        }
        st.success("查询完成。")
        _combined_results = dict((_case_state or {}).get("direction_results", {}))
        _combined_results.update(runner.direction_results)
        _case_state_out = {
                "schema_version": 1, "app_version": "Burning Sky", "saved_at": __import__("datetime").datetime.now().astimezone().isoformat(),
                "place": place, "date": forecast_date.isoformat(), "latitude": f"{lat:.6f}", "longitude": f"{lon:.6f}", "elevation_m": elevation,
                "weather_model": model_label, "model_code": {"ecmwf_ifs025": "EC", "ncep_gfs_global": "GFS", "icon_global": "ICON", "ecmwf_aifs025_single": "AIFS", "cma_grapes_global": "CMA_GRAPES"}.get(engine._weather_model_id_from_label(model_label), "EC"),
                "cloud_base_mode": cloud_base_mode, "aod_inputs": {"morning_aod": morning_aod_text, "evening_aod": evening_aod_text},
                "cloud_base_inputs": {"morning_cloud_base_km": morning_cloud_base, "evening_cloud_base_km": evening_cloud_base},
                "last_fetch_time": runner.last_fetch_time or (_case_state or {}).get("last_fetch_time"), "direction_results": engine.FireCloudApp._json_safe({k: {kk: vv for kk, vv in v.items() if kk != "raw_weather"} for k, v in _combined_results.items()}),
                "last_report": runner.last_report or (_case_state or {}).get("last_report", ""),
            }
        _bundle = io.BytesIO()
        with zipfile.ZipFile(_bundle, "w", zipfile.ZIP_DEFLATED) as _zf:
            _zf.writestr("analysis_state.json", json.dumps(_case_state_out, ensure_ascii=False, indent=2))
            _zf.writestr("request_cache.json", json.dumps(engine.FireCloudApp._json_safe(_web_case_cache if not use_case_cache else (st.session_state.get("case_bundle_cache") or {})), ensure_ascii=False))
            _zf.writestr("weather_raw.json", json.dumps(engine.FireCloudApp._json_safe({k: {"raw_weather": v.get("raw_weather", []), "samples": v.get("samples", []), "profiles": v.get("profiles", []), "raw_details": v.get("raw_details", []), "boundaries": v.get("boundaries"), "parallel_2d": v.get("parallel_2d"), "sunset_compass": v.get("sunset_compass"), "rainbow": v.get("rainbow")} for k, v in _combined_results.items()}), ensure_ascii=False))
            from burning_sky.case_manifest import write_manifest_zip
            write_manifest_zip(_zf, _case_state_out, {
                "cloud_base_mode": cloud_base_mode,
                "cloud_base_inputs": _case_state_out["cloud_base_inputs"],
                "aod_inputs": _case_state_out["aod_inputs"],
                "query_direction": query_direction,
                "manual_cloud_override": st.session_state.get('cloud_manual_override_text', ''),
                "effective_model": model_label,
                "local_cloud_geometry": "continuous_face_strict",
                "primary_spherical_path_gate": True,
            })
        st.download_button("保存当前查询案例 ZIP", data=_bundle.getvalue(), file_name=f"BurningSky_{forecast_date.isoformat()}_{place}_{_case_state_out['model_code']}.zip", mime="application/zip")
        _archive_note = st.text_input("案例备注（可选）", key="archive_note_current")
        if st.button("保存到网页案例库", key="save_case_in_library"):
            try:
                _entry = _case_library.save(_bundle.getvalue(), note=_archive_note)
                st.success(f"已保存案例ID：{_entry['id'][:12]}；请在上方案例库下载或管理。")
            except Exception as _archive_exc:
                st.error(f"案例存档失败：{_archive_exc}")


def _styled_cloud_summary(summary):
    """Display-only styling: emphasize mid/high cloud percentages without changing values."""
    import html
    text = html.escape(str(summary or ""))
    text = re.sub(r"(?<!\S)(中|高)\s*(\d+(?:\.\d+)?%)",
                  r'<span style="color:#c62828;font-weight:800;">\1 \2</span>', text)
    return text.replace("\\n", "<br>")


def _probability_label(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "暂不可判定"
    if value < 45:
        return "小概率"
    if value <= 75:
        return "有概率"
    return "高概率"

def _color_label(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "暂不可判定"
    if value < 30:
        return "平平无奇"
    if value < 50:
        return "普通"
    if value < 70:
        return "绚烂"
    if value < 90:
        return "史诗"
    return "世纪"

def _burn_label(inferred, summary):
    """Classify cloud scale from actual mid/high cloud cover × tintable ratio."""
    value = inferred.get("burn_scale_index")
    label = inferred.get("burn_scale_label")
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        value, label = engine.calculate_burn_scale(
            inferred.get("mid"), inferred.get("high"), inferred.get("tintable_ratio"))
    return (label or "光路待核验"), value

def _path_sampling_table(details):
    """Extract only the along-path sampling rows from the legacy detailed report."""
    columns = [
        "距离", "中心坐标", "总云/低云/中云/高云", "能见度", "风速",
        "云底绝对海拔", "有效云底绝对海拔", "理论深入距离", "几何筛选",
    ]
    rows = []
    in_table = False
    for line in str(details or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("距离 | 中心坐标 | 总云/低云/中云/高云"):
            in_table = True
            continue
        if not in_table:
            continue
        if not stripped or " | " not in stripped:
            if rows:
                break
            continue
        parts = [part.strip() for part in stripped.split("|")]
        if len(parts) == len(columns) and parts[0].endswith("km"):
            rows.append(parts)
        elif rows:
            break
    return pd.DataFrame(rows, columns=columns)


def _web_profile_figure(result):
    """Render the same spherical-ray cross section as the desktop view."""
    from burning_sky.profile_image import render_profile
    return render_profile(result)


def _web_gap_text(result):
    inferred=result.get("inferred") or {}
    boundaries=result.get("boundaries") or {}
    gaps=boundaries.get("observed_clear_gaps") or boundaries.get("clear_gaps") or []
    if not gaps:return "未识别到晴空间隔；球面光路需单独判断"
    label="晴空间隔（云量识别，非受光证明）" if boundaries.get("observed_clear_gaps") else "云洞"
    spans=[]
    for g in gaps:
        try:spans.append(f"{float(g['start_km']):.0f}–{float(g['end_km']):.0f} km（{float(g['size_km']):.0f} km）")
        except (KeyError,TypeError,ValueError):continue
    return label+"："+"；".join(spans) if spans else "可能不透光"

# Manual cloud correction: replay exact cached requests, never request weather again.
if st.session_state.get('fire_cloud_original_results'):
    with st.expander('☁️ 云图人工修正（不联网重新计算）', expanded=False):
        st.caption('每行：起点km,终点km,低云%,中云%,高云%。可填多行；云量留空表示保留原值。示例：100,175,0,10,0。清空后点击重新计算恢复原始结果。')
        _cloud_text=st.text_area('距离区间与云量',key='cloud_manual_override_text',height=100)
        if st.button('重新计算云图',key='cloud_manual_recompute'):
            import copy
            from burning_sky.cloud_overrides import parse_overrides
            try:
                _cloud_rows=parse_overrides(_cloud_text)
                if not _cloud_rows:
                    st.session_state['fire_cloud_last_results']=copy.deepcopy(st.session_state['fire_cloud_original_results'])
                    st.success('已恢复原始结果，无 API 请求。')
                else:
                    _cache=st.session_state.get('fire_cloud_replay_cache') or {}
                    _values=st.session_state.get('fire_cloud_replay_values')
                    if not _cache or not _values:
                        raise ValueError('没有完整的原始请求缓存，请重新查询一次并保留当前会话。')
                    _replay=WebForecastEngine(_values)
                    _prior=engine._CASE_REQUEST_CONTEXT
                    engine._CASE_REQUEST_CONTEXT={'mode':'replay','cache':_cache,'cloud_overrides':_cloud_rows}
                    try:
                        for _direction in st.session_state['fire_cloud_original_results']:
                            if _direction in ('morning','evening'):
                                _replay._fetch_weather_task(_direction)
                    finally:
                        engine._CASE_REQUEST_CONTEXT=_prior
                    if not _replay.direction_results:
                        raise RuntimeError('离线重新计算没有返回结果。')
                    st.session_state['fire_cloud_last_results']=_replay.direction_results
                    st.success('已同步修正采样云量和气压层剖面，并重新计算球面光路（无 API 请求）。')
                st.rerun()
            except Exception as _exc:
                st.error(f'人工云图修正失败：{_exc}')

results = st.session_state.get("fire_cloud_last_results")
meta = st.session_state.get("fire_cloud_last_meta", {})
if results:
    st.caption(f"最近一次结果：{meta.get('place','')} · {meta.get('date','')} · {meta.get('model','')} · {meta.get('fetched_at') or '时间信息不可用'}")
    # Export the *current* replayed case, including manual cloud edits and full cached responses.
    _current_cache = st.session_state.get('fire_cloud_replay_cache') or {}
    _current_values = st.session_state.get('fire_cloud_replay_values') or {}
    if _current_cache and _current_values:
        _state_now = {
            'schema_version': 3, 'app_version': 'Burning Sky',
            'saved_at': __import__('datetime').datetime.now().astimezone().isoformat(),
            'place': meta.get('place'), 'date': meta.get('date'),
            'latitude': meta.get('lat'), 'longitude': meta.get('lon'),
            'elevation_m': meta.get('elevation'), 'weather_model': meta.get('model'),
            'model_code': {'ecmwf_ifs025':'EC','ncep_gfs_global':'GFS','icon_global':'ICON',
                'ecmwf_aifs025_single':'AIFS','cma_grapes_global':'CMA_GRAPES'}.get(
                engine._weather_model_id_from_label(meta.get('model','')), 'EC'),
            'direction_results': engine.FireCloudApp._json_safe({k:{kk:vv for kk,vv in v.items() if kk!='raw_weather'}
                for k,v in results.items()}),
            'last_report': st.session_state.get('fire_cloud_last_report',''),
        }
        _current_zip=io.BytesIO()
        with zipfile.ZipFile(_current_zip,'w',zipfile.ZIP_DEFLATED) as _zf:
            _zf.writestr('analysis_state.json',json.dumps(_state_now,ensure_ascii=False,indent=2))
            _zf.writestr('request_cache.json',json.dumps(engine.FireCloudApp._json_safe(_current_cache),ensure_ascii=False))
            _zf.writestr('weather_raw.json',json.dumps(engine.FireCloudApp._json_safe({k:{
                'raw_weather':v.get('raw_weather',[]),'samples':v.get('samples',[]),
                'profiles':v.get('profiles',[]),'raw_details':v.get('raw_details',[]),
                'boundaries':v.get('boundaries'),'rainbow':v.get('rainbow'),
                'parallel_2d':v.get('parallel_2d')} for k,v in results.items()}),ensure_ascii=False))
            from burning_sky.case_manifest import write_manifest_zip
            from burning_sky.cloud_overrides import parse_overrides
            _manual_text=st.session_state.get('cloud_manual_override_text','')
            try: _manual_rows=parse_overrides(_manual_text)
            except (ValueError,TypeError): _manual_rows=[]
            write_manifest_zip(_zf,_state_now,{
                'request_inputs':_current_values,'manual_cloud_override_text':_manual_text,
                'manual_cloud_override_rows':_manual_rows,
                'primary_spherical_path_gate':True,'local_cloud_geometry':'continuous_face_strict'})
        st.download_button('保存完整当前案例 ZIP（含人工修正）',data=_current_zip.getvalue(),
            file_name=f"BurningSky_Repro_{meta.get('date','case')}_{_state_now['model_code']}.zip",
            mime='application/zip',key='download_complete_case')
        if st.button('存档当前人工修正案例到网页库', key='archive_recalculated_case'):
            try:
                _entry = _case_library.save(_current_zip.getvalue(), note='含人工修正')
                st.success('已存档：' + _entry['id'][:12])
            except Exception as _archive_exc:
                st.error('存档失败：' + str(_archive_exc))

    else:
        st.warning('当前缺少完整请求缓存，不能导出可保证离线复测的案例。')

    visible_direction = meta.get("query_direction", "all")
    directions = [("morning", "🌄 朝霞"), ("evening", "🌆 晚霞")]
    if visible_direction in ("morning", "evening"):
        directions = [item for item in directions if item[0] == visible_direction]
    columns = st.columns(len(directions))
    for col, (key, title) in zip(columns, directions):
        result = results.get(key, {})
        # The shared engine already applies all probability and special-verdict
        # rules (including strict geometry gates). Keep its result authoritative.
        # Do NOT infer cloud-base/low-cloud thresholds from rounded summary text,
        # and never mutate cached calculation results while rendering the page.
        inferred = result.get("inferred") or {}
        with col:
            st.subheader(title)
            c1, c2, c3 = st.columns(3)
            regular_score = inferred.get("regular_score", inferred.get("score", "—"))
            upper_score = inferred.get("upper_score", "—")
            color_score = inferred.get("color_score", "—")
            c1.metric("常规概率", f"{regular_score}%")
            c2.metric("上限概率", f"{upper_score}%")
            c3.metric("色彩评分", f"{color_score}/100")
            st.caption('概率评价：低于45% 小概率；45%–75% 有概率；高于75% 高概率。概率为未经实况校准的经验估计。')
            st.caption('色彩评价：低于30 平平无奇；30–不足50 普通；50–不足70 绚烂；70–不足90 史诗；90–100 世纪。')
            st.caption('规模评价：0% 不烧；大于0%–不足10% 微烧；10–不足30% 小烧；30–不足60% 中烧；60–100% 大烧。')
            burn_label, burn_index = _burn_label(inferred, result.get("summary", ""))
            regular_label = _probability_label(regular_score)
            color_class = _color_label(color_score)
            burn_text = burn_label
            color_palette = {"平平无奇": "#e2c76a", "普通": "#c99b24", "绚烂": "#e67e22", "史诗": "#d62828", "世纪": "#9d174d"}
            color_hex = color_palette.get(color_class, "#4b5563")
            special_verdict = inferred.get("verdict_override")
            if burn_index is not None and burn_index <= 0 and special_verdict not in ('光板天', '云底过低', '低云遮挡过多', '光线难以穿透', '云量不足'):
                special_verdict = '不烧'
            if special_verdict:
                st.markdown(
                    f'<div style="font-size:24px;font-weight:800;line-height:1.5;margin:6px 0 2px 0;color:#111111;">'
                    f'{special_verdict}</div>', unsafe_allow_html=True)
            else:
                st.markdown(
                    f'<div style="font-size:24px;font-weight:800;line-height:1.5;margin:6px 0 2px 0;">'
                    f'{regular_label} <span style="color:{color_hex};">{color_class}</span>的{burn_text}</div>',
                    unsafe_allow_html=True)
            # Full one-line interpretation under the short verdict.
            if not special_verdict:
                _scale_value = f'{burn_index:.1f}%' if isinstance(burn_index,(int,float)) else '暂不可估'
                st.markdown(f'预计{regular_label}{color_class}的{burn_text}；常规概率 {regular_score}%，色彩分 {color_score}，火烧云规模 {_scale_value}。')
            # The detailed original summary follows the short verdict.
            if result.get('summary'):
                for _line in _web_summary_lines(result['summary']):
                    if _line.startswith('视野内低云'):
                        st.markdown('<div style="font-weight:800;color:#c62828;">'+html.escape(_line)+'</div>', unsafe_allow_html=True)
                    else:
                        st.markdown(_styled_cloud_summary(_line), unsafe_allow_html=True)
            _low = inferred.get('visible_low_cloud_pct')
            if _low is not None and '视野内低云' not in str(result.get('summary','')):
                st.markdown(f'<div style="font-weight:800;color:#c62828;">视野内低云 {_low:.1f}%，以上结果不考虑低云影响可见性。</div>',unsafe_allow_html=True)
            rgb = inferred.get("peak_color_rgb") or inferred.get("color_rgb")
            if isinstance(rgb, (tuple, list)) and len(rgb) >= 3:
                hex_color = "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb[:3])
                st.markdown(f"**峰值云色倾向：** `{hex_color}` · {inferred.get('peak_color_name', inferred.get('color_name', '示意色'))}")
                st.markdown(f'<div style="height:34px;border-radius:8px;background:{hex_color};border:1px solid #888"></div>', unsafe_allow_html=True)
            rainbow = result.get("rainbow", {})
            if rainbow:
                st.markdown('**🌈 彩虹分析**')
                _rainbow_sunset_overlap=False
                if key == 'evening':
                    try:
                        from datetime import datetime, timedelta, timezone
                        _result_date = __import__("datetime").date.fromisoformat(meta["date"])
                        _sunset_dt,_=engine.find_sunset(_result_date,8,float(meta["lat"]),float(meta["lon"]))
                        _sunset_utc=(_sunset_dt-timedelta(hours=8)).replace(tzinfo=timezone.utc)
                        _rainbow_sunset_overlap=any(
                            float(_rr.get('primary_index') or 0)>30 and
                            abs((datetime.fromisoformat(str(_rr['time']).replace('Z','+00:00')).replace(tzinfo=timezone.utc)
                                 if datetime.fromisoformat(str(_rr['time']).replace('Z','+00:00')).tzinfo is None
                                 else datetime.fromisoformat(str(_rr['time']).replace('Z','+00:00'))) - _sunset_utc).total_seconds()<=2700
                            for _rr in rainbow.get('timeline',[]) or [])
                    except (ValueError,TypeError,KeyError,AttributeError):pass
                _web_rainbow_panel(rainbow,peak_color_rgb=rgb,sunset_context=_rainbow_sunset_overlap)
            curve = result.get("dynamic_light_curve", {})
            points = curve.get("samples", []) if isinstance(curve, dict) else []
            from burning_sky.cloud_genus import identify_cloud_genera
            cloud_genera = identify_cloud_genera(result.get("profiles", []))
            st.markdown("**预测云属（实验识别）**")
            if cloud_genera:
                counts = {"低云": 0, "中云": 0, "高云": 0}
                for item in cloud_genera:
                    band = item["layer"]
                    counts[band] += 1
                    suffix = str(counts[band]) if counts[band] > 1 else ""
                    st.markdown(f"{band}云属{suffix}：**{item['genus']} · 云量约 {item['cloud_cover']}%**")
                st.caption("百分比为对应气压层的平均预测云量近似值，不是云属独立覆盖率；无法可靠拆分的云属合并显示，暂未加入温度、露点和稳定度判据。")
            else:
                st.caption("当前气压层资料不足，暂无法识别云属。")
            st.markdown("**" + _web_gap_text(result) + "**")
            # Independent 360° sampled-light panel; no traditional cloud-hole prerequisite.
            from burning_sky.compass360_web import render_web_panel
            with st.expander("360°八方位受光补全图（替代二维霞云图）", expanded=True):
                render_web_panel(st, engine, result, meta, key, replay_only=bool(use_case_cache))
            _profile_fig = _web_profile_figure(result)
            if _profile_fig is not None:
                st.image(_profile_fig, use_container_width=True)
            with st.expander("旧版二维霞云 · 低中高云空间分布（已由八方位补全图替代）", expanded=False):
                st.caption("当前建议优先查看上方八方位补全图；此旧版二维图保留作对照。")
                from burning_sky.web_cloud_2d import render_cloud_2d
                _cloud_layer = st.selectbox("二维云图图层", ["综合", "低云", "中云", "高云"], key=f"cloud2d_{key}")
                _map_image, _map_note = render_cloud_2d(result, _cloud_layer)
                if _map_image is not None:
                    st.image(_map_image, use_container_width=True)
                st.caption(_map_note)
            st.subheader("云色时序")
            st.image(_draw_dynamic_color_curve(points, curve.get("peak"), key), use_container_width=True)
            if points:
                df = pd.DataFrame(points)
            else:
                st.caption(curve.get("note", "当前没有可参与动态受光估算的云层；已保留空白坐标框，不会伪造色彩曲线。"))
            with st.expander(f"{title}详细采样与分析报告"):
                path_table = _path_sampling_table(result.get("details", ""))
                if not path_table.empty:
                    st.dataframe(path_table, use_container_width=True, hide_index=True)
                else:
                    st.info("当前结果没有可显示的路径采样数据。")
else:
    st.info("请在左侧设置地点、日期和模式，然后点击查询。")

