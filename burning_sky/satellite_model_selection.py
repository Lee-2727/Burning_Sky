"""Shared EC/GFS baseline selection for satellite screenshot calibration."""
from __future__ import annotations

import math


def choose_cached_model(diagnostics: dict, *, tie_threshold: float = 2.0,
                        preferred: str = 'EC') -> str:
    """Choose the best-scoring cached model, preferring EC for near-ties.

    Lower scores indicate a closer qualitative match to the satellite screenshot.
    If both EC and GFS have valid scores less than ``tie_threshold`` apart, EC
    wins regardless of point count or confidence. If only one model can be
    scored, use it. If neither can be scored, use EC when available, otherwise
    the first available model.
    """
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    preferred = str(preferred or 'EC').upper()
    valid = {}
    for raw_code, item in diagnostics.items():
        code = str(raw_code).upper()
        if code not in ('EC', 'GFS') or not isinstance(item, dict):
            continue
        score = item.get('score')
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        if math.isfinite(score):
            valid[code] = score

    if 'EC' in valid and 'GFS' in valid:
        if abs(valid['EC'] - valid['GFS']) < float(tie_threshold):
            return 'EC'
        return 'EC' if valid['EC'] < valid['GFS'] else 'GFS'

    if len(valid) == 1:
        return next(iter(valid))

    if preferred in diagnostics:
        return preferred
    for code in ('EC', 'GFS'):
        if code in diagnostics:
            return code
    return next(iter(diagnostics), preferred)
