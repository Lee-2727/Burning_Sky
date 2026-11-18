"""Regression tests for the 700-km solar-source gate; run with pytest."""
from datetime import datetime
from unittest.mock import patch
import math

from burning_sky.sky_six import adaptive_equivalent_gaps

DT = datetime(2026, 11, 18, 6, 41)
DISTS = (0,25,50,100,150,200,250,300,350,400,450,500,550,600,650,700,750)


def profiles(block_at=None, missing_at=None):
    out=[]
    for dist in DISTS:
        levels=[]
        for height in (3.0,6.0,10.0,16.0,22.0):
            cover=(80. if dist==0 and height==6.0 else
                   100. if dist==block_at and height==6.0 else 0.)
            levels.append({'height_agl_km':height, 'cloud_cover':cover, 'pressure_hpa':500})
        out.append({'distance_km':dist, 'lat':31.8, 'lon':117.2,
                    'levels':[] if dist==missing_at else levels})
    return out


def run(profile_data, altitude=-1.7):
    with patch('burning_sky.sky_six.solar_position', return_value=(altitude,90.0)):
        return adaptive_equivalent_gaps(profile_data,DT,8,31.8,117.2,'morning',27.52,
                                        source_scan_km=700,target_limit_km=25)


def test_clear_full_path():
    r=run(profiles())
    assert r['status']=='local_targets_verified',r
    assert r['verified_ray_traces']
    assert all(x['source_check_km']>=700 for x in r['verified_ray_traces'])
    # The old 700 km is only a lower bound; the solar/target geometry can
    # extend source checks to 750 km. Never bless an unmeasured continuation.
    assert all(x['source_boundary_altitude_km']>0
               for x in r['verified_ray_traces'])


def test_far_cloud_blocks_even_if_250_km_clear():
    r=run(profiles(block_at=400))
    assert r['status']=='no_verified_source',r
    assert not r['verified_ray_traces']


def test_missing_far_profile_is_unresolved():
    r=run(profiles(missing_at=500))
    assert r['status']=='unresolved_data',r
    assert 500 in r['missing_profile_distances_km']


def test_short_profile_does_not_promote_clear():
    r=run(profiles()[:7])
    assert r['status']=='unresolved_data',r
    assert not r['verified_ray_traces']


def test_solar_earth_occultation():
    r=run(profiles(),altitude=-6.7)
    assert r['status']=='no_verified_source',r
    assert not r['verified_ray_traces']
