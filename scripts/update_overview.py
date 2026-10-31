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

# Requests are processed conservatively within each model batch; each completed
# model batch is published before the next model starts.
REQUEST_INTERVAL_SECONDS = 20

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


def _new_runner(city, day, model_label):
    lat, lon, elevation_m = CITY_CONFIG[city]
    runner = object.__new__(_HeadlessForecast)
    values = {
        "place": city, "date": day.isoformat(), "lat": f"{lat:.6f}", "lon": f"{lon:.6f}",
        "elevation": f"{elevation_m:.1f}", "weather_model": model_label,
        "cloud_base_mode": "自动剖面推算", "morning_cloud_base_km": "2.0",
        "evening_cloud_base_km": "2.0", "morning_aod": "", "evening_aod": "",
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


def _write_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _publish_model_batch(model_label, status):
    """Publish each completed model batch immediately from GitHub Actions."""
    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        return
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=ROOT, check=True)
        subprocess.run(["git", "add", "-f", "data/overview.json", "data/overview_*.json", "data/overview_status.json", "data/overview_history/"], cwd=ROOT, check=True)
        changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0
        if changed:
            subprocess.run(["git", "commit", "-m", f"chore: publish {model_label} overview batch"], cwd=ROOT, check=True)
            subprocess.run(["git", "push", "origin", f"HEAD:{branch}"], cwd=ROOT, check=True)
    except Exception as exc:
        print(f"WARNING: incremental publish failed for {model_label}: {exc}", flush=True)
        status["publish_error"] = str(exc)[:800]
        _write_json_atomic(ROOT / "data" / "overview_status.json", status)


def main():
    now = datetime.now(TZ)
    dates = [now.date() + timedelta(days=i) for i in range(3)]
    cities = list(CITY_CONFIG)
    models = list(engine.WEATHER_MODEL_CATALOG)
    if len(models) != 5:
        raise SystemExit(f"Expected five enabled models, found {len(models)}")

    # Build per-city snapshots, preserving existing records until a model is refreshed.
    snapshots = {}
    for city, (lat, lon, elev) in CITY_CONFIG.items():
        path = ROOT / "data" / f"overview_{city}.json"
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if [d.get("date") for d in old.get("days", [])] == [d.isoformat() for d in dates]:
                snapshots[city] = old
                continue
        except Exception:
            pass
        snapshots[city] = {
            "schema_version": 1, "place": city, "latitude": lat, "longitude": lon,
            "elevation_m": elev, "timezone": "Asia/Shanghai", "updated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "days": [{"date": d.isoformat(), "morning": {}, "evening": {}} for d in dates], "model_errors": {},
        }

    status = {
        "schema_version": 1, "state": "running", "started_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"), "current_model": None,
        "model_index": 0, "model_total": len(models), "completed_models": [],
        "cities": cities, "city_progress": 0, "next_update": "每日 00:30、03:00、06:00、09:00、12:00、15:00、18:00、21:00（北京时间）",
    }
    _write_json_atomic(ROOT / "data" / "overview_status.json", status)
    old_info, old_error = engine.messagebox.showinfo, engine.messagebox.showerror
    engine.messagebox.showinfo = lambda *a, **k: None
    engine.messagebox.showerror = lambda title, message, **kwargs: setattr(engine.messagebox, "last_error", (str(title), str(message)))
    try:
        for model_index, model in enumerate(models, 1):
            label = model["label"]
            status.update(state="running", current_model=label, model_index=model_index,
                         city_progress=0, updated_at=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z"))
            _write_json_atomic(ROOT / "data" / "overview_status.json", status)
            _publish_model_batch(f"start-{label}", status)
            print(f"\n===== MODEL BATCH {model_index}/{len(models)}: {label} =====", flush=True)
            task_count = len(cities) * len(dates)
            task_index = 0
            for city in cities:
                snapshot = snapshots[city]
                for day_index, day in enumerate(dates):
                    if task_index:
                        time.sleep(REQUEST_INTERVAL_SECONDS)
                    runner = _new_runner(city, day, label)
                    engine.messagebox.last_error = None
                    print(f"[{datetime.now(TZ):%H:%M:%S}] {city} {day} {label}: starting", flush=True)
                    try:
                        runner._fetch_weather_task("both")
                    except Exception as exc:
                        engine.messagebox.last_error = ("Forecast error", str(exc))
                    if engine.messagebox.last_error:
                        snapshot["model_errors"].setdefault(label, []).append({"date": day.isoformat(), "error": str(engine.messagebox.last_error[1])[:1200]})
                    day_entry = snapshot["days"][day_index]
                    for direction in ("morning", "evening"):
                        result = runner.direction_results.get(direction)
                        day_entry[direction][label] = _record(result) if result else {
                            "probability": None, "color_score": None, "burn_scale": None,
                            "burn_scale_label": "暂不可判定",
                            "error": str((engine.messagebox.last_error or [None, "无结果"])[1])[:1200],
                        }
                    snapshot["updated_at"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
                    task_index += 1
                    status.update(city_progress=task_index, updated_at=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z"))
                    print(f"[{datetime.now(TZ):%H:%M:%S}] {city} {day} {label}: finished ({task_index}/{task_count})", flush=True)

            # Commit this model's results into each city file and aggregate immediately.
            for city, snapshot in snapshots.items():
                _write_json_atomic(ROOT / "data" / f"overview_{city}.json", snapshot)
            aggregate = {"schema_version": 1, "updated_at": datetime.now(TZ).isoformat(timespec="seconds"),
                        "cities": {city: snapshots[city] for city in cities}}
            _write_json_atomic(ROOT / "data" / "overview.json", aggregate)
            status["completed_models"].append(label)
            status.update(current_model=None, state="between_models", city_progress=task_count,
                         updated_at=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z"))
            _write_json_atomic(ROOT / "data" / "overview_status.json", status)
            _publish_model_batch(label, status)
            if model_index < len(models):
                print(f"Model batch complete; waiting {REQUEST_INTERVAL_SECONDS}s before next model.", flush=True)
                time.sleep(REQUEST_INTERVAL_SECONDS)
    finally:
        engine.messagebox.showinfo, engine.messagebox.showerror = old_info, old_error

    status.update(state="completed", current_model=None, completed_at=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
                  updated_at=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z"))
    _write_json_atomic(ROOT / "data" / "overview_status.json", status)
    _publish_model_batch("all-models-complete", status)
    print(f"Completed model-major update for {len(cities)} cities and {len(models)} models.", flush=True)


if __name__ == "__main__":
    main()
