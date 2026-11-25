"""User-facing visual burn-scale grades; geometric scale remains diagnostic."""
from __future__ import annotations
import math


def visual_scale_grade(value):
    try:
        score=float(value)
    except (TypeError,ValueError):
        return '暂不可估'
    if not math.isfinite(score):
        return '暂不可估'
    score=max(0.0,min(100.0,score))
    if score < 3.0: return '不烧'
    if score <= 15.0: return '微烧'
    if score <= 40.0: return '小烧'
    if score <= 65.0: return '中烧'
    if score <= 90.0: return '大烧'
    return '世纪大烧'
