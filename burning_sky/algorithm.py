# -*- coding: utf-8 -*-
"""Pure Burning Sky forecasting algorithms extracted from validated v1.4.67.

No Tkinter, Streamlit, HTTP, filesystem, or UI dependencies.
Function bodies are preserved verbatim from the v1.4.67 baseline.
"""
from __future__ import annotations
import math
import colorsys
import re
from datetime import datetime, date, timedelta, timezone

def clamp(value, low, high):
    return max(low, min(high, value))

def parse_float(value, label):
    try:
        result = float(value.strip())
    except (ValueError, AttributeError):
        raise ValueError(f"“{label}”必须填写数字。")
    if not math.isfinite(result):
        raise ValueError(f"“{label}”必须是有限数值。")
    return result

def julian_day(dt_utc):
    """输入带 UTC 时区的 datetime，返回儒略日。"""
    dt_utc = dt_utc.astimezone(timezone.utc)
    year, month = dt_utc.year, dt_utc.month
    day_fraction = (
        dt_utc.day
        + (dt_utc.hour + dt_utc.minute / 60 + dt_utc.second / 3600) / 24
    )
    if month <= 2:
        year -= 1
        month += 12
    a = math.floor(year / 100)
    b = 2 - a + math.floor(a / 4)
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day_fraction + b - 1524.5
    )

def solar_position(dt_local_naive, utc_offset_hours, latitude, longitude):
    """
    NOAA 常用近似算法。
    返回 (太阳高度角_deg, 太阳方位角_deg)，方位角从正北顺时针。
    """
    tz = timezone(timedelta(hours=utc_offset_hours))
    dt_local = dt_local_naive.replace(tzinfo=tz)
    dt_utc = dt_local.astimezone(timezone.utc)
    jd = julian_day(dt_utc)
    t = (jd - 2451545.0) / 36525.0

    l0 = (280.46646 + t * (36000.76983 + 0.0003032 * t)) % 360
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    m_rad = math.radians(m)
    ecc = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    c = (
        math.sin(m_rad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * m_rad) * (0.019993 - 0.000101 * t)
        + math.sin(3 * m_rad) * 0.000289
    )
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    apparent_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    mean_obliq = (
        23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
    )
    obliq = mean_obliq + 0.00256 * math.cos(math.radians(omega))
    obliq_rad = math.radians(obliq)
    decl = math.degrees(math.asin(
        math.sin(obliq_rad) * math.sin(math.radians(apparent_long))
    ))

    y = math.tan(obliq_rad / 2) ** 2
    l0_rad = math.radians(l0)
    eq_time = 4 * math.degrees(
        y * math.sin(2 * l0_rad)
        - 2 * ecc * math.sin(m_rad)
        + 4 * ecc * y * math.sin(m_rad) * math.cos(2 * l0_rad)
        - 0.5 * y * y * math.sin(4 * l0_rad)
        - 1.25 * ecc * ecc * math.sin(2 * m_rad)
    )

    minutes = dt_local_naive.hour * 60 + dt_local_naive.minute + dt_local_naive.second / 60
    true_solar_minutes = (minutes + eq_time + 4 * longitude - 60 * utc_offset_hours) % 1440
    hour_angle = true_solar_minutes / 4 - 180
    if hour_angle < -180:
        hour_angle += 360

    lat_rad = math.radians(latitude)
    dec_rad = math.radians(decl)
    ha_rad = math.radians(hour_angle)
    cos_zenith = (
        math.sin(lat_rad) * math.sin(dec_rad)
        + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad)
    )
    zenith = math.degrees(math.acos(clamp(cos_zenith, -1, 1)))
    altitude = 90 - zenith

    # NOAA 方位角公式，转换为从正北顺时针
    azimuth = (
        math.degrees(math.atan2(
            math.sin(ha_rad),
            math.cos(ha_rad) * math.sin(lat_rad)
            - math.tan(dec_rad) * math.cos(lat_rad)
        )) + 180
    ) % 360
    return altitude, azimuth

def find_sunset(local_date, utc_offset, lat, lon):
    """用分钟扫描定位太阳中心向下穿过 -0.833° 的常用视日落阈值。"""
    start = datetime.combine(local_date, datetime.min.time())
    prev_dt = start
    prev_alt, _ = solar_position(prev_dt, utc_offset, lat, lon)
    for minute in range(1, 24 * 60 + 1):
        current_dt = start + timedelta(minutes=minute)
        current_alt, _ = solar_position(current_dt, utc_offset, lat, lon)
        if prev_alt >= -0.833 and current_alt < -0.833:
            # 标准视日落近似阈值：太阳中心高度角 -0.833°，考虑太阳视直径与常规折射。
            # 在线性区间内插，时间分辨率约为数十秒级；地形/气压/温度折射差异未单独建模。
            fraction = (prev_alt + 0.833) / (prev_alt - current_alt) if prev_alt != current_alt else 0
            sunset = prev_dt + timedelta(seconds=60 * fraction)
            _, az = solar_position(sunset, utc_offset, lat, lon)
            return sunset, az
        prev_dt, prev_alt = current_dt, current_alt
    return None, None

def find_sunrise(local_date, utc_offset, lat, lon):
    """定位太阳中心向上穿过 -0.833° 的常用视日出阈值。"""
    start = datetime.combine(local_date, datetime.min.time())
    prev_dt = start
    prev_alt, _ = solar_position(prev_dt, utc_offset, lat, lon)
    for minute in range(1, 24 * 60 + 1):
        current_dt = start + timedelta(minutes=minute)
        current_alt, _ = solar_position(current_dt, utc_offset, lat, lon)
        if prev_alt <= -0.833 and current_alt > -0.833:
            fraction = (-0.833 - prev_alt) / (current_alt - prev_alt) if current_alt != prev_alt else 0
            sunrise = prev_dt + timedelta(seconds=60 * fraction)
            _, az = solar_position(sunrise, utc_offset, lat, lon)
            return sunrise, az
        prev_dt, prev_alt = current_dt, current_alt
    return None, None

def estimate_geometry(local_date, utc_offset, lat, lon, effective_cloud_base_m,
                      edge_distance_km):
    """
    简化几何估算：
    find_sunset 使用太阳中心高度角 -0.833° 的视日落时刻；因此云缘照明阈值近似为
        -0.833° - atan(H_eff / D)
    即从视日落后，太阳中心再下降云底/云缘对应的角度后结束。
    """
    sunset, sunset_az = find_sunset(local_date, utc_offset, lat, lon)
    if sunset is None:
        return None

    if effective_cloud_base_m <= 0 or edge_distance_km <= 0:
        threshold = -0.833
    else:
        threshold = -0.833 - math.degrees(math.atan(
            (effective_cloud_base_m / 1000.0) / edge_distance_km
        ))

    end_time = None
    # 从日落起每 30 秒扫描，寻找太阳高度角低于几何阈值的时刻
    for sec in range(0, 3 * 3600 + 30, 30):
        dt = sunset + timedelta(seconds=sec)
        alt, _ = solar_position(dt, utc_offset, lat, lon)
        if alt < threshold:
            end_time = dt
            break

    duration_min = (end_time - sunset).total_seconds() / 60 if end_time else None
    return {
        "sunset": sunset,
        "sunset_azimuth": sunset_az,
        "threshold": threshold,
        "geometry_end": end_time,
        "geometry_duration_min": duration_min,
    }

def estimate_sunrise_geometry(local_date, utc_offset, lat, lon, effective_cloud_base_m,
                              edge_distance_km):
    """日出前的简化几何照明窗口；从日出时刻向前寻找太阳低于云缘阈值的边界。"""
    sunrise, sunrise_az = find_sunrise(local_date, utc_offset, lat, lon)
    if sunrise is None:
        return None
    if effective_cloud_base_m <= 0 or edge_distance_km <= 0:
        threshold = -0.833
    else:
        threshold = -0.833 - math.degrees(math.atan(
            (effective_cloud_base_m / 1000.0) / edge_distance_km
        ))
    start_time = None
    for sec in range(0, 3 * 3600 + 30, 30):
        dt = sunrise - timedelta(seconds=sec)
        alt, _ = solar_position(dt, utc_offset, lat, lon)
        if alt < threshold:
            start_time = dt
            break
    duration_min = (sunrise - start_time).total_seconds() / 60 if start_time else None
    return {"sunset": sunrise, "sunset_azimuth": sunrise_az, "threshold": threshold,
            "geometry_end": sunrise, "geometry_duration_min": duration_min,
            "geometry_start": start_time}

def destination_point(lat, lon, bearing_deg, distance_km):
    """球面近似：从目标点沿日落方位角前进 distance_km，得到采样点。"""
    radius_km = 6371.0088
    angular = distance_km / radius_km
    bearing = math.radians(bearing_deg)
    lat1, lon1 = math.radians(lat), math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular)
        + math.cos(lat1) * math.sin(angular) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), ((math.degrees(lon2) + 540) % 360) - 180

def aerosol_equivalent_height_km(aod, scale_height_km, beta_crit_km_inv=0.02):
    """按教程指数廓线计算等效地面高度：H=L*ln(AOD/(L*beta_crit))。

    AOD 为 550 nm 整层气溶胶光学厚度（无量纲）；L 单位 km；beta_crit 单位 km^-1。
    若地面消光系数已低于临界值（公式结果为负），本工具将等效高度下限截为 0 km。
    """
    if aod is None or not math.isfinite(float(aod)) or float(aod) <= 0:
        return 0.0 if aod == 0 else None
    if scale_height_km <= 0 or beta_crit_km_inv <= 0:
        raise ValueError("L 和临界消光系数必须大于 0。")
    ratio = float(aod) / (scale_height_km * beta_crit_km_inv)
    return max(0.0, scale_height_km * math.log(ratio)) if ratio > 0 else None

