"""GFS pressure-proxy/native cloud discrepancy protection; offline, no API."""
import pytest
from burning_sky.spatial_pipeline import calibrate_path_samples
from burning_sky.spatial_ray_fusion import check_horizontal_target_support, check_horizontal_ray_consistency
from burning_sky.spatial_om import _gfs_cloud_level_groups

LAT,LON = 31.8047,117.2214


def make(src='pressure_layer_proxy',domain='ncep_gfs025',cloud=(10.,20.,13.9),native=(10.,20.,100.),d=0):
    sample={'distance_km':d,'lat':LAT,'lon':LON,'cloud_cover_low':native[0],
            'cloud_cover_mid':native[1], 'cloud_cover_high':native[2],
            'cloud_cover':native[2]}
    profile={'distance_km':d,'lat':LAT,'lon':LON,'spatial_horizontal_time_uncertainty':True,
             'spatial_horizontal_om':dict(zip(('cloud_cover_low','cloud_cover_mid','cloud_cover_high'),cloud)),
             'spatial_horizontal_layer_origin':src,'spatial_horizontal_model_domain':domain,
             'levels':[{'pressure_hpa':350, 'height_agl_km':8.2,'cloud_cover':65}]}
    return sample,profile


def test_case_hf_api_100_gfs_proxy_13_9_keeps_native_and_vertical():
    sample,profile=make()
    diag=calibrate_path_samples([sample],[profile])
    assert diag['status']=='applied' and diag['conflict_count']==1
    assert sample['cloud_cover_high']==100
    assert sample['point_api_area_cloud_cover_high']==100
    assert sample['cloud_cover']==100
    assert profile['levels'][0]['cloud_cover']==65
    assert profile['spatial_horizontal_om']['cloud_cover_high']==13.9
    assert profile['spatial_horizontal_effective']['cloud_cover_high']==100
    assert profile['spatial_horizontal_conflict_layers']['cloud_cover_high']['delta_pp']==pytest.approx(86.1)
    assert check_horizontal_target_support(profile,8.2)['unresolved'] is False


def test_reapply_after_extra_sampling_preserves_native_instead_of_calibrated_value():
    sample,profile=make(native=(10.,20.,100.))
    calibrate_path_samples([sample],[profile])
    # The second run must use the saved original baseline (100%), not the
    # possibly already-calibrated value from another layer.
    result=calibrate_path_samples([sample],[profile])
    assert result['conflict_count']==1
    assert sample['cloud_cover_high']==100
    assert sample['point_api_area_cloud_cover_high']==100


def test_proxy_close_agreement_and_other_layers_still_calibrated():
    sample,profile=make(cloud=(12.,35.,70.),native=(10.,20.,100.))
    diag=calibrate_path_samples([sample],[profile])
    assert diag['conflict_count']==0  # 30pp exactly is allowed
    assert (sample['cloud_cover_low'], sample['cloud_cover_mid'],sample['cloud_cover_high'])==(12,35,70)


def test_conflict_per_layer_not_all_or_nothing():
    sample,profile=make(cloud=(5.,64.,13.9),native=(8.,20.,100.))
    diag=calibrate_path_samples([sample],[profile])
    assert diag['conflict_count']==2
    assert sample['cloud_cover_low']==5
    assert sample['cloud_cover_mid']==20
    assert sample['cloud_cover_high']==100
    assert profile['spatial_horizontal_effective']['cloud_cover_mid']==20


def test_ec_native_om_unaffected_even_if_big_difference():
    sample,profile=make(src='native_layers',domain='ecmwf_ifs025')
    diag=calibrate_path_samples([sample],[profile])
    assert diag['conflict_count']==0
    assert sample['cloud_cover_high']==13.9


def test_manual_edits_are_not_overwritten():
    sample,profile=make()
    assert calibrate_path_samples([sample],[profile],manual=True)['status']=='manual_priority'
    assert sample['cloud_cover_high']==100


def test_conflict_high_api_keeps_high_om_ray_veto_not_false_clear():
    sample,profile=make(d=400)
    calibrate_path_samples([sample],[profile])
    assert profile['spatial_horizontal_effective']['cloud_cover_high']==100
    # The 3D ray is at 8km in a possible high-cloud band while the tested
    # vertical levels are clear -- the 100% native layer must not vanish.
    profile['levels']=[{'height_agl_km':8., 'cloud_cover':0}]
    from unittest.mock import patch
    with patch('burning_sky.spherical_raytrace._ray_height_km', return_value=8.):
        r=check_horizontal_ray_consistency([profile],25,8,-1,700)
    assert r['unresolved'] is True
    assert r['conflicts'][0]['layer']=='cloud_cover_high'


def test_350hpa_selected_without_sixth_field():
    # Nine high-cloud levels; naive five-point interpolation can omit 350.
    levels=[100,150,175,200,225,250,275,300,350,400,450,500,550,600,650,700,750,800,850,900]
    groups=_gfs_cloud_level_groups({f'cloud_cover_{p}hPa' for p in levels})
    assert 350 in groups['cloud_cover_high']
    assert len(groups['cloud_cover_high'])<=5


def test_proxy_conflict_never_stands_in_for_missing_local_baseline():
    sample,profile=make(native=(10.,20.,100.))
    sample['cloud_cover_high']=None
    diag=calibrate_path_samples([sample],[profile])
    assert diag['conflict_count']==0  # no native comparable variable
    assert sample['cloud_cover_high']==13.9  # documented proxy, not a claimed native product
