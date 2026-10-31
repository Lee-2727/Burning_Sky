# -*- coding: utf-8 -*-
"""预计算并保存指定城市未来三天朝霞/晚霞多模式概览。"""
from __future__ import annotations

import json
import os
import sys
import time
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
CITY_CONFIG_FILE = ROOT / "overview_cities.txt"

# One model run per city/date. A 60-second gap between model runs is deliberately
# conservative for the public Open-Meteo endpoints, while still reducing the
# previous 75-second debug cadence. Cities are processed sequentially.
REQUEST_INTERVAL_SECONDS = 60

def load_city_config():
    cities = []
    if not CITY_CONFIG_FILE.is_file():
        raise SystemExit(f"Missing city config: {CITY_CONFIG_FILE}")
    for line_no, raw in enumerate(CITY_CONFIG_FILE.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split(",")]
        if len(parts) != 4:
            raise SystemExit(f"{CITY_CONFIG_FILE.name}:{line_no}: expected name,lat,lon,elevation")
        name = parts[0]
        try:
            lat, lon, elev = map(float, parts[1:])
        except ValueError:
            raise SystemExit(f"{CITY_CONFIG_FILE.name}:{line_no}: coordinates/elevation must be numeric")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and -500 <= elev <= 9000):
            raise SystemExit(f"{CITY_CONFIG_FILE.name}:{line_no}: coordinate/elevation out of range")
        if any(c[0] == name for c in cities):
            raise SystemExit(f"{CITY_CONFIG_FILE.name}:{line_no}: duplicate city {name}")
        cities.append((name, lat, lon, elev))
    if not cities:
        raise SystemExit("City config contains no enabled cities")
    return cities

CITY_CONFIG = {name: (lat, lon, elev) for name, lat, lon, elev in load_city_config()}
PLACE = os.environ.get("BURNING_SKY_CITY", "合肥")
if PLACE not in CITY_CONFIG:
    raise SystemExit(f"Unsupported city: {PLACE}; check overview_cities.txt")
LAT, LON, ELEVATION_M = CITY_CONFIG[PLACE]
OUTPUT = ROOT / "data" / f"overview_{PLACE}.json"
HISTORY_DIR = ROOT / "data" / "overview_history" / PLACE

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
    probability = numeric(inferred.get("regular_score", inferred.get("score")))
    burn_scale = numeric(inferred.get("burn_scale_index"))
    # 常规概率以百分数存储；乘以规模时换算为 0–1 概率，评分仍保持 0–100 的直观范围。
    fire_score = (probability / 100.0 * burn_scale
                  if probability is not None and burn_scale is not None else None)
    return {
        "probability": probability,
        "color_score": numeric(inferred.get("color_score")),
        "burn_scale": burn_scale,
        "fire_score_no_color": fire_score,
        "burn_scale_label": inferred.get("burn_scale_label", "暂不可判定"),
        "event_time": ((result or {}).get("event_dt").isoformat()
                       if hasattr((result or {}).get("event_dt"), "isoformat") else None),
    }


