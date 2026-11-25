import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import engine


def test_scale_uses_mid_high_cover_times_tintable_ratio():
    value, label = engine.calculate_burn_scale(8, 23, 85)
    assert value == 26.35
    assert label == "微烧"


def test_scale_classes_use_effective_cloud_amount_not_ratio_alone():
    value, label = engine.calculate_burn_scale(8, 23, 100)
    assert value == 31
    assert label == "小烧"


def test_scale_caps_combined_mid_high_cover_at_100_percent():
    value, label = engine.calculate_burn_scale(80, 50, 100)
    assert value == 100
    assert label == "大烧"


def test_scale_is_unavailable_when_required_inputs_are_missing_or_invalid():
    assert engine.calculate_burn_scale(8, None, 85) == (None, "暂不可判定")
    assert engine.calculate_burn_scale(8, 23, math.nan) == (None, "暂不可判定")