def analyze_aerosol_path(aod_samples, cloud_base_km, beta_crit_km_inv=0.02):
    """对每个日落方向 AOD 采样点计算 L=0.5..4 km 情景。

    这是各采样位置的局地垂直廓线敏感性分析，不是把这些点做成真实的斜向光路积分。
    """
    scale_heights = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    results = []
    for sample in aod_samples:
        aod = sample.get("aod")
        if aod is None:
            results.append({**sample, "scenarios": [], "height_min": None, "height_max": None,
                            "effective_base_min": None, "effective_base_max": None,
                            "reach_min": None, "reach_max": None})
            continue
        scenarios = []
        for scale_height in scale_heights:
            eq_height = aerosol_equivalent_height_km(aod, scale_height, beta_crit_km_inv)
            effective_base = cloud_base_km - eq_height
            reach = math.sqrt(8.0 * 6371.0088 * effective_base) if effective_base > 0 else 0.0
            scenarios.append({"L_km": scale_height, "equivalent_height_km": eq_height,
                              "effective_base_km": effective_base, "reach_km": reach})
        heights = [x["equivalent_height_km"] for x in scenarios]
        bases = [x["effective_base_km"] for x in scenarios]
        reaches = [x["reach_km"] for x in scenarios]
        results.append({**sample, "scenarios": scenarios,
                        "height_min": min(heights), "height_max": max(heights),
                        "effective_base_min": min(bases), "effective_base_max": max(bases),
                        "reach_min": min(reaches), "reach_max": max(reaches)})
    valid = [x for x in results if x.get("aod") is not None]
    return {"scale_heights": scale_heights, "beta_crit_km_inv": beta_crit_km_inv,
            "cloud_base_km": cloud_base_km, "samples": results,
            "valid_count": len(valid)}

def apply_high_elevation_nearby_low_cloud_filter(samples, profiles, site_elevation_m, radius_km=80.0):
    """高海拔机位的近场低云过滤；不删除80 km以外的光路低云。

    仅当本地气压层剖面显示：低层云候选主要位于机位海拔以下，且机位上方
    0–3 km 没有同等低层云候选时，才启用过滤。该判据是模式资料启发式判断。
    """
    try:
        site_elevation_m = float(site_elevation_m)
    except (TypeError, ValueError):
        return {"applied": False, "reason": "机位海拔无效", "removed_points": 0}
    if not math.isfinite(site_elevation_m) or site_elevation_m <= 0:
        return {"applied": False, "reason": "海拔为0或无效，保持原算法", "removed_points": 0}

    local = next((p for p in profiles or []
                  if abs(float(p.get("distance_km", 999999))) < 0.01), None)
    levels = (local or {}).get("levels") or []
    below_candidates = []
    above_candidates = []
    for row in levels:
        if not isinstance(row, dict) or not row.get("cloudy_candidate"):
            continue
        h_asl = row.get("height_m_asl")
        if not isinstance(h_asl, (int, float)) or not math.isfinite(float(h_asl)):
            continue
        h_asl = float(h_asl)
        if h_asl < site_elevation_m:
            below_candidates.append(h_asl)
        elif h_asl <= site_elevation_m + 3000.0:
            above_candidates.append(h_asl)
    # 只有确有机位以下低层云证据，且机位上方低层没有同类候选，才执行过滤。
    if not below_candidates or above_candidates:
        return {"applied": False,
                "reason": "剖面未能确认低云整体位于机位以下；保留原始云量与算法",
                "removed_points": 0}

    removed = 0
    for sample in samples or []:
        try:
            distance = float(sample.get("distance_km", 1e9))
        except (TypeError, ValueError):
            continue
        if distance > float(radius_km):
            continue
        low = sample.get("cloud_cover_low")
        if not isinstance(low, (int, float)) or not math.isfinite(float(low)) or float(low) <= 0:
            continue
        sample.setdefault("_elevation_filter_original_low", low)
        sample["cloud_cover_low"] = 0.0
        # 总云量没有可直接反演的独立剩余量；用保留下来的中/高云最大覆盖作保守代理，
        # 不触碰中云、高云和80 km外任何采样点。
        layers = [v for v in (sample.get("cloud_cover_mid"), sample.get("cloud_cover_high"))
                  if isinstance(v, (int, float)) and math.isfinite(float(v))]
        if layers:
            sample["cloud_cover"] = max(layers)
        sample["elevation_low_cloud_filtered"] = True
        removed += 1

    # 对0–80 km剖面，仅屏蔽低于机位海拔的低层云候选，再重新估算有效云底。
    # 这让云底/理论极限云距/云洞等后续计算统一使用过滤后的剖面。
    for profile in profiles or []:
        try:
            distance = float(profile.get("distance_km", 1e9))
        except (TypeError, ValueError):
            continue
        if distance > float(radius_km):
            continue
        original_levels = profile.get("levels") or []
        filtered_levels = []
        changed = False
        for row in original_levels:
            copy_row = dict(row)
            h_asl = copy_row.get("height_m_asl")
            if (copy_row.get("cloudy_candidate") and isinstance(h_asl, (int, float))
                    and math.isfinite(float(h_asl)) and float(h_asl) < site_elevation_m):
                copy_row["cloudy_candidate"] = False
                changed = True
            filtered_levels.append(copy_row)
        if changed:
            estimate = estimate_cloud_base_from_profile(filtered_levels, site_elevation_m)
            profile.update(estimate)
            profile["levels"] = estimate.get("levels", filtered_levels)
            profile["elevation_filter_applied"] = True
    return {"applied": True, "reason": "确认机位以下低云后，已过滤0–80 km低云并保留远端低云",
            "removed_points": removed, "radius_km": float(radius_km),
            "below_site_candidate_levels": len(below_candidates)}

def estimate_cloud_base_from_profile(profile_levels, ground_elevation_m, cloud_cover_threshold=25.0,
                                     rh_threshold=85.0):
    """从气压层云量/RH/位势高度估算最低可能云底（离地高度）。

    这是数值模式剖面的启发式推算，不是直接观测。优先使用模式云量>=阈值，
    或 RH>=阈值作为可能云层；云量阈值跨越时做线性插值以减轻气压层间距过粗的问题。
    profile_levels: [{pressure_hpa, height_m_asl, cloud_cover, relative_humidity}]
    """
    valid = []
    for level in profile_levels:
        height_asl = level.get("height_m_asl")
        if not isinstance(height_asl, (int, float)) or not math.isfinite(height_asl):
            continue
        height_agl_km = (float(height_asl) - float(ground_elevation_m)) / 1000.0
        if height_agl_km < -0.15 or height_agl_km > 18:
            continue
        row = dict(level)
        row["height_agl_km"] = max(0.0, height_agl_km)
        cc, rh = row.get("cloud_cover"), row.get("relative_humidity")
        row["cloudy_candidate"] = (
            (isinstance(cc, (int, float)) and math.isfinite(cc) and cc >= cloud_cover_threshold)
            or (isinstance(rh, (int, float)) and math.isfinite(rh) and rh >= rh_threshold)
        )
        valid.append(row)
    valid.sort(key=lambda x: x["height_agl_km"])
    if not valid:
        return {"estimated_base_km": None, "status": "无有效高度层", "levels": []}

    first_idx = next((i for i, row in enumerate(valid) if row["cloudy_candidate"]), None)
    if first_idx is None:
        return {"estimated_base_km": None, "status": "剖面未识别到明显云层", "levels": valid}

    current = valid[first_idx]
    base_km = current["height_agl_km"]
    method = "最低可能云层气压层高度"
    if first_idx > 0:
        lower = valid[first_idx - 1]
        low_cc, high_cc = lower.get("cloud_cover"), current.get("cloud_cover")
        if (isinstance(low_cc, (int, float)) and isinstance(high_cc, (int, float))
                and low_cc < cloud_cover_threshold <= high_cc and high_cc != low_cc):
            fraction = (cloud_cover_threshold - low_cc) / (high_cc - low_cc)
            base_km = lower["height_agl_km"] + fraction * (current["height_agl_km"] - lower["height_agl_km"])
            method = "云量阈值线性插值"
        else:
            method = "相邻气压层夹定（取首次可能云层高度）"
    else:
        method = "最低可用气压层已显示云层，真实云底可能更低"

    return {
        "estimated_base_km": max(0.0, base_km),
        "status": method,
        "levels": valid,
        "cloudy_level_pressure_hpa": current.get("pressure_hpa"),
    }

def analyze_cloud_base_geometry(samples, profile_samples, aod_samples, local_date, utc_offset,
                                latitude, longitude, cloud_base_mode, manual_base_km,
                                manual_aod=None, weighted_aod=None, scale_height_km=2.0,
                                event_type="sunset", ground_elevation_m=0.0):
    """将云底统一为海拔绝对高度（ASL，米/千米），再计算有效云底和理论照明距离。

    estimate_cloud_base_from_profile 返回的是相对当地地面的离地高度（AGL）；
    这里加上观测点海拔，转换为绝对高度。手动云底值则直接按绝对高度输入。
    0 m 机位时，数值与旧版离地高度输入一致。
    """
    profile_by_distance = {x["distance_km"]: x for x in profile_samples}
    aod_by_distance = {x["distance_km"]: x for x in aod_samples}
    rows = []
    for sample in samples:
        dist = sample["distance_km"]
        profile = profile_by_distance.get(dist, {})
        profile_base_agl = profile.get("estimated_base_km")
        if cloud_base_mode == "手动指定":
            cloud_base_km, base_source = manual_base_km, "手动绝对高度"
        elif profile_base_agl is not None:
            cloud_base_km = float(profile_base_agl) + max(0.0, float(ground_elevation_m or 0.0)) / 1000.0
            base_source = "剖面离地高度 + 地点海拔（绝对高度）"
        else:
            cloud_base_km, base_source = manual_base_km, "自动失败→手动绝对高度回退"

        aod_record = aod_by_distance.get(dist, {})
        if manual_aod is not None:
            aod_value, aod_source = manual_aod, "手动 AOD"
        elif aod_record.get("aod") is not None:
            aod_value, aod_source = aod_record["aod"], "CAMS 逐点 AOD"
        else:
            aod_value, aod_source = weighted_aod, "CAMS 加权 AOD 回退"

        eq_height = aerosol_equivalent_height_km(aod_value, scale_height_km) if aod_value is not None else 0.0
        if eq_height is None:
            eq_height = 0.0
        effective_base = max(0.0, cloud_base_km - eq_height)
        reach = math.sqrt(8.0 * 6371.0088 * effective_base) if effective_base > 0 else 0.0

        mid, high = sample.get("cloud_cover_mid"), sample.get("cloud_cover_high")
        total = sample.get("cloud_cover")
        if mid is not None or high is not None:
            target_cloud_cover = max(v for v in (mid, high) if v is not None)
            has_target_cloud = target_cloud_cover >= 15.0
            target_cloud_note = f"中/高云最大值 {target_cloud_cover:.0f}%"
        else:
            target_cloud_cover = total
            has_target_cloud = total is not None and total >= 25.0
            target_cloud_note = "中高云分层缺失，以总云量作弱替代" if total is not None else "云量缺失"

        within_reach = dist <= reach
        eligible = bool(has_target_cloud and within_reach and effective_base > 0)
        geometry_fn = estimate_sunrise_geometry if event_type == "sunrise" else estimate_geometry
        geometry = geometry_fn(local_date, utc_offset, latitude, longitude,
                               effective_base * 1000.0, max(1.0, float(dist)))
        rows.append({
            "distance_km": dist, "cloud_base_km": cloud_base_km, "cloud_base_asl_km": cloud_base_km,
            "cloud_base_agl_km": max(0.0, cloud_base_km - max(0.0, float(ground_elevation_m or 0.0)) / 1000.0),
            "base_source": base_source,
            "profile_status": profile.get("status", "未获取剖面"),
            "aod": aod_value, "aod_source": aod_source,
            "equivalent_height_km": eq_height, "effective_base_km": effective_base,
            "reach_km": reach, "target_cloud_cover": target_cloud_cover,
            "target_cloud_note": target_cloud_note, "has_target_cloud": has_target_cloud,
            "within_reach": within_reach, "eligible": eligible,
            "duration_min": geometry.get("geometry_duration_min") if geometry else None,
        })

    eligible_rows = [r for r in rows if r["eligible"]]
    farthest = max(eligible_rows, key=lambda r: r["distance_km"]) if eligible_rows else None
    return {
        "rows": rows,
        "farthest": farthest,
        "profile_success_count": sum(1 for x in profile_samples if x.get("estimated_base_km") is not None),
        "profile_count": len(profile_samples),
        "scale_height_km": scale_height_km,
    }

