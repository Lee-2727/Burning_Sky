from burning_sky.algorithm import enforce_single_mode_special_verdicts


def test_zero_potential_range_caps_regular_score_at_20():
    inferred = {"regular_score": 43, "score": 43, "upper_score": 60,
                "low": 0, "mid": 60, "high": 60, "total": 50}
    geometry = {"rows": [{"distance_km": 0, "reach_km": 0,
                          "cloud_base_km": 2.0, "base_source": "自动失败→手动绝对高度回退"}]}
    boundaries = {"fire_cloud_range_km": None, "cloud_edge_distance_km": 0,
                  "local_cloud_reach_km": 0, "gap_status": "unknown", "clear_gaps": []}
    result = enforce_single_mode_special_verdicts(inferred, geometry, boundaries, samples=[])
    assert result["regular_score"] == 20
    assert result["score"] == 20
    assert result["verdict_rule"] == "zero_potential_fire_cloud_range_cap_20pct"
    assert result["special_rule_diagnostics"]["zero_range_cap_applied"] is True


def test_valid_potential_range_does_not_apply_zero_range_cap():
    inferred = {"regular_score": 43, "score": 43, "upper_score": 60,
                "low": 0, "mid": 60, "high": 60, "total": 50}
    geometry = {"rows": []}
    boundaries = {"fire_cloud_range_km": 120, "cloud_edge_distance_km": 130,
                  "local_cloud_reach_km": 130, "gap_status": "unknown", "clear_gaps": []}
    result = enforce_single_mode_special_verdicts(inferred, geometry, boundaries, samples=[])
    assert result["regular_score"] == 43
    assert result["special_rule_diagnostics"]["zero_range_cap_applied"] is False
