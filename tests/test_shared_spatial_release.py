"""Offline EC/GFS release wiring tests: no network or real forecasts."""
from datetime import datetime,timezone,date
from pathlib import Path
import json
import pytest
from burning_sky.spatial_pipeline import HybridSpatialMixin,calibrate_path_samples,fetch_with_spatial,_BATCH_CACHE


def _row(distance,lat=31.8047,lon=117.2214,cover=70):
    return {'distance_km':distance,'lat':lat,'lon':lon,'time':'2026-11-19T09:00:00+00:00',
            'levels':[], 'cloud_cover_low':10.,'cloud_cover_mid':15.,'cloud_cover_high':20.,'cloud_cover':45.}


def _source(d=0,cloud=(20,65,32)):
    return {'samples':[{'distance_km':d,'lat':31.8047,'lon':117.2214,
             'cloud_cover_low':cloud[0], 'cloud_cover_mid':cloud[1], 'cloud_cover_high':cloud[2]}],
        'spatial_source':{'reference_time':'2026-11-17T06:00:00Z','valid_time':'2026-11-19T09:00:00Z',
                          'layer_origin':'native_layers'}}


def test_shared_hybrid_binds_and_updates_original_inputs(monkeypatch):
    from burning_sky import spatial_om
    _BATCH_CACHE.clear()
    requests=[]
    monkeypatch.setattr(spatial_om,'fetch_horizontal_clouds',lambda *a,**kw:(requests.append(1),_source())[1])
    class Base:
        def _fetch_cloud_base_profiles(self,*a,**kw):return {'samples':[_row(0)]}
    class Runner(HybridSpatialMixin,Base):
        spatial_enabled=True
        spatial_fetch_log=[]
    app=Runner()
    out=app._fetch_cloud_base_profiles([(31.8047,117.2214)],[0],datetime(2026,11,19,9), 'ecmwf_ifs025')
    attached=out['samples'][0]
    assert attached['spatial_horizontal_om']['cloud_cover_mid']==65
    s=_row(0)
    diag=calibrate_path_samples([s],out['samples'])
    assert diag['status']=='applied'
    assert s['cloud_cover_mid']==65
    assert s['point_api_area_cloud_cover_mid']==15
    assert s['cloud_cover']==45  # don't manufacture total from 3 overlapping bands
    # Same batch can be reused by many queries in the same process.
    app._fetch_cloud_base_profiles([(31.8047,117.2214)],[0],datetime(2026,11,19,9), 'ecmwf_ifs025')
    assert len(requests)==1


def test_manual_overrides_never_overwritten():
    s=_row(0);p=_row(0)
    p['spatial_horizontal_om']={'cloud_cover_low':0,'cloud_cover_mid':0,'cloud_cover_high':0}
    assert calibrate_path_samples([s],[p],manual=True)['status']=='manual_priority'
    assert s['cloud_cover_mid']==15


def test_invalid_layer_leaves_original_intact():
    s=_row(0);p=_row(0)
    p['spatial_horizontal_time_uncertainty']=True
    p['spatial_horizontal_om']={'cloud_cover_low':10,'cloud_cover_mid':None,'cloud_cover_high':90}
    assert calibrate_path_samples([s],[p])['status']=='not_available'
    assert s['cloud_cover_high']==20


def test_om_failure_has_api_fallback(monkeypatch):
    from burning_sky import spatial_om
    _BATCH_CACHE.clear()
    monkeypatch.setattr(spatial_om,'fetch_horizontal_clouds',lambda *a,**k:(_ for _ in ()).throw(ValueError('missing')))
    class Base:
        def _fetch_cloud_base_profiles(self,*a,**kw):return {'samples':[_row(0)]}
    class Runner(HybridSpatialMixin,Base):
        spatial_enabled=True
    app=Runner()
    out=app._fetch_cloud_base_profiles([(31.8047,117.2214)],[0],datetime(2026,11,19,9),'ncep_gfs_global')
    assert 'spatial_horizontal_om' not in out['samples'][0]
    assert app.spatial_fetch_log[0]['status']=='point_api_fallback'


def test_map_json_nan_is_null_and_second_click_overwrites(tmp_path,monkeypatch):
    from burning_sky import web_national_map as m,spatial_om
    monkeypatch.setattr(m,'CACHE',tmp_path)
    monkeypatch.setattr(spatial_om,'resolve_valid_file',lambda *_:dict(model_domain='ecmwf_ifs025',
        reference_time='2026-11-17T06:00:00Z',valid_time='2026-11-19T09:00:00Z',variables={'cloud_cover_low','cloud_cover_mid','cloud_cover_high'}))
    calls=[]
    def get(*a,**k):
        calls.append(1)
        return {'samples':[],'grid_view':{'latitude':[[30.]],'longitude':[[115.]],
                'layers':{'cloud_cover_low':[[float('nan')]],'cloud_cover_mid':[[32.]],'cloud_cover_high':[[50.]]}},
                'spatial_source':{'domain':'ecmwf_ifs025'}}
    monkeypatch.setattr(spatial_om,'fetch_national_clouds_from_source',get)
    m.fetch_and_store('EC 0.25°',date(2026,11,19))
    path=m._path('EC 0.25°',date(2026,11,19))
    assert json.loads(path.read_text())['data']['grid_view']['layers']['cloud_cover_low'][0][0] is None
    m.fetch_and_store('EC 0.25°',date(2026,11,19))
    assert len(calls)==2
    assert m.load_cached('EC 0.25°',date(2026,11,19))


def test_original_scoring_functions_reused_after_cloud_calibration():
    # No new OM score multiplier: unchanged scorer consumes corrected cloud path.
    from burning_sky.algorithm import infer_from_cloud_path,calculate_color_performance_score
    samples=[{'distance_km':d,'lat':31.8,'lon':117.2,'cloud_cover':55.,
              'cloud_cover_low':80.,'cloud_cover_mid':12.,'cloud_cover_high':5.,
              'visibility_m':18000.,'wind_ms':4.} for d in (0,25,50,100)]
    before=calculate_color_performance_score(infer_from_cloud_path([s.copy() for s in samples],.20))
    p=[{'distance_km':s['distance_km'],'lat':s['lat'],'lon':s['lon'],
        'spatial_horizontal_time_uncertainty':True,
        'spatial_horizontal_om':{'cloud_cover_low':6.,'cloud_cover_mid':51.,'cloud_cover_high':38.}}
        for s in samples]
    calibrate_path_samples(samples,p)
    after=calculate_color_performance_score(infer_from_cloud_path(samples,.20))
    assert before['score']==20
    assert after['score']==88
    # Cloud quantity affects the distinct chance/scale metrics, not the
    # colour quality of an illuminated zone under the same atmosphere.
    assert before['color_score']==after['color_score']