def estimate_path_cloud_boundaries(samples, local_cloud_reach_km=None):
    """识别理论极限云距以内的全部晴空间隔，并分别记录区间、长度与最远位置。

    中高云目标按中云/高云任一 >=25% 识别；有效晴空间隔要求低/中/高云均 <=15%。
    若晴空间隔延伸到理论照明距离，则其终点截断在理论极限云距。
    由于数据是离散采样，边界仍是经验估算，不是精确云缘。
    """
    ordered = sorted(samples, key=lambda x: x.get("distance_km", 0))
    def midhigh(row):
        vals = [v for v in (row.get("cloud_cover_mid"), row.get("cloud_cover_high"))
                if isinstance(v, (int, float))]
        return max(vals) if vals else None
    def all_layer_values(row):
        vals = [row.get(k) for k in ("cloud_cover_low", "cloud_cover_mid", "cloud_cover_high")]
        return vals if all(isinstance(v, (int, float)) for v in vals) else None
    def is_gap(row):
        vals = all_layer_values(row)
        return vals is not None and max(vals) <= 15.0

    try:
        reach = float(local_cloud_reach_km) if local_cloud_reach_km is not None else None
        if reach is not None and reach <= 0:
            reach = None
    except (TypeError, ValueError):
        reach = None
    relevant = [r for r in ordered if reach is None or float(r.get("distance_km", 0)) <= reach]
    cloudy = [r for r in relevant if midhigh(r) is not None and midhigh(r) >= 25.0]

    # 将连续的有效晴空采样点聚合成多个云洞。边界取相邻采样点中点，最后一个云洞若延伸到
    # 理论照明上限，则终点直接截断到极限云距，避免把采样终点误当作真实云缘。
    gaps = []
    i = 0
    while i < len(relevant):
        if not is_gap(relevant[i]):
            i += 1
            continue
        j = i
        while j + 1 < len(relevant) and is_gap(relevant[j + 1]):
            j += 1
        first_d = float(relevant[i].get("distance_km", 0))
        last_d = float(relevant[j].get("distance_km", 0))
        prev_d = float(relevant[i-1].get("distance_km", first_d)) if i > 0 else first_d
        next_d = float(relevant[j+1].get("distance_km", last_d)) if j + 1 < len(relevant) else last_d
        gap_start = first_d if i == 0 else (prev_d + first_d) / 2.0
        if j + 1 < len(relevant):
            gap_end = (last_d + next_d) / 2.0
        elif reach is not None and reach >= last_d:
            gap_end = reach
        else:
            gap_end = last_d
        gap_start = max(0.0, gap_start)
        gap_end = max(gap_start, gap_end)
        # 只保留有一定空间尺度的云洞，避免单个稀疏采样点被夸大。
        if gap_end - gap_start >= 15.0:
            gaps.append({"start_km": round(gap_start, 1), "end_km": round(gap_end, 1),
                         "size_km": round(gap_end-gap_start, 1),
                         "reaches_limit": bool(reach is not None and j == len(relevant)-1 and gap_end >= reach)})
        i = j + 1

    near_candidates = [r for r in cloudy if float(r.get("distance_km", 0)) <= 50.0]
    near_edge = near_candidates[0] if near_candidates else (cloudy[0] if cloudy else None)
    # 本地连续中高云区：从近端第一片云开始，遇到第一个明确云洞前的最后一个云点。
    fire_cloud_range = None
    if near_edge is not None:
        fire_cloud_range = float(near_edge.get("distance_km", 0))
        for row in relevant:
            d = float(row.get("distance_km", 0))
            if d < fire_cloud_range:
                continue
            if is_gap(row):
                break
            amount = midhigh(row)
            if amount is not None and amount >= 25.0:
                fire_cloud_range = d

    largest_gap = max(gaps, key=lambda g: g["size_km"], default=None)
    farthest_gap = max(gaps, key=lambda g: g["end_km"], default=None)
    # “极限云距”严格代表本地有效云底的理论照明距离，不再被远端云起点缩短。
    cloud_edge_distance = reach
    complete = [r for r in relevant if all_layer_values(r) is not None]
    path_span = (float(complete[-1].get("distance_km", 0)) - float(complete[0].get("distance_km", 0))) if len(complete) >= 2 else 0.0
    has_gap = bool(gaps)
    continuous_evidence = (len(complete) >= 4 and path_span >= 50.0 and not has_gap and
                           all(max(all_layer_values(r)) >= 25.0 for r in complete))
    if has_gap:
        gap_status = "effective_gap_candidate"
    elif continuous_evidence:
        gap_status = "continuous_cloud"
    else:
        gap_status = "unknown"
    return {
        "near_cloud_edge_km": near_edge.get("distance_km") if near_edge else None,
        "remote_cloud_start_km": min((float(r.get("distance_km", 0)) for r in cloudy
                                       if near_edge is not None and float(r.get("distance_km", 0)) > float(near_edge.get("distance_km", 0))), default=None),
        "cloud_edge_distance_km": cloud_edge_distance,
        "local_cloud_reach_km": reach,
        "fire_cloud_range_km": round(fire_cloud_range, 1) if fire_cloud_range is not None else None,
        "clear_gap_start_km": largest_gap["start_km"] if largest_gap else None,
        "clear_gap_end_km": largest_gap["end_km"] if largest_gap else None,
        "clear_gaps": gaps,
        "clear_gap_count": len(gaps),
        "largest_gap_size_km": largest_gap["size_km"] if largest_gap else 0.0,
        "farthest_gap_distance_km": farthest_gap["end_km"] if farthest_gap else None,
        "farthest_gap_start_km": farthest_gap["start_km"] if farthest_gap else None,
        "gap_status": gap_status,
        "gap_evidence_points": len(complete),
        "gap_evidence_span_km": round(path_span, 1),
        "cloudy_count": len(cloudy),
    }

def _cloud_height_color(height_km, cloud_cover=None, relative_humidity=None):
    """按高度带给剖面云层着色；颜色深浅近似表达模式云量强弱。"""
    if height_km < 2.0:
        palette = ((224, 241, 255), (80, 157, 222), (25, 91, 164))  # 低云：蓝
    elif height_km < 6.0:
        palette = ((255, 237, 205), (244, 166, 54), (178, 91, 12))  # 中云：橙
    else:
        palette = ((239, 226, 255), (166, 120, 218), (99, 54, 153))  # 高云：紫
    amount = cloud_cover if isinstance(cloud_cover, (int, float)) else None
    if amount is None and isinstance(relative_humidity, (int, float)):
        amount = max(0.0, min(100.0, (relative_humidity - 70.0) * 3.33))
    if amount is None:
        amount = 35.0
    t = max(0.0, min(1.0, amount / 100.0))
    if t < 0.35:
        rgb = palette[0]
    elif t < 0.70:
        rgb = palette[1]
    else:
        rgb = palette[2]
    return "#%02x%02x%02x" % rgb

