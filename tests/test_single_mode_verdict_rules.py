from burning_sky.algorithm import apply_path_score_pair


def _inputs(*, base_agl=1.2, base_source='剖面离地高度 + 地点海拔（绝对高度）',
            local_low=20, local_total=60, path_total=60, gap_status='unknown', gaps=None,
            effective=1.2, reach=100):
    samples = []
    for d in (0, 20, 40, 60, 80):
        samples.append({
            'distance_km': d,
            'cloud_cover': local_total if d == 0 else path_total,
            'cloud_cover_low': local_low if d == 0 else 20,
            'cloud_cover_mid': 60,
            'cloud_cover_high': 40,
        })
    inferred = {'low': 30, 'mid': 60, 'high': 40, 'total': 60, 'aod': 0.1}
    geometry = {'rows': [{
        'distance_km': 0, 'effective_base_km': effective, 'reach_km': reach,
        'cloud_base_agl_km': base_agl, 'base_source': base_source,
    }]}
    boundaries = {
        'gap_status': gap_status, 'clear_gaps': gaps or [],
        'largest_gap_size_km': 0, 'local_cloud_reach_km': reach,
        'cloud_edge_distance_km': reach, 'gap_evidence_points': 5,
        'gap_evidence_span_km': 80,
    }
    return inferred, geometry, boundaries, samples


def _run(**kwargs):
    inferred, geometry, boundaries, samples = _inputs(**kwargs)
    return apply_path_score_pair(inferred, geometry, boundaries, samples=samples)


def test_profile_cloud_base_below_500m_sets_zero_and_verdict():
    result = _run(base_agl=0.49)
    assert result['regular_score'] == 0
    assert result['verdict_override'] == '云底过底'


def test_local_low_cloud_at_90_percent_sets_zero_and_verdict():
    result = _run(local_low=90)
    assert result['regular_score'] == 0
    assert result['verdict_override'] == '低云遮挡过多'


def test_continuous_cloudy_path_over_50_percent_halves_regular_score():
    result = _run(gap_status='continuous_cloud', path_total=60)
    assert result['regular_score'] == 10  # existing continuous-cloud cap (20), then ×0.5
    assert result['verdict_override'] == '光线难以穿透'


def test_detected_clear_gap_prevents_continuous_cloud_reduction():
    result = _run(gap_status='effective_gap_candidate', path_total=80,
                  gaps=[{'start_km': 25, 'end_km': 50, 'size_km': 25}])
    assert result['verdict_override'] != '光线难以穿透'


def test_local_total_cloud_below_10_sets_clear_sky_verdict():
    result = _run(local_total=9)
    assert result['verdict_override'] == '光板天'


def test_regular_probability_below_20_uses_low_chance_verdict():
    inferred, geometry, boundaries, samples = _inputs(
        base_agl=1.2, base_source='手动绝对高度', effective=0, reach=0,
        gap_status='unknown', local_low=80, local_total=50, path_total=50)
    inferred.update({'low': 80, 'mid': 0, 'high': 0, 'total': 0})
    result = apply_path_score_pair(inferred, geometry, boundaries, samples=samples)
    assert result['regular_score'] < 20
    assert result['verdict_override'] == '机会太小，几乎不烧'
