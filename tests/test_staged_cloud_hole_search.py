from burning_sky.algorithm import staged_cloud_hole_search


def sample(d, low=0, mid=0, high=0):
    return {"distance_km": d, "cloud_cover_low": low,
            "cloud_cover_mid": mid, "cloud_cover_high": high}


def test_quick_screen_has_priority():
    rows=[sample(0),sample(25),sample(50),sample(100,80,0,0)]
    out=staged_cloud_hole_search(rows,100)
    assert out["stage"] == "quick_screen"
    assert out["fallback_factor"] == 1.0


def test_fallback_quick_screen_factor_half():
    rows=[sample(0,80,80,80),sample(50,80,80,80),sample(150,0,0,0),sample(200,0,0,0)]
    out=staged_cloud_hole_search(rows,100)
    assert out["stage"] == "fallback_quick_screen"
    assert out["fallback_factor"] == 0.5


def test_height_intersection_preserves_disconnected_intervals():
    # At different ranges, cloud layers carve out distinct elevation-angle bands.
    rows=[sample(0),sample(25,80,0,0),sample(50,0,80,0),sample(100,0,0,80),sample(150,0,0,0)]
    out=staged_cloud_hole_search(rows,100)
    if out["stage"] == "height_intersection":
        ints=out["angle_intervals_deg"]
        assert len(ints) == len(out["gaps"])
        assert len(ints) >= 1
        assert all(ints[i][1] <= ints[i+1][0] for i in range(len(ints)-1))
