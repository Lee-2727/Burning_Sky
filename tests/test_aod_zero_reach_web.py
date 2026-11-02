from burning_sky.algorithm import estimate_path_cloud_boundaries


def test_zero_local_reach_does_not_scan_remote_clouds():
    samples = [
        {"distance_km": 0, "cloud_cover_low": 0, "cloud_cover_mid": 0, "cloud_cover_high": 0},
        {"distance_km": 100, "cloud_cover_low": 0, "cloud_cover_mid": 80, "cloud_cover_high": 0},
        {"distance_km": 500, "cloud_cover_low": 0, "cloud_cover_mid": 80, "cloud_cover_high": 0},
    ]
    result = estimate_path_cloud_boundaries(samples, 0)
    assert result["local_cloud_reach_km"] == 0
    assert result["cloud_edge_distance_km"] == 0
    assert result["fire_cloud_range_km"] is None
    assert result["cloudy_count"] == 0
