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
