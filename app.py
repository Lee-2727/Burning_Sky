# -*- coding: utf-8 -*-
"""Streamlit web wrapper for the v1.4.49 fire-cloud forecast engine."""
from __future__ import annotations

import os
from datetime import date
from types import SimpleNamespace

import pandas as pd
import streamlit as st

import engine

st.set_page_config(page_title="火烧云预报工具 v1.4.49", page_icon="🌇", layout="wide")

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
        st.title("🌇 火烧云预报工具")
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
        self.morning_summary = _DummyLabel()
        self.evening_summary = _DummyLabel()
        self.morning_score_label = _DummyLabel()
        self.evening_score_label = _DummyLabel()
        self.morning_upper_label = _DummyLabel()
        self.evening_upper_label = _DummyLabel()
        self.morning_color_score_label = _DummyLabel()
        self.evening_color_score_label = _DummyLabel()
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

st.title("🌇 火烧云预报工具")
st.caption("网页迁移版 · 计算核心基于 v1.4.49 · 经验评分并非经历史观测校准的统计概率")

with st.expander("使用说明与重要限制", expanded=False):
    st.markdown("""
    - 本页面复用原脚本中的天气采样、云层剖面、CAMS AOD、低云遮挡、评分与动态亮度曲线计算方法。
    - 当前版本默认按 UTC+8 计算地点的日出日落；跨时区地点需要手动核对时区，不能直接把本工具当作全球时区自动识别服务。
    - Open-Meteo 或 CAMS 数据不可用、限流时，查询可能失败；请稍后再试。
    - 评分是模式云况的经验指标，不等于真实火烧云发生概率；动态曲线是相对亮度指数，不是实测辐射亮度。
    - 免费云端服务可能休眠。首次打开或长时间未访问后，启动可能较慢。
    """)

with st.sidebar:
    st.header("预报设置")
    place = st.text_input("地点名称", "合肥")
    forecast_date = st.date_input("预报日期", value=date.today())
    lat = st.number_input("纬度", min_value=-90.0, max_value=90.0, value=31.8047, format="%.4f")
    lon = st.number_input("经度", min_value=-180.0, max_value=180.0, value=117.2214, format="%.4f")
    model_label = st.selectbox("天气预报模式", ["ECMWF IFS 0.25°", "GFS Global"])
    cloud_base_mode = st.selectbox("云底来源", ["自动剖面推算", "手动指定"])
    st.caption("默认时区为 UTC+8。当前网页版本不自动识别任意地点的时区。")
    st.divider()
    st.subheader("朝霞参数")
    morning_cloud_base = st.number_input("朝霞手动云底/回退值（km）", 0.1, 15.0, 2.0, 0.1)
    morning_aod_text = st.text_input("朝霞手动 AOD（留空自动获取）", "")
    st.subheader("晚霞参数")
    evening_cloud_base = st.number_input("晚霞手动云底/回退值（km）", 0.1, 15.0, 2.0, 0.1)
    evening_aod_text = st.text_input("晚霞手动 AOD（留空自动获取）", "")
    run = st.button("查询 / 更新朝霞与晚霞", type="primary", use_container_width=True)

if run:
    engine.messagebox.last_error = None
    engine.messagebox.last_info = None
    values = {
        "place": place,
        "date": forecast_date.isoformat(),
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "weather_model": model_label,
        "cloud_base_mode": cloud_base_mode,
        "morning_cloud_base_km": f"{morning_cloud_base:.2f}",
        "evening_cloud_base_km": f"{evening_cloud_base:.2f}",
        "morning_aod": morning_aod_text.strip(),
        "evening_aod": evening_aod_text.strip(),
    }
    runner = WebForecastEngine(values)
    with st.spinner("正在获取天气、云层剖面与 CAMS AOD，并计算朝霞/晚霞……这可能需要几分钟。"):
        runner._fetch_weather_task()
    if engine.messagebox.last_error:
        st.error(f"{engine.messagebox.last_error[0]}：{engine.messagebox.last_error[1]}")
    elif not runner.direction_results:
        st.error("没有获得预报结果。请检查日期、坐标、天气接口状态后重试。")
    else:
        st.session_state["fire_cloud_last_results"] = runner.direction_results
        st.session_state["fire_cloud_last_report"] = runner.last_report
        st.session_state["fire_cloud_last_meta"] = {
            "place": place, "date": forecast_date.isoformat(), "model": model_label,
            "lat": lat, "lon": lon, "fetched_at": runner.last_fetch_time,
        }
        st.success("查询完成。")

results = st.session_state.get("fire_cloud_last_results")
meta = st.session_state.get("fire_cloud_last_meta", {})
if results:
    st.caption(f"最近一次结果：{meta.get('place','')} · {meta.get('date','')} · {meta.get('model','')} · {meta.get('fetched_at') or '时间信息不可用'}")
    morning, evening = st.columns(2)
    for col, key, title in [(morning, "morning", "🌄 朝霞"), (evening, "evening", "🌆 晚霞")]:
        result = results.get(key, {})
        inferred = result.get("inferred", {})
        with col:
            st.subheader(title)
            if result.get("summary"):
                st.text(result["summary"])
            c1, c2, c3 = st.columns(3)
            c1.metric("常规评分", f"{inferred.get('regular_score', inferred.get('score', '—'))}%")
            c2.metric("上限评分", f"{inferred.get('upper_score', '—')}%")
            c3.metric("色彩评分", f"{inferred.get('color_score', '—')}/100")
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
            if points:
                df = pd.DataFrame(points)
                if "minutes_from_event" in df.columns and "relative_brightness" in df.columns:
                    rel = df[["minutes_from_event", "relative_brightness"]].copy()
                    rel = rel.rename(columns={"minutes_from_event": "相对事件时刻（分钟）", "relative_brightness": "相对亮度（窗口峰值=100）"})
                    st.markdown("**动态相对亮度曲线（本次窗口峰值归一化为 100）**")
                    st.line_chart(rel.set_index("相对事件时刻（分钟）"))
                if "minutes_from_event" in df.columns and "brightness" in df.columns:
                    raw = df[["minutes_from_event", "brightness"]].copy()
                    raw = raw.rename(columns={"minutes_from_event": "相对事件时刻（分钟）", "brightness": "原始亮度经验指数"})
                    with st.expander("查看原始亮度经验指数曲线"):
                        st.line_chart(raw.set_index("相对事件时刻（分钟）"))
                else:
                    st.caption(curve.get("note", "已生成曲线数据；当前网页版本尚未识别其字段用于绘图。"))
                if curve.get("peak"):
                    peak = curve["peak"]
                    st.caption(f"预测峰值时刻：事件前/后 {peak.get('minutes_from_event', 0):+.1f} 分钟；原始亮度指数 {peak.get('brightness', '—')}；相对亮度 {peak.get('relative_brightness', '—')}。")
            with st.expander(f"{title}详细采样与分析报告"):
                st.text(result.get("details", "暂无详细报告"))
                samples = result.get("samples", [])
                if samples:
                    st.dataframe(pd.DataFrame(samples), use_container_width=True, hide_index=True)
    report = st.session_state.get("fire_cloud_last_report", "")
    if report:
        st.download_button("下载完整分析报告（TXT）", report.encode("utf-8-sig"), file_name=f"火烧云分析_{meta.get('date','report')}.txt", mime="text/plain")
else:
    st.info("请在左侧设置地点、日期和模式，然后点击查询。")

st.caption("网页版本初始迁移中：案例库/Excel 实况模拟/全国地图等原有 Tkinter 独立窗口功能尚未迁入本页面；预测核心查询优先。")
