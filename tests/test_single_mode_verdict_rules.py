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


def test_real_case_profile_180m_and_local_low_100pct_remains_hard_zero_after_later_screen():
    from burning_sky.algorithm import enforce_single_mode_special_verdicts, apply_low_cloud_screen_constraint
    inferred, geometry, boundaries, samples = _inputs(
        base_agl=0.18,
        base_source='剖面离地高度 + 地点海拔（绝对高度）',
        local_low=100,
        local_total=100,
        path_total=100,
        gap_status='continuous_cloud',
    )
    result = apply_path_score_pair(inferred, geometry, boundaries, samples=samples)
    # Simulate the subsequent low-cloud screen used by the live web pipeline.
    result = apply_low_cloud_screen_constraint(result, samples=samples, source='online_path',
                                               max_relevant_distance_km=100)
    result = enforce_single_mode_special_verdicts(result, geometry, boundaries, samples=samples)
    assert result['regular_score'] == 0
    assert result['verdict_override'] == '云底过底'
    assert result['verdict_rule'] == 'profile_cloud_base_below_500m'


def test_final_verdict_fallback_detects_local_low_cloud_from_sample():
    from burning_sky.algorithm import enforce_single_mode_special_verdicts
    inferred, geometry, boundaries, samples = _inputs(
        base_agl=1.2, base_source='手动绝对高度', local_low=100, local_total=100,
        path_total=100, gap_status='continuous_cloud')
    result = enforce_single_mode_special_verdicts(inferred, geometry, boundaries, samples=samples)
    assert result['regular_score'] == 0
    assert result['verdict_override'] == '低云遮挡过多'


def test_exact_profile_source_label_with_unknown_gap_still_triggers_cloud_base_rule():
    from burning_sky.algorithm import enforce_single_mode_special_verdicts
    inferred, geometry, boundaries, samples = _inputs(
        base_agl=0.18,
        base_source='剖面离地高度 + 地点海拔（绝对高度）',
        local_low=30, local_total=100, path_total=100, gap_status='unknown')
    result = enforce_single_mode_special_verdicts(inferred, geometry, boundaries, samples=samples)
    assert result['regular_score'] == 0
    assert result['verdict_override'] == '云底过底'
    assert result['special_rule_diagnostics']['profile_based'] is True


def test_profile_absolute_cloud_base_fallback_when_agl_field_missing():
    from burning_sky.algorithm import enforce_single_mode_special_verdicts
    inferred, geometry, boundaries, samples = _inputs(
        base_agl=1.2, base_source='剖面离地高度 + 地点海拔（绝对高度）',
        local_low=100, local_total=100, path_total=100)
    geometry['rows'][0].pop('cloud_base_agl_km', None)
    geometry['rows'][0]['cloud_base_km'] = 0.18
    result = enforce_single_mode_special_verdicts(inferred, geometry, boundaries, samples=samples)
    assert result['regular_score'] == 0
    assert result['verdict_override'] == '云底过底'
    assert result['special_rule_diagnostics']['profile_base_height_basis'].startswith('绝对高度')


def test_final_low_cloud_rule_uses_inferred_low_if_local_sample_missing():
    from burning_sky.algorithm import enforce_single_mode_special_verdicts
    inferred, geometry, boundaries, samples = _inputs(
        base_agl=1.2, base_source='手动绝对高度', local_low=100, local_total=100)
    samples[0].pop('center_cloud_cover_low', None)
    samples[0].pop('cloud_cover_low', None)
    inferred['low'] = 95
    result = enforce_single_mode_special_verdicts(inferred, geometry, boundaries, samples=samples)
    assert result['regular_score'] == 0
    assert result['verdict_override'] == '低云遮挡过多'
