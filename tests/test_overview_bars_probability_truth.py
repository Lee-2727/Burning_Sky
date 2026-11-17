"""Offline HTML regressions for literal probability opacity in Pro and Lite."""
import re
from burning_sky.overview_visual import render_direction
from lite.bar_cards import render

EC = 'ECMWF IFS 0.25°'
GFS = 'GFS Global'

def _days(ec_p, gfs_p, scale=40):
    return [{'date': '2026-11-18', 'morning': {
        EC: {'probability': ec_p, 'color_score': 72, 'burn_scale': scale},
        GFS: {'probability': gfs_p, 'color_score': 62, 'burn_scale': scale},
    }}]

def test_pro_zero_probability_draws_no_bar_or_outline():
    out = render_direction(_days(0, 65), 'morning', '朝霞')
    assert '常规概率 0%' in out
    assert 'height:0;flex:none;border:0;background:none' in out
    assert 'opacity:0.0000' not in out
    assert 'opacity:0.6500' in out
    assert 'border:1px solid #' not in out

def test_pro_one_hundred_probability_no_extra_fade():
    out = render_direction(_days(100, 25), 'morning', '朝霞')
    assert 'opacity:1.0000' in out
    assert 'opacity:0.2500' in out
    assert 'min-height:2px' not in out

def test_lite_zero_is_no_visible_bar_even_with_nonzero_scale():
    out = render(_days(0, 65), 'morning', '朝霞')
    assert 'class="bs-lite-bar empty"' in out
    assert 'opacity:0.0000' not in out
    assert 'opacity:0.6500' in out

def test_lite_bar_no_independent_outline_or_gray_overlay():
    from lite.bar_cards import CSS
    out = render(_days(100, 25), 'morning', '朝霞')
    assert 'opacity:1.0000' in out
    assert 'opacity:0.2500' in out
    assert '.bs-lite-bar{width:' in CSS
    assert 'border:0;border-radius:' in CSS
    assert 'gray' not in CSS.lower()

def test_missing_is_distinct_from_zero():
    rows = [{'date':'2026-11-18', 'morning':{EC:{'probability':0,'color_score':50,'burn_scale':30}}}]
    pro = render_direction(rows,'morning','朝霞')
    lite = render(rows,'morning','朝霞')
    assert '数据缺失' in pro
    assert '暂无数据' in lite
    assert 'bs-lite-bar missing' in lite
    assert 'bs-lite-bar empty' in lite
