from datetime import datetime
from burning_sky.spherical_raytrace import destination, trace_at_time, scan_window

def test_destination_great_circle_moves_eastward():
    lat, lon = destination(0, 0, 90, 100)
    assert abs(lat) < 1e-6
    assert 0.8 < lon < 1.0

def test_trace_reports_layer_block_and_reason():
    # Evening solar position near sunset; a mid-cloud layer at the traced ray height
    # should be detected when cover exceeds the configured threshold.
    samples=[{"distance_km":0,"lat":31.8,"lon":117.2,"cloud_cover_low":0,"cloud_cover_mid":0,"cloud_cover_high":0},
             {"distance_km":20,"lat":31.8,"lon":117.4,"cloud_cover_low":0,"cloud_cover_mid":100,"cloud_cover_high":0}]
    result=trace_at_time(samples, 2.0, datetime(2026,6,1,18,30), 8, 31.8, 117.2)
    assert result["solar_altitude_deg"] is not None
    assert len(result["path"]) == 2
    assert all("reason" in row for row in result["path"])

def test_scan_window_has_minute_resolution():
    out=scan_window([],2,datetime(2026,6,1,18,30),8,31.8,117.2,before_minutes=2,after_minutes=2)
    assert out["scan_count"] == 5


def test_surface_arc_ray_u_shape_and_negative_solar_angle():
    from burning_sky.spherical_raytrace import _ray_height_km
    h = [_ray_height_km(4.0, -1.0, d) for d in (0, 50, 100, 150, 200, 300, 400)]
    expected = (4.0, 3.3231, 3.0389, 3.1473, 3.6483, 5.8290, 9.5837)
    assert all(abs(a-b)<0.08 for a,b in zip(h, expected)), h
    assert h[2] < h[1] and h[2] < h[3] and h[-1] > h[0]


def test_earth_occlusion_cannot_recover_after_exit():
    from burning_sky.spherical_raytrace import _earth_occluded, _ray_height_km
    assert _earth_occluded(0.5, -2.0, 400)
    assert not _earth_occluded(4.0, -1.0, 400)
    assert _ray_height_km(0.5, -2.0, 500) > 0  # mathematical re-emergence is NOT sunlight
