# -*- coding: utf-8 -*-
"""Streamlit web client for the shared Burning Sky v1.4.67 engine."""
from __future__ import annotations

import os
import re
import math
import importlib.util
import sys
import json
import time
import urllib.request
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

def _github_latest_json(relative_path):
    """Fetch the latest public snapshot from the repository raw endpoint (no Streamlit cache)."""
    url = f"https://raw.githubusercontent.com/Lee-2727/Burning_Sky/main/{relative_path}"
    req = urllib.request.Request(url + f"?v={int(time.time())}", headers={"User-Agent": "BurningSky-Web/1.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))

def _latest_overview_snapshot(city, data_dir):
    """Prefer GitHub's newest published JSON; fall back to the deployed local snapshot."""
    for rel in (f"data/overview_{city}.json", "data/overview.json"):
        try:
            payload = _github_latest_json(rel)
            if rel.endswith("overview.json") and isinstance(payload, dict):
                cities = payload.get("cities", {})
                if isinstance(cities, dict) and city in cities:
                    return cities[city]
                if city == "合肥" and payload.get("days"):
                    return payload
            elif isinstance(payload, dict) and payload.get("days"):
                return payload
        except Exception:
            pass
    for rel in (f"overview_{city}.json", "overview.json"):
        try:
            with open(os.path.join(data_dir, rel), "r", encoding="utf-8") as f:
                payload = json.load(f)
            if rel == "overview.json" and isinstance(payload, dict):
                cities = payload.get("cities", {})
                if isinstance(cities, dict) and city in cities:
                    return cities[city]
                if city == "合肥" and payload.get("days"):
                    return payload
            elif isinstance(payload, dict) and payload.get("days"):
                return payload
        except Exception:
            pass
    return None

def _latest_status_snapshot(data_dir):
    try:
        return _github_latest_json("data/overview_status.json")
    except Exception:
        try:
            with open(os.path.join(data_dir, "overview_status.json"), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

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
    def _set_main_progress(self, percent, status=None):
        pass
    @staticmethod
    def _set_text(widget, value):
        pass
    def _draw_peak_color_swatch(self, direction_key):
        pass
    def _draw_colorbar(self, direction_key):
        pass


def _draw_dynamic_color_curve(samples, peak=None, direction_key="evening"):
    """Draw the model's per-sample RGB curve and color strip for the web UI.

    This is display-only: it uses the existing samples without changing forecast
    calculations, scores, brightness, or RGB values.
    """
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1400, 560
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        tiny = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except Exception:
        font = small = tiny = ImageFont.load_default()

    left, right, top, bottom = 78, W - 28, 34, 385
    def x_at(i):
        return left + (right - left) * i / max(1, len(samples) - 1)
    def y_at(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        return bottom - (bottom - top) * max(0.0, min(100.0, v)) / 100.0
    def rgb_of(point):
        c = point.get("color", (190, 125, 105))
        if not isinstance(c, (list, tuple)) or len(c) < 3:
            c = (190, 125, 105)
        return tuple(max(0, min(255, int(v))) for v in c[:3])

    # Grid and Y axis labels.
    for val in (0, 20, 40, 60, 80, 100):
        y = y_at(val)
        d.line((left, y, right, y), fill=(220, 226, 234), width=1)
        d.text((left - 12, y - 8), str(val), font=small, fill=(71, 85, 105), anchor="ra")
    d.line((left, top, left, bottom), fill=(100, 116, 139), width=2)
    d.line((left, bottom, right, bottom), fill=(100, 116, 139), width=2)

    if samples:
        # Use each sample's own estimated RGB for its segment, matching the desktop UI.
        for i in range(len(samples) - 1):
            x0, x1 = x_at(i), x_at(i + 1)
            y0 = y_at(samples[i].get("relative_brightness", 0))
            y1 = y_at(samples[i + 1].get("relative_brightness", 0))
            c = rgb_of(samples[i])
            fill = tuple(int(255 * 0.08 + c[k] * 0.92) for k in range(3))
            d.polygon([(x0, bottom), (x0, y0), (x1, y1), (x1, bottom)], fill=fill)
            d.line((x0, y0, x1, y1), fill=c, width=5)
        # Cover tiny seams between differently colored line segments.
        for i, point in enumerate(samples):
            x, y = x_at(i), y_at(point.get("relative_brightness", 0))
            c = rgb_of(point)
            d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=c, outline=c)

        # Sunrise/sunset event marker and evenly spaced local-time labels.
        event_i = min(range(len(samples)), key=lambda i: abs(float(samples[i].get("minutes_from_event", 999999))))
        event_x = x_at(event_i)
        d.line((event_x, top, event_x, bottom), fill=(100, 116, 139), width=1)
        for tick in range(7):
            i = round((len(samples) - 1) * tick / 6)
            x = x_at(i)
            d.line((x, bottom, x, bottom + 7), fill=(150, 160, 175), width=1)
            dt = samples[i].get("datetime")
            label = dt.strftime("%H:%M") if hasattr(dt, "strftime") else str(samples[i].get("minutes_from_event", ""))
            if i == event_i:
                label += " SUNRISE" if direction_key == "morning" else " SUNSET"
            d.text((x, bottom + 12), label, font=tiny, fill=(51, 65, 85), anchor="ma")

        if peak and isinstance(peak, dict):
            try:
                peak_dt = peak.get("datetime")
                if peak_dt is not None and hasattr(peak_dt, "timestamp"):
                    pi = min(range(len(samples)), key=lambda i: abs((samples[i]["datetime"] - peak_dt).total_seconds()))
                else:
                    pi = min(range(len(samples)), key=lambda i: abs(float(samples[i].get("minutes_from_event", 0)) - float(peak.get("minutes_from_event", 0))))
                px, py = x_at(pi), y_at(samples[pi].get("relative_brightness", 0))
                d.ellipse((px - 7, py - 7, px + 7, py + 7), fill=(255, 255, 255), outline=rgb_of(samples[pi]), width=3)
            except Exception:
                pass
    else:
        # Empty-but-valid forecast: keep the axes and zero baseline visible.
        d.line((left, bottom, right, bottom), fill=(148, 163, 184), width=2)

    # The strip follows the exact sample RGB sequence. With no samples, show a neutral placeholder.
    strip_y0, strip_y1 = 450, 478
    if samples:
        for i, point in enumerate(samples):
            xa = int(x_at(i))
            xb = int(x_at(i + 1)) if i + 1 < len(samples) else right
            d.rectangle((xa, strip_y0, max(xa + 1, xb), strip_y1), fill=rgb_of(point))
    else:
        d.rectangle((left, strip_y0, right, strip_y1), fill=(235, 235, 235))
    d.text((left, 492), "Estimated color by time (model RGB; no extra saturation enhancement)", font=small, fill=(71, 85, 105))
    d.text((left, 525), "Relative brightness (window peak = 100)", font=small, fill=(71, 85, 105))
    return img

st.title("🌇 Burning Sky")
# 仅为“未来多日概览”入口设置低饱和蓝色，不影响其他按钮。
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
</style>""", unsafe_allow_html=True)
with st.sidebar:
    if st.button("未来多日概览", use_container_width=True, key="open_multi_day_overview"):
        st.session_state["show_multi_day_overview"] = True
        st.rerun()
    st.header("预报设置")
    place_query = st.text_input("地点名称（输入前几个字联想）", "合肥", key="place_query")
    # 地名联想只读取仓库同目录 Excel，不访问地理编码网络服务。
    import pandas as pd
    _place_df = pd.DataFrame()
    _xlsx_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "china_city_coordinates.xlsx")
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
    st.subheader("朝霞参数")
    morning_cloud_base = st.number_input("朝霞手动云底/回退值（km）", 0.1, 15.0, 2.0, 0.1)
    morning_aod_text = st.text_input("朝霞手动 AOD（留空自动获取）", "")
    st.subheader("晚霞参数")
    evening_cloud_base = st.number_input("晚霞手动云底/回退值（km）", 0.1, 15.0, 2.0, 0.1)
    evening_aod_text = st.text_input("晚霞手动 AOD（留空自动获取）", "")
    morning_col, evening_col = st.columns(2)
    with morning_col:
        run_morning = st.button("查询朝霞", use_container_width=True)
    with evening_col:
        run_evening = st.button("查询晚霞", use_container_width=True)
    run_all = st.button("查询 / 更新朝霞与晚霞", type="primary", use_container_width=True)
    run = run_morning or run_evening or run_all
    query_direction = "morning" if run_morning else ("evening" if run_evening else "all")

# 在多日概览页面点击左侧任一查询按钮时，优先执行单次查询。
# 清除概览页面状态，避免 st.stop() 截断查询流程，也避免下次 rerun 又跳回概览。
if run and st.session_state.get("show_multi_day_overview"):
    st.session_state["show_multi_day_overview"] = False

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
            _update_status = _latest_status_snapshot(_data_dir)
            if not _update_status:
                raise ValueError("状态文件尚不可用")
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
                st.success(f"✅ {_active_direction}本轮五模式三日数据已完整发布。完成时间：{_update_status.get('completed_at', _update_status.get('updated_at', '未知'))}。下次计划：{_update_status.get('next_update', '按定时任务执行')}。")
            elif _state == "failed":
                st.error(f"❌ {_active_direction}本轮更新失败（{_update_status.get('failure_count', 0)} 项）。已保留上一次完整发布的数据。原因：{_update_status.get('message', '请查看 GitHub Actions 日志')}。")
            else:
                st.caption(f"多模概览后台状态：{_state} · 状态更新时间：{_update_status.get('updated_at', '未知')} · 下次计划：{_update_status.get('next_update', '按定时任务执行')}。")
        except Exception:
            st.caption("后台更新状态尚未发布；等待 GitHub Actions 启动。计划时刻：北京时间每日 00:00/04:00/20:00 更新朝霞，08:00/12:00/16:00 更新晚霞。")
        _city_cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overview_cities.txt")
        _overview_cities = []
        try:
            with open(_city_cfg, "r", encoding="utf-8-sig") as _cf:
                _overview_cities = [line.split(",")[0].strip() for line in _cf if line.strip() and not line.lstrip().startswith("#")]
        except Exception:
            _overview_cities = ["合肥", "深圳"]
        if not _overview_cities:
            _overview_cities = ["合肥", "深圳"]
        _overview_city = st.selectbox("概览城市", _overview_cities, index=0, key="overview_city_select")
        _overview_path = os.path.join(_data_dir, f"overview_{_overview_city}.json")
        _overview = _latest_overview_snapshot(_overview_city, _data_dir)
        if not _overview or not _overview.get("days"):
            st.info(f"尚无{_overview_city}已保存的概览数据。后台定时更新完成后，这里会显示包含今天的未来三日、五种天气模式的朝霞与晚霞结果。")
        else:
            st.caption(f"固定地点：{_overview_city}（{_overview.get('latitude', '—')}, {_overview.get('longitude', '—')}） · 预报范围：包含今天的未来三日 · 五模式。")
            _model_order = ["ECMWF IFS 0.25°", "GFS Global", "ICON Global", "CMA GRAPES Global", "ECMWF (AI)"]
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
                            _fire_score = (_prob / 100.0 * _scale) if isinstance(_prob, (int, float)) and isinstance(_scale, (int, float)) else None
                            _rows.append({
                                "模式": _model_short[_model],
                                "常规概率": (f"{_prob:.0f}%" if isinstance(_prob, (int, float)) else "—"),
                                "色彩分": (f"{_v['color_score']:.0f}" if isinstance(_v.get("color_score"), (int, float)) else "—"),
                                "霞云规模": (f"{_scale:.1f}" if isinstance(_scale, (int, float)) else "—"),
                                "火烧云评分（不考虑色彩）": (f"{_fire_score:.1f}" if _fire_score is not None else "—"),
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
                        _styled = _df.style.map(_fire_score_style, subset=["火烧云评分（不考虑色彩）"])
                        st.dataframe(_styled, use_container_width=True, hide_index=True)
    _render_multi_day_overview()
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
        "morning_cloud_base_km": f"{morning_cloud_base:.2f}",
        "evening_cloud_base_km": f"{evening_cloud_base:.2f}",
        "morning_aod": morning_aod_text.strip(),
        "evening_aod": evening_aod_text.strip(),
    }
    runner = WebForecastEngine(values)
    _direction_text = "朝霞与晚霞" if query_direction == "all" else ("朝霞" if query_direction == "morning" else "晚霞")
    with st.spinner(f"正在获取天气、云层剖面与 CAMS AOD，并计算{_direction_text}……这可能需要几分钟。"):
        runner._fetch_weather_task("both" if query_direction == "all" else query_direction)
    if engine.messagebox.last_error:
        st.error(f"{engine.messagebox.last_error[0]}：{engine.messagebox.last_error[1]}")
    elif not runner.direction_results:
        st.error("没有获得预报结果。请检查日期、坐标、天气接口状态后重试。")
    else:
        st.session_state["fire_cloud_last_results"] = runner.direction_results
        st.session_state["fire_cloud_last_report"] = runner.last_report
        st.session_state["fire_cloud_last_meta"] = {
            "place": place, "date": forecast_date.isoformat(), "model": model_label,
            "lat": lat, "lon": lon, "elevation": elevation, "fetched_at": runner.last_fetch_time,
            "query_direction": query_direction,
        }
        st.success("查询完成。")

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
    """Local 0–200 km scale estimate; missing tintable ratio is treated as zero."""
    local = inferred.get("local_scale_clouds") or {}
    mid, high, low = local.get("mid"), local.get("high"), local.get("low")
    if not all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in (mid, high, low)):
        return "暂不可判定", None

    # If the tintable-cloud ratio cannot be obtained, use 0 rather than blocking the judgment.
    ratio = inferred.get("tintable_ratio")
    if not isinstance(ratio, (int, float)) or not math.isfinite(float(ratio)):
        match = re.search(r"可染色云量占比\s+(?:约\s*)?(\d+(?:\.\d+)?)%", str(summary or ""))
        ratio = float(match.group(1)) if match else 0.0

    local_cloud_sum = float(low) + float(mid) + float(high)
    burn_index = max(0.0, max(float(mid), float(high)) - float(low)) * max(0.0, min(100.0, float(ratio))) / 100.0
    if local_cloud_sum < 10:
        label = "光板天"
    elif burn_index <= 0:
        label = "几乎不烧"
    elif burn_index < 10:
        label = "小小烧"
    elif burn_index < 30:
        label = "微烧"
    elif burn_index < 50:
        label = "小烧"
    elif burn_index < 70:
        label = "中烧"
    else:
        label = "大烧"
    return label, burn_index

def _apply_single_result_special_rules(result):
    """Apply requested single-query probability overrides and judgment text."""
    inferred = result.get("inferred", {})
    # Read local AGL cloud base from the local geometry row (profile-derived value).
    local_geometry = next((r for r in (result.get("geometry", {}) or {}).get("rows", [])
                          if isinstance(r, dict) and r.get("distance_km") == 0), {})
    base_agl_km = local_geometry.get("cloud_base_agl_km")
    if not isinstance(base_agl_km, (int, float)):
        base_agl_km = local_geometry.get("cloud_base_km")
    local = result.get("local_scale_clouds") or {}
    low = local.get("low")
    total = sum(float(local.get(k) or 0) for k in ("low", "mid", "high")
                if isinstance(local.get(k), (int, float)))
    samples = [x for x in result.get("samples", []) if isinstance(x, dict)]
    reach = result.get("max_reach")
    if not isinstance(reach, (int, float)) or reach <= 0:
        reach = (result.get("geometry", {}) or {}).get("reach_km")
    relevant = [x for x in samples if isinstance(x.get("distance_km"), (int, float))
                and (not isinstance(reach, (int, float)) or x.get("distance_km") <= reach)]
    cover_values = [x.get("cloud_cover") for x in relevant
                    if isinstance(x.get("cloud_cover"), (int, float))]
    avg_path_cloud = sum(cover_values) / len(cover_values) if cover_values else None
    boundaries = result.get("boundaries", {}) or {}
    has_gap = (boundaries.get("clear_gap_start_km") is not None
               and boundaries.get("clear_gap_end_km") is not None)
    # A cloud-free endpoint inside the modeled limit is also a recognized clear interval.
    endpoint_clear = any(isinstance(x.get("cloud_cover"), (int, float))
                         and x.get("cloud_cover") <= 15 for x in relevant)
    no_clear_gap = not has_gap and not endpoint_clear

    reason = None
    score = inferred.get("regular_score", inferred.get("score"))
    profile_derived = "剖面" in str(local_geometry.get("base_source", ""))
    if profile_derived and isinstance(base_agl_km, (int, float)) and base_agl_km < 0.5:
        score, reason = 0, "云底过底"
    elif isinstance(low, (int, float)) and low >= 90:
        score, reason = 0, "低云遮挡过多"
    elif isinstance(avg_path_cloud, (int, float)) and avg_path_cloud > 50 and no_clear_gap:
        if isinstance(score, (int, float)):
            score = int(round(score * 0.5))
        reason = "光线难以穿透"
    elif isinstance(total, (int, float)) and total < 10:
        reason = "光板天"
    elif isinstance(score, (int, float)) and score < 20:
        reason = "机会太小，几乎不烧"

    if isinstance(score, (int, float)):
        inferred["regular_score"] = int(round(max(0, min(100, score))))
        inferred["score"] = inferred["regular_score"]
    result["special_judgment"] = reason
    result["inferred"] = inferred
    return result

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


results = st.session_state.get("fire_cloud_last_results")
meta = st.session_state.get("fire_cloud_last_meta", {})
if results:
    st.caption(f"最近一次结果：{meta.get('place','')} · {meta.get('date','')} · {meta.get('model','')} · {meta.get('fetched_at') or '时间信息不可用'}")
    visible_direction = meta.get("query_direction", "all")
    directions = [("morning", "🌄 朝霞"), ("evening", "🌆 晚霞")]
    if visible_direction in ("morning", "evening"):
        directions = [item for item in directions if item[0] == visible_direction]
    columns = st.columns(len(directions))
    for col, (key, title) in zip(columns, directions):
        result = _apply_single_result_special_rules(results.get(key, {}))
        inferred = result.get("inferred", {})
        with col:
            st.subheader(title)
            if result.get("summary"):
                st.markdown(_styled_cloud_summary(result["summary"]), unsafe_allow_html=True)
            c1, c2, c3 = st.columns(3)
            regular_score = inferred.get("regular_score", inferred.get("score", "—"))
            upper_score = inferred.get("upper_score", "—")
            color_score = inferred.get("color_score", "—")
            c1.metric("常规概率", f"{regular_score}%")
            c2.metric("上限概率", f"{upper_score}%")
            c3.metric("色彩评分", f"{color_score}/100")
            burn_label, burn_index = _burn_label(inferred, result.get("summary", ""))
            regular_label = _probability_label(regular_score)
            color_class = _color_label(color_score)
            burn_text = burn_label
            special_judgment = result.get("special_judgment")
            color_palette = {"平平无奇": "#e2c76a", "普通": "#c99b24", "绚烂": "#e67e22", "史诗": "#d62828", "世纪": "#9d174d"}
            color_hex = color_palette.get(color_class, "#4b5563")
            if special_judgment:
                judgment_html = f'<span style="color:#111111;">{special_judgment}</span>'
            else:
                judgment_html = f'{regular_label} <span style="color:{color_hex};">{color_class}</span>的{burn_text}'
            st.markdown(
                f'<div style="font-size:24px;font-weight:800;line-height:1.5;margin:6px 0 2px 0;">'
                f'{judgment_html}</div>'
                f'<div style="font-size:11px;font-weight:400;color:#858585;line-height:1.5;margin:0 0 12px 0;">'
                f'常规概率：&lt;45% 小概率，45%–75% 有概率，&gt;75% 高概率；'
                f'色彩评分：&lt;30 平平无奇，30–&lt;50 普通，50–&lt;70 绚烂，70–&lt;90 史诗，≥90 世纪；'
                f'烧云规模：指数&lt;10 小小烧，10–&lt;30 微烧，30–&lt;50 小烧，50–&lt;70 中烧，≥70 大烧；采用本地 200 km 采样，公式为 max(中云,高云)−低云，再乘可染色云量占比。'
                f'</div>', unsafe_allow_html=True)
            rgb = inferred.get("peak_color_rgb") or inferred.get("color_rgb")
            if isinstance(rgb, (tuple, list)) and len(rgb) >= 3:
                hex_color = "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb[:3])
                st.markdown(f"**峰值云色倾向：** `{hex_color}` · {inferred.get('peak_color_name', inferred.get('color_name', '示意色'))}")
                st.markdown(f'<div style="height:34px;border-radius:8px;background:{hex_color};border:1px solid #888"></div>', unsafe_allow_html=True)
            rainbow = result.get("rainbow", {})
            if rainbow:
                st.caption(f"彩虹机会：{rainbow.get('label', '暂不可估')}；这是经验指数，不是统计概率。")
            curve = result.get("dynamic_light_curve", {})
            points = curve.get("samples", []) if isinstance(curve, dict) else []
            st.subheader("云色时序")
            st.markdown("**动态亮度—色彩曲线（曲线颜色与下方色带均使用模型逐分钟估算 RGB）**")
            st.image(_draw_dynamic_color_curve(points, curve.get("peak"), key), use_container_width=True)
            if points:
                df = pd.DataFrame(points)
                if curve.get("peak"):
                    peak = curve["peak"]
                    st.caption(f"预测峰值时刻：事件前/后 {peak.get('minutes_from_event', 0):+.1f} 分钟；原始亮度指数 {peak.get('brightness', '—')}；相对亮度 {peak.get('relative_brightness', '—')}。")
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

