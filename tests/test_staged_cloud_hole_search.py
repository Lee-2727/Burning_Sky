from burning_sky.algorithm import staged_cloud_hole_search


def sample(d, low=0, mid=0, high=0):
    return {"distance_km": d, "cloud_cover_low": low,
            "cloud_cover_mid": mid, "cloud_cover_high": high}


def test_quick_screen_has_priority():
    rows=[sample(0),sample(25),sample(50),sample(100,80,0,0)]
    out=staged_cloud_hole_search(rows,100)
    assert out["stage"] == "quick_screen"
    assert out["fallback_factor"] == 1.0


def test_height_intersection_precedes_fallback_when_vertical_interval_survives():
    rows=[sample(0,80,80,80),sample(50,80,80,80),sample(150,0,0,0),sample(200,0,0,0)]
    out=staged_cloud_hole_search(rows,100)
    assert out["stage"] == "height_intersection"
    assert out["angle_intervals_deg"][-1][1] == 90.0


def test_height_intersection_preserves_disconnected_intervals():
    # At different ranges, cloud layers carve out distinct elevation-angle bands.
    rows=[sample(0),sample(25,80,0,0),sample(50,0,80,0),sample(100,0,0,80),sample(150,0,0,0)]
    out=staged_cloud_hole_search(rows,100)
    if out["stage"] == "height_intersection":
        ints=out["angle_intervals_deg"]
        assert len(ints) == len(out["gaps"])
        assert len(ints) >= 1
        assert all(ints[i][1] <= ints[i+1][0] for i in range(len(ints)-1))



def test_height_intersection_keeps_valid_interval_ending_at_90_degrees():
    # A distant low-cloud layer blocks only a tiny near-horizontal band;
    # the remaining valid interval extends all the way to vertical (90°).
    rows = [sample(0, low=80, mid=80, high=80), sample(1000, low=80, mid=0, high=0)]
    out = staged_cloud_hole_search(rows, 1000)
    assert out["stage"] == "height_intersection"
    assert out["angle_intervals_deg"]
    assert out["angle_intervals_deg"][-1][1] == 90.0
    assert out["angle_intersection_trace"]
    assert out["projection_angle_cap_deg"] == 89.9
    assert out["gaps"][-1]["size_km"] > 0
