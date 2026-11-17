from burning_sky.algorithm import staged_cloud_hole_search

def sample(d, low=0, mid=0, high=0):
    return {"distance_km": d, "cloud_cover_low": low,
            "cloud_cover_mid": mid, "cloud_cover_high": high}

def test_quick_screen_has_priority():
    rows=[sample(0),sample(25),sample(50),sample(100,80,0,0)]
    out=staged_cloud_hole_search(rows,100)
    assert out["stage"] == "quick_screen"
    assert out["fallback_factor"] == 1.0
    assert out["gaps"]

def test_legacy_height_angle_intersection_is_disabled():
    rows=[sample(0,80,80,80),sample(50,80,80,80),sample(100,80,80,80)]
    out=staged_cloud_hole_search(rows,100)
    assert out["stage"] == "no_clear_gap_quick_screen"
    assert out["gaps"] == []
    assert out["angle_intervals_deg"] == []
    assert "spherical" in out["note"]

def test_clear_gap_outside_theoretical_reach_does_not_pass_quick_screen():
    rows=[sample(0,80,80,80),sample(50,80,80,80),sample(150,0,0,0),sample(200,0,0,0)]
    out=staged_cloud_hole_search(rows,100)
    assert out["stage"] == "no_clear_gap_quick_screen"