def infer_from_cloud_path(samples, aod=None, local_low_cloud_above_ground=None):
    """透明的经验推断，不是经历史观测校准的概率模型。

    samples: [{distance_km, cloud_cover, cloud_cover_low, cloud_cover_mid,
               cloud_cover_high, visibility_m, wind_ms, ...}]
    """
    usable = [x for x in samples if any(x.get(k) is not None for k in
              ("cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high"))]
    if not usable:
        raise ValueError("日落方向采样点没有可用云量数据。")
    weights = [1.0 / (1.0 + x["distance_km"] / 100.0) for x in usable]
    def weighted_mean(key):
        pairs = [(x.get(key), w) for x, w in zip(usable, weights) if x.get(key) is not None]
        return sum(v*w for v,w in pairs) / sum(w for _,w in pairs) if pairs else None
    elevation_filtered = any(bool(x.get("elevation_low_cloud_filtered")) for x in usable)
    if elevation_filtered:
        # 海拔过滤已经改写了0–80 km采样点的低云量；结果区和评分都必须读取过滤后的数据，
        # 包括距离0 km的本地采样点，不能再沿用“排除本地点”的旧分支而把过滤效果藏起来。
        low = weighted_mean("cloud_cover_low")
    elif local_low_cloud_above_ground is False:
        # 原有0 m/未触发海拔过滤的逻辑保持不变：若本地剖面未显示地面以上低云，
        # 不把观测点的聚合低云量误当作本地遮挡；远端路径点低云仍参与加权。
        low_pairs = [(x.get("cloud_cover_low"), w) for x, w in zip(usable, weights)
                     if not (x.get("distance_km") is not None and abs(float(x.get("distance_km", 999999))) < 0.01)
                     and x.get("cloud_cover_low") is not None]
        low = sum(v*w for v, w in low_pairs) / sum(w for _, w in low_pairs) if low_pairs else None
    else:
        low = weighted_mean("cloud_cover_low")
    mid = weighted_mean("cloud_cover_mid")
    high = weighted_mean("cloud_cover_high")
    total = weighted_mean("cloud_cover")
    vis = weighted_mean("visibility_m")
    wind = weighted_mean("wind_ms")

    # 中/高云提供潜在受光目标；低云过多会遮挡近地平线；全层总云量极高时也可能过度阴蔽。
    score = 50.0
    reasons = []
    layer = [v for v in (mid, high) if v is not None]
    if layer:
        target_cloud = sum(layer) / len(layer)
        if 25 <= target_cloud <= 75:
            score += 20
            reasons.append(f"日落方向中/高云加权均值约 {target_cloud:.0f}%：有一定受光云量，处于经验上较有利的范围。")
        elif target_cloud < 15:
            score -= 18
            reasons.append(f"日落方向中/高云加权均值约 {target_cloud:.0f}%：可供着色的中高云偏少。")
        elif target_cloud > 90:
            score -= 8
            reasons.append(f"日落方向中/高云加权均值约 {target_cloud:.0f}%：云量很高，云层厚度与遮蔽可能削弱色彩层次。")
        else:
            score += 8
            reasons.append(f"日落方向中/高云加权均值约 {target_cloud:.0f}%：存在潜在受光云层。")
    else:
        target_cloud = None
        reasons.append("模式未提供中云/高云分层数据，受光云层判断能力有限。")
    if low is not None:
        if low >= 75:
            score -= 22
            reasons.append(f"低云加权均值约 {low:.0f}%：低云可能遮挡地平线附近光路。")
        elif low >= 50:
            score -= 12
            reasons.append(f"低云加权均值约 {low:.0f}%：存在一定近地平线遮挡风险。")
        elif low <= 20:
            score += 8
            reasons.append(f"低云加权均值约 {low:.0f}%：模式显示低云遮挡风险较低。")
    else:
        reasons.append("模式未提供低云分层数据，无法可靠评估低云遮挡。")
    if total is not None and total >= 90:
        score -= 12
        reasons.append(f"总云量加权均值约 {total:.0f}%：整体云量很高，需警惕厚云/阴天情形。")
    elif total is not None and 25 <= total <= 80:
        score += 5
        reasons.append(f"总云量加权均值约 {total:.0f}%：云量不是明显过少或极端覆盖。")
    if vis is not None:
        vis_km = vis / 1000.0
        if vis_km < 5:
            score -= 8
            reasons.append(f"预报能见度加权均值约 {vis_km:.1f} km：低能见度可能削弱远距离色彩，但能见度不等于 AOD。")
        elif vis_km >= 15:
            score += 4
            reasons.append(f"预报能见度加权均值约 {vis_km:.1f} km：通透度条件可能较好；它不能替代整条光路的气溶胶廓线。")
    if wind is not None and wind >= 15:
        score -= 5
        reasons.append(f"采样点风速加权均值约 {wind:.1f} m/s：云缘移动较快，预报不确定性增加。")

    # AOD 来自 CAMS Global 的 550 nm 整层气溶胶光学厚度。
    # 这里采用透明的经验修正项，不将 AOD 单值强行换算成气溶胶等效高度。
    if aod is not None:
        if aod <= 0.10:
            score += 6
            reasons.append(f"日落方向 AOD（550 nm）约 {aod:.3f}：气溶胶光学厚度较低，按经验规则小幅加分。")
        elif aod <= 0.15:
            score += 4
            reasons.append(f"日落方向 AOD（550 nm）约 {aod:.3f}：气溶胶光学厚度较低，按经验规则加分。")
        elif aod <= 0.25:
            score += 1
            reasons.append(f"日落方向 AOD（550 nm）约 {aod:.3f}：气溶胶影响按经验规则作轻微修正。")
        elif aod < 0.50:
            score -= 5
            reasons.append(f"日落方向 AOD（550 nm）约 {aod:.3f}：气溶胶消光可能削弱远距离光线，按经验规则扣分。")
        else:
            score -= 10
            reasons.append(f"日落方向 AOD（550 nm）约 {aod:.3f}：气溶胶光学厚度较高，可能削弱光线透过，按经验规则扣分。")
    else:
        reasons.append("没有可用的 AOD 预报或手动值，本次评分未进行 AOD 修正。")

    score = int(clamp(round(score), 0, 100))
    if score >= 70:
        tendency = "模式云况较有利"
    elif score >= 50:
        tendency = "有机会，需关注现场云缘"
    elif score >= 30:
        tendency = "模式云况偏弱"
    else:
        tendency = "模式云况不利"

    # 色彩只是示意：云量用于估计“是否有云可着色”，不能从模式云量直接推算真实光谱颜色。
    if target_cloud is None:
        color_pos, color_name, rgb = 0.45, "颜色不确定（缺少中高云分层）", (230, 135, 85)
    elif target_cloud < 15 or (low is not None and low >= 85):
        color_pos, color_name, rgb = 0.08, "偏淡黄白／颜色不明显", (255, 225, 150)
    elif vis is not None and vis < 5000:
        color_pos, color_name, rgb = 0.34, "偏暗橙红（低能见度情景）", (222, 92, 48)
    elif high is not None and high >= 35 and (low is None or low < 60):
        color_pos, color_name, rgb = 0.78, "橘红至洋红倾向（示意）", (221, 75, 105)
    elif target_cloud >= 25:
        color_pos, color_name, rgb = 0.48, "金橙至橘红倾向（示意）", (255, 132, 48)
    else:
        color_pos, color_name, rgb = 0.22, "偏金黄橙色（示意）", (255, 185, 70)

    # AOD只修正预测色块的饱和度与少量亮度；color_pos不变，固定Color Bar不受AOD影响。
    rgb, aod_color_note = adjust_color_for_aod(rgb, aod)
    if aod is not None and aod > 0.15:
        color_name += " · " + aod_color_note

    return {
        "score": score, "tendency": tendency, "low": low, "mid": mid,
        "high": high, "total": total, "visibility_m": vis, "wind_ms": wind,
        "aod": aod,
        "color_pos": color_pos, "color_name": color_name, "color_rgb": rgb,
        "reasons": reasons,
    }

CLOUD_LABELS = {
    "层积云": "层积云",
    "积云": "积云",
    "高层云": "高层云",
    "高积云": "高积云",
    "积雨云/云砧": "积雨云/云砧",
    "卷层云": "卷层云",
    "未知": "未知",
}

def analyze(data):
    lat = data["lat"]
    lon = data["lon"]
    if not -90 <= lat <= 90:
        raise ValueError("纬度必须在 -90 到 90 之间。")
    if not -180 <= lon <= 180:
        raise ValueError("经度必须在 -180 到 180 之间。")
    if not -12 <= data["utc_offset"] <= 14:
        raise ValueError("UTC 时差应在 -12 到 +14 小时之间。")
    if data["cloud_base"] <= 0:
        raise ValueError("云底高度必须大于 0 米。")
    if data["edge_distance"] <= 0:
        raise ValueError("云缘距离必须大于 0 公里。")
    if data["aerosol_height"] < 0:
        raise ValueError("气溶胶等效地面高度不能小于 0。")
    if data["aod"] < 0:
        raise ValueError("AOD 不能小于 0。")
    if data["wind"] < 0:
        raise ValueError("风速不能小于 0。")

    effective_base = data["cloud_base"] - data["aerosol_height"]
    geo = estimate_geometry(
        data["date"], data["utc_offset"], lat, lon,
        effective_base, data["edge_distance"]
    )
    if geo is None:
        raise ValueError("无法计算日落。请检查日期、纬度和时区。")

    # 经验规则：只作相对倾向，不是经过校准的概率。
    score = 50
    reasons = []
    cloud = data["cloud_type"]

    if effective_base <= 0:
        score -= 35
        reasons.append("气溶胶修正后的有效云底高度不大于 0，按本简化模型，照明条件很不利。")
    elif effective_base < 500:
        score -= 12
        reasons.append("有效云底偏低，远距离日落光照窗口通常较短。")
    elif effective_base > 6000:
        score += 8
        reasons.append("有效云底较高，几何上可能获得较长的受光窗口；实际仍受云型和遮挡影响。")
    else:
        reasons.append("有效云底高度处于中间范围，需结合云缘距离和遮挡判断。")

    if data["edge_distance"] < 50:
        score += 8
        reasons.append("日落方向云缘较近，光照几何条件相对有利。")
    elif data["edge_distance"] > 200:
        score -= 10
        reasons.append("日落方向云缘较远，太阳沉入地平线后可照亮云层的窗口可能较短。")

    if cloud == "层积云":
        score += 3
        reasons.append("层积云在特定云底高度、云缘距离和清洁大气条件下可能呈现短暂橘红色晚霞。")
    elif cloud == "高层云":
        score += 8
        reasons.append("高层云可形成较大面积的受光云层，但云厚和遮挡会显著改变效果。")
    elif cloud == "卷层云":
        score += 5
        reasons.append("薄卷层云可能呈现晚霞或伴随晕象，但不能仅凭云型保证出现。")
    elif cloud == "积云":
        score += 2
        reasons.append("积云的云顶高度和对流发展程度会影响受光面积与持续时间。")
    elif cloud == "高积云":
        score += 3
        reasons.append("高积云可呈现橙色至橘红色，但需要核对云量、云厚和其他云层遮挡。")
    elif cloud == "积雨云/云砧":
        score += 5
        reasons.append("积雨云残余云砧可能受夕阳照亮；需额外关注雷雨、阵风和强对流风险。")
    else:
        score -= 5
        reasons.append("云型未知，云型判断带来的不确定性较高。")

    if data["aod"] >= 0.5:
        score -= 10
        reasons.append("输入 AOD 较高，气溶胶消光可能削弱光线；地面 AOD 不代表整条传播路径。")
    elif data["aod"] <= 0.15:
        score += 4
        reasons.append("输入 AOD 较低，可能有利于大气通透度，但垂直分布仍未知。")

    if data["obstruction"] == "较少":
        score += 8
        reasons.append("输入显示日落方向其他云层遮挡较少。")
    elif data["obstruction"] == "中等":
        score -= 5
        reasons.append("存在部分遮挡，实际亮云范围可能小于几何估算。")
    elif data["obstruction"] == "较多":
        score -= 18
        reasons.append("其他云层遮挡较多，可能明显削弱或遮住受光云层。")

    if data["cloud_amount"] == "充足":
        score += 8
        reasons.append("输入显示可供受光的云量较充足。")
    elif data["cloud_amount"] == "偏少":
        score -= 12
        reasons.append("可供受光的云量偏少，可能只有局部晚霞。")

    if data["wind"] >= 15:
        score -= 4
        reasons.append("风速较大时云缘位置可能快速变化，预报不确定性增加。")
    if data["wind"] >= 30:
        score -= 6
        reasons.append("风速达到较高水平，短时云况变化风险进一步增加。")

    score = int(clamp(score, 0, 100))
    if score >= 70:
        tendency = "较有利"
    elif score >= 50:
        tendency = "存在机会，但依赖现场云况"
    elif score >= 30:
        tendency = "条件偏弱"
    else:
        tendency = "条件较不利"

    if effective_base <= 0:
        color = "颜色判断意义有限：有效云底高度非正"
    elif effective_base >= 5000 and data["aod"] <= 0.2:
        color = "具备较鲜明橙红色的潜力（经验倾向）"
    elif effective_base >= 2000:
        color = "可能呈橙黄至橘红色，取决于云厚与大气消光"
    else:
        color = "更倾向短暂橙红色或局部暖色晚霞"

    # 视野扩展时间：仅作为粗略参考，不与核心几何窗口混为“精确总时长”。
    view_extension_min = None
    if effective_base > 0:
        visual_distance_km = (effective_base / 1000) / math.tan(math.radians(5))
        view_extension_min = visual_distance_km / data["sunset_speed"]

    uncertainty = []
    if data["aerosol_height"] == 0:
        uncertainty.append("未进行气溶胶等效地面高度修正；有效云底高度可能被高估。")
    if data["cloud_top"] <= data["cloud_base"]:
        uncertainty.append("未提供有效的云顶高度（或云顶不高于云底），无法评估完整云层厚度。")
    if data["edge_distance"] > 150:
        uncertainty.append("远距离云缘位置对模式分辨率、云缘形状和风向较敏感。")
    if data["obstruction"] == "未知":
        uncertainty.append("其他云层遮挡情况未知。")
    if cloud == "未知":
        uncertainty.append("云型未知，经验规则的适用性有限。")
    if data["aod"] > 0 and data["aerosol_height"] > 0:
        uncertainty.append("AOD 与气溶胶等效地面高度为独立输入，程序不会擅自假定两者存在唯一换算关系。")
    if not uncertainty:
        uncertainty.append("输入项相对完整，但仍未接入实时卫星云图、探空和数值模式数据。")

    return {
        "geo": geo,
        "effective_base": effective_base,
        "score": score,
        "tendency": tendency,
        "color": color,
        "reasons": reasons,
        "uncertainty": uncertainty,
        "view_extension_min": view_extension_min,
    }

