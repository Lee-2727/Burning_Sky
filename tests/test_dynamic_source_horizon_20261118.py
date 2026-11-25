"""Non-700-km solar cloud-source coverage. Offline physical regression."""
from datetime import datetime
from unittest.mock import patch
from burning_sky.sky_six import (
    dynamic_solar_source_horizon, adaptive_equivalent_gaps,
    _solar_cloud_exit_travel_km, _cloud_ray_intersects_bottom)
from burning_sky.spherical_raytrace import _ray_height_km

DT = datetime(2026,11,20,6,41)
RANGE=[0,25,50]+list(range(100,1201,50))


def data(max_distance=1200,blocker=False, missing=None):
    rows=[]
    for distance in RANGE:
        if distance > max_distance:break
        levels=[{'height_agl_km':h,'cloud_cover':(
            85. if distance in (200,250) and h==10.0 else
            100. if blocker and distance in (850,900,950) and h in (7.,8.,9.,10.,12.) else 0.),
            'pressure_hpa':350} for h in (3.,7.,8.,9.,10.,12.,16.,22.)]
        rows.append({'distance_km':distance,'lat':0.,'lon':0.,
                     'levels':[] if missing==distance else levels})
    return rows


def plan(rows,alt=-3.):
    with patch('burning_sky.sky_six.solar_position',return_value=(alt,90.)):
        return dynamic_solar_source_horizon(rows,DT,8,0.,0.,'morning',550.,
                                            target_limit_km=275.)


def trace(rows,alt=-3.):
    with patch('burning_sky.sky_six.solar_position',return_value=(alt,90.)):
        return adaptive_equivalent_gaps(rows,DT,8,0.,0.,'morning',550.,
                      target_limit_km=275.,source_scan_km=700.,use_horizontal_fusion=False)


def test_lower_sun_demands_longer_real_cloud_sourcing():
    p1=plan(data(),-1.)
    p2=plan(data(),-3.)
    assert p2['required_source_km'] > p1['required_source_km']
    assert p2['required_source_km'] >= 1000, p2
    assert p2['limiting_ray']
    assert abs(_ray_height_km(9.,-3.,_solar_cloud_exit_travel_km(9.,-3.))-20.)<1e-5


def test_cloud_beyond_700_can_block_even_when_near_cloud_hole_is_clear():
    rows=data(blocker=True)
    assert _cloud_ray_intersects_bottom(250.,9.,-3.,
        {'start_km':875.,'end_km':925.,'bottom_km':7.,'top_km':12.},1200.)
    result=trace(rows)
    assert not result.get('verified_ray_traces'),result.get('verified_ray_traces')
    assert result['required_profile_km']>700


def test_missing_far_source_is_not_automatically_clear():
    result=trace(data(max_distance=750))
    assert result['status']=='unresolved_data',result
    assert result['required_profile_km']>result['available_profile_km']
    result2=trace(data(missing=900))
    assert result2['status']=='unresolved_data',result2
    assert 900 in result2['missing_profile_distances_km']


def test_complete_clear_source_can_verify_cloud_bottom():
    result=trace(data())
    assert result['required_profile_km']<=1200,result
    assert result['bottom_verified_point_count']>0,result
    assert any(x['status']=='verified_bottom' for x in result['verified_target_bottom_points'])


def test_earth_still_blocks_even_with_full_far_samples():
    result=trace(data(),alt=-6.)
    assert not result.get('verified_ray_traces')
