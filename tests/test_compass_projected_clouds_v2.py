"""Source-cloud alignment and view-sector test (no network)."""
from datetime import datetime
from pathlib import Path
import numpy as np
import pytest

from burning_sky.compass_illuminated_sectors import compass_from_verified_rays


def _field(region):
    lat,lon=np.meshgrid(np.linspace(20,43,95),np.linspace(103,131,113),indexing='ij')
    patch=np.zeros(lat.shape,dtype=float)
    if region=='east':
        patch[(lon>117.7)&(lon<119.1)&(abs(lat-31.8)<.6)]=85.
    elif region=='southwest':
        patch[(lon>115.8)&(lon<116.8)&(lat>30.7)&(lat<31.5)]=85.
    elif region=='west':
        patch[(lon>115.8)&(lon<116.8)&(abs(lat-31.8)<.35)]=85.
    elif region=='uniform':
        patch[:]=70.
    return {'grid_view':{
        'latitude':lat.tolist(),'longitude':lon.tolist(),
        'layers':{'cloud_cover_low':np.zeros_like(patch).tolist(),
                  'cloud_cover_mid':patch.tolist(),
                  'cloud_cover_high':np.zeros_like(patch).tolist()}},
        'spatial_source':{'domain':'ecmwf_ifs025',
            'reference_time':'2026-11-17T06:00:00+00:00',
            'valid_time':'2026-11-19T09:00:00+00:00'}}


def _evaluate(which):
    return compass_from_verified_rays(_field(which),31.8,117.2,
        datetime(2026,11,19,17,9),'evening',
        verified_rays=[{'minute_offset':0,'target_height_km':6.0,
                        'target_face_km':20.0}])


def _value(r,name):
    return next(s['relative_score'] for s in r['sectors'] if s['name']==name)


def test_southward_offset_shifts_advantage_to_southwest():
    r=_evaluate('southwest')
    assert _value(r,'西南')>_value(r,'西')
    assert _value(r,'西南')>_value(r,'东南')
    assert r['seeded_verified_minutes']>0
    assert r['method']=='projected_target_clouds_from_solar_rays_v2'


def test_western_cloud_shifts_advantage_west():
    r=_evaluate('west')
    assert _value(r,'西')>_value(r,'西南')


def test_antisolar_clouds_make_backfire_candidate_without_false_verified_claim():
    r=_evaluate('east')
    assert _value(r,'东')>_value(r,'西')
    assert r['backfire_candidate_checks']>0
    assert '尚无各自完整垂直剖面' in r['note']
    assert sum(s.get('strict_original_ray_seeds',0) for s in r['sectors'])==1


def test_missing_source_not_treated_as_zero():
    field=_field('uniform')
    # Remove all upstream geometry outside a tiny square; local target cells
    # exist, but they cannot be marked unlit or lit without full 700-km cover.
    arrays=field['grid_view']
    y=np.asarray(arrays['latitude']);x=np.asarray(arrays['longitude'])
    keep=(abs(y-31.8)<1)&(abs(x-117.2)<1)
    for name in ('cloud_cover_low','cloud_cover_mid','cloud_cover_high'):
        v=np.asarray(arrays['layers'][name]);v[~keep]=np.nan
        arrays['layers'][name]=v.tolist()
    r=compass_from_verified_rays(field,31.8,117.2,
         datetime(2026,11,19,17,9),'evening')
    assert all(s['relative_score'] is None for s in r['sectors']
               if s['cloud_cells']>0)


def test_no_verified_seed_does_not_claim_strict_illumination():
    r=compass_from_verified_rays(_field('east'),31.8,117.2,
         datetime(2026,11,19,17,9),'evening',verified_rays=[])
    assert r['strict_source_rays']==0
    assert not any(s.get('strict_original_ray_seeds') for s in r['sectors'])
    assert _value(r,'东')>0


def test_figure_is_polar_and_marks_strict_seed(tmp_path):
    from desktop.spatial_area_analysis import render_compass_figure
    r=_evaluate('east')
    fig=render_compass_figure(r)
    assert fig.axes[0].name=='polar'
    assert len(fig.axes[0].collections)>=2
    out=tmp_path/'compass.png';fig.savefig(out)
    assert out.stat().st_size>5000


def test_existing_legacy_result_figure_still_works():
    from desktop.spatial_area_analysis import render_compass_figure
    s=[{'bearing':i*45,'relative_score':0.0,'name':x} for i,x in enumerate(
       ['北','东北','东','东南','南','西南','西','西北'])]
    fig=render_compass_figure({'sectors':s,'direction':'evening',
         'sun_azimuth_deg':250,'valid':'test','model':'test'})
    assert fig.axes[0].name=='polar'
