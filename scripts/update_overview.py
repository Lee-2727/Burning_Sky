# -*- coding: utf-8 -*-
"""预计算并保存合肥未来三天朝霞/晚霞多模式概览。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
LAT, LON, ELEVATION_M = 31.8047, 117.2214, 30.0
PLACE = "合肥"
OUTPUT = ROOT / "data" / "overview.json"


class _Value:
    def __init__(self, value=""):
        self.value = str(value)
    def get(self):
        return self.value
    def set(self, value):
        self.value = str(value)


class _Widget:
    def configure(self, **kwargs):
        pass


class _HeadlessForecast(engine.FireCloudApp):
    """不创建 Tk 窗口，直接复用现有完整预测计算流程。"""
    def _set_main_progress(self, percent, status=None):
        pass
    @staticmethod
    def _set_text(widget, value):
        pass
    @staticmethod
    def _set_judgment_text(widget, regular_score, color_score, burn_label):
        pass
    def _draw_peak_color_swatch(self, direction_key):
        pass
    def _draw_colorbar(self, direction_key):
        pass


def _new_runner(day, model_label):
    runner = object.__new__(_HeadlessForecast)
    values = {
        "place": PLACE,
        "date": day.isoformat(),
        "lat": f"{LAT:.6f}",
        "lon": f"{LON:.6f}",
        "elevation": f"{ELEVATION_M:.1f}",
        "weather_model": model_label,
        "cloud_base_mode": "自动剖面推算",
        "morning_cloud_base_km": "2.0",
        "evening_cloud_base_km": "2.0",
        "morning_aod": "",
        "evening_aod": "",
    }
    runner.vars = {k: _Value(v) for k, v in values.items()}
    runner.direction_results = {}
    runner.last_report = ""
    runner.last_fetch_time = None
    runner.aod_manual_override = False
    runner._setting_aod_programmatically = False
    runner.web_aod_value = None
    runner.web_aod_valid_time = None
    runner.aod_path_values = []
    runner.cloud_path_samples = []
    runner.cloud_path_result = None
    runner.fetch_in_progress = False
    for name in (
        "morning_summary", "evening_summary", "morning_score_label", "evening_score_label",
        "morning_upper_label", "evening_upper_label", "morning_color_score_label",
        "evening_color_score_label", "morning_judgment_label", "evening_judgment_label",
        "morning_rainbow_label", "evening_rainbow_label",
    ):
        setattr(runner, name, _Widget())
    runner.morning_aod_status_var = _Value("")
    runner.evening_aod_status_var = _Value("")
    return runner


def _record(result):
    inferred = (result or {}).get("inferred", {})
    def numeric(value):
        try:
            value = float(value)
            return value if value == value and abs(value) != float("inf") else None
        except (TypeError, ValueError):
            return None
    return {
        "probability": numeric(inferred.get("regular_score", inferred.get("score"))),
        "color_score": numeric(inferred.get("color_score")),
        "burn_scale": numeric(inferred.get("burn_scale_index")),
        "burn_scale_label": inferred.get("burn_scale_label", "暂不可判定"),
        "event_time": ((result or {}).get("event_dt").isoformat()
                       if hasattr((result or {}).get("event_dt"), "isoformat") else None),
    }


def main():
    now = datetime.now(TZ)
    # GUI 和网页均以北京时间为默认时区；保留未来三个自然日（含今天）。
    dates = [now.date() + timedelta(days=i) for i in range(3)]
    models = [item for item in engine.WEATHER_MODEL_CATALOG]
    snapshot = {
        "schema_version": 1,
        "place": PLACE,
        "latitude": LAT,
        "longitude": LON,
        "elevation_m": ELEVATION_M,
        "timezone": "Asia/Shanghai",
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "days": [],
        "model_errors": {},
    }

    # 屏蔽 Tk 消息框：定时任务在无桌面环境运行，错误写入快照/日志。
    old_info, old_error = engine.messagebox.showinfo, engine.messagebox.showerror
    engine.messagebox.showinfo = lambda *a, **k: None
    engine.messagebox.showerror = lambda title, message, **kwargs: setattr(engine.messagebox, "last_error", (str(title), str(message)))
    try:
        for day in dates:
            day_entry = {"date": day.isoformat(), "morning": {}, "evening": {}}
            for model in models:
                label = model["label"]
                runner = _new_runner(day, label)
                engine.messagebox.last_error = None
                print(f"[{datetime.now(TZ):%H:%M:%S}] {day} {label}: starting", flush=True)
                runner._fetch_weather_task("both")
                if engine.messagebox.last_error:
                    error = engine.messagebox.last_error[1]
                    snapshot["model_errors"].setdefault(label, []).append({"date": day.isoformat(), "error": error[:1200]})
                    print(f"ERROR {day} {label}: {error}", flush=True)
                for direction in ("morning", "evening"):
                    result = runner.direction_results.get(direction)
                    day_entry[direction][label] = _record(result) if result else {
                        "probability": None, "color_score": None, "burn_scale": None,
                        "burn_scale_label": "暂不可判定", "error": (engine.messagebox.last_error or [None, "无结果"])[1][:1200],
                    }
                print(f"[{datetime.now(TZ):%H:%M:%S}] {day} {label}: finished", flush=True)
            snapshot["days"].append(day_entry)
    finally:
        engine.messagebox.showinfo, engine.messagebox.showerror = old_info, old_error

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUTPUT)
    success_count = sum(
        1 for day in snapshot["days"] for direction in ("morning", "evening")
        for value in day[direction].values() if value.get("probability") is not None
    )
    print(f"Saved {OUTPUT}; successful direction/model records: {success_count}/30", flush=True)
    # 部分模式暂时不可用时仍保存其他模式；仅当全部结果为空时让 workflow 标红。
    if success_count == 0:
        raise SystemExit("所有模式均未生成有效结果；已写入错误快照。")


if __name__ == "__main__":
    main()
