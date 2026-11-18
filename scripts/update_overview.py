# -*- coding: utf-8 -*-
"""预计算并保存指定城市未来五天朝霞/晚霞双模式概览。"""
from __future__ import annotations

import json
import os
import traceback
import sys
import time
import subprocess
import shutil
import tempfile
import signal
import threading
from contextlib import contextmanager
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
MAX_TASK_ATTEMPTS = 2  # Fresh run: at most one retry for actual transient exceptions.
RETRY_WAIT_SECONDS = 25  # Short bounded wait, never repeated 180-second waits.
# When a quota remains exhausted, retain checkpoint and leave the task unfinished;
# later scheduled runs will retry it without redoing completed tasks.

UPDATER_BUILD_ID = 'partial-results-publish-safe-resume-20261118'

# GitHub Actions uses Linux; cap a single stalled API/forecast task so that
# remaining valid results can still be published. No hard kill on Windows.
@contextmanager
def _bounded_forecast_task(seconds):
    if (not hasattr(signal, 'SIGALRM') or
            threading.current_thread() is not threading.main_thread()):
        yield
        return
    def _expired(_signum, _frame):
        raise TimeoutError(f'单项预测超过 {int(seconds)} 秒，推迟到下次定时计算')
    previous = signal.signal(signal.SIGALRM, _expired)
    old_seconds = signal.alarm(int(seconds))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        if old_seconds:
            signal.alarm(old_seconds)

def _check_scheduled_rate():
    # Verify the HTTP limiter used by the imported engine, not just an env flag.
    actual = _api_rate_limit._profile()
    pacing = _api_rate_limit._POLICY.get(actual)
    print(f'[BURNING SKY UPDATER] build={UPDATER_BUILD_ID} profile={actual} policy={pacing}', flush=True)
    if actual != 'scheduled' or pacing != (12.0, 5):
        raise RuntimeError('定时任务的真实HTTP档位不是 scheduled (12秒/次，5次/分钟)，拒绝以 interactive 高速模式运行')

def _publish_paths(branch, paths, message):
    """Publish ONLY selected data paths from a clean detached Git worktree.

    Never rebase the running checkout: Open-Meteo cache/status JSONs can be
    modified by the engine at any point. A rejected push retries against a
    newly fetched remote head. No force push or unrelated file staging.
    """
    if os.environ.get('GITHUB_ACTIONS', '').lower() != 'true':
        return False
    unique = sorted({str(Path(p).relative_to(ROOT)) for p in paths})
    subprocess.run(['git', 'config', 'user.name', 'github-actions[bot]'], cwd=ROOT, check=True)
    subprocess.run(['git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com'], cwd=ROOT, check=True)
    for attempt in range(1, 6):
        subprocess.run(['git', 'fetch', '--no-tags', 'origin', branch], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL)
        with tempfile.TemporaryDirectory(prefix='burning_skies_publish_') as temp:
            clean = Path(temp) / 'checkout'
            subprocess.run(['git', 'worktree', 'add', '--detach', str(clean), 'FETCH_HEAD'],
                           cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
            try:
                effective = []
                for rel in unique:
                    source = ROOT / rel
                    target = clean / rel
                    if source.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)
                        effective.append(rel)
                    elif target.is_file():
                        # A deleted checkpoint must be deleted remotely, too.
                        target.unlink()
                        effective.append(rel)
                if not effective:
                    print('[overview publish] nothing to stage', flush=True)
                    return True
                subprocess.run(['git', 'add', '-A', '-f', '--', *effective], cwd=clean, check=True)
                changed = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=clean).returncode != 0
                if not changed:
                    print(f'[overview publish] remote already matches {len(unique)} files', flush=True)
                    return True
                subprocess.run(['git', '-c', 'user.name=github-actions[bot]',
                                '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                                'commit', '-m', message], cwd=clean, check=True,
                               stdout=subprocess.DEVNULL)
                pushed = subprocess.run(['git', 'push', 'origin', f'HEAD:{branch}'],
                                        cwd=clean, capture_output=True, text=True)
                if pushed.returncode == 0:
                    print(f'[overview publish] committed and pushed {branch} '
                          f'from clean worktree (attempt {attempt}/5)', flush=True)
                    return True
                detail = (pushed.stderr or pushed.stdout or '')[-900:]
                if not any(x in detail.lower() for x in ('fetch first', 'non-fast-forward', 'rejected')):
                    raise RuntimeError(f'git push failed: {detail}')
                print(f'[overview publish] concurrent remote update; refresh and retry '
                      f'(attempt {attempt}/5)', flush=True)
            finally:
                subprocess.run(['git', 'worktree', 'remove', '--force', str(clean)],
                               cwd=ROOT, check=False, capture_output=True)
        time.sleep(min(attempt * 2, 8))
    raise RuntimeError('5次远端并发提交冲突；结果仍保存在本次运行器，但尚未发布')


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


from burning_sky.spatial_pipeline import HybridSpatialMixin
from burning_sky.overview_partial_publish import prepare_publication

class _HeadlessForecast(HybridSpatialMixin, engine.FireCloudApp):
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
    runner.spatial_enabled = True
    runner.spatial_fetch_log = []
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
        "spatial_calibration_status": (inferred.get('spatial_calibration') or {}).get('status','not_available'),
        "spatial_corrected_point_count": len((inferred.get('spatial_calibration') or {}).get('corrected_distances',[])),
        "event_time": ((result or {}).get("event_dt").isoformat()
                       if hasattr((result or {}).get("event_dt"), "isoformat") else None),
    }


