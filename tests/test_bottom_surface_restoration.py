"""Regression: cloud BOTTOM illumination is not the same as cloud FRONT height.

These tests use synthetic geometry: no API calls or weather probabilistic assumptions.
"""
from datetime import datetime
from unittest.mock import patch
from burning_sky.sky_six import (
    _cloud_ray_intersects_bottom,_validated_cloud_bottom,adaptive_equivalent_gaps)
from burning_sky.local_cloud_illumination import local_illumination
from burning_sky.spherical_raytrace import profile_cloud_cells

DT=datetime(2026,11,20,6,40)


def test_underside_touch_is_not_self_blocker():
    cloud={'start_km':0.,'end_km':50.,'bottom_km':8.,'top_km':9.}
    # Negative solar altitude: ray moves below cloud base initially.
    assert not _cloud_ray_intersects_bottom(10.,8.,-0.8,cloud,700.)
    # Positive solar altitude: ray enters the cloud material; bottom is dark.
    assert _cloud_ray_intersects_bottom(10.,8.,+0.8,cloud,700.)
    wall={'start_km':70.,'end_km':90.,'bottom_km':4.,'top_km':11.}
    assert _cloud_ray_intersects_bottom(10.,8.,-0.8,wall,700.)


def test_strict_cloud_bottom_is_verified_even_inside_cloud_horizontal_span():
    target={'start_km':0.,'end_km':50.,'bottom_km':8.,'top_km':9.,
            'cloud_cover':90.,'sample_distance_km':25.}
    cells=[target]
    with patch('burning_sky.sky_six.solar_position',return_value=(-0.8,100.0)):
        points=_validated_cloud_bottom(target,-5,DT,100.,0.,0.,8,700.,cells,[],{},False,{})
    assert len(points)==5
    assert all(p['status']=='verified_bottom' for p in points),points


def test_bottom_horizon_and_cloud_wall_are_actual_blockers():
    target={'start_km':0.,'end_km':50.,'bottom_km':8.,'top_km':9.,
            'cloud_cover':90.,'sample_distance_km':25.}
    wall={'start_km':80.,'end_km':100.,'bottom_km':5.,'top_km':15.,'cloud_cover':100.}
    with patch('burning_sky.sky_six.solar_position',return_value=(-0.8,100.0)):
        points=_validated_cloud_bottom(target,-5,DT,100.,0.,0.,8,700.,[target,wall],[],{},False,{})
    assert all(p['status']=='cloud_blocked' for p in points),points
    with patch('burning_sky.sky_six.solar_position',return_value=(-5.0,100.0)):
        points=_validated_cloud_bottom(target,-5,DT,100.,0.,0.,8,700.,[target],[],{},False,{})
    assert all(p['status']=='earth_or_source_horizon' for p in points),points


def test_bottom_area_has_independent_gate_from_sunward_face():
    profiles=[]
    for d in (0,25,50,100,150,200,250,300,350,400,450,500,550,600,650,700,750):
        profiles.append({'distance_km':d,'levels':[
            {'height_agl_km':8.,'cloud_cover':90. if d<50 else 0.,'pressure_hpa':350},
            {'height_agl_km':10.,'cloud_cover':0.,'pressure_hpa':250}]})
    with patch('burning_sky.sky_six.solar_position',return_value=(-0.8,100.0)):
        scan=adaptive_equivalent_gaps(profiles,DT,8,0,0,'morning',27.52,
                                      target_limit_km=50,source_scan_km=700,
                                      use_horizontal_fusion=False)
    assert scan['bottom_verified_point_count']>0,scan
    assert any(p['status']=='verified_bottom' for p in scan['verified_target_bottom_points'])
    with patch('burning_sky.local_cloud_illumination.solar_position',return_value=(-0.8,100.0)):
        light=local_illumination(profiles,DT,'morning',8,0,0,50,
            verified_target_bands=[],
            verified_target_bottom_points=scan['verified_target_bottom_points'])
    assert light['available']
    assert max(p['fraction'] for p in light['times'])>.2
    assert all(p['possible_fraction']>=p['strict_fraction'] for p in light['times'])


def test_unmeasured_off_axis_ray_must_not_be_called_clear():
    target={'start_km':0.,'end_km':50.,'bottom_km':8.,'top_km':9.,
            'cloud_cover':90.,'sample_distance_km':25.}
    with patch('burning_sky.sky_six.solar_position',return_value=(-0.8,130.0)):
        points=_validated_cloud_bottom(target,-5,DT,100.,0.,0.,8,700.,[target],[],{},False,{})
    assert all(p['status']=='unresolved_off_axis' for p in points),points
