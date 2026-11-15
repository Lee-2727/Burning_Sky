# -*- coding: utf-8 -*-
"""预计算并保存指定城市未来五天朝霞/晚霞双模式概览。"""
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
# This GitHub Actions batch is NOT a human-triggered interactive query.
# Set the environment BEFORE importing engine/api_rate_limit (profile is read at import).
os.environ['BURNING_SKY_REQUEST_PROFILE'] = 'scheduled'
import engine  # noqa: E402
from burning_sky import api_rate_limit as _api_rate_limit

TZ = ZoneInfo("Asia/Shanghai")
CITY_CONFIG_FILE = ROOT / "overview_cities.txt"

# Requests are processed conservatively within each model batch; each completed
# model batch is published before the next model starts.
REQUEST_INTERVAL_SECONDS = 20  # Additional spacing between individual direction tasks.
MINUTE_429_RETRIES = 3  # 429 cooldown/resume is limited; do not retry hourly/data quotas.

UPDATER_BUILD_ID = 'scheduled-v2.1.1-20261116-profile-check-push-rebase'

def _check_scheduled_rate():
    # Verify the HTTP limiter used by the imported engine, not just an env flag.
    actual = _api_rate_limit._profile()
    pacing = _api_rate_limit._POLICY.get(actual)
    print(f'[BURNING SKY UPDATER] build={UPDATER_BUILD_ID} profile={actual} policy={pacing}', flush=True)
    if actual != 'scheduled' or pacing != (12.0, 5):
        raise RuntimeError('定时任务的真实HTTP档位不是 scheduled (12秒/次，5次/分钟)，拒绝以 interactive 高速模式运行')

def _push_with_rebase(branch):
    """Fast-forward remote updates on a non-fast-forward push; never force-push.

    Rebase can conflict when multiple workflows touch the same status JSON.
    In that case abort safely, report failure, and leave remote data untouched.
    """
    for attempt in range(1, 4):
        pushed = subprocess.run(['git','push','origin',f'HEAD:{branch}'], cwd=ROOT, capture_output=True, text=True)
        if pushed.returncode == 0:
            print(f'[overview publish] pushed {branch} (attempt {attempt})', flush=True)
            return
        detail = (pushed.stderr or pushed.stdout or '')[-500:]
        if not any(x in detail.lower() for x in ('fetch first', 'non-fast-forward', 'rejected')):
            raise RuntimeError(f'git push failed: {detail}')
        print(f'[overview publish] remote changed, fetch+rebase (attempt {attempt}/3)', flush=True)
        subprocess.run(['git','fetch','origin',branch], cwd=ROOT, check=True)
        rebased = subprocess.run(['git','rebase','FETCH_HEAD'], cwd=ROOT, capture_output=True, text=True)
        if rebased.returncode:
            subprocess.run(['git','rebase','--abort'], cwd=ROOT, capture_output=True)
            raise RuntimeError('并发更新产生数据文件冲突；已中止rebase，保留远端数据。请在Actions工作流设置 concurrency 互斥。' + (rebased.stderr or '')[-400:])
    raise RuntimeError('远端持续出现并发提交，三次重试后停止，未强制覆盖远端')

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
CHECKPOINT = ROOT / "data" / f"overview_checkpoint_{PLACE}.json"
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


def _new_runner(city, day, model_label, lat=None, lon=None, elevation_m=None):
    """headless 预测进程。

    手动多日概览可以查询 overview_cities.txt 之外的任意地点，因此允许显式传入坐标；
    省略坐标时仍按城市配置表取默认值（定时任务保持原行为）。
    """
    if lat is None or lon is None or elevation_m is None:
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
    runner.skip_rainbow_for_overview = True
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
    tintable_ratio = numeric(inferred.get("tintable_ratio"))
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
    # Reuse the engine's visible-horizon scale rather than the 0-km cloud sample.
    burn_scale = numeric(inferred.get("burn_scale_index"))
    if burn_scale is None:
        local_cover = numeric(inferred.get('potential_range_mid_high_pct'))
        burn_scale = (round(local_cover*tintable_ratio/100.0, 2)
                      if local_cover is not None and tintable_ratio is not None else None)
    fire_score = (round(probability * burn_scale / 100.0, 2)
                  if probability is not None and burn_scale is not None else None)
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
        _push_with_rebase(branch)
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
        if CHECKPOINT.is_file():
            paths.append(CHECKPOINT)
        if include_results:
            paths += [ROOT / "data" / "overview.json"]
            paths += sorted((ROOT / "data").glob("overview_*.json"))
        existing = [str(x.relative_to(ROOT)) for x in paths if x.is_file()]
        if existing:
            subprocess.run(["git", "add", "-f", "--", *existing], cwd=ROOT, check=True)
        # On successful completion remove the durable checkpoint from Git as well.
        relative_checkpoint = str(CHECKPOINT.relative_to(ROOT))
        if not CHECKPOINT.exists() and subprocess.run(
                ['git', 'ls-files', '--error-unmatch', '--', relative_checkpoint],
                cwd=ROOT, capture_output=True).returncode == 0:
            subprocess.run(['git', 'add', '-u', '--', relative_checkpoint], cwd=ROOT, check=True)
        changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0
        if changed:
            msg = "chore: publish completed " + status.get("direction_title", "") + " overview" if include_results else "chore: publish overview live status"
            subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
        _push_with_rebase(branch)
        print(f"Published {'completed direction results' if include_results else 'live status'} to {branch}", flush=True)
    except Exception as exc:
        print(f"WARNING: publish failed: {exc}", flush=True)
        status["publish_error"] = str(exc)[:800]
        _write_json_atomic(ROOT / "data" / "overview_status.json", status)