def aod_score_penalties(aod):
    """AOD分档扣分，区间内线性变化以避免阈值处突然跳分；非概率校准值。"""
    if aod is None:
        return 0.0, 0.0
    x = max(0.0, float(aod))
    def interp(points):
        if x <= points[0][0]:
            return points[0][1]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x <= x1:
                t = (x - x0) / (x1 - x0)
                return y0 + (y1 - y0) * t
        return points[-1][1]
    # 常规扣分：≤0.15无惩罚；0.35约2分；0.50约5分；0.80约9分；AOD≥1约14分。
    regular = interp([(0.15, 0), (0.35, 2), (0.50, 5), (0.80, 9), (1.00, 14)])
    # 上限评分更宽容，但仍保留明显消光的影响。
    upper = interp([(0.15, 0), (0.35, 1), (0.50, 2), (0.80, 5), (1.00, 9)])
    return regular, upper

def aod_color_quality_factor(aod):
    """经验色彩透过系数：低 AOD 基本保色，AOD=0.8 时接近中性灰。"""
    if aod is None:
        return 1.0
    x = max(0.0, float(aod))
    points = [(0.15, 1.00), (0.25, 0.88), (0.35, 0.65),
              (0.50, 0.30), (0.65, 0.10), (0.80, 0.025), (1.00, 0.005)]
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + (y1 - y0) * t
    return points[-1][1]

def adjust_color_for_aod(rgb, aod):
    """按 AOD 增强示意色的灰化；高 AOD 下暖色向灰粉橘过渡。"""
    if aod is None:
        return tuple(int(clamp(c, 0, 255)) for c in rgb), "AOD不可用，未修正色块"
    x = max(0.0, float(aod))
    def interp(points):
        if x <= points[0][0]:
            return points[0][1]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x <= x1:
                t = (x - x0) / (x1 - x0)
                return y0 + (y1 - y0) * t
        return points[-1][1]
    # 低 AOD 基本不动；0.8 时仅保留约 2.5% 原饱和度，接近无彩色；更高时继续灰化。
    saturation_factor = interp([(0.15, 1.00), (0.35, 0.65), (0.50, 0.30), (0.65, 0.10), (0.80, 0.025), (1.00, 0.005)])
    brightness_factor = interp([(0.15, 1.00), (0.35, 0.98), (0.50, 0.95), (0.80, 0.92), (1.00, 0.84)])
    # 高 AOD 时暖色轻微向橘粉方向偏移，避免红色只变成暗红，而不是灰粉橘。
    hue_shift = interp([(0.15, 0.0), (0.35, 0.004), (0.50, 0.010), (0.80, 0.026), (1.00, 0.032)])
    r, g, b = [clamp(float(c) / 255.0, 0.0, 1.0) for c in rgb]
    h, l, sat = colorsys.rgb_to_hls(r, g, b)
    # 只对红—橙—黄暖色做偏移；蓝紫等颜色不强行改色相。
    if h <= 0.17 or h >= 0.97:
        h = (h + hue_shift) % 1.0
    sat *= saturation_factor
    l *= brightness_factor
    rr, gg, bb = colorsys.hls_to_rgb(h, l, sat)
    adjusted = tuple(round(clamp(c * 255.0, 0, 255)) for c in (rr, gg, bb))
    if x <= 0.15:
        label = "AOD低：色块基本保留原饱和度"
    elif x < 0.35:
        label = "AOD轻微影响：色块轻度减饱和"
    elif x < 0.50:
        label = "AOD中等影响：色块适度减饱和"
    elif x < 0.80:
        label = "AOD较重影响：色块强烈减饱和"
    else:
        label = "AOD高：暖色明显灰化，红橙转为灰粉橘"
    return adjusted, label

def apply_peak_cloud_color(inferred, boundaries, samples):
    """估算主要受光云区最鲜艳时的代表色块；与发生评分分离，AOD/低云约束仍生效。"""
    if not isinstance(inferred, dict):
        return inferred
    samples = sorted([x for x in (samples or []) if isinstance(x, dict)],
                     key=lambda x: float(x.get("distance_km", 0) or 0))
    local = min(samples, key=lambda x: abs(float(x.get("distance_km", 0) or 0))) if samples else {}
    def num(obj, *keys):
        for key in keys:
            value = obj.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value):
                return float(value)
        return None
    local_total = num(local, "cloud_cover", "total")
    local_low = num(local, "cloud_cover_low", "low")
    local_mid = num(local, "cloud_cover_mid", "mid")
    local_high = num(local, "cloud_cover_high", "high")
    b = boundaries or {}
    gap_status = b.get("gap_status", "unknown")
    gap_start = num(b, "clear_gap_start_km")
    edge = num(b, "cloud_edge_distance_km", "fire_cloud_range_km")
    gaps = b.get("clear_gaps") or []
    gap_count = len(gaps)
    near_gap = min(gaps, key=lambda g: float(g.get("start_km", 0)), default=None)
    far_gap = max(gaps, key=lambda g: float(g.get("end_km", 0)), default=None)
    largest_gap = max(gaps, key=lambda g: float(g.get("size_km", 0)), default=None)
    near_gap_start = float(near_gap.get("start_km", 0)) if near_gap else gap_start
    far_gap_end = float(far_gap.get("end_km", 0)) if far_gap else None
    largest_gap_size = float(largest_gap.get("size_km", 0)) if largest_gap else float(b.get("largest_gap_size_km", 0) or 0)
    gap_found = bool(gaps) or (gap_status == "effective_gap_candidate" and gap_start is not None)

    # 远处是否存在足够大的中/高云目标区。不能把本地云少误判为“必有红霞”。
    remote_targets = []
    for sample in samples:
        d = num(sample, "distance_km") or 0.0
        if d < 25:
            continue
        mid = num(sample, "cloud_cover_mid")
        high = num(sample, "cloud_cover_high")
        if (mid is not None and mid >= 25) or (high is not None and high >= 25):
            remote_targets.append((d, mid or 0.0, high or 0.0))
    has_remote_target = bool(remote_targets)
    remote_target_distance = min((x[0] for x in remote_targets), default=None)
    local_cloudy = ((local_total is not None and local_total >= 65) or
                    (local_mid is not None and local_mid >= 55) or
                    (local_low is not None and local_low >= 45))

    # 颜色类别的优先级：低云占优先判橘色；近云缘判金橘；本地厚云且云洞遥远判粉紫；
    # 本地较通透、远处有可着色云并且确有云隙时，才偏红/粉。
    if local_low is not None and local_low >= 40 and (local_high is None or local_high < 25):
        base_rgb, pos, name = (255, 145, 48), 0.36, "低云占优：金橘色倾向"
    elif gap_found and near_gap_start is not None and near_gap_start <= 50 and far_gap_end is not None and far_gap_end >= 80 and gap_count >= 2:
        # 近端云洞偏金、远端云洞偏红；多个云洞同时存在时给出综合色相，而不是只看第一个云洞。
        near_size = float(near_gap.get("size_km", 0) or 0) if near_gap else 0.0
        far_size = float(far_gap.get("size_km", 0) or 0) if far_gap else 0.0
        red_weight = far_size / max(1.0, near_size + far_size)
        if red_weight >= 0.55:
            base_rgb, pos, name = (239, 73, 83), 0.69, "近端金霞与远端红霞共同贡献：综合色偏红"
        else:
            base_rgb, pos, name = (255, 151, 58), 0.39, "近端金霞与远端红霞共同贡献：综合色偏金橘"
    elif (gap_found and near_gap_start is not None and near_gap_start <= 50) or (not gap_found and edge is not None and edge <= 45):
        base_rgb, pos, name = (255, 164, 55), 0.30, "近端云洞较有利：金黄橘色倾向"
    elif local_cloudy and ((gap_found and ((far_gap_end or 0) >= 100 or largest_gap_size >= 100)) or (not gap_found and edge is not None and edge >= 120)):
        base_rgb, pos, name = (190, 91, 157), 0.86, "本地云量较多且有效云洞偏远：粉紫色倾向"
    elif (not local_cloudy and has_remote_target and gap_found and
          ((near_gap_start is not None and near_gap_start >= 40) or
           (remote_target_distance is not None and remote_target_distance >= 50))):
        base_rgb, pos, name = (232, 66, 105), 0.73, "近处较通透、远处有受光云：红粉色倾向"
    elif local_low is not None and local_low >= 25 and (local_high is None or local_high < 30):
        base_rgb, pos, name = (255, 150, 52), 0.34, "低层云较多、高云偏少：橘色倾向"
    elif has_remote_target and gap_found:
        base_rgb, pos, name = (225, 83, 112), 0.70, "存在云隙与远处云层：粉红色倾向"
    else:
        # 云洞/云缘证据不足时不擅自强行判红；按旧色相位置还原基础色，避免重复应用 AOD 灰化。
        pos = float(inferred.get("color_pos_before_low_cloud", inferred.get("color_pos", 0.45)) or 0.45)
        if pos < 0.18:
            base_rgb = (255, 225, 150)
        elif pos < 0.38:
            base_rgb = (255, 185, 70)
        elif pos < 0.58:
            base_rgb = (255, 132, 48)
        elif pos < 0.78:
            base_rgb = (232, 66, 105)
        else:
            base_rgb = (190, 91, 157)
        name = "云洞或主要受光云区证据不足：" + inferred.get("color_name", "颜色倾向待估算")

    aod_rgb, aod_note = adjust_color_for_aod(base_rgb, inferred.get("aod"))
    try:
        low_factor = float(inferred.get("low_cloud_color_factor", 1.0))
        low_factor = max(0.0, min(1.0, low_factor))
    except (TypeError, ValueError):
        low_factor = 1.0
    # 低云屏障仍压低代表色；与原先的屏障逻辑一致，但不再继承旧色相。
    neutral = (116, 119, 126)
    final_rgb = tuple(round(neutral[i] * (1-low_factor) + aod_rgb[i] * low_factor) for i in range(3))
    final_pos = round(0.08 + (float(pos) - 0.08) * low_factor, 3)
    if inferred.get("aod") is not None and float(inferred["aod"]) > 0.15:
        name += " · " + aod_note
    if low_factor < 0.99:
        name += " · 低云屏障减色"
    inferred["color_rgb"] = final_rgb
    inferred["color_pos"] = final_pos
    inferred["color_name"] = name
    inferred["peak_cloud_color_rule"] = name
    inferred["peak_cloud_color_base_rgb"] = tuple(base_rgb)
    inferred["peak_cloud_color_gap_start_km"] = gap_start
    inferred["peak_cloud_color_edge_km"] = edge
    inferred["peak_cloud_color_note"] = "代表主要受光云区的最鲜艳色相倾向；并非整片天空平均色或精确光谱预测。"
    return inferred

