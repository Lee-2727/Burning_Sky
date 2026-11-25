from burning_sky.sky_six import _exposed_cloud_target_cells


def cell(a, b, lo, hi, cover=80):
    return {'start_km': a, 'end_km': b, 'bottom_km': lo, 'top_km': hi,
            'cloud_cover': cover, 'sample_distance_km': (a+b)/2}


def test_contiguous_vertical_stack_keeps_only_lowest_target():
    bottom = cell(0, 15, 2, 3, 90)
    middle = cell(0, 15, 3, 4, 85)
    upper = cell(0, 15, 4, 5, 80)
    targets, diag = _exposed_cloud_target_cells([bottom, middle, upper], [bottom, middle, upper])
    assert len(targets) == 1
    assert targets[0]['bottom_km'] == 2
    assert targets[0]['top_km'] == 3
    assert diag['removed_stacked_horizontal_km'] == 30


def test_real_vertical_gap_keeps_separate_cloud_target():
    lower = cell(0, 15, 2, 3, 80)
    upper = cell(0, 15, 3.4, 4.4, 80)
    targets, _ = _exposed_cloud_target_cells([lower, upper], [lower, upper])
    assert len(targets) == 2


def test_partial_horizontal_stack_only_subtracts_covered_upper_part():
    lower = cell(0, 10, 2, 3, 80)
    upper = cell(0, 15, 3, 4, 80)
    targets, _ = _exposed_cloud_target_cells([lower, upper], [lower, upper])
    upper_parts = [x for x in targets if x['bottom_km'] == 3]
    assert len(upper_parts) == 1
    assert upper_parts[0]['start_km'] == 10
    assert upper_parts[0]['end_km'] == 15


def test_thin_lower_cloud_does_not_hide_upper_target():
    lower = cell(0, 15, 2, 3, 10)
    upper = cell(0, 15, 3, 4, 90)
    targets, _ = _exposed_cloud_target_cells([lower, upper], [lower, upper])
    assert len(targets) == 2
