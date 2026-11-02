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
    burn_scale = numeric(inferred.get("tintable_ratio", inferred.get("burn_scale_index")))
    # 本地云量必须取预测结果的 distance_km == 0 原始天气采样点。
    # inferred.local_scale_clouds 并不是当前引擎稳定输出的字段，不能依赖它。
    samples = (result or {}).get("samples") or []
    local_sample = next((sample for sample in samples
                         if isinstance(sample, dict)
                         and numeric(sample.get("distance_km")) is not None
                         and abs(numeric(sample.get("distance_km"))) < 1e-6), None)
    local_mid_cloud = numeric(local_sample.get("cloud_cover_mid")) if local_sample else None
    local_high_layer_cloud = numeric(local_sample.get("cloud_cover_high")) if local_sample else None
    # 本地高云 = 本地中云 + 本地高云；云量按百分比显示，封顶 100%。
    local_high_cloud = (min(100.0, max(0.0, local_mid_cloud) + max(0.0, local_high_layer_cloud))
                        if local_mid_cloud is not None and local_high_layer_cloud is not None else None)
    # 三个百分比相乘后归一化，评分保持 0–100 的直观范围。
    fire_score = (probability * burn_scale * local_high_cloud / 10000.0
                  if probability is not None and burn_scale is not None and local_high_cloud is not None else None)
    return {
        "probability": probability,
        "color_score": numeric(inferred.get("color_score")),
        "burn_scale": burn_scale,
        "local_mid_cloud": local_mid_cloud,
        "local_high_layer_cloud": local_high_layer_cloud,
        "local_high_cloud": local_high_cloud,
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
    """Publish status/results safely; never fail on an absent optional history folder."""
    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        return
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=ROOT, check=True)

        # Build an explicit list of paths that actually exist. The previous
        # implementation always staged data/overview_history/, which is not
        # present in a clean checkout and made `git add` fail before status
        # could ever be pushed. Also avoid shell-style wildcard arguments to
        # subprocess; expand city snapshots in Python instead.
        data_dir = ROOT / "data"
        publish_paths = [data_dir / "overview.json", data_dir / "overview_status.json"]
        publish_paths.extend(sorted(data_dir.glob("overview_*.json")))
        history_dir = data_dir / "overview_history"
        if history_dir.is_dir():
            publish_paths.extend(p for p in history_dir.rglob("*") if p.is_file())
        existing = [str(p.relative_to(ROOT)) for p in publish_paths if p.is_file()]
        if existing:
            subprocess.run(["git", "add", "-f", "--", *existing], cwd=ROOT, check=True)

        changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0
        if changed:
            subprocess.run(["git", "commit", "-m", f"chore: publish {model_label} overview batch"], cwd=ROOT, check=True)
        # Always try pushing HEAD, even when there are no newly staged files:
        # a previous push may have failed after its local commit succeeded.
        subprocess.run(["git", "push", "origin", f"HEAD:{branch}"], cwd=ROOT, check=True)
        print(f"Published status/results to {branch}: {model_label}", flush=True)
    except Exception as exc:
        print(f"WARNING: incremental publish failed for {model_label}: {exc}", flush=True)
        status["publish_error"] = str(exc)[:800]
        _write_json_atomic(ROOT / "data" / "overview_status.json", status)


def _publish_files(status, include_results=False):
    """Publish live status alone, or atomically publish a completed direction batch."""
    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        return
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=ROOT, check=True)
        paths = [ROOT / "data" / "overview_status.json"]
        if include_results:
            paths += [ROOT / "data" / "overview.json"]
            paths += sorted((ROOT / "data").glob("overview_*.json"))
        existing = [str(x.relative_to(ROOT)) for x in paths if x.is_file()]
        if existing:
            subprocess.run(["git", "add", "-f", "--", *existing], cwd=ROOT, check=True)
        changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0
        if changed:
            msg = "chore: publish completed " + status.get("direction_title", "") + " overview" if include_results else "chore: publish overview live status"
            subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
        subprocess.run(["git", "push", "origin", f"HEAD:{branch}"], cwd=ROOT, check=True)
        print(f"Published {'completed direction results' if include_results else 'live status'} to {branch}", flush=True)
    except Exception as exc:
        print(f"WARNING: publish failed: {exc}", flush=True)
        status["publish_error"] = str(exc)[:800]
        _write_json_atomic(ROOT / "data" / "overview_status.json", status)