def apply_path_score_pair(inferred, geometry, boundaries):
    """按显式权重拆分常规分与理想情景上限分；分数不是统计概率。"""
    low, mid, high, total = (inferred.get(k) for k in ("low", "mid", "high", "total"))
    aod = inferred.get("aod")
    target_values = [v for v in (mid, high) if isinstance(v, (int, float))]
    target = sum(target_values) / len(target_values) if target_values else None
    local = next((r for r in geometry.get("rows", []) if r.get("distance_km") == 0), None)
    effective = local.get("effective_base_km") if local else None
    reach = local.get("reach_km") if local else None

    # 六项显式权重合计 100：几何25、光路/云隙25、中高云20、低云15、总云10、AOD 5。
    if isinstance(effective, (int, float)) and effective <= 0:
        geometry_score = 0
    elif isinstance(effective, (int, float)) and effective < 0.5:
        geometry_score = 30
    elif isinstance(effective, (int, float)) and effective < 1.0:
        geometry_score = 45
    elif isinstance(effective, (int, float)) and effective < 2.0:
        geometry_score = 60
    elif isinstance(effective, (int, float)) and effective < 4.0:
        geometry_score = 75
    elif isinstance(effective, (int, float)):
        geometry_score = 90
    else:
        geometry_score = 40  # 云底/几何缺失：不按有利处理
    if not isinstance(reach, (int, float)) or reach <= 0:
        geometry_score = min(geometry_score, 25)

    gaps = boundaries.get("clear_gaps") or []
    gap_count = len(gaps)
    largest_gap = float(boundaries.get("largest_gap_size_km", 0) or 0)
    total_gap_size = sum(float(g.get("size_km", 0) or 0) for g in gaps)
    reach_for_gap = boundaries.get("local_cloud_reach_km") or boundaries.get("cloud_edge_distance_km")
    gap_fraction = min(1.0, total_gap_size / max(1.0, float(reach_for_gap))) if isinstance(reach_for_gap, (int, float)) else 0.0
    gap_found = gap_count > 0 or (boundaries.get("gap_status") == "effective_gap_candidate" and boundaries.get("clear_gap_start_km") is not None)
    gap_status = boundaries.get("gap_status", "unknown")
    inferred["clear_gap_count"] = gap_count
    inferred["largest_gap_size_km"] = round(largest_gap, 1)
    inferred["total_gap_size_km"] = round(total_gap_size, 1)
    inferred["gap_fraction_of_reach"] = round(gap_fraction, 3)
    inferred["farthest_gap_distance_km"] = boundaries.get("farthest_gap_distance_km")
    if gap_found:
        # 全部云洞共同贡献：最大云洞决定基础通透度，云洞总长度占比与数量提供有限加成。
        size_component = max(0.0, min(100.0, largest_gap / 1.5))
        gap_regular = min(100.0, 25.0 + 0.55 * size_component + 25.0 * gap_fraction + min(10.0, max(0, gap_count-1)*3.0))
        gap_upper = min(100.0, max(gap_regular, 45.0 + 0.45 * size_component + 30.0 * gap_fraction + min(10.0, max(0, gap_count-1)*3.0)))
    else:
        gap_regular = 0 if gap_status == "continuous_cloud" else 15
        gap_upper = 10 if gap_status == "continuous_cloud" else 78

    if target is None:
        target_regular, target_upper = 35, 50
    elif target < 15:
        target_regular, target_upper = 15, 25
    elif target < 25:
        target_regular, target_upper = 45, 60
    elif target <= 75:
        target_regular, target_upper = 90, 100
    elif target <= 90:
        target_regular, target_upper = 65, 85
    else:
        target_regular, target_upper = 40, 75

    if low is None:
        low_regular, low_upper = 40, 60
    elif low < 20:
        low_regular, low_upper = 100, 100
    elif low < 35:
        low_regular, low_upper = 80, 90
    elif low < 55:
        low_regular, low_upper = 55, 75
    elif low < 75:
        low_regular, low_upper = 30, 60
    else:
        low_regular, low_upper = 10, 45

    if total is None:
        total_regular, total_upper = 40, 60
    elif 30 <= total <= 80:
        total_regular, total_upper = 100, 100
    elif 20 <= total < 30 or 80 < total <= 90:
        total_regular, total_upper = 70, 85
    elif total > 90:
        total_regular, total_upper = 30, 75
    else:
        total_regular, total_upper = 35, 50

    # 基础天气评分只计算云况/几何因素；AOD作为独立扣分项，避免仅占5%权重而影响过弱。
    # 五项权重归一化为100：几何25、光路/云隙25、中高云20、低云15、总云10。
    weights = {"geometry": 25, "gap": 25, "target": 20, "low": 15, "total": 10}
    weight_sum = sum(weights.values())
    regular_base = (geometry_score * weights["geometry"] + gap_regular * weights["gap"] +
                    target_regular * weights["target"] + low_regular * weights["low"] +
                    total_regular * weights["total"]) / weight_sum
    upper_base = (geometry_score * weights["geometry"] + gap_upper * weights["gap"] +
                  target_upper * weights["target"] + low_upper * weights["low"] +
                  total_upper * weights["total"]) / weight_sum
    aod_regular_penalty, aod_upper_penalty = aod_score_penalties(aod)
    regular = regular_base - aod_regular_penalty
    upper = upper_base - aod_upper_penalty
    if isinstance(effective, (int, float)) and effective <= 0:
        upper = min(upper, 35)  # 明确的几何矛盾不能被“理想情况”抹去
    if target is not None and target < 15:
        upper = min(upper, 35)
    # 关键修正：有足够距离采样、低/中/高云数据齐全，且相关照明路径内没有任何有效云洞时，
    # 不允许“中高云很多”把总分重新抬高。该上限是保守经验约束，不等于校准后的统计概率。
    if gap_status == "continuous_cloud":
        regular = min(regular, 20)
        upper = min(upper, 35)
    regular = int(round(max(0, min(100, regular))))
    upper = int(round(max(regular, min(100, upper))))
    inferred["regular_score"] = regular
    inferred["upper_score"] = upper
    inferred["score"] = regular  # 旧 UI 兼容：主分数统一指常规评分
    inferred["score_weights"] = weights
    inferred["aod_regular_penalty"] = round(aod_regular_penalty, 1)
    inferred["aod_upper_penalty"] = round(aod_upper_penalty, 1)
    inferred["score_notes"] = [
        "基础评分权重：光照几何25%、光路/晴空间隔25%、中高云20%、低云遮挡15%、总云量10%；基础权重归一化后，再独立应用AOD扣分。",
        f"AOD扣分：常规-{aod_regular_penalty:.1f}分，上限-{aod_upper_penalty:.1f}分；区间内线性变化，未经过历史实况校准。" if aod is not None else "AOD缺测：未做AOD扣分，不把缺测视为良好空气条件。",
        "上限评分保留几何和受光云量的硬约束，放宽部分云况惩罚；它描述理想情景潜力，不代表发生概率。",
    ]
    if gap_status == "continuous_cloud":
        inferred["score_notes"].append(
            f"云洞优先约束：理论照明距离内有{boundaries.get('gap_evidence_points', 0)}个完整低/中/高云采样点，"
            f"覆盖跨度约{boundaries.get('gap_evidence_span_km', 0):.0f} km，未发现低/中/高云量均<=15%的有效云洞；"
            "常规评分封顶20、上限评分封顶35，避免连续云层条件下仅因中高云充足而虚高。"
        )
    elif gap_status == "unknown":
        inferred["score_notes"].append("云洞状态未知：距离采样不足或低/中/高云数据不完整，不能把缺测当作晴空，也不按已证实连续云层硬封顶。")
    return inferred

