"""Truthful overview status banners derived from *published* weather data.

The publication and the heartbeat may be committed by different Actions runs.
Never treat a `running` flag with no progress as authoritative proof that an
already complete public snapshot is unavailable.
"""
from datetime import datetime, timedelta, timezone

CN = timezone(timedelta(hours=8))
MODELS = ('ECMWF IFS 0.25°', 'GFS Global')
DIRECTIONS = ('morning', 'evening')
FIELDS = ('probability', 'color_score', 'burn_scale', 'visual_burn_scale')


def _date(value):
    try:
        return datetime.fromisoformat(str(value).strip().replace(' CST', '+08:00').replace(' ', 'T', 1))
    except (ValueError, TypeError):
        return None


def _complete_forecast(snapshot, now):
    """Require 3 consecutive days and every EC/GFS × morning/evening metric.

    Zero is a legitimate value. A carried-over incomplete checkpoint cannot
    satisfy this test after failed records have been removed by app.py.
    """
    if not isinstance(snapshot, dict):
        return False
    days = snapshot.get('days')
    if not isinstance(days, list):
        return False
    index = {str(d.get('date')): d for d in days if isinstance(d, dict)}
    for offset in range(3):
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
    task_total = status.get('task_total', 12)
    try:
        task_progress = int(task_progress or 0)
        task_total = int(task_total or 12)
    except (ValueError, TypeError):
        task_progress, task_total = 0, 12
    updated = _date(status.get('updated_at'))
    elapsed = (now - updated.astimezone(CN)).total_seconds() if updated else None
    # A verified full publication must not be presented as an unfinished batch
    # simply because the non-authoritative heartbeat/checkpoint is newer.
    # Old progress can belong to a subsequent run; do not describe it as proof
    # that the displayed published data are incomplete.
    if completed:
        if state in ('partial', 'rate_limited') and status.get('partial_published'):
            return ('warning', f'⚠️ 本轮部分更新已发布：新有效 {status.get("success_count", "?")}/{task_total}，'
                    f'沿用上一轮 {status.get("retained_old_count", 0)} 项，'
                    f'暂无有效结果 {status.get("missing_count", 0)} 项。已显示的完整数据不等于本轮12项全部成功。')
        if state == 'completed':
            return ('success', f'✅ 未来三日 EC/GFS 朝霞及晚霞数据已完整发布（{published_at}）。')
        if state in ('running', 'between_models'):
            return ('success', f'✅ 已有完整的未来三日 EC/GFS 朝霞及晚霞数据（最近发布：{published_at}）。'
                    f'后台状态文件另记 {task_progress}/{task_total}，不能据此否定已发布的12项结果。')
        return ('success', f'✅ 当前已有完整的未来三日 EC/GFS 朝霞及晚霞数据（{published_at}）。')
    if state in ('running', 'between_models'):
        if elapsed is None or elapsed < -60 or elapsed > (15*60 if task_progress == 0 else 90*60):
            if completed:
                return ('success', f'✅ 已有完整的未来三日预测（{published_at}）；后台进度记录未及时同步。')
            return ('caption', '后台进度记录已过期或时间不可靠；以下展示可获取的已发布数据。')
        return ('info', f'🔄 后台任务状态：{task_progress}/{task_total}；最近记录：'
                f'{status.get("updated_at", "未知")}。下方仍展示可获取的已发布数据。')
    if state == 'completed':
        return ('success', f'✅ 本轮多日概览更新完成，状态更新时间：{status.get("updated_at", "未知")}。')
    if state in ('partial', 'failed', 'rate_limited'):
        if status.get('partial_published'):
            return ('warning', f'⚠️ 本轮 {status.get("success_count", "?")}/{task_total} 项有效，'
                    f'已正式发布；沿用旧数据 {status.get("retained_old_count", 0)} 项，'
                    f'缺失 {status.get("missing_count", 0)} 项。')
        if completed:
            return ('success', f'✅ 当前仍可查看完整三日预测（{published_at}）；最近一次后台任务状态为 {state}。')
        return ('warning', f'⚠️ 后台任务尚未全部完成（{state}）；已成功的预测仍可查看。'
                f'最近状态：{status.get("updated_at", "未知")}。')
    if completed:
        return ('success', f'✅ 已发布完整三日预测（{published_at}）；后台状态信息暂不可用。')
    return ('caption', '后台状态尚未确认；以下展示当前可取得的概览数据。')


def render_overview_status(st, status, snapshot, city=''):
    kind, message = status_view(status, snapshot)
    getattr(st, kind)(message)
