# -*- coding: utf-8 -*-
"""No-weather-network tests for Desktop V2.2.0 GFS OM diagnosis."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from zipfile import ZipFile
from unittest.mock import patch

import pytest
from desktop.gfs_om_case_audit import audit_case_zip
from desktop.gfs_om_archive_replay import bind_saved_horizontal
from desktop.spatial_fusion_desktop import SpatialDesktopMixin


def state(model='ncep_gfs_global'):
    profile = {'distance_km': 0, 'lat': 31.8047, 'lon': 117.2214,
               'time': '2026-11-19 23:00 UTC',
               'spatial_horizontal_valid_time': '2026-11-19T23:00:00+00:00',
               'spatial_horizontal_time_uncertainty': True,
               'spatial_horizontal_run': '2026-11-17T18:00:00+00:00',
               'spatial_horizontal_om': {'cloud_cover_low':0,'cloud_cover_mid':0,'cloud_cover_high':13.9}}
    sample = {'distance_km':0,'lat':31.8047,'lon':117.2214,
              'cloud_cover_low':0,'cloud_cover_mid':0,'cloud_cover_high':13.9,
              'point_api_area_cloud_cover_low':0,
              'point_api_area_cloud_cover_mid':1.0,
              'point_api_area_cloud_cover_high':100.0}
    return {'place':'合肥','date':'2026-11-20', 'weather_model':'GFS Global',
            'direction_results':{'morning':{'query_model_id':model,
                 'samples':[sample], 'profiles':[profile], 'inferred':{'regular_score':12.0}}}}


def case_zip(tmp_path, data):
    file=tmp_path/'case.zip'
    with ZipFile(file,'w') as z:
        z.writestr('case/analysis_state.json',json.dumps(data,ensure_ascii=False))
    return file


def test_archive_gfs_legacy_provenance_and_old_value_restored(tmp_path):
    r=audit_case_zip(case_zip(tmp_path,state()))
    d=r['directions']['morning']
    assert d['calibration']['conflict_count']==1
    assert d['legacy_provenance_inferred']==1
    high=next(x for x in r['rows'] if x['layer']=='高云')
    assert high['native_api_pct']==100.0
    assert high['raw_om_pct']==pytest.approx(13.9)
    assert high['old_calculation_pct']==pytest.approx(13.9)
    assert high['current_effective_pct']==100.0
    assert r['scores_recomputed'] is False


def test_archive_ec_not_treated_as_gfs_proxy(tmp_path):
    d=state('ecmwf_ifs025')
    d['weather_model']='EC'
    r=audit_case_zip(case_zip(tmp_path,d))
    assert r['directions']['morning']['legacy_provenance_inferred']==0
    assert r['directions']['morning']['calibration']['conflict_count']==0
    assert next(x for x in r['rows'] if x['layer']=='高云')['current_effective_pct']==pytest.approx(13.9)


def test_archive_zip_traversal_not_extracted(tmp_path):
    case=tmp_path/'case.zip'
    with ZipFile(case,'w') as z:
        z.writestr('../analysis_state.json',json.dumps(state()))
    # The audit only reads the trusted name; absolutely no file extracted.
    r=audit_case_zip(case)
    assert len(r['rows'])==3
    assert not (tmp_path.parent/'analysis_state.json').exists()


def test_archive_replay_matches_exact_time_coords_and_model():
    st=state()
    x=deepcopy(st['direction_results']['morning']['profiles'][0])
    for field in tuple(x):
        if field.startswith('spatial_'):x.pop(field)
    p={'samples':[x]}
    result=bind_saved_horizontal(p,st,datetime(2026,11,19,22,45,tzinfo=timezone.utc),'ncep_gfs_global')
    assert result['attached']==1
    assert p['samples'][0]['spatial_horizontal_model_domain']=='ncep_gfs025'
    assert p['samples'][0]['spatial_horizontal_om']['cloud_cover_high']==pytest.approx(13.9)
    assert bind_saved_horizontal({'samples':[x]},st,datetime(2026,11,20,8,tzinfo=timezone.utc),'ncep_gfs_global')['attached']==0
    assert bind_saved_horizontal({'samples':[x]},st,datetime(2026,11,19,22,45,tzinfo=timezone.utc),'ecmwf_ifs025')['attached']==0


def test_desktop_replay_forbids_om_network(monkeypatch):
    import engine
    st=state()
    x=deepcopy(st['direction_results']['morning']['profiles'][0])
    for field in tuple(x):
        if field.startswith('spatial_'):x.pop(field)
    class Base:
        def _fetch_cloud_base_profiles(self,coords,distances,sunset_utc,model_id,local_ground_elevation_m=None):
            return {'samples':[deepcopy(x)]}
    class App(SpatialDesktopMixin,Base):
        _spatial_enabled_snapshot=True
        loaded_case_state=st
        spatial_fetch_log=[]
    app=App()
    monkeypatch.setattr(engine,'_CASE_REQUEST_CONTEXT',{'mode':'replay','cache':{}})
    with patch('desktop.spatial_fusion_desktop.fetch_desktop_horizontal',side_effect=AssertionError('network used')):
        r=app._fetch_cloud_base_profiles([(31.8047,117.2214)],[0],datetime(2026,11,19,22,45,tzinfo=timezone.utc),'ncep_gfs_global')
    assert r['samples'][0]['spatial_horizontal_om']['cloud_cover_high']==pytest.approx(13.9)
    assert app.spatial_fetch_log[-1]['status']=='archive_om_used'