def calculate_color_performance_score(inferred):
    """独立估算色彩表现潜力；这是经验指数，不是光谱/辐射传输模拟。"""
    low, mid, high, total = (inferred.get(k) for k in ("low", "mid", "high", "total"))
    vis = inferred.get("visibility_m")
    aod = inferred.get("aod")
    score = 35.0
    reasons = []

    layers = [v for v in (mid, high) if isinstance(v, (int, float))]
    target = sum(layers) / len(layers) if layers else None
    if target is None:
        score -= 10
        reasons.append("中高云分层缺测，色彩潜力判断不确定")
    elif target < 10:
        score -= 28
        reasons.append("中高云偏少，可被染色的云体不足")
    elif target < 25:
        score -= 12
        reasons.append("中高云偏少，可能只有局部显色")
    elif target <= 70:
        score += 20
        reasons.append("中高云量处于较有利的显色区间")
    elif target <= 85:
        score += 10
        reasons.append("中高云较多，色彩覆盖可能较大但层次有不确定性")
    else:
        score -= 8
        reasons.append("中高云覆盖很高，厚云/重叠可能压低色彩层次")

    if isinstance(high, (int, float)):
        if 20 <= high <= 65:
            score += 6
            reasons.append("高云有一定占比，可能增加高空染色范围")
        elif high > 85:
            score -= 4

    if isinstance(low, (int, float)):
        if low >= 85:
            score -= 22
            reasons.append("低云覆盖很高，可能压缩可见暖色区域")
        elif low >= 65:
            score -= 14
            reasons.append("低云较多，近地平线和远景显色可能受限")
        elif low <= 25:
            score += 4

    if isinstance(total, (int, float)):
        if total < 15:
            score -= 8
            reasons.append("总云量偏少，天空整体显色面积可能有限")
        elif 25 <= total <= 80:
            score += 4
        elif total >= 95:
            score -= 8
            reasons.append("总云量接近全覆盖，需警惕厚云和光线被削弱")

    if isinstance(vis, (int, float)):
        if vis < 5000:
            score -= 12
            reasons.append("能见度偏低，远距离色彩可能发灰、反差下降")
        elif vis >= 20000:
            score += 4
            reasons.append("预报能见度较好，有利于保持远景反差")

    # 云洞色彩贡献不再只看第一个晴空间隔：多个有效云洞及较大的总晴空跨度，
    # 可增加远近云层同时参与显色的潜力；加分封顶，避免仅凭云洞数量虚高。
    gap_count = int(inferred.get("clear_gap_count", 0) or 0)
    largest_gap = float(inferred.get("largest_gap_size_km", 0) or 0)
    gap_fraction = float(inferred.get("gap_fraction_of_reach", 0) or 0)
    if gap_count > 0:
        gap_bonus = min(12.0, min(6.0, max(0.0, largest_gap) / 25.0) +
                        min(4.0, max(0, gap_count - 1) * 2.0) + min(2.0, gap_fraction * 4.0))
        score += gap_bonus
        reasons.append(f"极限云距内识别到 {gap_count} 个有效云洞，最大约 {largest_gap:.0f} km；多个云洞共同增加远近云层显色潜力（加分 {gap_bonus:.0f}）。")
    if isinstance(aod, (int, float)):
        if aod <= 0.12:
            score += 4
        elif aod <= 0.25:
            score += 1
        elif aod >= 0.50:
            score -= 14
            reasons.append("AOD较高，远距离光线消光风险较大")
        elif aod >= 0.35:
            score -= 7
            reasons.append("AOD偏高，远距离色彩可能减弱")
    else:
        reasons.append("AOD缺测，未能充分评估大气消光")

    # 与发生评分分离，但把低云屏障对色彩的直接影响带入色彩评分。
    factor = inferred.get("low_cloud_color_factor", 1.0)
    try:
        factor = max(0.0, min(1.0, float(factor)))
    except (TypeError, ValueError):
        factor = 1.0
    score = score * (0.55 + 0.45 * factor)
    if factor < 0.5:
        reasons.append("太阳光路低云屏障明显压低暖色受光潜力")

    score = int(round(max(0, min(100, score))))
    if score >= 75:
        label = "色彩潜力较好"
    elif score >= 55:
        label = "色彩潜力中等偏好"
    elif score >= 35:
        label = "色彩潜力一般"
    else:
        label = "色彩潜力偏弱"
    inferred["color_score"] = score
    inferred["color_score_label"] = label
    inferred["color_score_reasons"] = reasons
    inferred["color_score_note"] = "色彩表现经验指数，不是实际色彩饱和度/红霞面积的精确预测。"
    return inferred

def has_local_low_cloud_above_ground(profile_levels, max_height_agl_km=3.0):
    """Return True/False when pressure-level profile supports a low-cloud layer above local terrain.

    The profile heights are converted from metres ASL to kilometres AGL by
    estimate_cloud_base_from_profile. None means there is insufficient profile evidence.
    This is a coarse model-level filter, not a direct cloud-base observation.
    """
    if not isinstance(profile_levels, (list, tuple)) or not profile_levels:
        return None
    usable = [r for r in profile_levels if isinstance(r, dict)
              and isinstance(r.get("height_agl_km"), (int, float))
              and math.isfinite(float(r["height_agl_km"]))]
    if not usable:
        return None
    for row in usable:
        h = float(row["height_agl_km"])
        if 0.0 <= h <= float(max_height_agl_km) and row.get("cloudy_candidate"):
            return True
    # Only return False if there were usable levels in the low-layer window; otherwise the
    # vertical sampling may simply be too coarse to establish absence.
    if any(0.0 <= float(row["height_agl_km"]) <= float(max_height_agl_km) for row in usable):
        return False
    return None

