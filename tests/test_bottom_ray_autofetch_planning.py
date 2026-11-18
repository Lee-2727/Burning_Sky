"""Prevent face-only meteorological planning from regressing again."""
from datetime import datetime
from unittest.mock import patch
from burning_sky.sky_six import plan_off_axis_rays, materialize_off_axis_rays, _bottom_sweep_strips
from burning_sky.spherical_raytrace import destination, profile_cloud_cells

LAT,LON=31.8047,117.2214
DT=datetime(2026,11,20,6,40)

def sample(d, cover):
    la,lo=destination(LAT,LON,90.,d)
    return {'distance_km':d,'lat':la,'lon':lo,'levels':[
        {'height_agl_km':8.,'cloud_cover':cover,'pressure_hpa':350},
        {'height_agl_km':10.,'cloud_cover':0.,'pressure_hpa':250}]}

def sun(dt,offset,lat,lon):
    return (-.8,90. if abs(lat-LAT)<1e-6 and abs(lon-LON)<1e-6 and dt==DT else 102.)

def test_plan_contains_inner_bottoms_in_addition_to_cloud_front():
    axis=[sample(d,90. if d<=50 else 0.) for d in [0.,25.,50.,75.,100.,150.,200.,250.,300.,350.,400.,450.,500.,550.,600.,650.,700.,750.]]
    clouds=profile_cloud_cells(axis,threshold=.000001,blur_horizontal_km=0,blur_vertical_km=0)
    target=next(c for c in clouds if c['top_km']>1.5 and c['start_km']<50)
    strips=_bottom_sweep_strips(target,50.)
    with patch('burning_sky.sky_six.solar_position',side_effect=sun):
        plan=plan_off_axis_rays(axis,DT,8,LAT,LON,'morning',50.,50.,source_scan_km=750.)
    # It cannot request only the face, because the solver checks 0,15,30,45.
    actual={pos for _,pos in plan['rays']}
    assert len(actual)>1,(actual,strips)
    assert all(round(s['position_km'],5) in plan['bottom_positions'] for s in strips)
    assert plan['coordinates']
    # The additional stations can be materialized into real per-target rays.
    measurements=[{'lat':lat,'lon':lon,'levels':[{'height_agl_km':8.,'cloud_cover':0.}]} for lat,lon in plan['coordinates']]
    rays=materialize_off_axis_rays(plan,measurements)
    assert rays and all(rays.values())

def test_missing_extra_profile_not_promoted_to_clear_air():
    axis=[sample(d,90. if d<=50 else 0.) for d in [0.,25.,50.,75.,100.,150.,200.,250.,300.,350.,400.,450.,500.,550.,600.,650.,700.,750.]]
    with patch('burning_sky.sky_six.solar_position',side_effect=sun):
        plan=plan_off_axis_rays(axis,DT,8,LAT,LON,'morning',50.,50.,source_scan_km=750.)
    unavailable=[{'levels':[]} for _ in plan['coordinates']]
    rays=materialize_off_axis_rays(plan,unavailable)
    assert any(not r for r in rays.values())
