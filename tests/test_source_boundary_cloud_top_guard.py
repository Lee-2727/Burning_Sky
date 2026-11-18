"""Regression for ray traced through the *measured* sky corridor.

Cloud-bottom sunlight requires all upstream clouds to be examined until the
actual solar ray rises beyond the cloud-bearing atmosphere, not just to 700km.
The height of 20km is the upper model cloud domain, not an obligatory height
at a FIXED distance. The route may need 1000km or more.
"""
from datetime import datetime
from unittest.mock import patch
from burning_sky import sky_six


def _bottom_at_solar_altitude(deg, extra_cells=(), source_end_km=700.):
    target={"start_km":100.,"end_km":200.,"bottom_km":10.,"top_km":11.,
            "cloud_cover":70.,"sample_distance_km":150.}
    with patch.object(sky_six, "solar_position", return_value=(deg, 90.)), \
         patch.object(sky_six, "_solar_corridor_coverage", return_value={"covered":True}):
        return sky_six._validated_cloud_bottom(
            target, -3, datetime(2026,11,20,6,0), 90.,31.8,117.2,8.,
            source_end_km, [target,*extra_cells],[],{},False,{}
        )


def test_cloud_bottom_needs_longer_than_700km_when_low_sun():
    # Clear air up to 700 km does NOT prove no cloud obstruction at 900 km.
    points=_bottom_at_solar_altitude(-3.)
    assert len(points)==5
    assert all(p['status']=='unresolved_source_boundary' for p in points),points
    assert min(p['required_source_km'] for p in points)>700
    # The same rays can be geometrically validated with a sufficient upstream
    # corridor (the complete model-domain must be sampled by the caller).
    longer=_bottom_at_solar_altitude(-3.,source_end_km=1150.)
    assert all(p['status']=='verified_bottom' for p in longer),longer


def test_far_upstream_cloud_still_vetoes_sunlight_after_near_gap():
    # 100-200km target has clear air until 480-540km; that far obstacle
    # actually intersects the ray despite the earlier wide blue-sky gap.
    wall={"start_km":480.,"end_km":540.,"bottom_km":0.,"top_km":20.,
          "cloud_cover":100.,"sample_distance_km":510.}
    points=_bottom_at_solar_altitude(-3., [wall])
    assert len(points)==5
    assert all(p['status']=='cloud_blocked' for p in points),points


def test_true_earth_occlusion_is_independent_of_700km_end_height():
    points=_bottom_at_solar_altitude(-5.)
    assert len(points)==5
    assert all(p['status']=='earth_or_source_horizon' for p in points),points


def test_full_solar_earth_criterion_is_geometric_not_future_cloud_guess():
    assert not sky_six._sun_ray_earth_occluded(10,-3)
    assert sky_six._sun_ray_earth_occluded(10,-5)
    assert not sky_six._sun_ray_earth_occluded(1,3)
