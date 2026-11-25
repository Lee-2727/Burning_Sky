# -*- coding: utf-8 -*-
"""No-network tests for the Pro three-zone map and restored 8-bearing radar."""
from datetime import date,datetime,timezone
from unittest.mock import patch
import numpy as np


def _sample_grid():
    lon,lat=np.meshgrid(np.linspace(65,144,81),np.linspace(12,60,51))
    return {'samples':[], 'grid_view':{
        'latitude':lat.tolist(),'longitude':lon.tolist(),
        'layers':{'cloud_cover_low':np.where(lon>107,10.,12.).tolist(),
                  'cloud_cover_mid':np.where(lon>113,53.,40.).tolist(),
                  'cloud_cover_high':np.where(lon<95,65.,45.).tolist()}},
        'spatial_source':{'domain':'ecmwf_ifs025','reference_time':'2026-11-17T06:00:00+00:00',
                          'valid_time':'2026-11-19T09:00:00+00:00'}}


def test_automatic_solar_event_east_before_west():
    from burning_sky.web_national_map import _local_event, REGIONS
    day=date(2026,11,19)
    for direction in ('morning','evening'):
        times=[_local_event(day,r['reference_lat'],r['reference_lon'],direction) for r in REGIONS]
        assert times[2]<times[1]<times[0]
        assert all(t.date()==day for t in times)


def test_atomic_three_zone_reuse_and_red_map(tmp_path,monkeypatch):
    from burning_sky import web_national_map as m,spatial_om
    import desktop.spatial_area_analysis as area
    monkeypatch.setattr(m,'CACHE',tmp_path)
    monkeypatch.setattr(spatial_om,'_get_metadata_cached',lambda *a:{'completed':True})
    requested=[];get_count=[]
    def resolve(mid,t,metadata=None):
        requested.append(t)
        return {'model_domain':'ecmwf_ifs025','reference_time':'2026-11-17T06:00:00+00:00',
                'valid_time':t.isoformat(),'delta_seconds':0,'variables':{'cloud_cover_low','cloud_cover_mid','cloud_cover_high'}}
    def get_cloud(src,**kwargs):
        assert kwargs['bounds'][0]<73 and kwargs['bounds'][1]>136 # upstream border
        get_count.append(src['valid_time'])
        grid=_sample_grid();grid['spatial_source']['valid_time']=src['valid_time']
        return grid
    def fake_candidate(grid,*,longitude_bounds,latitude_bounds,**kwargs):
        la=np.asarray(grid['grid_view']['latitude'])
        lo=np.asarray(grid['grid_view']['longitude'])
        score=np.full(la.shape,np.nan,dtype=float)
        score[(lo>=longitude_bounds[0])&(lo<longitude_bounds[1])&
              (la>=latitude_bounds[0])&(la<=latitude_bounds[1])]=35.
        return {'score':score,'stride':4,'in_twilight_cells':20,'evaluated_cells':18}
    monkeypatch.setattr(spatial_om,'resolve_valid_file',resolve)
    monkeypatch.setattr(spatial_om,'fetch_national_clouds_from_source',get_cloud)
    monkeypatch.setattr(area,'national_candidate',fake_candidate)
    d=date(2026,11,19)
    first=m.fetch_and_store('EC 0.25°',d,'evening')
    assert len(requested)==len(get_count)==3
    assert len({str(t) for t in requested})==3
    assert first['direction']=='evening' and len(first['zones'])==3
    assert first['data']['spatial_source']['time_mosaic'] is True
    assert len(first['score'])==51
    assert m.load_cached('EC 0.25°',d,'evening')
    assert m.load_cached('EC 0.25°',d,'morning') is None
    with patch('urllib.request.urlopen',side_effect=AssertionError('painting should be offline')):
        fig=m._render_potential_map(first)
        png=tmp_path/'red.png';fig.savefig(png)
        assert png.stat().st_size>9000
        cloud=m._render_cloud_mosaic(first,'中云')
        cloud.savefig(tmp_path/'cloud.png')
    second=m.fetch_and_store('EC 0.25°',d,'evening')
    assert len(get_count)==6
    assert m.load_cached('EC 0.25°',d,'evening')['schema_version']==2


def test_eight_bearing_figure_is_polar_not_bars(tmp_path):
    from desktop.spatial_area_analysis import render_compass_figure
    rows=[{'name':n,'bearing':i*45,'relative_score':float(20+i*8)}
          for i,n in enumerate(['北','东北','东','东南','南','西南','西','西北'])]
    result={'sectors':rows,'direction':'evening','model':'ecmwf_ifs025',
            'valid':'2026-11-19T09:00:00+00:00','sun_azimuth_deg':255.0}
    fig=render_compass_figure(result)
    assert len(fig.axes)==1 and fig.axes[0].name=='polar'
    assert len(fig.axes[0].lines)>=2 # polygon + sun bearing
    fig.savefig(tmp_path/'radar.png')
    assert (tmp_path/'radar.png').stat().st_size>5000
