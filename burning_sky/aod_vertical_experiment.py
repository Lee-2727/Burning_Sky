"""Experimental optical colour sensitivity from CAMS AOD and model humidity profile.

This is NOT a measured extinction profile.  It never changes geometric cloud
blocking, cloud reach, probability or tintable area.  Missing evidence returns
an identity modifier.  The modest modifier is a sensitivity experiment, not
Beer-Lambert line-of-sight radiative transfer.
"""
import math


def estimate_vertical_color_factor(profiles, aod):
    if not isinstance(aod, (int, float)) or not math.isfinite(aod) or aod < 0:
        return {"status": "缺少 AOD，沿用原模型", "factor": 1.0}
    local = next((p for p in profiles if abs(float(p.get('distance_km', 1e6))) < 0.01), None)
    if not local:
        return {"status": "缺少本地气压层，沿用原模型", "factor": 1.0}
    ground = local.get('ground_elevation_m') or 0.0
    levels = []
    for lv in local.get('levels') or []:
        z = lv.get('height_m_asl')
        rh = lv.get('relative_humidity')
        if not isinstance(z, (float, int)) or not isinstance(rh, (float, int)):
            continue
        if not math.isfinite(z) or not math.isfinite(rh):
            continue
        # Restrict to direct geopotential heights; do not silently use
        # standard-atmosphere heights as if they were actual model profiles.
        if lv.get('height_source') not in ('model_geopotential',):
            continue
        z_km = (z - ground) / 1000.0
        if 0 <= z_km <= 8:
            levels.append((z_km, max(0., min(100., float(rh)))))
    levels.sort()
    if len(levels) < 3 or levels[-1][0] - levels[0][0] < 1.5:
        return {"status": "垂直数据不足，沿用原模型", "factor": 1.0, "valid_levels": len(levels)}
    # AOD-preserving vertical weights: boundary-layer proxy + humidity
    # sensitivity. Not an observed aerosol vertical distribution.
    weights = []
    for z, rh in levels:
        weight = math.exp(-z / 1.5) * (0.8 + 0.4 * rh / 100.)
        weights.append(weight)
    norm = sum(weights)
    mean_height = sum(z*w for (z, _), w in zip(levels, weights)) / norm
    # Bounded colour sensitivity; no horizontal path multiplier.
    # Reference 1.0 km; larger mean height implies slightly stronger
    # haze impact on twilight colour in this *experimental* proxy.
    correction = max(-0.12, min(0.12, (mean_height - 1.0) * 0.08))
    factor = max(0.90, min(1.10, math.exp(-max(0., aod) * correction)))
    return {"status": "已估算", "factor": round(factor, 5),
            "aod": float(aod), "valid_levels": len(levels),
            "weighted_mean_height_km": round(mean_height, 3),
            "method": "气压层湿度+高度加权的有限色彩敏感性实验；非实测消光廓线"}