def _write_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _publish_files(status, include_results=False):
    """Publish status/checkpoint and optionally complete overview data.

    Failed publication is never silently reported as published. The caller
    can keep calculating and retry at completion, or fail clearly at the end.
    """
    if os.environ.get('GITHUB_ACTIONS', '').lower() != 'true':
        return True
    branch = os.environ.get('GITHUB_REF_NAME', 'main')
    paths = [ROOT / 'data' / 'overview_status.json', CHECKPOINT]
    if include_results:
        paths.extend([ROOT / 'data' / 'overview.json', OUTPUT])
    try:
        success = _publish_paths(branch, paths,
                                 'chore: publish complete five-day overview' if include_results
                                 else 'chore: publish overview checkpoint and status')
        status.pop('publish_error', None)
        print(f"Published {'complete results' if include_results else 'live status'} to {branch}", flush=True)
        return success
    except Exception as exc:
        print(f'WARNING: overview publish failed: {exc}', flush=True)
        status['publish_error'] = str(exc)[:800]
        _write_json_atomic(ROOT / 'data' / 'overview_status.json', status)
        return False


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
    run_mode = os.environ.get('BURNING_SKY_OVERVIEW_RUN_MODE', 'resume').strip().lower()
    if run_mode not in ('resume', 'fresh'):
        raise ValueError('BURNING_SKY_OVERVIEW_RUN_MODE must be resume or fresh')
    print(f'[overview run mode] {run_mode}; fresh recomputes the current five-day window, '
          'resume skips validated checkpoint items', flush=True)
    try:
        previous_checkpoint = json.loads(CHECKPOINT.read_text(encoding='utf-8'))
        if (run_mode == 'resume' and previous_checkpoint.get('batch_day') == now.date().isoformat()
                and previous_checkpoint.get('place') == PLACE
                and previous_checkpoint.get('model_labels') == [m['label'] for m in models]
                and previous_checkpoint.get('dates') == [d.isoformat() for d in dates]):
            recovered = previous_checkpoint.get('snapshot')
            if isinstance(recovered, dict) and recovered.get('days'):
                from burning_sky.overview_snapshot_guard import restore_missing_only
                checkpoint_completed = set(str(x) for x in previous_checkpoint.get('completed', []))
                snapshots[PLACE], restored = restore_missing_only(
                    snapshots[PLACE], recovered, [d.isoformat() for d in dates],
                    checkpoint_completed, PLACE)
                print(f'Resuming checkpoint: {len(checkpoint_completed)} saved task keys, '
                      f'{restored} previously missing rows restored; already published rows kept.', flush=True)
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

    # The most important resume guarantee: publish the validated checkpoint
    # immediately BEFORE attempting a pending task. Even if that last task
    # stalls in the network layer, 19/20 already exists in public JSON.
    if run_mode == 'resume' and checkpoint_completed and len(checkpoint_completed) < status['task_total']:
        early_stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        early_snapshots, early_info = prepare_publication(
            snapshots, dates, [m['label'] for m in models], checkpoint_completed,
            [], early_stamp, now.date().isoformat())
        for city, ready in early_snapshots.items():
            _write_json_atomic(ROOT / 'data' / f'overview_{city}.json', ready)
        _write_json_atomic(ROOT / 'data' / 'overview.json', {
            'schema_version': 2, 'updated_at': datetime.now(TZ).isoformat(timespec='seconds'),
            'cities': early_snapshots})
        status.update(state='partial', partial_published=True,
                      success_count=early_info['current_valid'],
                      visible_valid_count=early_info['visible_valid'],
                      retained_old_count=len(early_info['stale']),
                      missing_count=len(early_info['missing']),
                      updated_at=early_stamp,
                      message='恢复任务启动前已发布有效检查点数据；现在只补未完成项目。')
        _write_json_atomic(ROOT / 'data' / 'overview_status.json', status)
        if not _publish_files(status, include_results=True):
            raise SystemExit('Failed to publish the restored checkpoint before retrying')
        print(f'[resume early publish] checkpoint {early_info["current_valid"]}/'
              f'{early_info["total"]} made publicly visible before retry.', flush=True)
        status.update(state='running', partial_published=False)

    old_info, old_error = engine.messagebox.showinfo, engine.messagebox.showerror
    engine.messagebox.showinfo = lambda *a, **k: None
    engine.messagebox.showerror = lambda title, message, **kwargs: setattr(engine.messagebox, "last_error", (str(title), str(message)))
    failures = []
    task_index = 0
    limit_hit = False
    attempts_diagnostics = {}
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

                        if task_index and run_mode != 'resume':
                            time.sleep(REQUEST_INTERVAL_SECONDS)
                        record = None
                        error = None
                        reason = ''
                        # Retry ONLY the unfinished task. Every successful task is
                        # checkpointed and must never be recalculated in this batch.
                        # Retrying transient invalid results is essential: some engine
                        # branches produce no direction_results without raising.
                        attempt_limit = 1 if run_mode == 'resume' else MAX_TASK_ATTEMPTS
                        for attempt in range(1, attempt_limit + 1):
                            runner = _new_runner(city, day, label)
                            engine.messagebox.last_error = None
                            print(f'[{datetime.now(TZ):%H:%M:%S}] {city} {day} {label} {direction_title}: starting (attempt {attempt}/{attempt_limit})', flush=True)
                            diagnostic_traceback = None
                            try:
                                with _bounded_forecast_task(480 if run_mode == 'resume' else 900):
                                    runner._fetch_weather_task(direction, raise_errors=True,
                                                               notify_success=False)
                            except Exception as exc:
                                diagnostic_traceback = traceback.format_exc()
                                engine.messagebox.last_error = ('Forecast error', str(exc))
                            result = runner.direction_results.get(direction)
                            record = _record(result) if result else None
                            error = engine.messagebox.last_error
                            valid_attempt = (not error and isinstance(record, dict)
                                             and all(isinstance(record.get(k), (int, float))
                                                     for k in ('probability', 'color_score', 'burn_scale')))
                            if not valid_attempt:
                                inferred_diagnostic = ((result or {}).get('inferred') or {})
                                print('[OVERVIEW DIAGNOSTIC] '
                                      f'key={task_key} attempt={attempt}/{attempt_limit} '
                                      f'result_present={isinstance(result, dict)} '
                                      f'fields={list((result or {}).keys()) if isinstance(result, dict) else []} '
                                      f'inferred_fields={sorted(inferred_diagnostic.keys())} '
                                      f'raw_prob={inferred_diagnostic.get("regular_score")!r} '
                                      f'raw_color={inferred_diagnostic.get("color_score")!r} '
                                      f'raw_scale={inferred_diagnostic.get("burn_scale_index")!r} '
                                      f'raw_tint={inferred_diagnostic.get("tintable_ratio")!r} '
                                      f'verdict={inferred_diagnostic.get("verdict_override")!r} '
                                      f'parsed={record!r} error={error!r}', flush=True)
                                if diagnostic_traceback:
                                    print('[OVERVIEW TRACEBACK]\n' + diagnostic_traceback, flush=True)
                            if valid_attempt:
                                if attempt > 1:
                                    print(f'[recovered] {task_key}: successful on attempt {attempt}/{attempt_limit}', flush=True)
                                break
                            reason = (str(error[1]) if error else '预测流程返回指标不完整：' + ', '.join(
                                f'{key}={record.get(key) if isinstance(record, dict) else None!r}'
                                for key in ('probability', 'color_score', 'burn_scale')))
                            attempts_diagnostics[task_key] = {'attempt': attempt, 'reason': reason[:500]}
                            # Incomplete scores/unknown illumination are not fixed by
                            # repeatedly downloading the same weather run. A 429 is
                            # deferred until the next scheduled batch as well.
                            transient = bool(error) and not any(
                                tag in reason.lower() for tag in ('429', 'limit exceeded', '限流'))
                            if attempt >= attempt_limit or not transient:
                                print(f'[task deferred] {task_key}: {reason[:250]}; '
                                      'no long retry or partial-result loss.', flush=True)
                                break
                            from burning_sky.api_rate_limit import cooldown_remaining
                            delay = min(60.0, max(RETRY_WAIT_SECONDS, float(cooldown_remaining() or 0) + 5))
                            print(f'[retry wait] {task_key}: {reason[:250]} '
                                  f'— wait at most {delay:.0f}s for one transient retry '
                                  f'({attempt + 1}/{attempt_limit}); successful checkpoint retained.', flush=True)
                            save_checkpoint()
                            status.update(state='cooldown',
                                          message=f'第{attempt}次失败，等待{delay:.0f}秒后续跑 {task_key}',
                                          retrying_task=task_key,
                                          retry_attempt=attempt + 1,
                                          updated_at=datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z'))
                            _write_json_atomic(ROOT / 'data' / 'overview_status.json', status)
                            time.sleep(delay)
                            status.update(state='running', retrying_task=None, retry_attempt=None)

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
                            # Quota exhaustion is reported but must not erase earlier
                            # results. A bounded retry prevents infinite API traffic.
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
                        # Continue to the next unfinished task after bounded retries;
                        # a failing task never blocks the remaining 19 items.
            status['completed_models'].append(label)
            save_checkpoint()
            _write_json_atomic(ROOT / 'data' / 'overview_status.json', status)
            _publish_files(status, include_results=False)
    finally:
        engine.messagebox.showinfo, engine.messagebox.showerror = old_info, old_error

    completed_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    published_snapshots, summary = prepare_publication(
        snapshots, dates, [m['label'] for m in models], checkpoint_completed,
        failures, completed_at, now.date().isoformat())
    # The formal five-day JSON is ALWAYS refreshed with independently valid
    # results. A failed cell preserves an old valid record for the SAME date,
    # model and direction, otherwise remains absent/unknown.
    for city, snapshot in published_snapshots.items():
        _write_json_atomic(ROOT / 'data' / f'overview_{city}.json', snapshot)
    _write_json_atomic(ROOT / 'data' / 'overview.json', {
        'schema_version': 2, 'updated_at': datetime.now(TZ).isoformat(timespec='seconds'),
        'cities': published_snapshots})

    if failures:
        save_checkpoint()  # Keep only unfinished task keys eligible for resume.
        status.update(
            state='rate_limited' if limit_hit else 'partial',
            failed_at=completed_at, updated_at=completed_at,
            success_count=summary['current_valid'],
            visible_valid_count=summary['visible_valid'],
            retained_old_count=len(summary['stale']),
            missing_count=len(summary['missing']),
            failure_count=len(failures), failures=failures[:30],
            retry_diagnostics=attempts_diagnostics,
            partial_published=True,
            message=(f'本轮有效 {summary["current_valid"]}/{summary["total"]}；'
                     f'正式概览已发布，旧有效记录保留 {len(summary["stale"])} 项，'
                     f'缺失 {len(summary["missing"])} 项。失败项留待下次定时任务。'))
        _write_json_atomic(ROOT / 'data' / 'overview_status.json', status)
        published = _publish_files(status, include_results=True)
        if not published:
            raise SystemExit('Partial results computed but GitHub publication failed; inspect publish_error')
        print(f'[overview partial published] fresh={summary["current_valid"]}/{summary["total"]}; '
              f'old_retained={len(summary["stale"])}; unknown={len(summary["missing"])}; '
              'next scheduled run can retry only unfinished tasks.', flush=True)
        # A PARTIAL published data set is a successful publication, but NOT a
        # falsely completed 20/20 batch: the warning stays in status JSON.
        return

    status.update(state='completed', completed_at=completed_at, updated_at=completed_at,
                  success_count=summary['current_valid'],
                  visible_valid_count=summary['visible_valid'],
                  failure_count=0,
                  message='双模式五日朝霞及晚霞数据已完整发布。')
    _write_json_atomic(ROOT / 'data' / 'overview_status.json', status)
    # Keep checkpoint until the published complete snapshot is durable.
    published = _publish_files(status, include_results=True)
    if not published:
        raise SystemExit('All tasks computed but GitHub publish failed; see publish_error log')
    CHECKPOINT.unlink(missing_ok=True)
    # A separate best-effort cleanup; a stale checkpoint never overrides a
    # complete formally published snapshot.
    _publish_files(status, include_results=False)
    print(f'Completed and published {direction_title}: '
          f'{len(cities)} cities × 2 models × 5 days × 2 directions', flush=True)

if __name__ == "__main__":
    main()