def _scheduled_direction(now):
    """Retained for compatibility; scheduled batches now run both directions."""
    return "both"


def main():
    _check_scheduled_rate()
    now = datetime.now(TZ)
    direction = "both"
    direction_title = "朝霞及晚霞"
    dates = [now.date() + timedelta(days=i) for i in range(5)]
    cities = list(CITY_CONFIG)
    models = [m for m in engine.WEATHER_MODEL_CATALOG if m["label"] in ("ECMWF IFS 0.25°", "GFS Global")]
    if len(models) != 2:
        raise SystemExit(f"Expected two enabled models, found {len(models)}")
    if cities != ["合肥"]:
        raise SystemExit(f"Expected only 合肥 in overview_cities.txt; found {cities}")

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

    # Preserve partial work on a failed or rate-limited run. A checkpoint may
    # resume only for the same calendar-day batch, same city and same models.
    checkpoint_completed = set()
    try:
        previous_checkpoint = json.loads(CHECKPOINT.read_text(encoding='utf-8'))
        if (previous_checkpoint.get('batch_day') == now.date().isoformat()
                and previous_checkpoint.get('place') == PLACE
                and previous_checkpoint.get('model_labels') == [m['label'] for m in models]
                and previous_checkpoint.get('dates') == [d.isoformat() for d in dates]):
            recovered = previous_checkpoint.get('snapshot')
            if isinstance(recovered, dict) and recovered.get('days'):
                snapshots[PLACE] = recovered
                checkpoint_completed = set(str(x) for x in previous_checkpoint.get('completed', []))
                print(f'Resuming {len(checkpoint_completed)} successful tasks from checkpoint.', flush=True)
    except (OSError, ValueError, TypeError, KeyError):
        pass

    def save_checkpoint():
        _write_json_atomic(CHECKPOINT, {
            'schema_version': 1, 'batch_day': now.date().isoformat(),
            'place': PLACE, 'model_labels': [m['label'] for m in models],
            'dates': [d.isoformat() for d in dates],
            'completed': sorted(checkpoint_completed), 'snapshot': snapshots[PLACE],
            'saved_at': datetime.now(TZ).isoformat(timespec='seconds'),
        })

    status = {
        "schema_version": 2, "state": "running", "direction": direction,
        "direction_title": direction_title,
        "started_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "current_model": models[0]["label"], "model_index": 1,
        "model_total": len(models), "completed_models": [],
        "cities": cities, "city_progress": 0, "city_total": len(cities),
        "task_progress": 0, "task_total": len(cities) * len(dates) * len(models) * 2,
        "next_update": "北京时间每日 03:00、09:00、14:00 同时更新朝霞和晚霞",
    }
    print(f"Updater version: {UPDATER_BUILD_ID}", flush=True)
    print(f"Direction: {direction_title}; cities: {' → '.join(cities)}; models: 2; days: 5", flush=True)
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
            label = model['label']
            status.update(current_model=label, model_index=model_index,
                          updated_at=datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z'))
            _write_json_atomic(ROOT / 'data' / 'overview_status.json', status)
            _publish_files(status, include_results=False)
            print(f"\n===== MODEL {model_index}/{len(models)}: {label} · {direction_title} =====", flush=True)
            for city_index, city in enumerate(cities, 1):
                snapshot = snapshots[city]
                for day_index, day in enumerate(dates):
                    for direction in ('morning', 'evening'):
                        direction_title = '朝霞' if direction == 'morning' else '晚霞'
                        task_key = '|'.join((city, day.isoformat(), label, direction))
                        if task_key in checkpoint_completed:
                            previous = snapshot['days'][day_index][direction].get(label)
                            if isinstance(previous, dict) and all(isinstance(previous.get(k), (int,float))
                                                      for k in ('probability','color_score','burn_scale')):
                                task_index += 1
                                print(f'[checkpoint] {task_key}: valid ({task_index}/{status["task_total"]})', flush=True)
                                status.update(task_progress=task_index, city_progress=city_index)
                                continue
                            checkpoint_completed.discard(task_key)

                        if task_index:
                            time.sleep(REQUEST_INTERVAL_SECONDS)
                        record = None
                        error = None
                        reason = ''
                        for attempt in range(1, MINUTE_429_RETRIES + 2):
                            runner = _new_runner(city, day, label)
                            engine.messagebox.last_error = None
                            print(f'[{datetime.now(TZ):%H:%M:%S}] {city} {day} {label} {direction_title}: starting (attempt {attempt})', flush=True)
                            try:
                                runner._fetch_weather_task(direction)
                            except Exception as exc:
                                engine.messagebox.last_error = ('Forecast error', str(exc))
                            result = runner.direction_results.get(direction)
                            record = _record(result) if result else None
                            error = engine.messagebox.last_error
                            if (not error and isinstance(record, dict)
                                    and all(isinstance(record.get(k), (int,float))
                                            for k in ('probability','color_score','burn_scale'))):
                                break
                            reason = str(error[1]) if error else '预测流程未返回有效概率结果'
                            minute_limited = ('Minutely API request limit exceeded' in reason
                                              or ('HTTP 429' in reason and 'Minutely' in reason))
                            if not minute_limited or attempt > MINUTE_429_RETRIES:
                                break
                            from burning_sky.api_rate_limit import cooldown_remaining
                            delay = max(125, cooldown_remaining() + 25)
                            print(f'[rate limited] Minute quota: wait {delay}s and retry current task '
                                  f'({attempt}/{MINUTE_429_RETRIES}); completed results retained.', flush=True)
                            save_checkpoint()
                            status.update(state='cooldown', message=f'分钟限流，等待{delay}秒后重试当前任务',
                                          updated_at=datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z'))
                            _write_json_atomic(ROOT / 'data' / 'overview_status.json', status)
                            time.sleep(delay)
                            status['state'] = 'running'

                        valid = (not error and isinstance(record, dict)
                                 and all(isinstance(record.get(k), (int,float))
                                         for k in ('probability','color_score','burn_scale')))
                        if valid:
                            snapshot['days'][day_index][direction][label] = record
                            checkpoint_completed.add(task_key)
                            save_checkpoint()
                        else:
                            failures.append({'city': city, 'date': day.isoformat(), 'model': label,
                                             'direction': direction, 'error': reason[:1000]})
                            # For exhausted minute retries or any longer-term quota, stop this
                            # batch, but checkpoint successes so a subsequent run can resume.
                            if '429' in reason or 'limit exceeded' in reason.lower() or '限流' in reason:
                                limit_hit = True
                            print(f'ERROR: {city} {day} {label} {direction_title}: {reason[:500]}', flush=True)
                        task_index += 1
                        status.update(task_progress=task_index, city_progress=city_index,
                                      updated_at=datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z'),
                                      failure_count=len(failures), last_error=(failures[-1] if failures else None))
                        _write_json_atomic(ROOT / 'data' / 'overview_status.json', status)
                        print(f'[{datetime.now(TZ):%H:%M:%S}] {city} {day} {label} {direction_title}: '
                              f'{"valid" if valid else "failed"} ({task_index}/{status["task_total"]})', flush=True)
                        if limit_hit:
                            print('Quota still exhausted after bounded retry; checkpoint saved. '
                                  'Stopping without discarding successful tasks.', flush=True)
                            break
                    if limit_hit:
                        break
                if limit_hit:
                    break
            if limit_hit:
                break
            status['completed_models'].append(label)
            save_checkpoint()
            _write_json_atomic(ROOT / 'data' / 'overview_status.json', status)
            _publish_files(status, include_results=False)
    finally:
        engine.messagebox.showinfo, engine.messagebox.showerror = old_info, old_error

    if failures:
        save_checkpoint()
        status.update(state='rate_limited' if limit_hit else 'failed',
                      failed_at=datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z'),
                      failure_count=len(failures), failures=failures[:30],
                      message=f'本轮已有{len(checkpoint_completed)}/{status["task_total"]}项成功缓存；'
                              '未完成任务下次同日运行可续跑，保留上次完整发布的数据。')
        _write_json_atomic(ROOT / 'data' / 'overview_status.json', status)
        _publish_files(status, include_results=False)
        raise SystemExit(f'Overview batch incomplete ({len(failures)} failed tasks); checkpoint retained')

    completed_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    for city, snapshot in snapshots.items():
        snapshot["direction_updated_at"]["morning"] = completed_at
        snapshot["direction_updated_at"]["evening"] = completed_at
        snapshot["updated_at"] = completed_at
        _write_json_atomic(ROOT / "data" / f"overview_{city}.json", snapshot)
    aggregate = {"schema_version": 2, "updated_at": datetime.now(TZ).isoformat(timespec="seconds"),
                 "cities": {city: snapshots[city] for city in cities}}
    _write_json_atomic(ROOT / "data" / "overview.json", aggregate)
    status.update(state="completed", completed_at=completed_at, updated_at=completed_at,
                  failure_count=0, message=f"{direction_title}双模式五日朝霞及晚霞数据已完整发布。")
    _write_json_atomic(ROOT / "data" / "overview_status.json", status)
    CHECKPOINT.unlink(missing_ok=True)
    _publish_files(status, include_results=True)
    print(f"Completed and atomically published {direction_title}: {len(cities)} cities × 2 models × 5 days × 2 directions", flush=True)


if __name__ == "__main__":
    main()