def _scheduled_direction(now):
    """Push deploys initialize morning; scheduled runs follow Beijing 6-slot cycle."""
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    forced = os.environ.get("BURNING_SKY_DIRECTION", "").strip().lower()
    if forced in ("morning", "evening"):
        return forced
    if event in ("push", "workflow_dispatch"):
        return "morning"
    slots = {0: "morning", 4: "morning", 8: "evening", 12: "evening", 16: "evening", 20: "morning"}
    # GitHub cron can start late; select the nearest configured slot within 90 minutes.
    candidates = []
    for hour, direction in slots.items():
        delta = abs((now.hour * 60 + now.minute) - hour * 60)
        delta = min(delta, 1440 - delta)
        candidates.append((delta, direction, hour))
    delta, direction, hour = min(candidates)
    if delta > 90:
        raise SystemExit(f"Not within a configured update window: {now:%Y-%m-%d %H:%M %Z}")
    print(f"Scheduled slot: {hour:02d}:00 Beijing; direction={direction}", flush=True)
    return direction


def main():
    now = datetime.now(TZ)
    direction = _scheduled_direction(now)
    direction_title = "朝霞" if direction == "morning" else "晚霞"
    dates = [now.date() + timedelta(days=i) for i in range(3)]
    cities = list(CITY_CONFIG)
    models = list(engine.WEATHER_MODEL_CATALOG)
    if len(models) != 5:
        raise SystemExit(f"Expected five enabled models, found {len(models)}")
    if cities != ["合肥", "深圳"]:
        raise SystemExit(f"Expected only 合肥 and 深圳 in overview_cities.txt; found {cities}")

    snapshots = {}
    for city, (lat, lon, elev) in CITY_CONFIG.items():
        path = ROOT / "data" / f"overview_{city}.json"
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            old = {}
        # Migration/compatibility: older deployments may have only overview.json.
        # Reuse that city's saved snapshot instead of silently resetting its history.
        if not isinstance(old, dict) or not old.get("days"):
            try:
                aggregate_old = json.loads((ROOT / "data" / "overview.json").read_text(encoding="utf-8"))
                aggregate_cities = aggregate_old.get("cities", {}) if isinstance(aggregate_old, dict) else {}
                if isinstance(aggregate_cities, dict) and isinstance(aggregate_cities.get(city), dict):
                    old = aggregate_cities[city]
                elif city == "合肥" and isinstance(aggregate_old, dict) and aggregate_old.get("days"):
                    old = aggregate_old
            except Exception:
                pass
        # Preserve the last complete morning/evening data independently.
        old_days = {d.get("date"): d for d in old.get("days", []) if isinstance(d, dict)}
        days_payload = []
        for day in dates:
            old_day = old_days.get(day.isoformat(), {})
            days_payload.append({
                "date": day.isoformat(),
                "morning": dict(old_day.get("morning", {})),
                "evening": dict(old_day.get("evening", {})),
            })
        snapshots[city] = {
            "schema_version": 2, "place": city, "latitude": lat, "longitude": lon,
            "elevation_m": elev, "timezone": "Asia/Shanghai",
            "updated_at": old.get("updated_at", "尚未更新"),
            "direction_updated_at": dict(old.get("direction_updated_at", {})),
            "days": days_payload,
        }

    status = {
        "schema_version": 2, "state": "running", "direction": direction,
        "direction_title": direction_title,
        "started_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "current_model": models[0]["label"], "model_index": 1,
        "model_total": len(models), "completed_models": [],
        "cities": cities, "city_progress": 0, "city_total": len(cities),
        "task_progress": 0, "task_total": len(cities) * len(dates) * len(models),
        "next_update": "北京时间每日 00:00/04:00/20:00 朝霞；08:00/12:00/16:00 晚霞",
    }
    print("Updater version: split-direction-atomic-v1", flush=True)
    print(f"Direction: {direction_title}; cities: {' → '.join(cities)}; models: 5; days: 3", flush=True)
    _write_json_atomic(ROOT / "data" / "overview_status.json", status)
    _publish_files(status, include_results=False)

    old_info, old_error = engine.messagebox.showinfo, engine.messagebox.showerror
    engine.messagebox.showinfo = lambda *a, **k: None
    engine.messagebox.showerror = lambda title, message, **kwargs: setattr(engine.messagebox, "last_error", (str(title), str(message)))
    failures = []
    task_index = 0
    limit_hit = False
    try:
        for model_index, model in enumerate(models, 1):
            label = model["label"]
            status.update(current_model=label, model_index=model_index,
                          updated_at=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z"))
            _write_json_atomic(ROOT / "data" / "overview_status.json", status)
            _publish_files(status, include_results=False)
            print(f"\n===== MODEL {model_index}/{len(models)}: {label} · {direction_title} =====", flush=True)
            for city_index, city in enumerate(cities, 1):
                snapshot = snapshots[city]
                for day_index, day in enumerate(dates):
                    if task_index:
                        time.sleep(REQUEST_INTERVAL_SECONDS)
                    runner = _new_runner(city, day, label)
                    engine.messagebox.last_error = None
                    print(f"[{datetime.now(TZ):%H:%M:%S}] {city} {day} {label} {direction_title}: starting", flush=True)
                    try:
                        runner._fetch_weather_task(direction)
                    except Exception as exc:
                        engine.messagebox.last_error = ("Forecast error", str(exc))
                    result = runner.direction_results.get(direction)
                    record = _record(result) if result else None
                    error = engine.messagebox.last_error
                    if error or not isinstance(record, dict) or not isinstance(record.get("probability"), (int, float)):
                        reason = str(error[1]) if error else "预测流程未返回有效概率结果"
                        failures.append({"city": city, "date": day.isoformat(), "model": label, "error": reason[:1000]})
                        if "Hourly API request limit exceeded" in reason or "429" in reason:
                            limit_hit = True
                        print(f"ERROR: {city} {day} {label} {direction_title}: {reason[:500]}", flush=True)
                    else:
                        snapshot["days"][day_index][direction][label] = record
                    task_index += 1
                    status.update(task_progress=task_index, city_progress=city_index,
                                  updated_at=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
                                  failure_count=len(failures), last_error=(failures[-1] if failures else None))
                    _write_json_atomic(ROOT / "data" / "overview_status.json", status)
                    print(f"[{datetime.now(TZ):%H:%M:%S}] {city} {day} {label} {direction_title}: {'valid' if not error and record and isinstance(record.get('probability'), (int,float)) else 'failed'} ({task_index}/{status['task_total']})", flush=True)
                    if limit_hit:
                        print("Hourly API limit detected; stopping this batch early to avoid wasting further requests.", flush=True)
                        break
                if limit_hit:
                    break
            if limit_hit:
                break
            status["completed_models"].append(label)
            _write_json_atomic(ROOT / "data" / "overview_status.json", status)
            _publish_files(status, include_results=False)
    finally:
        engine.messagebox.showinfo, engine.messagebox.showerror = old_info, old_error

    if failures:
        status.update(state="failed", failed_at=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
                      failure_count=len(failures), failures=failures[:30],
                      message=f"本轮{direction_title}有 {len(failures)} 项失败；保留上一次完整发布的数据。")
        _write_json_atomic(ROOT / "data" / "overview_status.json", status)
        _publish_files(status, include_results=False)
        raise SystemExit(f"{direction_title} batch failed ({len(failures)} failed tasks); old published data retained")

    completed_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    for city, snapshot in snapshots.items():
        snapshot["direction_updated_at"][direction] = completed_at
        snapshot["updated_at"] = completed_at
        _write_json_atomic(ROOT / "data" / f"overview_{city}.json", snapshot)
    aggregate = {"schema_version": 2, "updated_at": datetime.now(TZ).isoformat(timespec="seconds"),
                 "cities": {city: snapshots[city] for city in cities}}
    _write_json_atomic(ROOT / "data" / "overview.json", aggregate)
    status.update(state="completed", completed_at=completed_at, updated_at=completed_at,
                  failure_count=0, message=f"{direction_title}五模式三日数据已完整发布。")
    _write_json_atomic(ROOT / "data" / "overview_status.json", status)
    _publish_files(status, include_results=True)
    print(f"Completed and atomically published {direction_title}: {len(cities)} cities × 5 models × 3 days", flush=True)


if __name__ == "__main__":
    main()