def apply_low_cloud_screen_constraint(inferred, samples=None, zones=None, source="online", max_relevant_distance_km=None,
                                      local_low_cloud_above_ground=None):
    """Apply a conservative score ceiling for broad, continuous high-coverage low-cloud screens.

    This is a heuristic constraint, not a claim that cloud-cover percentage equals opacity.
    Online samples use cloud_cover_low; Excel zones require an explicitly listed layer intersecting 0-2 km.
    """
    runs = []
    valid_sample_count = 0
    # 低云只有在“太阳照明几何可能相关的距离范围内”才可作为屏障。
    # 超出理论可照明范围的孤立远端低云，不应把本地目标云评分直接压到硬上限。
    try:
        relevant_limit = max(0.0, float(max_relevant_distance_km)) if max_relevant_distance_km is not None else None
    except (TypeError, ValueError):
        relevant_limit = None
    if zones:
        # Excel 区间是用户明确给出的连续距离带；将其裁剪到相关距离范围后再识别屏障。
        current = None
        for z in sorted(zones, key=lambda q: q["start"]):
            z_start = float(z["start"])
            z_end = float(z["end"])
            if relevant_limit is not None:
                if z_start >= relevant_limit:
                    if current: runs.append(current); current = None
                    continue
                z_end = min(z_end, relevant_limit)
            if z_end <= z_start:
                continue
            has_low = any(lo < 2000 and hi > 0 for lo, hi in z.get("layers", []))
            qualifies = has_low and float(z.get("cover", 0) or 0) >= 70
            if qualifies:
                if current and abs(z_start - current["end"]) < 0.01:
                    current["end"] = z_end
                    current["covers"].append(float(z["cover"]))
                else:
                    if current: runs.append(current)
                    current = {"start": z_start, "end": z_end, "covers": [float(z["cover"])]}
            else:
                if current: runs.append(current); current = None
        if current: runs.append(current)
    elif samples:
        valid = sorted([x for x in samples if x.get("distance_km") is not None and x.get("cloud_cover_low") is not None
                        and (relevant_limit is None or float(x["distance_km"]) <= relevant_limit)
                        and not (local_low_cloud_above_ground is False and abs(float(x.get("distance_km", 999999))) < 0.01)],
                       key=lambda x: x["distance_km"])
        if valid:
            valid_sample_count = len(valid)
            gaps = [valid[i+1]["distance_km"]-valid[i]["distance_km"] for i in range(len(valid)-1) if valid[i+1]["distance_km"] > valid[i]["distance_km"]]
            gaps.sort()
            step = gaps[len(gaps)//2] if gaps else 0.0
            current = None
            for x in valid:
                d = float(x["distance_km"]); cover = float(x["cloud_cover_low"] or 0)
                qualifies = cover >= 70
                if qualifies:
                    if current and (d-current["last"] <= max(step*1.8, 1.0)):
                        current["last"] = d; current["covers"].append(cover)
                    else:
                        if current and len(current["covers"]) >= 2:
                            runs.append({"start":current["start"], "end":current["last"]+step/2, "covers":current["covers"]})
                        current = {"start":max(0.0,d-step/2), "last":d, "covers":[cover]}
                else:
                    if current and len(current["covers"]) >= 2:
                        runs.append({"start":current["start"], "end":current["last"]+step/2, "covers":current["covers"]})
                    current=None
            if current and len(current["covers"]) >= 2:
                runs.append({"start":current["start"], "end":current["last"]+step/2, "covers":current["covers"]})

    # 单个纵向距离截面也可能对应一片足够宽的低云体；若横向 6 个点中至少 4 个低云量>=70%，
    # 且中心低云量>=85%，允许触发“横向覆盖型单截面屏障”，不再一概要求纵向两个点。
    if samples and not zones and relevant_limit is not None:
        for x in samples:
            try:
                d = float(x.get("distance_km"))
                center_low = x.get("center_cloud_cover_low", x.get("cloud_cover_low"))
                transverse = [float(v) for v in (x.get("cross_track_low_cover") or []) if v is not None]
            except (TypeError, ValueError):
                continue
            if d < 0 or d > relevant_limit or center_low is None or len(transverse) < 4:
                continue
            broad_count = sum(v >= 70 for v in transverse)
            if float(center_low) >= 90 and broad_count >= 4:
                cross_mean = sum(transverse) / len(transverse)
                rule = (18, 30, "单截面中心低云>=90%、横向至少4/6点低云>=70%") if cross_mean >= 85 else (25, 40, "单截面中心低云>=90%、横向至少4/6点低云>=70%")
                runs.append({"start": max(0.0, d-12.5), "end": min(relevant_limit, d+12.5),
                             "covers": [float(center_low)], "single_cross_track": True,
                             "cross_track_count": broad_count, "cross_track_mean": cross_mean,
                             "forced_rule": rule})
            elif float(center_low) >= 85 and broad_count >= 4:
                cross_mean = sum(transverse) / len(transverse)
                runs.append({"start": max(0.0, d-12.5), "end": min(relevant_limit, d+12.5),
                             "covers": [float(center_low)], "single_cross_track": True,
                             "cross_track_count": broad_count, "cross_track_mean": cross_mean,
                             "forced_rule": (25, 40, "单截面中心低云>=85%、横向至少4/6点低云>=70%")})

    # For a single-point forecast there is no path width information, but very high local low cloud
    # still merits a cautious ceiling rather than being averaged away by other scoring components.
    local_low = inferred.get("low")
    if (not zones and local_low_cloud_above_ground is not False and local_low is not None and local_low >= 85 and
            (valid_sample_count <= 1 or not any(r.get("end", 0)-r.get("start", 0) >= 30 for r in runs))):
        runs.append({"start":0.0, "end":0.0, "covers":[float(local_low)], "local_only":True})

    strongest = None
    for run in runs:
        width = max(0.0, run["end"]-run["start"])
        cover = sum(run["covers"])/len(run["covers"])
        if run.get("forced_rule"):
            rule = run["forced_rule"]
        elif run.get("local_only"):
            if cover >= 95: rule = (18, 30, "本地低云≥95%")
            else: rule = (28, 42, "本地低云≥85%")
        elif cover >= 90 and width >= 30:
            rule = (12, 25, "低云≥90%、连续宽度≥30 km")
        elif cover >= 85 and width >= 30:
            rule = (18, 30, "低云≥85%、连续宽度≥30 km")
        elif cover >= 75 and width >= 50:
            rule = (25, 40, "低云≥75%、连续宽度≥50 km")
        elif cover >= 70 and width >= 80:
            rule = (32, 48, "低云≥70%、连续宽度≥80 km")
        else:
            continue
        # 如果屏障前存在至少 50 km 的连续晴空走廊（总云量和低云量都<=20%），
        # 且屏障不是横向覆盖充分的单截面大云体，则将屏障严重度下调一级。
        # 这是针对离散网格资料的保守启发式，不等于证明真实太阳光线必然穿过云洞。
        clear_gap_before = 0.0
        if samples and not run.get("local_only") and not run.get("single_cross_track"):
            prev = sorted([x for x in samples if x.get("distance_km") is not None and float(x["distance_km"]) < float(run["start"])],
                          key=lambda x: float(x["distance_km"]))
            gap_start = None
            last_clear = None
            for sx in prev:
                tc = sx.get("cloud_cover")
                lc = sx.get("center_cloud_cover_low", sx.get("cloud_cover_low"))
                clear = tc is not None and lc is not None and float(tc) <= 20 and float(lc) <= 20
                if clear:
                    if gap_start is None: gap_start = float(sx["distance_km"])
                    last_clear = float(sx["distance_km"])
                else:
                    if gap_start is not None:
                        clear_gap_before = max(clear_gap_before, last_clear-gap_start)
                    gap_start = last_clear = None
            if gap_start is not None:
                clear_gap_before = max(clear_gap_before, last_clear-gap_start)
        if clear_gap_before >= 50 and not run.get("single_cross_track"):
            if rule[0] <= 18:
                rule = (25, 40, rule[2] + "；屏障前有>=50 km晴空走廊，降一级")
            elif rule[0] <= 25:
                rule = (32, 48, rule[2] + "；屏障前有>=50 km晴空走廊，降一级")
        candidate = {"start_km":round(run["start"],1), "end_km":round(run["end"],1), "width_km":round(width,1),
                     "mean_cover":round(cover,1), "regular_cap":rule[0], "upper_cap":rule[1], "rule":rule[2],
                     "local_only": bool(run.get("local_only")),
                     "single_cross_track": bool(run.get("single_cross_track")),
                     "clear_gap_before_km": round(clear_gap_before, 1),
                     "cross_track_count": run.get("cross_track_count"),
                     "cross_track_mean": round(run["cross_track_mean"], 1) if run.get("cross_track_mean") is not None else None}
        if strongest is None or candidate["regular_cap"] < strongest["regular_cap"]:
            strongest = candidate
    if strongest:
        before_regular = int(inferred.get("regular_score", inferred.get("score", 50)))
        before_upper = int(inferred.get("upper_score", before_regular))
        inferred["pre_low_screen_regular_score"] = before_regular
        inferred["pre_low_screen_upper_score"] = before_upper
        inferred["low_cloud_screen"] = strongest
        inferred["regular_score"] = min(before_regular, strongest["regular_cap"])
        inferred["upper_score"] = max(inferred["regular_score"], min(before_upper, strongest["upper_cap"]))
        inferred["score"] = inferred["regular_score"]
        inferred.setdefault("score_notes", []).append(
            f"低云屏障硬约束：{strongest['start_km']:.0f}–{strongest['end_km']:.0f} km，平均低云量 {strongest['mean_cover']:.0f}%，"
            f"连续宽度约 {strongest['width_km']:.0f} km；命中规则“{strongest['rule']}”，常规分上限 {strongest['regular_cap']}、上限评分上限 {strongest['upper_cap']}。"
        )
        # 评分和颜色不再分家：低云屏障命中后，统一压低“暖色受光潜力”。
        if strongest.get("local_only"):
            color_factor = 0.18 if cover >= 95 else 0.35
        elif strongest.get("single_cross_track"):
            color_factor = 0.22 if strongest.get("regular_cap", 25) <= 18 else 0.38
        elif strongest.get("clear_gap_before_km", 0) >= 50:
            color_factor = 0.52
        elif cover >= 90 and width >= 30:
            color_factor = 0.12
        elif cover >= 85 and width >= 30:
            color_factor = 0.22
        elif cover >= 75 and width >= 50:
            color_factor = 0.38
        else:
            color_factor = 0.52
        inferred["low_cloud_color_factor"] = color_factor
        inferred["low_cloud_color_note"] = (
            f"低云屏障使暖色受光显示系数降至 {color_factor:.2f}；这是可解释的示意性衰减，不是精确辐射传输值。"
        )
        base_rgb = inferred.get("color_rgb")
        if isinstance(base_rgb, (tuple, list)) and len(base_rgb) == 3:
            # 向低饱和灰褐色混合，避免只改文字而色块仍保持原样。
            neutral = (116, 119, 126)
            inferred["color_rgb_before_low_cloud"] = tuple(int(c) for c in base_rgb)
            inferred["color_rgb"] = tuple(int(neutral[i] * (1-color_factor) + float(base_rgb[i]) * color_factor) for i in range(3))
        old_pos = inferred.get("color_pos")
        if isinstance(old_pos, (int, float)):
            inferred["color_pos_before_low_cloud"] = float(old_pos)
            inferred["color_pos"] = round(0.08 + (float(old_pos) - 0.08) * color_factor, 3)
        old_name = inferred.get("color_name", "颜色倾向待估算")
        if "低云遮挡" not in old_name:
            inferred["color_name"] = f"低云遮挡后：暖色明显减弱（{old_name}）"
    else:
        inferred["low_cloud_screen"] = None
        inferred["low_cloud_color_factor"] = 1.0
        inferred["low_cloud_color_note"] = "未命中连续低云屏障规则；不额外压低暖色显示。"
    # 两种在线预测路径均在相同低云约束之后，使用同一色彩评分核心。
    return calculate_color_performance_score(inferred)

def score_two_day_event_shared_core(hourly, idx, model_id, ground_elevation_m=0.0, aod=None):
    """未来两日使用与单次路径查询相同的 apply_path_score_pair 核心评分。

    两日预报只有本地坐标的逐小时预报，因此用本地云况 + 同一套气压层云底估算，
    不伪造沿太阳方向的远端云隙。光路/云隙在单点模式下属于未知，常规分保守处理，
    上限分保留“可能存在云隙”的潜力空间。AOD缺测时不当作空气清洁。
    """
    def val(key):
        arr = hourly.get(key, [])
        try:
            x = arr[idx]
            return float(x) if x is not None and math.isfinite(float(x)) else None
        except (IndexError, TypeError, ValueError):
            return None

    sample = {
        "distance_km": 0.0,
        "cloud_cover": val("cloud_cover"),
        "cloud_cover_low": val("cloud_cover_low"),
        "cloud_cover_mid": val("cloud_cover_mid"),
        "cloud_cover_high": val("cloud_cover_high"),
        "visibility_m": val("visibility"),
        "wind_ms": val("wind_speed_10m"),
        "precipitation_probability": val("precipitation_probability"),
        "precipitation": val("precipitation"),
    }
    profile_levels = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50] if model_id == "ecmwf_ifs025" else [1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200]
    approx_height_asl_m = {1000:110, 975:320, 950:500, 925:800, 900:1000, 850:1500, 800:1900, 750:2500, 700:3000, 650:3600, 600:4200, 550:4900, 500:5600, 450:6300, 400:7200, 350:8100, 300:9200, 250:10400, 200:11800, 150:13500, 100:15800, 50:19300}
    levels = []
    for pressure in profile_levels:
        def profile_value(prefix):
            return val(f"{prefix}_{pressure}hPa")
        height = profile_value("geopotential_height")
        if height is None:
            height = approx_height_asl_m.get(pressure)
        levels.append({"pressure_hpa": pressure, "height_m_asl": height,
                       "cloud_cover": profile_value("cloud_cover"),
                       "relative_humidity": profile_value("relative_humidity")})
    try:
        profile = estimate_cloud_base_from_profile(levels, ground_elevation_m)
        cloud_base = profile.get("estimated_base_km")
    except Exception:
        cloud_base = None
    effective_base = None
    reach = None
    if isinstance(cloud_base, (int, float)):
        aerosol_height = aerosol_equivalent_height_km(aod, 0.5) if aod is not None else 0.0
        effective_base = max(0.0, cloud_base - (aerosol_height or 0.0))
        reach = math.sqrt(8.0 * 6371.0088 * effective_base) if effective_base > 0 else 0.0
    local_low_above_ground = has_local_low_cloud_above_ground(profile.get("levels") if isinstance(profile, dict) else None)
    inferred = infer_from_cloud_path([sample], aod, local_low_cloud_above_ground=local_low_above_ground)
    geometry = {"rows": [{"distance_km": 0.0, "effective_base_km": effective_base, "reach_km": reach}]}
    boundaries = estimate_path_cloud_boundaries([sample], reach)
    inferred = apply_path_score_pair(inferred, geometry, boundaries)
    inferred = apply_low_cloud_screen_constraint(inferred, samples=[sample], source="two_day",
                                                 max_relevant_distance_km=reach,
                                                 local_low_cloud_above_ground=local_low_above_ground)
    return inferred, {"cloud_base_km": cloud_base, "effective_base_km": effective_base,
                      "reach_km": reach, "aod": aod,
                      "profile_found": cloud_base is not None}


__all__ = [
    "CLOUD_LABELS", "clamp", "parse_float", "julian_day", "solar_position",
    "find_sunset", "find_sunrise", "estimate_geometry", "estimate_sunrise_geometry",
    "destination_point", "aerosol_equivalent_height_km", "analyze_aerosol_path",
    "apply_high_elevation_nearby_low_cloud_filter", "estimate_cloud_base_from_profile",
    "analyze_cloud_base_geometry", "estimate_path_cloud_boundaries", "infer_from_cloud_path",
    "analyze", "aod_score_penalties", "aod_color_quality_factor", "adjust_color_for_aod",
    "apply_peak_cloud_color", "apply_path_score_pair", "calculate_color_performance_score",
    "has_local_low_cloud_above_ground", "apply_low_cloud_screen_constraint",
    "score_two_day_event_shared_core",
]
