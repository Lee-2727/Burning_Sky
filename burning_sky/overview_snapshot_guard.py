"""Non-destructive selection of public five-day overview data and checkpoints.

A checkpoint is working progress, not a replacement for an already complete
published snapshot. The only permissible merge from a partial checkpoint is
filling genuinely absent records; an existing valid published metric wins.
"""
from copy import deepcopy
from datetime import date, timedelta
import math

MODELS = ('ECMWF IFS 0.25°', 'GFS Global')
DIRECTIONS = ('morning', 'evening')
FIELDS = ('probability', 'color_score', 'burn_scale')


def valid_record(row):
    return isinstance(row, dict) and all(
        isinstance(row.get(field), (int, float))
        and not isinstance(row.get(field), bool)
        and math.isfinite(row[field])
        for field in FIELDS
    )


def count_valid(snapshot, dates, models=MODELS):
    days = (snapshot or {}).get('days') if isinstance(snapshot, dict) else None
    if not isinstance(days, list):
        return 0
    index = {str(day.get('date')): day for day in days if isinstance(day, dict)}
    return sum(valid_record(((index.get(day) or {}).get(direction) or {}).get(model))
               for day in dates for direction in DIRECTIONS for model in models)


def expected_dates(first_day, count=5):
    first = date.fromisoformat(str(first_day))
    return [(first + timedelta(days=i)).isoformat() for i in range(count)]


def restore_missing_only(published, checkpoint, dates, completed, city, models=MODELS):
    """Return (snapshot, filled_count) without overwriting published rows.

    The checkpoint is only trusted for explicitly completed task identifiers.
    It is never promoted as an entire new snapshot, and its stale inherited
    records cannot overwrite a published result.
    """
    if not isinstance(published, dict) or not isinstance(checkpoint, dict):
        return published, 0
    result = deepcopy(published)
    dst_days = result.get('days')
    source_days = checkpoint.get('days')
    if not isinstance(dst_days, list) or not isinstance(source_days, list):
        return published, 0
    valid_dates = set(dates)
    by_src = {str(day.get('date')): day for day in source_days if isinstance(day, dict)}
    by_dst = {str(day.get('date')): day for day in dst_days if isinstance(day, dict)}
    completed = set(completed or ())
    filled = 0
    for day_str in dates:
        src = by_src.get(day_str)
        if not isinstance(src, dict):
            continue
        dst = by_dst.get(day_str)
        if dst is None:
            dst = {'date': day_str, 'morning': {}, 'evening': {}}
            dst_days.append(dst)
            by_dst[day_str] = dst
        for direction in DIRECTIONS:
            src_group = src.get(direction) or {}
            if not isinstance(src_group, dict):
                continue
            dst_group = dst.setdefault(direction, {})
            if not isinstance(dst_group, dict):
                continue
            for model in models:
                task_id = '|'.join((city, day_str, model, direction))
                src_record = src_group.get(model)
                if (task_id in completed and valid_record(src_record)
                        and not valid_record(dst_group.get(model))):
                    dst_group[model] = deepcopy(src_record)
                    filled += 1
    dst_days.sort(key=lambda d: str(d.get('date', '')))
    # Keep the official publication timestamp. A checkpoint cannot assert
    # that the full 20-item batch was published at its saved_at time.
    return result, filled
