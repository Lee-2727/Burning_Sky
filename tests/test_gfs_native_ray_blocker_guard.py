"""GFS native 2D clouds must constrain pressure-layer ray obstacles."""
import copy
import pytest
from burning_sky.spherical_raytrace import profile_cloud_cells


def _profile(domain='ncep_gfs025', sources=True, manual=False):
    p = {
        'distance_km': 300.0,
        'levels': [
            {'height_agl_km': 5.0, 'cloud_cover': 40.0, 'pressure_hpa': 500},
            {'height_agl_km': 7.3, 'cloud_cover': 40.0, 'pressure_hpa': 400},
            {'height_agl_km': 8.2, 'cloud_cover': 25.0, 'pressure_hpa': 350},
        ],
        'spatial_horizontal_model_domain': domain,
        'spatial_horizontal_layer_origin': 'pressure_layer_proxy',
        'spatial_horizontal_effective': {
            'cloud_cover_low': 0.0,
            'cloud_cover_mid': 1.7,
            'cloud_cover_high': 3.9,
        },
        'spatial_horizontal_effective_sources': {
            k:'native_point_api_gfs_proxy_reference' for k in
            ('cloud_cover_low','cloud_cover_mid','cloud_cover_high')
        } if sources else {},
    }
    if manual: p['manual_override_intervals'] = [(280.0, 320.0, None, 80., 80.)]
    return p


def test_native_gfs_2d_near_clear_prevents_fake_solid_high_cloud_wall():
    p=_profile()
    assert not profile_cloud_cells([p], threshold=25.0, blur_horizontal_km=0.0,
                                   blur_vertical_km=0.0)
    assert [x['cloud_cover'] for x in p['levels']]==[40.,40.,25.], 'original OM values must remain available'


def test_ec_and_unattributed_pressure_profiles_are_not_capped():
    for profile in (_profile(domain='ecmwf_ifs025'),_profile(sources=False)):
        cs=profile_cloud_cells([profile], threshold=25., blur_horizontal_km=0.,blur_vertical_km=0.)
        assert len(cs)==3


def test_manual_cloud_override_remains_authoritative():
    p=_profile(manual=True)
    cs=profile_cloud_cells([p], threshold=25., blur_horizontal_km=0.,blur_vertical_km=0.)
    assert [round(c['cloud_cover']) for c in cs if c['start_km'] >= 280.] == [80,80,80]
    assert all(c['native_gfs_layer_cap_pct'] is None for c in cs)


def test_native_gfs_cap_preserves_partial_cloud_target_not_binary_clear():
    p=_profile()
    p['spatial_horizontal_effective']['cloud_cover_high']=30.
    cs=profile_cloud_cells([p],threshold=25.,blur_horizontal_km=0.,blur_vertical_km=0.)
    assert [(c['pressure_hpa'],c['cloud_cover']) for c in cs]==[(400,30.0),(350,25.0)]
    assert all(c['native_gfs_layer_cap_pct']==30 for c in cs)
