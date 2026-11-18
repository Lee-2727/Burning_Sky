"""Publish valid overview rows independently, without inventing missing metrics.

One failed model/day/direction must not block the other rows.  Prior valid
records for the exact same date/model/direction are retained and labelled stale.
"""
from __future__ import annotations

from copy import deepcopy
from burning_sky.overview_snapshot_guard import DIRECTIONS, valid_record


def prepare_publication(snapshots, dates, models, completed, failures, stamp, batch_day):
    """Return (new_snapshots, summary) without mutating runner checkpoints.

    `completed` is a set of task identifiers with validated results from this
    batch only, and is NOT inflated with carried-forward old values.
    """
    snapshots = deepcopy(snapshots)
    completed = set(completed)
    failed = {"|".join(str(row.get(k, '')) for k in ('city', 'date', 'model', 'direction'))
              for row in failures if isinstance(row, dict)}
    stale, missing = [], []
    total = 0
    current_valid = 0
    visible_valid = 0
    for city, snapshot in snapshots.items():
        by_date = {str(day.get('date')): day for day in snapshot.get('days', [])
                   if isinstance(day, dict)}
        updated_directions = set()
        city_stale, city_missing = [], []
        for day in dates:
            date_string = day.isoformat() if hasattr(day, 'isoformat') else str(day)
            row = by_date.get(date_string) or {}
            for direction in DIRECTIONS:
                group = row.get(direction) or {}
                for label in models:
                    key = '|'.join((city, date_string, label, direction))
                    valid = valid_record(group.get(label))
                    total += 1
                    visible_valid += int(valid)
                    if key in completed and valid:
                        current_valid += 1
                        updated_directions.add(direction)
                    elif valid:
                        stale.append(key)
                        city_stale.append(key)
                    else:
                        missing.append(key)
                        city_missing.append(key)
        snapshot['updated_at'] = stamp
        times = dict(snapshot.get('direction_updated_at') or {})
        for direction in updated_directions:
            times[direction] = stamp
        snapshot['direction_updated_at'] = times
        snapshot['publication'] = {
            'state': 'partial' if failures or city_stale or city_missing else 'completed',
            'batch_day': batch_day,
            'published_at': stamp,
            'valid_in_batch': sum(key.split('|', 1)[0] == city for key in completed),
            'retained_old_keys': city_stale,
            'missing_keys': city_missing,
            'failed_keys': sorted(k for k in failed if k.startswith(city + '|')),
        }
    return snapshots, {
        'total': total, 'current_valid': current_valid,
        'visible_valid': visible_valid, 'stale': stale, 'missing': missing,
        'failed': sorted(failed),
    }