def main():
    now = datetime.now(TZ)
    # Three forecast dates including today: today, tomorrow, and the day after.
    dates = [now.date() + timedelta(days=i) for i in range(3)]
    models = list(engine.WEATHER_MODEL_CATALOG)
    if len(models) != 5:
        raise SystemExit(f"Expected five enabled models, found {len(models)}")
    snapshot = {
        "schema_version": 1, "place": PLACE, "latitude": LAT, "longitude": LON,
        "elevation_m": ELEVATION_M, "timezone": "Asia/Shanghai",
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"), "days": [], "model_errors": {},
    }
    old_info, old_error = engine.messagebox.showinfo, engine.messagebox.showerror
    engine.messagebox.showinfo = lambda *a, **k: None
    engine.messagebox.showerror = lambda title, message, **kwargs: setattr(engine.messagebox, "last_error", (str(title), str(message)))
    try:
        request_index = 0
        total_requests = len(dates) * len(models)
        for day in dates:
            day_entry = {"date": day.isoformat(), "morning": {}, "evening": {}}
            for model in models:
                label = model["label"]
                if request_index:
                    print(f"Rate-limit safeguard: sleeping {REQUEST_INTERVAL_SECONDS}s before next model request ({request_index+1}/{total_requests}).", flush=True)
                    time.sleep(REQUEST_INTERVAL_SECONDS)
                runner = _new_runner(day, label)
                engine.messagebox.last_error = None
                print(f"[{datetime.now(TZ):%H:%M:%S}] {PLACE} {day} {label}: starting", flush=True)
                try:
                    runner._fetch_weather_task("both")
                except Exception as exc:
                    engine.messagebox.last_error = ("Forecast error", str(exc))
                if engine.messagebox.last_error:
                    error = str(engine.messagebox.last_error[1])
                    snapshot["model_errors"].setdefault(label, []).append({"date": day.isoformat(), "error": error[:1200]})
                    print(f"ERROR {PLACE} {day} {label}: {error[:1200]}", flush=True)
                for direction in ("morning", "evening"):
                    result = runner.direction_results.get(direction)
                    day_entry[direction][label] = _record(result) if result else {
                        "probability": None, "color_score": None, "burn_scale": None,
                        "burn_scale_label": "暂不可判定",
                        "error": str((engine.messagebox.last_error or [None, "无结果"])[1])[:1200],
                    }
                print(f"[{datetime.now(TZ):%H:%M:%S}] {PLACE} {day} {label}: finished", flush=True)
                request_index += 1
            snapshot["days"].append(day_entry)
    finally:
        engine.messagebox.showinfo, engine.messagebox.showerror = old_info, old_error

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    history_file = HISTORY_DIR / f"{now.date().isoformat()}.json"
    history_tmp = history_file.with_suffix(".json.tmp")
    history_tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    history_tmp.replace(history_file)
    cutoff_date = now.date() - timedelta(days=1)
    for old_file in HISTORY_DIR.glob("????-??-??.json"):
        try:
            if datetime.strptime(old_file.stem, "%Y-%m-%d").date() < cutoff_date:
                old_file.unlink()
        except ValueError:
            continue
    temp = OUTPUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUTPUT)
    success_count = sum(1 for day in snapshot["days"] for direction in ("morning", "evening")
                       for value in day[direction].values() if value.get("probability") is not None)
    expected = len(dates) * len(models) * 2
    print(f"Saved {OUTPUT}; dates={dates[0].isoformat()}..{dates[-1].isoformat()} (3 days incl. today); models=5; successful direction/model records: {success_count}/{expected}", flush=True)
    if success_count == 0:
        raise SystemExit("All models returned no valid results; error snapshot saved.")


if __name__ == "__main__":
    requested_city = os.environ.get("BURNING_SKY_CITY", "").strip()
    if requested_city:
        main()
    else:
        cities = load_city_config()
        print("Configured city order: " + " → ".join(c[0] for c in cities), flush=True)
        aggregate = {"schema_version": 1, "updated_at": datetime.now(TZ).isoformat(timespec="seconds"), "cities": {}}
        for index, (city, _lat, _lon, _elev) in enumerate(cities):
            if index:
                print(f"Rate-limit safeguard: sleeping {REQUEST_INTERVAL_SECONDS}s before next city.", flush=True)
                time.sleep(REQUEST_INTERVAL_SECONDS)
            print(f"\n===== Updating city {index+1}/{len(cities)}: {city} =====", flush=True)
            env = os.environ.copy()
            env["BURNING_SKY_CITY"] = city
            subprocess.run([sys.executable, "-u", str(Path(__file__).resolve())], cwd=str(ROOT), env=env, check=True)
            city_file = ROOT / "data" / f"overview_{city}.json"
            if not city_file.is_file():
                raise SystemExit(f"Missing city snapshot after update: {city_file}")
            city_data = json.loads(city_file.read_text(encoding="utf-8"))
            if len(city_data.get("days", [])) != 3:
                raise SystemExit(f"City snapshot must contain 3 dates: {city_file}")
            aggregate["cities"][city] = city_data
        legacy_output = ROOT / "data" / "overview.json"
        legacy_temp = legacy_output.with_suffix(".json.tmp")
        legacy_temp.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
        legacy_temp.replace(legacy_output)
        print(f"Saved aggregate snapshot {legacy_output}; cities={','.join(aggregate['cities'])}", flush=True)
