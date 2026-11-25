"""Regressions for the 0-to-霞云距 cloud-bottom target sweep."""
import math
from datetime import datetime
from unittest.mock import patch
from burning_sky.sky_six import (_cloud_bottom_sweep_grid,
     _bottom_sweep_strips, adaptive_equivalent_gaps)
from burning_sky.local_cloud_illumination import local_illumination

DT=datetime(2026,11,20,6,40)

def test_275_km_grid_strict_15km():
    nodes=_cloud_bottom_sweep_grid(275.)
    assert nodes[:4]==[0.,15.,30.,45.]
    assert nodes[-2:]==[255.,270.]
    assert len(nodes)==19
    assert all(abs((b-a)-15.)<1e-8 for a,b in zip(nodes,nodes[1:]))

def test_under_15_only_one_sample():
    for d in (0.1, 5., 14.99):
        nodes=_cloud_bottom_sweep_grid(d)
        assert len(nodes)==1 and nodes[0]==d/2.
    assert _cloud_bottom_sweep_grid(15.)==[0.,15.]
    assert _cloud_bottom_sweep_grid(50.)==[0.,15.,30.,45.]
    assert _cloud_bottom_sweep_grid(75.)==[0.,15.,30.,45.,60.,75.]

def test_clipped_cell_widths_and_sparse_cloud_not_lost():
    cloud={'start_km':17.,'end_km':31.}
    strips=_bottom_sweep_strips(cloud,275.)
    assert [p['position_km'] for p in strips]==[30.]
    assert abs(sum(p['area_width_km'] for p in strips)-14.)<1e-8
    clipped=_bottom_sweep_strips({'start_km':0.,'end_km':300.},275.)
    assert [p['position_km'] for p in clipped]==_cloud_bottom_sweep_grid(275.)
    assert abs(sum(p['area_width_km'] for p in clipped)-275.)<1e-8

def test_short_cell_between_grid_nodes_gets_checked():
    rows=_bottom_sweep_strips({'start_km':16.,'end_km':17.},275.)
    assert len(rows)==1 and rows[0]['position_km']==16.5
    assert rows[0]['area_width_km']==1.

def test_adaptive_cloud_bottom_scans_local_nodes_and_weights_real_width():
    # Genuine cloud over the observer, clear 75..750 km.
    profiles=[{'distance_km':float(d),'levels':[
        {'height_agl_km':8.,'cloud_cover':90. if d<=50 else 0.,'pressure_hpa':350},
        {'height_agl_km':10.,'cloud_cover':0.,'pressure_hpa':250}]}
        for d in (0,25,50,75,*range(100,751,50))]
    with patch('burning_sky.sky_six.solar_position',return_value=(-.8,100.)):
        scan=adaptive_equivalent_gaps(profiles,DT,8,0,0,'morning',50.,
                     source_scan_km=700.,use_horizontal_fusion=False)
    points=[p for p in scan['verified_target_bottom_points'] if p['minute_offset']==-5]
    assert points,scan
    assert any(abs(p['position_km'])<1e-6 for p in points)
    assert len(points)>=4
    assert abs(sum(p['area_width_km'] for p in points)-50.)<.0001
    assert scan['bottom_inner_sweep_method'].startswith('global_fixed_15km')
    with patch('burning_sky.local_cloud_illumination.solar_position',return_value=(-.8,100.)):
        lit=local_illumination(profiles,DT,'morning',8,0,0,50.,
            verified_target_bands=[],verified_target_bottom_points=scan['verified_target_bottom_points'])
    assert lit['available']
    assert max(x['fraction'] for x in lit['times'])>.2

def test_short_range_only_one_point_in_actual_illumination():
    rows=_bottom_sweep_strips({'start_km':0.,'end_km':8.},8.)
    assert len(rows)==1
    assert rows[0]['position_km']==4.
    assert rows[0]['area_width_km']==8.

def test_tail_coverage_without_extra_probe():
    rows=_bottom_sweep_strips({'start_km':0.,'end_km':50.},50.)
    assert [x['position_km'] for x in rows]==[0.,15.,30.,45.]
    assert abs(sum(x['area_width_km'] for x in rows)-50.)<1e-8
