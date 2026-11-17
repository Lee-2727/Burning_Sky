"""OM 八方位：时间窗口、云洞南北偏移、中高云加权、取消白圈。"""
from datetime import datetime, timedelta
import numpy as np
import pytest
from burning_sky import compass_illuminated_sectors as c
from desktop.spatial_area_analysis import render_compass_figure


def sample_clouds():
    lat,lon=np.meshgrid(np.linspace(26,38,61),np.linspace(108,126,91),indexing='ij')
    low=np.zeros_like(lat)
    mid=np.ones_like(lat)*70.
    high=np.zeros_like(lat)
    return {'grid_view':{'latitude':lat.tolist(),'longitude':lon.tolist(),
                         'layers':{'cloud_cover_low':low.tolist(),
                                   'cloud_cover_mid':mid.tolist(),
                                   'cloud_cover_high':high.tolist()}},
            'spatial_source':{'domain':'ecmwf_ifs025',
                             'reference_time':'2026-11-17T06:00:00+00:00',
                             'valid_time':'2026-11-19T09:00:00+00:00'}}


@pytest.mark.parametrize('direction, sign',[('morning',-1),('evening',1)])
def test_strict_event_window(monkeypatch,direction,sign):
    event=datetime(2026,11,19,17,5)
    called=[]
    def fake_sun(t,*args):
        called.append(t)
        return (-3,260)
    monkeypatch.setattr(c,'solar_position',fake_sun)
    monkeypatch.setattr(c,'_gap_support',lambda *args: .8)
    c.compass_from_verified_rays(sample_clouds(),31.8,117.2,event,direction)
    offsets=[(t-event).total_seconds()/60 for t in called]
    # 最后一项允许参考事件本身用于画虚线；用于评分的所有时刻必须在正确一侧。
    assert len(offsets)==11
    assert offsets[-1]==0
    assert all((v<0 if sign<0 else v>0) for v in offsets[:-1])
    assert min(map(abs,offsets[:-1]))>=1


def test_gap_offset_to_southwest_and_northwest(monkeypatch):
    monkeypatch.setattr(c,'solar_position',lambda *args:(-3.,270.))
    chosen={'az':225.}
    monkeypatch.setattr(c,'_gap_support',lambda field,la,lo,b: 1.0 if c._angle_diff(b,chosen['az'])<1 else 0.0)
    d=sample_clouds()
    a=c.compass_from_verified_rays(d,31.8,117.2,datetime(2026,11,19,17,0),'evening')
    scores={x['name']:x['relative_score'] for x in a['sectors']}
    assert scores['西南']>scores['西北']
    chosen['az']=315.
    b=c.compass_from_verified_rays(d,31.8,117.2,datetime(2026,11,19,17,0),'evening')
    scores={x['name']:x['relative_score'] for x in b['sectors']}
    assert scores['西北']>scores['西南']


def test_mid_high_cloud_weights_score(monkeypatch):
    monkeypatch.setattr(c,'solar_position',lambda *args:(-3.,270.))
    monkeypatch.setattr(c,'_gap_support',lambda *args:.8)
    full=sample_clouds()
    fig=render_compass_figure(c.compass_from_verified_rays(full,31.8,117.2,datetime(2026,11,19,17,0),'evening'))
    assert fig.axes[0].name=='polar'
    # 白圈曾通过 ax.scatter(facecolors='none', ...) 添加；现在只留下日字标记圆圈。
    assert len(fig.axes[0].collections)==2
    for l in fig.axes[0].lines:
        assert l.get_linewidth()!=0
    empty=sample_clouds()
    empty['grid_view']['layers']['cloud_cover_mid']=(np.zeros((61,91))).tolist()
    after=c.compass_from_verified_rays(empty,31.8,117.2,datetime(2026,11,19,17,0),'evening')
    assert all(x['relative_score']==0 for x in after['sectors'] if x['relative_score'] is not None)


def test_unknown_upstream_is_not_clear(monkeypatch):
    monkeypatch.setattr(c,'solar_position',lambda *args:(-3.,270.))
    monkeypatch.setattr(c,'_gap_support',lambda *args:None)
    r=c.compass_from_verified_rays(sample_clouds(),31.8,117.2,datetime(2026,11,19,17,0),'evening')
    assert r['evaluated_solar_paths']==0
    assert all(x['relative_score'] is None for x in r['sectors'])
