"""Truthful overview status banners derived from *published* weather data.

The publication and the heartbeat may be committed by different Actions runs.
Never treat a `running` flag with no progress as authoritative proof that an
already complete public snapshot is unavailable.
"""
from datetime import datetime, timedelta, timezone

CN = timezone(timedelta(hours=8))
MODELS = ('ECMWF IFS 0.25°', 'GFS Global')
DIRECTIONS = ('morning', 'evening')
FIELDS = ('probability', 'color_score', 'burn_scale')


def _date(value):
    try:
        return datetime.fromisoformat(str(value).strip().replace(' CST', '+08:00').replace(' ', 'T', 1))
    except (ValueError, TypeError):
        return None


def _complete_forecast(snapshot, now):
    """Require 5 consecutive days and every EC/GFS × morning/evening metric.

    Zero is a legitimate value. A carried-over incomplete checkpoint cannot
    satisfy this test after failed records have been removed by app.py.
    """
    if not isinstance(snapshot, dict):
        return False
    days = snapshot.get('days')
    if not isinstance(days, list):
        return False
    index = {str(d.get('date')): d for d in days if isinstance(d, dict)}
    for offset in range(5):
        day = index.get((now.date() + timedelta(days=offset)).isoformat())
        if not isinstance(day, dict):
            return False
        for direction in DIRECTIONS:
            records = day.get(direction)
            if not isinstance(records, dict):
                return False
            for model in MODELS:
                row = records.get(model)
                if not isinstance(row, dict):
                    return False
                for field in FIELDS:
                    value = row.get(field)
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        return False
                    if value != value or value in (float('inf'), float('-inf')):
                        return False
    return True


def status_view(status, snapshot, now=None):
    """Return (kind, message); pure for regression tests."""
    now = now or datetime.now(CN)
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN)
    status = status if isinstance(status, dict) else {}
    state = status.get('state', 'unknown')
    completed = _complete_forecast(snapshot, now)
    published_at = str((snapshot or {}).get('updated_at') or '') if isinstance(snapshot, dict) else ''
    task_progress = status.get('task_progress', 0)
    task_total = status.get('task_total', 20)
    try:
        task_progress = int(task_progress or 0)
        task_total = int(task_total or 20)
    except (ValueError, TypeError):
        task_progress, task_total = 0, 20
    updated = _date(status.get('updated_at'))
    elapsed = (now - updated.astimezone(CN)).total_seconds() if updated else None
    # A complete, current five-day snapshot is authoritative for the ability
    # to display results. A 0/20 heartbeat is not proof of ongoing work.
    if completed and state in ('running', 'between_models') and task_progress == 0:
        return ('success', f'✅ 已有完整的未来五日 EC/GFS 朝霞及晚霞数据（最近发布：{published_at}）。'
                '后台进度文件仍停留在 0/20，无法据此确认任务仍在计算；不影响已发布结果。')
    if completed and state == 'completed':
        return ('success', f'✅ 未来五日 EC/GFS 朝霞及晚霞数据已发布（{published_at}）。')
    if state in ('running', 'between_models'):
        if elapsed is None or elapsed < -60 or elapsed > (15*60 if task_progress == 0 else 90*60):
            if completed:
                return ('success', f'✅ 已有完整的未来五日预测（{published_at}）；后台进度记录未及时同步。')
            return ('caption', '后台进度记录已过期或时间不可靠；以下展示可获取的已发布数据。')
        return ('info', f'🔄 后台任务状态：{task_progress}/{task_total}；最近记录：'
                f'{status.get("updated_at", "未知")}。下方仍展示可获取的已发布数据。')
    if state == 'completed':
        return ('success', f'✅ 本轮多日概览更新完成，状态更新时间：{status.get("updated_at", "未知")}。')
    if state in ('partial', 'failed', 'rate_limited'):
        if completed:
            return ('success', f'✅ 当前仍可查看完整五日预测（{published_at}）；最近一次后台任务状态为 {state}。')
        return ('warning', f'⚠️ 后台任务尚未全部完成（{state}）；已成功的预测仍可查看。'
                f'最近状态：{status.get("updated_at", "未知")}。')
    if completed:
        return ('success', f'✅ 已发布完整五日预测（{published_at}）；后台状态信息暂不可用。')
    return ('caption', '后台状态尚未确认；以下展示当前可取得的概览数据。')


def render_overview_status(st, status, snapshot, city=''):
    kind, message = status_view(status, snapshot)
    getattr(st, kind)(message)
