# -*- coding: utf-8 -*-
"""
火烧云预报工具 v1.4.51（朝晚霞查询与数据时间修正）
Python 3.11 / Tkinter，无第三方依赖。

重要说明：
1. 本程序是可运行的初版分析工具，不是经过实况校准的业务预报系统。
2. 太阳位置采用 NOAA 常用近似算法。
3. 火烧云三角使用 D_max=2*sqrt(2*R*h_eff) 估算理论最远云边界；新增 Excel 实况模拟的太阳斜向光路—云层高度带相交分析，识别连续高云量屏障并对评分施加独立惩罚。
   该分析是基于区间云量/高度带的二维近似，不是三维云体、云光学厚度或辐射传输模拟。
4. 自动获取 CAMS Global 的 550 nm AOD 预报，并允许用户手动覆盖。
5. 按教程的指数垂直廓线与临界消光系数，计算多个衰减高度 L 情景下的气溶胶等效地面高度。
6. 优先从 Open-Meteo 气压层云量/相对湿度/位势高度剖面估算沿日落方向采样点的最低可能云底；失败时回退到手动值。
7. 将局地 AOD 与云底估算结合，逐点计算有效云底、理论最远云边界，并检查采样点的中高云是否落入理论照明范围。
8. 云底剖面属于数值模式推算，不是实测；气溶胶垂直分布仍按教程公式做 L 情景，评分未经过实况校准。
"""

import math
import colorsys
import json
import urllib.parse
import urllib.request
import urllib.error
import time
import os
import re
import csv
import hashlib
import threading
from datetime import datetime, date, timedelta, timezone

# Tkinter is only needed by the desktop GUI. Streamlit Cloud is headless and may
# not ship the compiled _tkinter module, so provide lightweight import-time
# compatibility objects there. The web wrapper calls the calculation methods
# directly and never creates the desktop window.
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except (ImportError, ModuleNotFoundError):
    from types import SimpleNamespace

    class _HeadlessTk:
        pass

    class _HeadlessMessageBox:
        last_error = None
        last_info = None

        @classmethod
        def showerror(cls, title, message, **kwargs):
            cls.last_error = (str(title), str(message))
            return None

        @classmethod
        def showinfo(cls, title, message, **kwargs):
            cls.last_info = (str(title), str(message))
            return None

        @classmethod
        def askyesno(cls, *args, **kwargs):
            return False

    tk = SimpleNamespace(Tk=_HeadlessTk, TclError=Exception)
    ttk = SimpleNamespace()
    messagebox = _HeadlessMessageBox
    filedialog = SimpleNamespace()

# Expose forecast errors to the Streamlit wrapper while preserving desktop dialogs.
if not hasattr(messagebox, "last_error"):
    messagebox.last_error = None
if not hasattr(messagebox, "last_info"):
    messagebox.last_info = None
if not getattr(messagebox, "_firecloud_tracking_wrapped", False):
    _original_showerror = getattr(messagebox, "showerror", None)
    _original_showinfo = getattr(messagebox, "showinfo", None)

    def _tracked_showerror(title, message, **kwargs):
        messagebox.last_error = (str(title), str(message))
        if _original_showerror:
            return _original_showerror(title, message, **kwargs)
        return None

    def _tracked_showinfo(title, message, **kwargs):
        messagebox.last_info = (str(title), str(message))
        if _original_showinfo:
            return _original_showinfo(title, message, **kwargs)
        return None

    messagebox.showerror = _tracked_showerror
    messagebox.showinfo = _tracked_showinfo
    messagebox._firecloud_tracking_wrapped = True

from xml.sax.saxutils import escape as xml_escape

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None


APP_TITLE = "火烧云预报工具 v1.4.63（近场低云过滤修正）"

# 全国天气数据内存缓存：避免用户反复绘图时重复请求相同模型和坐标。
_NATIONAL_WEATHER_CACHE = {}
_NATIONAL_CACHE_TTL_SECONDS = 3600
# 全进程共享请求锁：避免全国地图和本地/单次预报同时向 Open-Meteo 发请求。
_HTTP_REQUEST_LOCK = threading.Lock()
_HTTP_JSON_CACHE = {}
_HTTP_JSON_CACHE_TTL = 900


# 磁盘缓存放在脚本同目录的 fire_cloud_cache 文件夹；关闭程序后仍保留。
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fire_cloud_cache")
_MODEL_META_CACHE = {}
_MODEL_META_CACHE_TTL = 300


def _metadata_candidates(model_id):
    candidates = [model_id]
    if model_id == "ncep_gfs_global":
        candidates.extend(["gfs_seamless", "ncep_gfs025", "ncep_gfs_global"])
    elif model_id == "ecmwf_ifs025":
        candidates.extend(["ecmwf_ifs", "ecmwf_ifs025_seamless", "ecmwf_ifs025"])
    return list(dict.fromkeys(candidates))


def _get_model_metadata_signature(model_id):
    """读取官方模型元数据。Open-Meteo 文档说明 metadata API 不计入日/月请求限额。"""
    now = time.time()
    cached = _MODEL_META_CACHE.get(model_id)
    if cached and now - cached[0] < _MODEL_META_CACHE_TTL:
        return cached[1]
    last_error = None
    deadline = time.time() + 8.0
    for candidate_id in _metadata_candidates(model_id):
        for host in ("https://api.open-meteo.com", "https://customer-api.open-meteo.com"):
            if time.time() >= deadline:
                break
            url = f"{host}/data/{candidate_id}/static/meta.json"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "FireCloudForecast/1.4.0"})
                with urllib.request.urlopen(req, timeout=min(3.0, max(0.5, deadline-time.time()))) as response:
                    data = json.loads(response.read().decode("utf-8"))
                if isinstance(data, dict) and any(k in data for k in (
                    "last_run_initialisation_time", "last_run_availability_time", "last_run_modification_time"
                )):
                    signature = {
                        "initialisation": data.get("last_run_initialisation_time"),
                        "availability": data.get("last_run_availability_time"),
                        "modification": data.get("last_run_modification_time"),
                    }
                    _MODEL_META_CACHE[model_id] = (time.time(), signature)
                    return signature
            except Exception as exc:
                last_error = exc
    _MODEL_META_CACHE[model_id] = (time.time(), None)
    return None


def _disk_cache_path(url):
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(_CACHE_DIR, digest + ".json")


def _read_disk_cache(url):
    path = _disk_cache_path(url)
    try:
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f)
        if isinstance(entry, dict) and "payload" in entry:
            return entry
    except (OSError, ValueError, TypeError):
        pass
    return None


def _write_disk_cache(url, payload, signature):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = _disk_cache_path(url)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"saved_at": time.time(), "meta_signature": signature, "payload": payload}, f,
                      ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        # 缓存写入失败不应阻断天气查询。
        pass


def request_json(req, timeout=20, retries=3, cache_key=None, cache_ttl=None):
    """带内存/磁盘缓存的 HTTP 请求；对明确指定的 Open-Meteo 模型按最新数据上线时间判缓存。"""
    now = time.time()
    url = getattr(req, "full_url", "")
    is_forecast = "api.open-meteo.com/v1/forecast?" in url
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query) if url else {}
    model_values = query.get("models", [])
    model_id = model_values[0] if model_values else None
    signature = _get_model_metadata_signature(model_id) if is_forecast and model_id else None
    disk_entry = _read_disk_cache(url) if is_forecast else None

    # 若元数据确认模型运行批次没变，直接使用磁盘缓存；不再请求庞大的逐小时天气数据。
    if disk_entry is not None:
        age = now - float(disk_entry.get("saved_at", 0))
        old_sig = disk_entry.get("meta_signature")
        if signature is not None and old_sig == signature:
            if cache_key:
                _HTTP_JSON_CACHE[cache_key] = (now, disk_entry["payload"])
            return disk_entry["payload"]
        # 元数据暂时不可访问时，优先用最多 3 小时的缓存兜底，避免网络故障时反复请求。
        if signature is None and age < 3 * 3600:
            if cache_key:
                _HTTP_JSON_CACHE[cache_key] = (now, disk_entry["payload"])
            return disk_entry["payload"]
        # 未显式指定模型（Best Match）时无法把缓存绑定到单一模式运行批次，采用 1 小时 TTL。
        if not model_id and age < 3600:
            if cache_key:
                _HTTP_JSON_CACHE[cache_key] = (now, disk_entry["payload"])
            return disk_entry["payload"]

    # 对普通接口/空气质量接口仍采用短期内存缓存；明确模型的 forecast 不让短 TTL 覆盖元数据判断。
    if cache_key and not (is_forecast and model_id):
        cached = _HTTP_JSON_CACHE.get(cache_key)
        if cached and now - cached[0] < (cache_ttl or _HTTP_JSON_CACHE_TTL):
            return cached[1]

    acquired = _HTTP_REQUEST_LOCK.acquire(timeout=8)
    if not acquired:
        raise RuntimeError("天气请求正在被另一个任务占用。请等待当前任务结束后再查询。")
    try:
        if cache_key and not (is_forecast and model_id):
            cached = _HTTP_JSON_CACHE.get(cache_key)
            if cached and time.time() - cached[0] < (cache_ttl or _HTTP_JSON_CACHE_TTL):
                return cached[1]
        last_error = None
        for attempt in range(max(0, retries) + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                if cache_key:
                    _HTTP_JSON_CACHE[cache_key] = (time.time(), data)
                if is_forecast:
                    _write_disk_cache(url, data, signature)
                return data
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    try:
                        body = exc.read(1200).decode("utf-8", errors="replace")
                    except Exception:
                        body = ""
                    reason = ""
                    try:
                        obj = json.loads(body) if body else {}
                        reason = obj.get("reason", "") if isinstance(obj, dict) else ""
                    except Exception:
                        pass
                    extra = (f"\n接口说明：{reason}" if reason else (f"\n接口返回：{body[:300]}" if body else ""))
                    raise RuntimeError("Open-Meteo 返回 HTTP 429，当前请求被限流。程序已停止重试，避免继续排队。请暂停查询 10–20 分钟后再试。" + extra) from exc
                # 5xx 多为服务端临时不可用；少量退避重试，不对 429 重试，避免加重限流。
                if exc.code in (500, 502, 503, 504) and attempt < max(0, retries):
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        wait_s = min(15.0, max(1.0, float(retry_after))) if retry_after else min(8.0, 2.0 ** (attempt + 1))
                    except (TypeError, ValueError):
                        wait_s = min(8.0, 2.0 ** (attempt + 1))
                    time.sleep(wait_s)
                    continue
                if exc.code in (500, 502, 503, 504) and disk_entry is not None:
                    return disk_entry["payload"]
                try:
                    body = exc.read(800).decode("utf-8", errors="replace")
                except Exception:
                    body = ""
                detail = f"\n接口返回：{body[:300]}" if body else ""
                if exc.code in (500, 502, 503, 504):
                    raise RuntimeError(f"Open-Meteo 服务暂时不可用（HTTP {exc.code}）。已进行有限重试；请稍后再试。{detail}") from exc
                raise RuntimeError(f"Open-Meteo 请求失败（HTTP {exc.code}）。{detail}") from exc
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                last_error = exc
                if attempt >= max(0, retries):
                    # 网络失败且存在缓存时返回旧数据；避免因为暂时的 SSL/代理故障丢失可用结果。
                    if disk_entry is not None:
                        return disk_entry["payload"]
                    raise RuntimeError(f"天气接口连接失败或超时（单次最多等待 {timeout} 秒）：{exc}") from exc
                time.sleep(2)
        raise RuntimeError(f"天气接口请求失败：{last_error}")
    finally:
        _HTTP_REQUEST_LOCK.release()


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


class FireCloudApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1080x900")
        self.minsize(760, 560)
        self.configure(bg="#eef2f5")
        self._setup_style()
        self.vars = {}
        self.last_fetch_time = None
        self.aod_manual_override = False
        self._setting_aod_programmatically = False
        self.web_aod_value = None
        self.web_aod_valid_time = None
        self.aod_path_values = []
        self.cloud_path_samples = []
        self.cloud_path_result = None
        self.last_report = ""
        self.direction_results = {}
        self.fetch_in_progress = False
        self.loaded_case_path = None
        self.case_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "案例数据")
        self._build_ui()

    def _scroll_main_with_wheel(self, event):
        """鼠标位于主窗口内容区时滚动主窗口；不抢占弹出的独立窗口滚动。"""
        widget = self.winfo_containing(event.x_root, event.y_root)
        node = widget
        belongs = False
        while node is not None:
            if node == self.main_scroll_canvas:
                belongs = True
                break
            node = getattr(node, "master", None)
        if not belongs:
            return
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            delta = getattr(event, "delta", 0)
            units = -int(delta / 120) * 3 if delta else 0
        if units:
            self.main_scroll_canvas.yview_scroll(units, "units")
        return "break"

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#eef2f5")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#eef2f5", foreground="#243447", font=("Microsoft YaHei UI", 10))
        style.configure("Card.TLabel", background="#ffffff", foreground="#243447", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background="#eef2f5", foreground="#172b4d", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Sub.TLabel", background="#eef2f5", foreground="#5e6c84", font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", background="#ffffff", foreground="#172b4d", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 7))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 8))
        style.configure("TEntry", padding=5)
        style.configure("TCombobox", padding=5)

    def _add_var(self, key, value):
        self.vars[key] = tk.StringVar(value=str(value))

    def _build_ui(self):
        shell = ttk.Frame(self)
        shell.pack(fill="both", expand=True)
        self.main_scroll_canvas = tk.Canvas(shell, highlightthickness=0, bg="#eef2f5")
        main_scrollbar = ttk.Scrollbar(shell, orient="vertical", command=self.main_scroll_canvas.yview)
        self.main_scroll_canvas.configure(yscrollcommand=main_scrollbar.set)
        main_scrollbar.pack(side="right", fill="y")
        self.main_scroll_canvas.pack(side="left", fill="both", expand=True)
        outer = ttk.Frame(self.main_scroll_canvas, padding=16)
        self._main_outer_window = self.main_scroll_canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _e: self.main_scroll_canvas.configure(scrollregion=self.main_scroll_canvas.bbox("all")))
        self.main_scroll_canvas.bind("<Configure>", lambda e: self.main_scroll_canvas.itemconfigure(self._main_outer_window, width=e.width))
        self.bind_all("<MouseWheel>", self._scroll_main_with_wheel, add="+")
        self.bind_all("<Button-4>", self._scroll_main_with_wheel, add="+")
        self.bind_all("<Button-5>", self._scroll_main_with_wheel, add="+")

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="火烧云预报工具", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="v1.4.63 · 地名联想/近场低云过滤修正；案例保存/加载",
            style="Sub.TLabel"
        ).pack(anchor="w", pady=(4, 0))
        # 全国地图快捷入口放在窗口顶部，避免左侧设置项较多时被挤到可视区域之外。
        ttk.Button(
            header, text="一键预报本地未来两日（8项）",
            style="Accent.TButton", command=self.open_local_2day_forecast
        ).pack(side="right", padx=(8, 0), pady=2)
        ttk.Button(
            header, text="一键绘制全国双模式地图",
            command=self.open_national_map
        ).pack(side="right", padx=(8, 0), pady=2)

        main = ttk.Frame(outer)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main, style="Card.TFrame", padding=14)
        left.pack(side="left", fill="y", padx=(0, 12))
        left.configure(width=340)
        # 让左侧设置面板按内容自然增长高度；超出屏幕的部分由主窗口滚动条访问。

        right = ttk.Frame(main, style="Card.TFrame", padding=14)
        right.pack(side="left", fill="both", expand=True)

        ttk.Label(left, text="案例库 / 实况验证", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        case_buttons = ttk.Frame(left, style="Card.TFrame")
        case_buttons.pack(fill="x", pady=(0, 10))
        ttk.Button(case_buttons, text="加载案例", command=self.load_case).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(case_buttons, text="保存案例", command=self.save_case).pack(side="left", fill="x", expand=True, padx=3)
        ttk.Button(case_buttons, text="记录现场实况", command=self.record_field_observation).pack(side="left", fill="x", expand=True, padx=(3, 0))
        ttk.Button(left, text="实况模拟（读取 Excel）", style="Accent.TButton", command=self.run_live_simulation).pack(fill="x", pady=(0, 10))
        ttk.Label(left, text="预报设置", style="Section.TLabel").pack(anchor="w", pady=(0, 10))
        form = ttk.Frame(left, style="Card.TFrame")
        form.pack(fill="x")

        visible_defaults = [
            ("地点名称", "合肥", "place"),
            ("预报日期 YYYY-MM-DD", date.today().isoformat(), "date"),
            ("纬度（°）", "31.8047", "lat"),
            ("经度（°）", "117.2214", "lon"),
            ("海拔（m）", "0", "elevation"),
        ]
        for label, value, key in visible_defaults:
            self._add_var(key, value)
            row = ttk.Frame(form, style="Card.TFrame")
            row.pack(fill="x", pady=5)
            ttk.Label(row, text=label, style="Card.TLabel").pack(anchor="w")
            entry = ttk.Entry(row, textvariable=self.vars[key], width=25)
            entry.pack(fill="x", pady=(2, 0))
            if key == "place":
                entry.bind("<KeyRelease>", self.update_place_suggestions)
                entry.bind("<Return>", self.on_place_enter)
                entry.bind("<Down>", self.focus_place_suggestions)
                self.place_suggestion_list = tk.Listbox(
                    row, height=6, activestyle="dotbox", exportselection=False,
                    font=("Microsoft YaHei UI", 9), relief="solid", bd=1
                )
                self.place_suggestion_list.pack(fill="x", pady=(2, 0))
                self.place_suggestion_list.pack_forget()
                self.place_suggestion_list.bind("<ButtonRelease-1>", self.select_place_suggestion)
                self.place_suggestion_list.bind("<Return>", self.select_place_suggestion)
                ttk.Label(row, text="输入前几个字即可联想 Excel 地名；点击候选项，或按 Enter 选择首项。垭口等地点可自行添加表格行。", style="Sub.TLabel", wraplength=300).pack(anchor="w", pady=(2, 0))
            elif key == "elevation":
                ttk.Label(row, text="读取地名表中的海拔；也可直接修改，单位为米。", style="Sub.TLabel", wraplength=300).pack(anchor="w", pady=(2, 0))

        self.place_lookup_status_var = tk.StringVar(value="优先读取同目录 china_city_coordinates.xlsx；地名表中的海拔可手动维护。")
        ttk.Label(form, textvariable=self.place_lookup_status_var, style="Sub.TLabel", wraplength=300).pack(anchor="w", pady=(0, 4))

        # 云底来源模式仍为全局设置；AOD 与手动云底数值分别放在朝霞/晚霞结果区。
        base_row = ttk.Frame(form, style="Card.TFrame")
        base_row.pack(fill="x", pady=5)
        self._add_var("cloud_base_mode", "自动剖面推算")
        ttk.Label(base_row, text="云底来源", style="Card.TLabel").pack(anchor="w", pady=(5, 0))
        ttk.Combobox(
            base_row, textvariable=self.vars["cloud_base_mode"],
            values=["自动剖面推算", "手动指定"], state="readonly"
        ).pack(fill="x", pady=(2, 0))
        ttk.Label(base_row, text="自动模式优先使用各自方向剖面，手动云底仅在自动推算失败时回退；要让手动值直接生效，请切换为‘手动指定’，再点击对应的‘查询朝霞’或‘查询晚霞’。", style="Sub.TLabel", wraplength=300).pack(anchor="w", pady=(3, 0))

        self._add_var("weather_model", "ECMWF IFS 0.25°")
        row = ttk.Frame(form, style="Card.TFrame")
        row.pack(fill="x", pady=5)
        ttk.Label(row, text="天气预报模式", style="Card.TLabel").pack(anchor="w")
        ttk.Combobox(
            row, textvariable=self.vars["weather_model"],
            values=["ECMWF IFS 0.25°", "GFS Global"],
            state="readonly"
        ).pack(fill="x", pady=(2, 0))

        btns = ttk.Frame(left, style="Card.TFrame")
        btns.pack(fill="x", pady=(8, 0))
        self.fetch_buttons = {}
        query_row = ttk.Frame(btns, style="Card.TFrame")
        query_row.pack(fill="x", pady=(0, 6))
        self.fetch_buttons["morning"] = ttk.Button(query_row, text="查询朝霞", style="Accent.TButton", command=lambda: self.fetch_weather("morning"))
        self.fetch_buttons["morning"].pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.fetch_buttons["evening"] = ttk.Button(query_row, text="查询晚霞", style="Accent.TButton", command=lambda: self.fetch_weather("evening"))
        self.fetch_buttons["evening"].pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.fetch_buttons["both"] = ttk.Button(btns, text="一键查询朝晚霞", style="Accent.TButton", command=lambda: self.fetch_weather("both"))
        self.fetch_buttons["both"].pack(fill="x", pady=(0, 6))
        self.fetch_button = self.fetch_buttons["morning"]  # 兼容旧代码中对按钮状态的引用
        self.fetch_status_var = tk.StringVar(value="可分别查询朝霞/晚霞，也可一键更新两个时段。")
        ttk.Label(btns, textvariable=self.fetch_status_var, style="Sub.TLabel", wraplength=300).pack(anchor="w", pady=(0, 6))
        ttk.Button(btns, text="恢复合肥默认设置", command=self.reset_defaults).pack(fill="x")
        ttk.Label(
            left,
            text="提示：默认坐标为合肥。时区按中国标准时间（UTC+8）计算；更换国家/地区时需进一步设置时区。",
            style="Sub.TLabel", wraplength=300
        ).pack(anchor="w", pady=(12, 0))

        # 左下角统一进度显示；快捷功能仍保留在窗口右上角，避免左侧重复按钮。
        progress_panel = ttk.Frame(left, style="Card.TFrame")
        progress_panel.pack(side="bottom", fill="x", pady=(18, 0))
        progress_header = ttk.Frame(progress_panel, style="Card.TFrame")
        progress_header.pack(fill="x")
        ttk.Label(progress_header, text="任务进度", style="Section.TLabel").pack(side="left")
        self.main_progress_percent_var = tk.StringVar(value="0%")
        ttk.Label(progress_header, textvariable=self.main_progress_percent_var, style="Card.TLabel",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(side="right")
        self.main_progressbar = ttk.Progressbar(progress_panel, mode="determinate", maximum=100, value=0)
        self.main_progressbar.pack(fill="x", pady=(6, 4))
        self.main_progress_status_var = tk.StringVar(value="等待任务")
        ttk.Label(progress_panel, textvariable=self.main_progress_status_var, style="Sub.TLabel",
                  wraplength=300).pack(anchor="w")

        ttk.Label(right, text="朝霞与晚霞预报", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        morning_result_row = ttk.Frame(right, style="Card.TFrame")
        morning_result_row.pack(fill="x", pady=(0, 8))
        self.morning_summary = tk.Text(
            morning_result_row, height=5, wrap="word", font=("Microsoft YaHei UI", 10),
            bg="#f7f9fb", fg="#243447", relief="flat", padx=12, pady=9
        )
        morning_score_frame = ttk.Frame(morning_result_row, style="Card.TFrame", padding=(6, 4), width=142)
        morning_score_frame.pack(side="right", fill="y")
        morning_score_frame.pack_propagate(False)
        self.morning_summary.pack(side="left", fill="both", expand=True)
        self.morning_summary.configure(state="disabled")
        morning_score_head = ttk.Frame(morning_score_frame, style="Card.TFrame")
        morning_score_head.pack(fill="x", pady=(3, 0))
        morning_swatch_col = ttk.Frame(morning_score_head, style="Card.TFrame")
        morning_swatch_col.pack(side="left", padx=(0, 5))
        self.morning_peak_swatch = tk.Canvas(morning_swatch_col, width=38, height=30, bg="#ffffff", highlightthickness=0)
        self.morning_peak_swatch.pack()
        ttk.Label(morning_swatch_col, text="峰值云色", style="Sub.TLabel", font=("Microsoft YaHei UI", 7)).pack()
        morning_score_num_col = ttk.Frame(morning_score_head, style="Card.TFrame")
        morning_score_num_col.pack(side="left", fill="x", expand=True)
        self.morning_score_label = tk.Label(morning_score_num_col, text="—", font=("Microsoft YaHei UI", 23, "bold"),
                                            fg="#1d4ed8", bg="#ffffff", width=5)
        self.morning_score_label.pack(pady=(0, 0))
        ttk.Label(morning_score_num_col, text="常规概率*", style="Sub.TLabel").pack()
        self.morning_upper_label = ttk.Label(morning_score_frame, text="上限概率 —", style="Card.TLabel", anchor="center")
        self.morning_upper_label.pack(pady=(5, 0))
        self.morning_color_score_label = tk.Label(morning_score_frame, text="色彩 —/100", font=("Microsoft YaHei UI", 9, "bold"),
                                                  fg="#c66a2b", bg="#ffffff", wraplength=116, justify="center")
        self.morning_color_score_label.pack(fill="x", pady=(4, 0))
        self.morning_rainbow_label = tk.Label(morning_score_frame, text="彩虹：—", font=("Microsoft YaHei UI", 8),
                                              fg="#7c5a9e", bg="#ffffff", wraplength=116, justify="center")
        self.morning_rainbow_label.pack(fill="x", pady=(7, 3))
        self._set_text(self.morning_summary, "朝霞：尚未查询。选择日期与 ECMWF/GFS 模式后点击对应的‘查询朝霞’或‘查询晚霞’。")
        self.morning_params_frame = ttk.Frame(right, style="Card.TFrame")
        self.morning_params_frame.pack(fill="x", pady=(0, 6))
        self._add_var("morning_aod", "")
        self._add_var("morning_cloud_base_km", "2.0")
        ttk.Label(self.morning_params_frame, text="朝霞 AOD（留空使用 CAMS）", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Entry(self.morning_params_frame, textvariable=self.vars["morning_aod"], width=10).grid(row=0, column=1, sticky="w", padx=(0, 16), pady=2)
        ttk.Button(self.morning_params_frame, text="清空/恢复 CAMS", command=lambda: self.vars["morning_aod"].set("")).grid(row=0, column=2, sticky="w", padx=(0, 14), pady=2)
        ttk.Label(self.morning_params_frame, text="云底绝对高度手动值/回退值（km，海拔；改后需重新查询）", style="Card.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Entry(self.morning_params_frame, textvariable=self.vars["morning_cloud_base_km"], width=10).grid(row=1, column=1, sticky="w", pady=2)
        self.morning_aod_status_var = tk.StringVar(value="AOD 留空时自动使用朝霞方向 CAMS 值")
        ttk.Label(self.morning_params_frame, textvariable=self.morning_aod_status_var, style="Sub.TLabel").grid(row=0, column=3, sticky="w", padx=(8, 0), pady=2)
        self.morning_params_frame.columnconfigure(3, weight=1)
        self.morning_color_canvas = tk.Canvas(right, height=82, bg="#ffffff", highlightthickness=0)
        self.morning_color_canvas.pack(fill="x", pady=(0, 5))
        self.morning_color_canvas.bind("<Configure>", lambda _event: self._draw_colorbar("morning"))
        morning_actions = ttk.Frame(right, style="Card.TFrame")
        morning_actions.pack(fill="x", pady=(0, 10))
        ttk.Button(morning_actions, text="查看朝霞地图云层分布", command=lambda: self.show_path_cloud_map("morning")).pack(side="left")
        ttk.Button(morning_actions, text="高度剖面", command=lambda: self.show_path_cloud_profile("morning")).pack(side="left", padx=(5, 0))
        ttk.Button(morning_actions, text="显示朝霞各采样点具体云况", command=lambda: self.show_direction_details("morning")).pack(side="right")

        evening_result_row = ttk.Frame(right, style="Card.TFrame")
        evening_result_row.pack(fill="x", pady=(0, 8))
        self.evening_summary = tk.Text(
            evening_result_row, height=5, wrap="word", font=("Microsoft YaHei UI", 10),
            bg="#f7f9fb", fg="#243447", relief="flat", padx=12, pady=9
        )
        evening_score_frame = ttk.Frame(evening_result_row, style="Card.TFrame", padding=(6, 4), width=142)
        evening_score_frame.pack(side="right", fill="y")
        evening_score_frame.pack_propagate(False)
        self.evening_summary.pack(side="left", fill="both", expand=True)
        self.evening_summary.configure(state="disabled")
        evening_score_head = ttk.Frame(evening_score_frame, style="Card.TFrame")
        evening_score_head.pack(fill="x", pady=(3, 0))
        evening_swatch_col = ttk.Frame(evening_score_head, style="Card.TFrame")
        evening_swatch_col.pack(side="left", padx=(0, 5))
        self.evening_peak_swatch = tk.Canvas(evening_swatch_col, width=38, height=30, bg="#ffffff", highlightthickness=0)
        self.evening_peak_swatch.pack()
        ttk.Label(evening_swatch_col, text="峰值云色", style="Sub.TLabel", font=("Microsoft YaHei UI", 7)).pack()
        evening_score_num_col = ttk.Frame(evening_score_head, style="Card.TFrame")
        evening_score_num_col.pack(side="left", fill="x", expand=True)
        self.evening_score_label = tk.Label(evening_score_num_col, text="—", font=("Microsoft YaHei UI", 23, "bold"),
                                            fg="#1d4ed8", bg="#ffffff", width=5)
        self.evening_score_label.pack(pady=(0, 0))
        ttk.Label(evening_score_num_col, text="常规概率*", style="Sub.TLabel").pack()
        self.evening_upper_label = ttk.Label(evening_score_frame, text="上限概率 —", style="Card.TLabel", anchor="center")
        self.evening_upper_label.pack(pady=(5, 0))
        self.evening_color_score_label = tk.Label(evening_score_frame, text="色彩 —/100", font=("Microsoft YaHei UI", 9, "bold"),
                                                  fg="#c66a2b", bg="#ffffff", wraplength=116, justify="center")
        self.evening_color_score_label.pack(fill="x", pady=(4, 0))
        self.evening_rainbow_label = tk.Label(evening_score_frame, text="彩虹：—", font=("Microsoft YaHei UI", 8),
                                              fg="#7c5a9e", bg="#ffffff", wraplength=116, justify="center")
        self.evening_rainbow_label.pack(fill="x", pady=(7, 3))
        self._set_text(self.evening_summary, "晚霞：尚未查询。")
        self.evening_params_frame = ttk.Frame(right, style="Card.TFrame")
        self.evening_params_frame.pack(fill="x", pady=(0, 6))
        self._add_var("evening_aod", "")
        self._add_var("evening_cloud_base_km", "2.0")
        ttk.Label(self.evening_params_frame, text="晚霞 AOD（留空使用 CAMS）", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Entry(self.evening_params_frame, textvariable=self.vars["evening_aod"], width=10).grid(row=0, column=1, sticky="w", padx=(0, 16), pady=2)
        ttk.Button(self.evening_params_frame, text="清空/恢复 CAMS", command=lambda: self.vars["evening_aod"].set("")).grid(row=0, column=2, sticky="w", padx=(0, 14), pady=2)
        ttk.Label(self.evening_params_frame, text="云底绝对高度手动值/回退值（km，海拔；改后需重新查询）", style="Card.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Entry(self.evening_params_frame, textvariable=self.vars["evening_cloud_base_km"], width=10).grid(row=1, column=1, sticky="w", pady=2)
        self.evening_aod_status_var = tk.StringVar(value="AOD 留空时自动使用晚霞方向 CAMS 值")
        ttk.Label(self.evening_params_frame, textvariable=self.evening_aod_status_var, style="Sub.TLabel").grid(row=0, column=3, sticky="w", padx=(8, 0), pady=2)
        self.evening_params_frame.columnconfigure(3, weight=1)
        self.evening_color_canvas = tk.Canvas(right, height=82, bg="#ffffff", highlightthickness=0)
        self.evening_color_canvas.pack(fill="x", pady=(0, 5))
        self.evening_color_canvas.bind("<Configure>", lambda _event: self._draw_colorbar("evening"))
        evening_actions = ttk.Frame(right, style="Card.TFrame")
        evening_actions.pack(fill="x", pady=(0, 8))
        ttk.Button(evening_actions, text="查看晚霞地图云层分布", command=lambda: self.show_path_cloud_map("evening")).pack(side="left")
        ttk.Button(evening_actions, text="高度剖面", command=lambda: self.show_path_cloud_profile("evening")).pack(side="left", padx=(5, 0))
        ttk.Button(evening_actions, text="显示晚霞各采样点具体云况", command=lambda: self.show_direction_details("evening")).pack(side="right")
        ttk.Label(right, text="采样范围根据各点推算的有效云底动态扩展；单次最远限制 600 km。具体云况默认收起，点击上方按钮查看。", style="Sub.TLabel", wraplength=620).pack(anchor="w", pady=(2, 0))

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="保存分析报告", command=self.save_report).pack(side="right")

    def _set_main_progress(self, percent, status=None):
        """安全更新主窗口左下角的百分比进度条，可从后台线程调用。"""
        try:
            value = max(0, min(100, int(round(float(percent)))))
        except (TypeError, ValueError):
            value = 0
        def apply():
            try:
                self.main_progressbar.configure(value=value)
                self.main_progress_percent_var.set(f"{value}%")
                if status is not None:
                    self.main_progress_status_var.set(str(status))
            except (tk.TclError, AttributeError):
                pass
        try:
            self.after(0, apply)
        except tk.TclError:
            pass

    def fetch_weather(self, direction_key="both"):
        """在后台查询指定方向；不清空另一方向已有结果。"""
        if self.fetch_in_progress:
            return
        self.fetch_in_progress = True
        self._active_query_direction = direction_key
        try:
            for key, button in getattr(self, "fetch_buttons", {}).items():
                button.configure(state="disabled")
            self.fetch_status_var.set(("正在查询朝霞" if direction_key == "morning" else "正在查询晚霞" if direction_key == "evening" else "正在查询朝霞与晚霞") + "；天气接口遇到限流时会停止并提示。")
        except Exception:
            pass

        def worker():
            try:
                self._fetch_weather_task(direction_key)
            finally:
                try:
                    self.after(0, self._finish_fetch_weather)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True, name="single-weather-query").start()

    def _finish_fetch_weather(self):
        self.fetch_in_progress = False
        try:
            for button in getattr(self, "fetch_buttons", {}).values():
                button.configure(state="normal")
            self.fetch_status_var.set("查询结束。若显示 HTTP 429，请先暂停 10–20 分钟再试。")
        except Exception:
            pass

    def _fetch_weather_task(self, requested_direction="both"):
        """后台查询：按所选模式计算朝霞、晚霞或指定的单一方向。"""
        try:
            data = self._read_inputs()
            model_label = self.vars["weather_model"].get()
            model_id = "ecmwf_ifs025" if model_label.startswith("ECMWF") else "ncep_gfs_global"
            cloud_base_mode = self.vars["cloud_base_mode"].get()

            fetched_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
            # 读取 Open-Meteo 模型元数据：起报时间与数据上线时间，均不是本机查询时间。
            model_meta = _get_model_metadata_signature(model_id)
            def _fmt_model_epoch(value):
                try:
                    return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(
                        timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
                except (TypeError, ValueError, OSError, OverflowError):
                    return None
            model_init_time = _fmt_model_epoch((model_meta or {}).get("initialisation"))
            model_available_time = _fmt_model_epoch((model_meta or {}).get("availability"))
            model_modified_time = _fmt_model_epoch((model_meta or {}).get("modification"))
            if model_available_time:
                model_time_text = f"模型数据上线：{model_available_time}（北京时间）"
            elif model_modified_time:
                model_time_text = f"模型数据处理完成：{model_modified_time}（北京时间；上线时间不可用）"
            elif model_init_time:
                model_time_text = f"模式起报时间：{model_init_time}（北京时间；数据上线时间不可用）"
            else:
                model_time_text = "模型数据时间：暂时无法读取（元数据接口不可用）"
            # 起报时间已由上方模型数据时间字段说明，不再重复追加“| 模式起报”。
            results = {}
            directions_to_query = (("morning", "evening") if requested_direction == "both" else (requested_direction,))
            for direction_index, direction_key in enumerate(directions_to_query):
                base_progress = 3 if direction_index == 0 else 51
                self._set_main_progress(base_progress, ("正在计算朝霞路径……" if direction_key == "morning" else "朝霞完成，正在计算晚霞路径……"))
                if direction_key == "morning":
                    event_dt, azimuth = find_sunrise(data["date"], data["utc_offset"], data["lat"], data["lon"])
                    direction_name = "朝霞"
                else:
                    event_dt, azimuth = find_sunset(data["date"], data["utc_offset"], data["lat"], data["lon"])
                    direction_name = "晚霞"
                # 朝霞与晚霞各自读取参数，互不影响。
                cloud_base_key = f"{direction_key}_cloud_base_km"
                aod_key = f"{direction_key}_aod"
                cloud_base_km = parse_float(self.vars[cloud_base_key].get(), f"{direction_name}云底高度手动值/回退值")
                if not 0.1 <= cloud_base_km <= 15.0:
                    raise ValueError(f"{direction_name}云底绝对高度应在 0.1–15 km 之间，请以海拔绝对高度输入。")
                aod_text = self.vars[aod_key].get().strip()
                manual_aod = parse_float(aod_text, f"{direction_name} AOD") if aod_text else None
                if manual_aod is not None and manual_aod < 0:
                    raise ValueError(f"{direction_name} AOD 不能小于 0。")
                if event_dt is None:
                    results[direction_key] = {"summary": f"{direction_name}：该日期/纬度无法定位日出日落。", "details": "无太阳事件数据。"}
                    continue
                event_utc = (event_dt - timedelta(hours=data["utc_offset"])).replace(tzinfo=None)
                # 先用 0–200 km 粗采样剖面估算理论范围，再按最大几何深入距离扩展。
                coarse_distances = [0, 25, 50, 100, 150, 200]
                coarse_coords = [destination_point(data["lat"], data["lon"], azimuth, d) for d in coarse_distances]
                self._set_main_progress(base_progress + 7, f"{direction_name}：获取初始云层剖面……")
                try:
                    coarse_profiles = self._fetch_cloud_base_profiles(coarse_coords, coarse_distances, event_utc, model_id,
                                                                          local_ground_elevation_m=data.get("elevation_m"))["samples"]
                except Exception as exc:
                    coarse_profiles = [{"distance_km": d, "estimated_base_km": None, "status": f"剖面失败：{exc}", "levels": []} for d in coarse_distances]
                self._set_main_progress(base_progress + 13, f"{direction_name}：获取气溶胶数据……")
                try:
                    coarse_aod = self._fetch_aod_path(coarse_coords, coarse_distances, event_utc)["samples"]
                except Exception:
                    coarse_aod = [{"distance_km": d, "aod": None} for d in coarse_distances]
                aod_map = {x["distance_km"]: x.get("aod") for x in coarse_aod}
                reaches = []
                for profile in coarse_profiles:
                    base = profile.get("estimated_base_km") if cloud_base_mode == "自动剖面推算" else cloud_base_km
                    if base is None:
                        base = cloud_base_km
                    aod = manual_aod if manual_aod is not None else aod_map.get(profile["distance_km"])
                    eq = aerosol_equivalent_height_km(aod, 0.5) if aod is not None else 0.0
                    effective_base = max(0.0, base - (eq or 0.0))
                    reaches.append(math.sqrt(8.0 * 6371.0088 * effective_base) if effective_base > 0 else 0.0)
                max_reach = max(reaches) if reaches else 0.0
                # 至少到 200 km；按 50 km 步长向外扩展，并给最远理论范围预留约 100 km 的采样缓冲。
                sample_limit = min(600, max(200, int(math.ceil((max_reach + 100.0) / 50.0) * 50)))
                distances = [0, 25, 50, 100, 150, 200]
                distances.extend(range(250, sample_limit + 1, 50))
                distances = sorted(set(d for d in distances if d <= 600))
                coords = [destination_point(data["lat"], data["lon"], azimuth, d) for d in distances]

                # 每个纵向距离截面采样 9 点：中心点、垂直光路方向左右各 12/25/40 km，
                # 以及沿光路方向前后各 12 km。这样既保留局地空间均值，也能估算低云横向覆盖，
                # 避免把单个纵向采样点简单等同于“连续云墙”。
                area_coords_by_center = []
                cross_offsets_by_center = []
                for center_lat, center_lon in coords:
                    offsets = [None, -40.0, -25.0, -12.0, 12.0, 25.0, 40.0, None, None]
                    neighborhood = [(center_lat, center_lon)]
                    for off in offsets[1:7]:
                        bearing = (azimuth + (90.0 if off > 0 else -90.0)) % 360
                        neighborhood.append(destination_point(center_lat, center_lon, bearing, abs(off)))
                    neighborhood.append(destination_point(center_lat, center_lon, azimuth, 12.0))
                    neighborhood.append(destination_point(center_lat, center_lon, (azimuth + 180.0) % 360, 12.0))
                    area_coords_by_center.append(neighborhood)
                    cross_offsets_by_center.append(offsets)
                query_coords = [pt for group in area_coords_by_center for pt in group]
                # 独立的本地烧云规模采样：中心点 + 20–200 km、每 20 km 一环的 8 方位点。
                # 这组采样只用于最终“小小烧/微烧/...”标签，不参与原有路径采样及评分。
                local_scale_coords = []
                for radius_km in range(20, 201, 20):
                    for bearing_deg in range(0, 360, 45):
                        local_scale_coords.append(destination_point(data["lat"], data["lon"], bearing_deg, radius_km))
                path_location_count = len(query_coords)
                query_coords.extend(local_scale_coords)
                self._set_main_progress(base_progress + 22, f"{direction_name}：请求沿线天气与本地 200 km 规模采样（{len(query_coords)} 个空间点）……")
                raw_locations = self._fetch_weather_locations(query_coords, model_id)
                # 以中心点和 80 个本地环点计算 0–200 km 的独立云量均值。
                local_scale_records = []
                for loc in ([raw_locations[0]] if raw_locations else []) + raw_locations[path_location_count:]:
                    hourly = loc.get("hourly", {})
                    times = hourly.get("time", [])
                    if not times:
                        continue
                    idx = min(range(len(times)), key=lambda i: abs((datetime.fromisoformat(times[i]) - event_utc).total_seconds()))
                    if abs((datetime.fromisoformat(times[idx]) - event_utc).total_seconds()) > 5400:
                        continue
                    rec = {}
                    for key in ("cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high"):
                        series = hourly.get(key, [])
                        val = series[idx] if idx < len(series) else None
                        rec[key] = val if isinstance(val, (int, float)) and math.isfinite(val) else None
                    local_scale_records.append(rec)
                def _local_scale_avg(key):
                    vals = [r[key] for r in local_scale_records if r.get(key) is not None]
                    return sum(vals) / len(vals) if vals else None
                local_scale_clouds = {
                    "total": _local_scale_avg("cloud_cover"),
                    "low": _local_scale_avg("cloud_cover_low"),
                    "mid": _local_scale_avg("cloud_cover_mid"),
                    "high": _local_scale_avg("cloud_cover_high"),
                    "radius_km": 200, "sample_count": len(local_scale_records),
                }
                self._set_main_progress(base_progress + 34, f"{direction_name}：整理沿线云量与天气数据……")
                cloud_keys = ("cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
                              "visibility", "wind_speed_10m", "precipitation_probability", "precipitation", "wind_gusts_10m")
                samples, raw_details = [], []
                for dist, coord, neighborhood, cross_offsets, locs in zip(distances, coords, area_coords_by_center, cross_offsets_by_center,
                                                          [raw_locations[i:i+len(area_coords_by_center[0])] for i in range(0, len(raw_locations), len(area_coords_by_center[0]))]):
                    records = []
                    for rec_idx, (subcoord, loc) in enumerate(zip(neighborhood, locs)):
                        hourly = loc.get("hourly", {})
                        times = hourly.get("time", [])
                        if not times:
                            continue
                        idx = min(range(len(times)), key=lambda i: abs((datetime.fromisoformat(times[i]) - event_utc).total_seconds()))
                        valid_dt = datetime.fromisoformat(times[idx])
                        gap_seconds = abs((valid_dt - event_utc).total_seconds())
                        if gap_seconds > 5400:
                            # 把时间窗写进错误提示，便于区分历史数据窗不足、时区偏移和接口缺数。
                            first_time = times[0] if times else "无"
                            last_time = times[-1] if times else "无"
                            raise ValueError(
                                f"{direction_name} {dist} km 采样点没有接近事件时刻的预报数据。\n"
                                f"目标时刻：{event_utc:%Y-%m-%d %H:%M} UTC；"
                                f"接口时间范围：{first_time} 至 {last_time}；"
                                f"最近数据相差 {gap_seconds / 3600:.1f} 小时。\n"
                                f"本版已请求过去 36 小时数据；如果仍出现此提示，请检查所选日期、系统时间和接口返回的时区/时间范围。"
                            )
                        record = {"lat": subcoord[0], "lon": subcoord[1], "time": times[idx],
                                  "cross_track_offset_km": cross_offsets[rec_idx]}
                        for key in cloud_keys:
                            series = hourly.get(key, [])
                            val = series[idx] if idx < len(series) else None
                            record[key] = val if isinstance(val, (int, float)) and math.isfinite(val) else None
                        dseries = hourly.get("wind_direction_10m", [])
                        record["wind_direction_10m"] = dseries[idx] if idx < len(dseries) and isinstance(dseries[idx], (int, float)) else None
                        records.append(record)
                    if not records:
                        raise ValueError(f"{direction_name} {dist} km 没有可用小时预报。")
                    def avg(k):
                        vals = [r[k] for r in records if r.get(k) is not None]
                        return sum(vals) / len(vals) if vals else None
                    dirs = [r["wind_direction_10m"] for r in records if r.get("wind_direction_10m") is not None]
                    wind_dir = math.degrees(math.atan2(sum(math.sin(math.radians(x)) for x in dirs),
                                                        sum(math.cos(math.radians(x)) for x in dirs))) % 360 if dirs else None
                    transverse_records = [r for r in records if r.get("cross_track_offset_km") is not None]
                    transverse_low = [r.get("cloud_cover_low") for r in transverse_records if r.get("cloud_cover_low") is not None]
                    center_low = records[0].get("cloud_cover_low") if records else None
                    sample = {"distance_km": dist, "lat": coord[0], "lon": coord[1], "time": records[0]["time"],
                              "cloud_cover": avg("cloud_cover"), "cloud_cover_low": avg("cloud_cover_low"),
                              "center_cloud_cover_low": center_low,
                              "cross_track_low_cover": transverse_low,
                              "cross_track_offsets_km": [r.get("cross_track_offset_km") for r in transverse_records],
                              "cross_track_low_ge70_count": sum(v >= 70 for v in transverse_low),
                              "cross_track_low_ge70_fraction": (sum(v >= 70 for v in transverse_low) / len(transverse_low)) if transverse_low else None,
                              "cloud_cover_mid": avg("cloud_cover_mid"), "cloud_cover_high": avg("cloud_cover_high"),
                              "visibility_m": avg("visibility"), "wind_ms": avg("wind_speed_10m"),
                              "precipitation_probability": avg("precipitation_probability"), "precipitation": avg("precipitation"),
                              "wind_direction": wind_dir, "wind_gusts": avg("wind_gusts_10m"),
                              "area_radius_km": 15, "area_sample_count": len(records)}
                    samples.append(sample)
                    raw_details.append({"distance_km": dist, "center": coord, "records": records, "mean": sample})

                # 彩虹位于反太阳方向：只额外查询 5 个稀疏点，失败不影响霞光主预报。
                self._set_main_progress(base_progress + 37, f"{direction_name}：估算反太阳方向彩虹条件……")
                try:
                    rainbow_result = self._estimate_rainbow_opportunity(
                        data["lat"], data["lon"], azimuth, event_utc, model_id, samples[0] if samples else {})
                except Exception as rainbow_exc:
                    rainbow_result = {"score": None, "label": "暂不可估", "distance_km": None,
                                      "precip_probability": None, "precipitation": None,
                                      "note": f"反太阳方向降水数据不可用：{rainbow_exc}"}

                self._set_main_progress(base_progress + 39, f"{direction_name}：细化气溶胶路径……")
                try:
                    aod_result = self._fetch_aod_path(coords, distances, event_utc)
                    aod_samples = aod_result["samples"]
                    aod_weighted = aod_result["weighted_aod"]
                    aod_time = aod_result.get("valid_time")
                except Exception as exc:
                    aod_samples = [{"distance_km": d, "aod": None} for d in distances]
                    aod_weighted, aod_time = None, None
                    aod_error = str(exc)
                else:
                    aod_error = ""
                if manual_aod is not None:
                    effective_aod = manual_aod
                else:
                    effective_aod = aod_weighted
                self.aod_path_values = aod_samples
                self.web_aod_value = aod_weighted
                self.web_aod_valid_time = aod_time
                self._set_main_progress(base_progress + 44, f"{direction_name}：细化云底剖面并计算评分……")
                try:
                    profile_result = self._fetch_cloud_base_profiles(coords, distances, event_utc, model_id,
                                                                          local_ground_elevation_m=data.get("elevation_m"))
                    profiles = profile_result["samples"]
                except Exception as profile_exc:
                    profiles = [{"distance_km": d, "estimated_base_km": None,
                                 "status": f"剖面获取失败：{profile_exc}", "levels": []}
                                for d in distances]
                elevation_filter_result = apply_high_elevation_nearby_low_cloud_filter(
                    samples, profiles, data.get("elevation_m", 0.0), radius_km=80.0)
                geometry = analyze_cloud_base_geometry(
                    samples, profiles, aod_samples, data["date"], data["utc_offset"], data["lat"], data["lon"],
                    cloud_base_mode, cloud_base_km, manual_aod=manual_aod,
                    weighted_aod=effective_aod, scale_height_km=0.5,
                    event_type="sunrise" if direction_key == "morning" else "sunset",
                    ground_elevation_m=data.get("elevation_m", 0.0))
                local_profile = next((p for p in profiles if abs(float(p.get("distance_km", 999999))) < 0.01), None)
                local_low_above_ground = has_local_low_cloud_above_ground(local_profile.get("levels") if local_profile else None)
                inferred = infer_from_cloud_path(samples, effective_aod,
                                                 local_low_cloud_above_ground=local_low_above_ground)
                farthest = geometry["farthest"]
                local_geometry = next((r for r in geometry.get("rows", []) if r.get("distance_km") == 0), None)
                local_reach = local_geometry.get("reach_km") if local_geometry else None
                if not isinstance(local_reach, (int, float)) or local_reach <= 0:
                    local_reach = max_reach if max_reach > 0 else None
                boundaries = estimate_path_cloud_boundaries(samples, local_reach)
                inferred = apply_path_score_pair(inferred, geometry, boundaries)
                inferred = apply_low_cloud_screen_constraint(inferred, samples=samples, source="online_path",
                                                             max_relevant_distance_km=max_reach,
                                                             local_low_cloud_above_ground=local_low_above_ground)
                inferred = apply_peak_cloud_color(inferred, boundaries, samples)
                if elevation_filter_result.get("applied"):
                    inferred.setdefault("score_notes", []).append(
                        "高海拔近场低云过滤：" + elevation_filter_result.get("reason", "已应用") +
                        f"；处理采样点 {elevation_filter_result.get('removed_points', 0)} 个。80 km外低云保留参与原有光路判断。")
                    inferred.setdefault("reasons", []).append(
                        "结果区低云量已按海拔过滤后的采样点重新加权；0–80 km内已确认位于机位以下的低云按0计，80 km外低云仍参与统计。")
                # 不再把“最远中高云采样点”作为独立结论展示，避免与“云边距”混淆。
                cloud_edge_text = '未估算' if boundaries['cloud_edge_distance_km'] is None else f"约 {boundaries['cloud_edge_distance_km']:.0f} km"
                fire_range_text = '未识别' if boundaries['fire_cloud_range_km'] is None else f"约 {boundaries['fire_cloud_range_km']} km"
                all_gaps = boundaries.get('clear_gaps') or []
                gap_text = ('未形成可判读的晴空间隔' if not all_gaps else
                            '；'.join(f"{g['start_km']:.0f}–{g['end_km']:.0f} km（{g['size_km']:.0f} km）" for g in all_gaps))
                cap_note = "已触及 600 km 采样上限；若理论深入距离更远，需进一步提高上限或采用更稀疏的远距离采样。" if sample_limit >= 600 and max_reach > 600 else ""
                if rainbow_result.get("score") is None:
                    rainbow_summary = "彩虹出现机会：暂不可估"
                else:
                    rainbow_summary = f"彩虹出现机会：{rainbow_result['label']}（指数 {rainbow_result['score']}/100）"
                    if rainbow_result.get("distance_km") is not None:
                        rainbow_summary += f"；反太阳方向约 {rainbow_result['distance_km']:.0f} km 处降水条件最突出"
                # 主结果区只保留拍摄决策所需的摘要；完整解释和逐点数据仍在“详细采样报告”中。
                aod_short = ('手动 ' + format(effective_aod, '.2f') if manual_aod is not None else
                             ('—' if effective_aod is None else format(effective_aod, '.2f')))
                base_short = ('—' if local_geometry is None or local_geometry.get('cloud_base_km') is None else
                              format(local_geometry.get('cloud_base_km'), '.2f') + ' km')
                base_source_raw = local_geometry.get('base_source', '未知来源') if local_geometry else '未知来源'
                if '剖面推算' in base_source_raw:
                    base_source_short = '推算'
                elif '自动失败' in base_source_raw:
                    base_source_short = '默认值'
                elif '手动' in base_source_raw:
                    base_source_short = '手动值'
                else:
                    base_source_short = '默认值' if cloud_base_mode == '自动' else base_source_raw
                # “可染色云量占比”是启发式估算，不是气象模式直接输出的观测量。
                # 先估算本地总云量中中/高云所占比例，再根据太阳方向云路是否存在近距离晴空缺口、
                # 以及本地连续云区是否延伸很远进行折减。晴空缺口越远/路径云层越连续，估算占比越低。
                local_total = inferred.get("total")
                local_mid = inferred.get("mid")
                local_high = inferred.get("high")
                target_cloud = max([v for v in (local_mid, local_high) if isinstance(v, (int, float))], default=None)
                all_gaps = boundaries.get("clear_gaps") or []
                gap_start = min((float(g.get("start_km", 0)) for g in all_gaps), default=boundaries.get("clear_gap_start_km"))
                gap_count = len(all_gaps)
                total_gap_size = sum(float(g.get("size_km", 0) or 0) for g in all_gaps)
                local_cloud_extent = boundaries.get("fire_cloud_range_km")
                tintable_ratio = None
                if isinstance(local_total, (int, float)) and local_total > 0 and target_cloud is not None:
                    base_ratio = max(0.0, min(1.0, target_cloud / local_total))
                    if isinstance(gap_start, (int, float)):
                        # 近端云洞有利于金色表现；多个云洞的总宽度和数量也会增加潜在可染色机会。
                        gap_factor = max(0.15, min(1.0, 1.0 - max(0.0, gap_start - 30.0) / 220.0))
                        reach_for_ratio = boundaries.get("local_cloud_reach_km") or boundaries.get("cloud_edge_distance_km")
                        if isinstance(reach_for_ratio, (int, float)) and reach_for_ratio > 0:
                            gap_factor = min(1.0, gap_factor + 0.18 * min(1.0, total_gap_size / reach_for_ratio) + 0.04 * min(3, max(0, gap_count-1)))
                    else:
                        # 未识别到晴空间隔时，连续云路越长，越保守；数据不足也不直接判为 0。
                        extent = float(local_cloud_extent) if isinstance(local_cloud_extent, (int, float)) else 100.0
                        gap_factor = max(0.15, min(0.65, 0.65 - max(0.0, extent - 30.0) / 300.0))
                    tintable_ratio = max(0, min(100, round(base_ratio * gap_factor * 100)))
                    tintable_text = f"约 {tintable_ratio}%估算"
                elif isinstance(local_total, (int, float)) and local_total <= 0:
                    tintable_ratio = 0
                    tintable_text = "0%估算（本地无云）"
                else:
                    # 可染色云量占比无法估算时按 0 计，不再因此阻断烧云规模判读。
                    tintable_ratio = 0
                    tintable_text = "0%估算（数据不足，按0计）"
                # 烧云规模单独使用本地 200 km 采样；不改动路径算法的 15 km 采样与概率评分。
                local_mid_scale = local_scale_clouds.get("mid")
                local_high_scale = local_scale_clouds.get("high")
                local_low_scale = local_scale_clouds.get("low")
                burn_scale_index = None
                burn_scale_label = "暂不可判定"
                if all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in (local_mid_scale, local_high_scale, local_low_scale)):
                    local_scale_cloud_sum = float(local_low_scale) + float(local_mid_scale) + float(local_high_scale)
                    ratio_for_scale = float(tintable_ratio) if isinstance(tintable_ratio, (int, float)) and math.isfinite(float(tintable_ratio)) else 0.0
                    burn_scale_index = max(0.0, max(float(local_mid_scale), float(local_high_scale)) - float(local_low_scale)) * max(0.0, min(100.0, ratio_for_scale)) / 100.0
                    if local_scale_cloud_sum < 10:
                        burn_scale_label = "光板天"
                    elif burn_scale_index <= 0:
                        burn_scale_label = "几乎不烧"
                    elif burn_scale_index < 10:
                        burn_scale_label = "小小烧"
                    elif burn_scale_index < 30:
                        burn_scale_label = "微烧"
                    elif burn_scale_index < 50:
                        burn_scale_label = "小烧"
                    elif burn_scale_index < 70:
                        burn_scale_label = "中烧"
                    else:
                        burn_scale_label = "大烧"
                inferred["local_scale_clouds"] = local_scale_clouds
                inferred["burn_scale_index"] = burn_scale_index
                inferred["burn_scale_label"] = burn_scale_label
                inferred["tintable_ratio"] = tintable_ratio
                summary = (
                    f"{model_time_text}\n"
                    f"{direction_name} · {data['place']} · {data['date'].isoformat()}　{event_dt.strftime('%H:%M')} / 方位 {azimuth:.0f}°\n"
                    f"{model_label}　|　云量 总 {self._fmt_pct(inferred['total'])} · 低 {self._fmt_pct(inferred['low'])} · 中 {self._fmt_pct(inferred['mid'])} · 高 {self._fmt_pct(inferred['high'])} · 可染色云量占比 {tintable_text}\n"
                    f"潜在霞云距 {fire_range_text}　|　极限云距 {cloud_edge_text}　|　晴空间隔 {gap_text}\n"
                    f"云底 {base_short}（{base_source_short}）　|　AOD {aod_short}"
                )
                detail_lines = [
                    f"{direction_name}详细采样报告", f"地点：{data['place']}；日期：{data['date']}；模式：{model_label}；事件时刻：{event_dt}；方位角：{azimuth:.2f}°",
                    f"火烧云发生概率估计：{inferred['regular_score']}%（经验估计，尚未经历史样本校准）",
                    f"有利情景上限：{inferred['upper_score']}%（表示条件理想时的潜在机会，不代表实际发生概率）",
                    f"色彩表现评分：{inferred.get('color_score', '—')}/100（{inferred.get('color_score_label', '待估')}；{inferred.get('color_score_note', '经验指数')}）",
                    f"可染色云量占比：{tintable_text}。该值是结合本地中高云比例、太阳方向晴空间隔距离和连续云区延伸距离的启发式估算，不是模式直接输出的真实受光比例。",
                    model_time_text,
                    f"潜在霞云距（本地连续中高云末端）：{fire_range_text}；极限云距（几何上限）：{cloud_edge_text}；晴空间隔：{gap_text}",
                    (f"彩虹出现机会：暂不可估；{rainbow_result.get('note', '反太阳方向降水条件数据不可用。')}" if rainbow_result.get("score") is None else
                     f"彩虹出现机会：{rainbow_result['label']}（经验指数 {rainbow_result['score']}/100，非统计概率）；反太阳方向采样点 {rainbow_result['distance_km']:.0f} km，降水概率 {self._fmt_pct(rainbow_result.get('precip_probability'))}，小时降水 {('—' if rainbow_result.get('precipitation') is None else format(rainbow_result['precipitation'], '.2f')+' mm/h')}"),
                    f"采样动态扩展：初始 0–200 km；粗采样最大理论距离 {max_reach:.1f} km；按约 100 km 缓冲扩展，最终采样上限 {distances[-1]} km。",
                    "距离 | 中心坐标 | 总云/低云/中云/高云 | 能见度 | 风速 | 云底绝对海拔 | 有效云底绝对海拔 | 理论深入距离 | 几何筛选",
                ]
                prof_map = {x["distance_km"]: x for x in profiles}
                geo_map = {x["distance_km"]: x for x in geometry["rows"]}
                aod_map_final = {x["distance_km"]: x.get("aod") for x in aod_samples}
                for sm in samples:
                    pr, gr = prof_map.get(sm["distance_km"], {}), geo_map.get(sm["distance_km"], {})
                    cb = gr.get("cloud_base_km")
                    detail_lines.append(
                        f"{sm['distance_km']:>3} km | {sm['lat']:.3f},{sm['lon']:.3f} | "
                        f"{self._fmt_pct(sm.get('cloud_cover'))}/{self._fmt_pct(sm.get('cloud_cover_low'))}/"
                        f"{self._fmt_pct(sm.get('cloud_cover_mid'))}/{self._fmt_pct(sm.get('cloud_cover_high'))} | "
                        f"{('—' if sm.get('visibility_m') is None else format(sm['visibility_m']/1000,'.1f')+' km')} | "
                        f"{('—' if sm.get('wind_ms') is None else format(sm['wind_ms'],'.1f')+' m/s')} | "
                        f"{('—' if cb is None else format(cb,'.2f')+' km')} | {gr.get('effective_base_km',0):.2f} km | "
                        f"{gr.get('reach_km',0):.0f} km | {'通过' if gr.get('eligible') else '未通过'}"
                    )
                detail_lines.extend(["", "【各采样点 15 km 半径内的原始空间采样】"])
                for raw in raw_details:
                    detail_lines.append(f"\n距离 {raw['distance_km']} km（中心 {raw['center'][0]:.4f},{raw['center'][1]:.4f}）")
                    for i, rec in enumerate(raw["records"], 1):
                        detail_lines.append(
                            f"  点{i} {rec['lat']:.4f},{rec['lon']:.4f} | 总/低/中/高云 "
                            f"{self._fmt_pct(rec.get('cloud_cover'))}/{self._fmt_pct(rec.get('cloud_cover_low'))}/"
                            f"{self._fmt_pct(rec.get('cloud_cover_mid'))}/{self._fmt_pct(rec.get('cloud_cover_high'))} | "
                            f"能见度 {('—' if rec.get('visibility') is None else format(rec['visibility']/1000,'.1f')+' km')} | "
                            f"风速 {('—' if rec.get('wind_speed_10m') is None else format(rec['wind_speed_10m'],'.1f')+' m/s')}"
                        )
                detail_lines.extend(["", "【双评分说明】", *["• "+r for r in inferred["reasons"]],
                                     "常规概率为基于当前预报证据的经验估计；上限概率是假设有利云层形态/云隙恰好形成时的潜在机会估计。两者尚未经历史实况校准。",
                                     "边界定义：以中云或高云任一云量≥25%标记中高云采样点；潜在霞云距取本地连续中高云区沿太阳方向的末端。极限云距仅由本地有效云底按 D=2√(2Rh_eff) 计算，不再被远端云起点缩短。晴空间隔列出极限云距以内识别到的全部有效云洞，括号内为各云洞宽度；若最后一个云洞延伸至理论上限，则终点截断到极限云距。云洞边界为离散采样估算，不是连续云缘。",
                                     "重要限制：当前边界沿观测点至太阳方位的单线采样估算；地图视图使用现有每个距离点周边约15 km采样点，属于稀疏空间示意，不等于卫星云图或连续三维云体。要确认真实云洞边界，需要更密集二维采样与云顶/云底信息。",
                                     "彩虹原理依据教程第 2.3.2 节：一次内反射彩虹的视角约 42°，位于反太阳方向；观察者不一定要在本地下雨。当前仅用反太阳方向 0/25/50/100/150 km 的模式降水概率、小时降水量和本地总云量估算机会指数，无法识别雨滴直径、液态相态、云幡结构或实际可见度，因此指数不是统计概率。",
                                     "说明：剖面云底为气压层云量/RH启发式推算，不是实测。云底采用最低可能云层，不保证就是最易着色的云层。",
                                     "几何距离按 D=2√(2Rh_eff) 计算；实际照明时间使用简化太阳高度角模型。远距离采样间距较疏，采样点之间的云缘位置无法精确确定。"])
                # 用现有沿线云量采样构造动态曲线输入，不增加任何天气 API 请求。
                # 自动预报只有低/中/高云量层，没有精确云底云顶，因此使用宽高度带作为明确标注的近似。
                curve_zones = []
                ordered_curve_samples = sorted(samples, key=lambda q: q.get("distance_km", 0))
                local_curve_reach = None
                if local_geometry and isinstance(local_geometry.get("reach_km"), (int, float)) and local_geometry.get("reach_km") > 0:
                    local_curve_reach = float(local_geometry["reach_km"])
                for si, sm_curve in enumerate(ordered_curve_samples):
                    dist_c = float(sm_curve.get("distance_km", 0))
                    if local_curve_reach is not None and dist_c > local_curve_reach:
                        continue
                    prev_d = float(ordered_curve_samples[si-1].get("distance_km", dist_c)) if si > 0 else dist_c
                    next_d = float(ordered_curve_samples[si+1].get("distance_km", dist_c)) if si+1 < len(ordered_curve_samples) else dist_c
                    left_d = max(0.0, (prev_d + dist_c) / 2.0) if si > 0 else 0.0
                    right_d = (dist_c + next_d) / 2.0 if si+1 < len(ordered_curve_samples) else dist_c + max(5.0, (dist_c-prev_d)/2.0 if si > 0 else 12.5)
                    if local_curve_reach is not None:
                        left_d = min(left_d, local_curve_reach)
                        right_d = min(right_d, local_curve_reach)
                    if right_d <= left_d:
                        right_d = left_d + 5.0
                    for cloud_key, height_band in (("cloud_cover_low", (0.0, 2000.0)),
                                                   ("cloud_cover_mid", (2000.0, 6000.0)),
                                                   ("cloud_cover_high", (6000.0, 12000.0))):
                        cloud_val = sm_curve.get(cloud_key)
                        if isinstance(cloud_val, (int, float)) and math.isfinite(cloud_val) and cloud_val > 0:
                            curve_zones.append({"start": left_d, "end": right_d, "cover": float(cloud_val),
                                                "layers": [height_band], "row": si+1})
                visibility_values = [sm.get("visibility_m")/1000.0 for sm in samples
                                     if isinstance(sm.get("visibility_m"), (int, float)) and sm.get("visibility_m") > 0]
                curve_data = {"direction": direction_key, "date": data["date"], "lat": data["lat"], "lon": data["lon"],
                              "utc_offset": data["utc_offset"], "aod": effective_aod,
                              "visibility_km": (sum(visibility_values)/len(visibility_values) if visibility_values else None)}
                dynamic_curve = self._build_dynamic_light_curve(curve_data, curve_zones, (event_dt, azimuth), single_mode=True) if curve_zones else {"samples": [], "peak": None, "note": "缺少可用云量采样，未生成动态曲线。"}
                # 峰值云色与动态曲线峰值时刻对齐：使用所有有效距离段共同计算出的峰值时刻色彩，
                # 避免 GUI 色块只由第一个云洞的距离规则决定；色相仍属于示意估算。
                dynamic_peak = dynamic_curve.get("peak")
                if dynamic_peak and isinstance(dynamic_peak.get("color"), (tuple, list)) and len(dynamic_peak["color"]) == 3:
                    inferred["color_rgb"] = tuple(int(max(0, min(255, c))) for c in dynamic_peak["color"])
                    inferred["peak_cloud_color_rule"] = "动态曲线峰值时刻的综合受光色（所有有效距离段加权；色相为示意估算）"
                    inferred["color_name"] = inferred["peak_cloud_color_rule"]
                results[direction_key] = {"summary": summary, "details": "\n".join(detail_lines), "inferred": inferred,
                                          "samples": samples, "raw_details": raw_details, "profiles": profiles, "geometry": geometry,
                                          "distances": distances, "max_reach": max_reach, "aod": effective_aod,
                                          "rainbow": rainbow_result, "dynamic_light_curve": dynamic_curve,
                                          "origin": (data["lat"], data["lon"]), "azimuth": azimuth, "event_dt": event_dt,
                                          "boundaries": boundaries, "local_scale_clouds": local_scale_clouds,
                                          # 保存本次实际请求返回的原始天气响应，供案例复盘；不额外发起请求。
                                          "raw_weather": raw_locations}

            self._set_main_progress(100, "朝霞与晚霞预报完成")
            merged_results = dict(getattr(self, "direction_results", {}) or {})
            merged_results.update(results)
            self.direction_results = merged_results
            self.last_fetch_time = fetched_at
            self.last_report = "\n\n".join(self.direction_results[k]["details"] for k in ("morning", "evening") if k in self.direction_results)
            if "morning" in results:
                self._set_text(self.morning_summary, results["morning"].get("summary", "朝霞暂无结果"))
            if "evening" in results:
                self._set_text(self.evening_summary, results["evening"].get("summary", "晚霞暂无结果"))
            for direction_key, prefix in (("morning", "morning"), ("evening", "evening")):
                if direction_key not in results:
                    continue
                result = results.get(direction_key, {})
                inferred_result = result.get("inferred", {})
                regular_score = inferred_result.get("regular_score", inferred_result.get("score", "—"))
                upper_score = inferred_result.get("upper_score", "—")
                getattr(self, f"{prefix}_score_label").configure(text=f"{regular_score}%" if regular_score != "—" else "—")
                getattr(self, f"{prefix}_upper_label").configure(text=f"上限概率 {upper_score}%")
                color_score = inferred_result.get("color_score")
                color_label = inferred_result.get("color_score_label", "色彩待估")
                getattr(self, f"{prefix}_color_score_label").configure(
                    text=(f"色彩 {color_score}/100\n{color_label}" if color_score is not None else "色彩 —/100"))
                rainbow = result.get("rainbow", {})
                getattr(self, f"{prefix}_rainbow_label").configure(
                    text=self._rainbow_indicator_text(rainbow, result.get("event_dt"), direction_key))
            for _draw_key in results:
                self._draw_peak_color_swatch(_draw_key)
                self._draw_colorbar(_draw_key)
            # 分别显示每个方向本次采用的 AOD 来源，不覆盖用户手动输入。
            for direction_key, status_var in (("morning", self.morning_aod_status_var), ("evening", self.evening_aod_status_var)):
                if direction_key not in results:
                    continue
                direction_result = results.get(direction_key, {})
                direction_aod = direction_result.get("aod")
                manual_text = self.vars[f"{direction_key}_aod"].get().strip()
                if manual_text:
                    status_var.set(f"采用手动 AOD={float(manual_text):.3f}")
                elif direction_aod is not None:
                    status_var.set(f"采用该方向 CAMS AOD={direction_aod:.3f}")
                else:
                    status_var.set("该方向 CAMS AOD 不可用；未做 AOD 修正")
            done_name = "朝霞" if requested_direction == "morning" else "晚霞" if requested_direction == "evening" else "朝霞与晚霞"
            messagebox.showinfo("查询完成", f"已完成 {model_label} {done_name}预报。")
        except Exception as exc:
            self._set_main_progress(0, "查询失败；请查看错误提示")
            messagebox.showerror("获取失败", f"无法完成朝霞/晚霞预报：\n{exc}")

    @staticmethod
    def _rainbow_indicator_text(rainbow, event_dt=None, direction_key="evening"):
        """将彩虹经验指数压缩成评分下方的提示；不把经验指数冒充真实发生概率。"""
        score = rainbow.get("score") if isinstance(rainbow, dict) else None
        if score is None:
            return "彩虹：暂不可估"
        try:
            score = int(round(float(score)))
        except (TypeError, ValueError):
            return "彩虹：暂不可估"
        # 中等及以上才在主评分区突出显示，减少低信号干扰。
        if score < 45:
            return f"彩虹机会偏低\n指数 {score}/100\n未达到提示阈值"
        label = rainbow.get("label", "中等")
        time_text = "日出/日落前后约 1 小时"
        if isinstance(event_dt, str):
            try:
                event_dt = datetime.fromisoformat(event_dt)
            except ValueError:
                event_dt = None
        if isinstance(event_dt, datetime):
            start = event_dt - timedelta(hours=1)
            end = event_dt + timedelta(hours=1)
            time_text = f"{start:%H:%M}–{end:%H:%M}"
        precip_prob = rainbow.get("precip_probability")
        precip_text = f"降水 {float(precip_prob):.0f}%" if isinstance(precip_prob, (int, float)) else "降水概率缺测"
        return f"🌈 彩虹机会{label}\n指数 {score}/100\n{time_text}\n{precip_text}"

    @staticmethod
    def _estimate_rainbow_opportunity(lat, lon, sun_azimuth, event_utc, model_id, local_sample=None):
        """用反太阳方向稀疏降水采样估算彩虹机会指数；不是校准后的发生概率。"""
        distances = (0, 25, 50, 100, 150)
        anti_azimuth = (float(sun_azimuth) + 180.0) % 360.0
        coords = [destination_point(lat, lon, anti_azimuth, d) for d in distances]
        locations = FireCloudApp._fetch_weather_locations(coords, model_id)
        candidates = []
        for profile_index, (dist, coord, loc) in enumerate(zip(distances, coords, locations)):
            hourly = loc.get("hourly", {})
            times = hourly.get("time", [])
            if not times:
                continue
            idx = min(range(len(times)), key=lambda i: abs((datetime.fromisoformat(times[i]) - event_utc).total_seconds()))
            if abs((datetime.fromisoformat(times[idx]) - event_utc).total_seconds()) > 5400:
                continue
            def value(key):
                arr = hourly.get(key, [])
                try:
                    v = arr[idx]
                    return float(v) if v is not None and math.isfinite(float(v)) else None
                except (IndexError, TypeError, ValueError):
                    return None
            prob, amount, cloud = value("precipitation_probability"), value("precipitation"), value("cloud_cover")
            if prob is None and amount is None:
                continue
            candidates.append({"distance_km": dist, "lat": coord[0], "lon": coord[1],
                               "precip_probability": prob, "precipitation": amount, "cloud_cover": cloud})
        if not candidates:
            return {"score": None, "label": "暂不可估", "distance_km": None,
                    "precip_probability": None, "precipitation": None,
                    "note": "反太阳方向没有接近事件时刻的有效降水数据。"}
        def rain_signal(row):
            prob = row["precip_probability"] or 0.0
            amount = row["precipitation"] or 0.0
            amount_signal = min(100.0, amount / 0.8 * 100.0) if amount > 0 else 0.0
            return max(prob, amount_signal * 0.75)
        best = max(candidates, key=rain_signal)
        raw = rain_signal(best)
        distance_factor = {0: 1.0, 25: 1.0, 50: 0.95, 100: 0.85, 150: 0.75}.get(best["distance_km"], 0.75)
        local_cloud = (local_sample or {}).get("cloud_cover")
        solar_visibility_factor = 1.0 if local_cloud is None else max(0.45, 1.0 - max(0.0, local_cloud - 45.0) * 0.011)
        score = int(round(max(0.0, min(100.0, raw * distance_factor * solar_visibility_factor))))
        if score < 20:
            label = "较低"
        elif score < 45:
            label = "偏低"
        elif score < 70:
            label = "中等"
        else:
            label = "较高"
        return {"score": score, "label": label, "distance_km": best["distance_km"],
                "precip_probability": best["precip_probability"], "precipitation": best["precipitation"],
                "cloud_cover": best["cloud_cover"], "anti_solar_azimuth": anti_azimuth,
                "note": "经验指数，未校准为真实发生概率。"}

    @staticmethod
    def _fetch_weather_locations(coords, model_id):
        """按批次查询多个坐标，避免动态扩展后 URL/坐标数量过大。"""
        all_locations = []
        batch_size = 70
        for start in range(0, len(coords), batch_size):
            batch = coords[start:start + batch_size]
            params = {
                "latitude": ",".join(f"{x[0]:.5f}" for x in batch),
                "longitude": ",".join(f"{x[1]:.5f}" for x in batch),
                "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,precipitation_probability,precipitation,visibility,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
                "models": model_id, "timezone": "UTC", "forecast_days": 16, "past_hours": 36,
                "wind_speed_unit": "ms", "timeformat": "iso8601",
            }
            url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": "FireCloudForecast/1.4.7"})
            payload = request_json(req, timeout=20, cache_key=url)
            locations = payload if isinstance(payload, list) else [payload]
            if len(locations) != len(batch):
                raise ValueError(f"天气接口返回 {len(locations)} 个位置，预期 {len(batch)} 个。")
            all_locations.extend(locations)
        return all_locations


    # ---------------- 全国双模式地图 ----------------
    CHINA_CAPITALS = [
        ("北京", 39.9042, 116.4074), ("天津", 39.0842, 117.2010), ("石家庄", 38.0428, 114.5149),
        ("太原", 37.8706, 112.5489), ("呼和浩特", 40.8426, 111.7492), ("沈阳", 41.8057, 123.4315),
        ("长春", 43.8171, 125.3235), ("哈尔滨", 45.8038, 126.5350), ("上海", 31.2304, 121.4737),
        ("南京", 32.0603, 118.7969), ("杭州", 30.2741, 120.1551), ("合肥", 31.8206, 117.2272),
        ("福州", 26.0745, 119.2965), ("南昌", 28.6820, 115.8579), ("济南", 36.6512, 117.1201),
        ("郑州", 34.7466, 113.6254), ("武汉", 30.5928, 114.3055), ("长沙", 28.2282, 112.9388),
        ("广州", 23.1291, 113.2644), ("南宁", 22.8170, 108.3665), ("海口", 20.0440, 110.1999),
        ("重庆", 29.5630, 106.5516), ("成都", 30.5728, 104.0668), ("贵阳", 26.6470, 106.6302),
        ("昆明", 25.0389, 102.7183), ("拉萨", 29.6520, 91.1721), ("西安", 34.3416, 108.9398),
        ("兰州", 36.0611, 103.8343), ("西宁", 36.6171, 101.7782), ("银川", 38.4872, 106.2309),
        ("乌鲁木齐", 43.8256, 87.6168), ("台北", 25.0330, 121.5654),
    ]

    # 路径地图使用的常见地级市候选点；不联网查询地名，避免为了标注城市额外增加 API 访问。
    MAP_CITIES = CHINA_CAPITALS + [
        ("苏州",31.2989,120.5853),("无锡",31.4912,120.3119),("常州",31.8107,119.9737),
        ("南通",31.9802,120.8943),("扬州",32.3936,119.4127),("镇江",32.1878,119.4250),
        ("宁波",29.8683,121.5440),("温州",27.9943,120.6994),("金华",29.0792,119.6474),
        ("台州",28.6564,121.4208),("绍兴",30.0303,120.5802),("嘉兴",30.7522,120.7509),
        ("芜湖",31.3525,118.4331),("安庆",30.5435,117.0638),("黄山",29.7152,118.3387),
        ("阜阳",32.8896,115.8142),("蚌埠",32.9168,117.3897),("六安",31.7357,116.5197),
        ("宿州",33.6461,116.9639),("徐州",34.2044,117.2841),("连云港",34.5967,119.2229),
        ("盐城",33.3474,120.1636),("泰州",32.4558,119.9231),("淮安",33.6104,119.0153),
        ("洛阳",34.6197,112.4540),("开封",34.7972,114.3076),("南阳",32.9908,112.5283),
        ("襄阳",32.0090,112.1224),("宜昌",30.6919,111.2865),("荆州",30.3348,112.2407),
        ("岳阳",29.3571,113.1289),("衡阳",26.8932,112.5719),("郴州",25.7706,113.0149),
        ("赣州",25.8311,114.9348),("九江",29.7051,116.0019),("上饶",28.4546,117.9434),
        ("泉州",24.8741,118.6757),("厦门",24.4798,118.0894),("漳州",24.5129,117.6471),
        ("汕头",23.3541,116.6819),("深圳",22.5431,114.0579),("珠海",22.2707,113.5767),
        ("佛山",23.0218,113.1219),("东莞",23.0207,113.7518),("惠州",23.1115,114.4152),
        ("桂林",25.2736,110.2900),("柳州",24.3255,109.4155),("三亚",18.2528,109.5119),
        ("绵阳",31.4675,104.6796),("乐山",29.5521,103.7656),("宜宾",28.7518,104.6417),
        ("泸州",28.8718,105.4423),("大理",25.6065,100.2676),("遵义",27.7257,106.9274),
        ("毕节",27.3017,105.2863),("大同",40.0768,113.3001),("临汾",36.0880,111.5189),
        ("包头",40.6574,109.8403),("榆林",38.2854,109.7346),("延安",36.5853,109.4897),
        ("天水",34.5809,105.7249),("张掖",38.9259,100.4498),("敦煌",40.1421,94.6619),
        ("喀什",39.4704,75.9898),("伊宁",43.9222,81.3163),("桂平",23.3943,110.0810),
        ("牡丹江",44.5517,129.6332),("齐齐哈尔",47.3543,123.9182),("吉林",43.8379,126.5496),
        ("锦州",41.0951,121.1270),("秦皇岛",39.9354,119.5996),("唐山",39.6305,118.1802),
        ("潍坊",36.7069,119.1618),("烟台",37.4638,121.4479),("青岛",36.0671,120.3826),
        ("临沂",35.1047,118.3564),("济宁",35.4154,116.5871),("菏泽",35.2338,115.4807),
        ("大连",38.9140,121.6147),("鞍山",41.1087,122.9946),("延吉",42.9068,129.5158),
    ]

    @staticmethod
    def _point_in_ring(lon, lat, ring):
        inside = False
        j = len(ring) - 1
        for i, pt in enumerate(ring):
            xi, yi = pt[0], pt[1]
            xj, yj = ring[j][0], ring[j][1]
            if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
                inside = not inside
            j = i
        return inside

    @classmethod
    def _geojson_polygons(cls, geojson):
        polygons = []
        def add_geometry(geom):
            if not geom:
                return
            typ, coords = geom.get("type"), geom.get("coordinates", [])
            if typ == "Polygon":
                if coords:
                    polygons.append(coords)
            elif typ == "MultiPolygon":
                polygons.extend(coords)
        if geojson.get("type") == "FeatureCollection":
            for feat in geojson.get("features", []):
                add_geometry(feat.get("geometry"))
        elif geojson.get("type") == "Feature":
            add_geometry(geojson.get("geometry"))
        else:
            add_geometry(geojson)
        return polygons

    @classmethod
    def _point_in_china(cls, lon, lat, polygons):
        # GeoJSON polygon ring 0 is outer boundary; later rings are holes.
        for poly in polygons:
            if poly and cls._point_in_ring(lon, lat, poly[0]):
                if not any(cls._point_in_ring(lon, lat, hole) for hole in poly[1:]):
                    return True
        return False

    @staticmethod
    def _national_score(hourly, idx):
        """全国初筛：返回保守常规分；vals 同时携带上限分供地图/卡片展示。"""
        def v(key):
            arr = hourly.get(key, [])
            try:
                x = arr[idx]
                return None if x is None else float(x)
            except (IndexError, TypeError, ValueError):
                return None
        total, low, mid, high = v("cloud_cover"), v("cloud_cover_low"), v("cloud_cover_mid"), v("cloud_cover_high")
        precip = v("precipitation_probability")
        vals = {"total": total, "low": low, "mid": mid, "high": high, "precip": precip}
        if all(x is None for x in (total, low, mid, high)):
            vals.update({"upper_score": None})
            return None, vals
        t = 0 if total is None else total
        l = 0 if low is None else low
        m = 0 if mid is None else mid
        h = 0 if high is None else high
        rain = 0 if precip is None else precip
        target = min(100.0, 0.55*m + 0.75*h)
        useful = min(1.0, target / 65.0)
        coverage = max(0.0, 1.0 - abs(t - 55.0) / 100.0)
        # 常规分：对低云、极端满云、降水和缺测采取偏保守处理。
        regular = 100.0 * (0.62 * useful + 0.38 * coverage)
        regular *= (1 - min(0.55, l / 100.0 * 0.55))
        regular *= (1 - min(0.30, rain / 100.0 * 0.30))
        if total is None or low is None or (mid is None and high is None):
            regular -= 8
        if total is not None and total >= 95 and low is not None and low >= 65:
            regular -= 12
        # 上限分：放宽低云/降水/满云惩罚，但不忽略中高云不足。
        upper = 100.0 * (0.72 * useful + 0.28 * coverage)
        upper *= (1 - min(0.25, l / 100.0 * 0.25))
        upper *= (1 - min(0.12, rain / 100.0 * 0.12))
        if target < 15:
            upper = min(upper, 35)
        if mid is None and high is None:
            upper = min(upper, 55)
        regular = int(round(max(0, min(100, regular))))
        upper = int(round(max(regular, min(100, upper))))
        # 全国初筛没有路径宽度/云层高度带，不能假装已做光路追踪；但本地低云极高时
        # 仍使用统一的保守上限，避免此入口与主评分对同一低云条件给出相反结论。
        if low is not None and low >= 95:
            regular = min(regular, 18); upper = max(regular, min(upper, 30))
            vals["low_cloud_screen"] = "本地低云≥95%（无路径信息，按本地保守约束）"
        elif low is not None and low >= 85:
            regular = min(regular, 28); upper = max(regular, min(upper, 42))
            vals["low_cloud_screen"] = "本地低云≥85%（无路径信息，按本地保守约束）"
        vals.update({"upper_score": upper, "target_cloud": round(target, 1)})
        return regular, vals

    @staticmethod
    def _national_color(score):
        if score is None:
            return "#bfc5cc"
        stops = [(0, (220, 224, 230)), (20, (242, 229, 160)), (40, (247, 190, 83)),
                 (60, (244, 125, 53)), (80, (220, 62, 58)), (100, (139, 42, 112))]
        for i in range(len(stops) - 1):
            a, ca = stops[i]; b, cb = stops[i + 1]
            if score <= b:
                t = (score - a) / (b - a)
                rgb = tuple(round(ca[k] + (cb[k] - ca[k]) * t) for k in range(3))
                return "#%02x%02x%02x" % rgb
        return "#8b2a70"

    def open_national_map(self):
        try:
            target_date = datetime.strptime(self.vars["date"].get().strip(), "%Y-%m-%d").date()
        except ValueError:
            messagebox.showerror("日期错误", "请先把预报日期填写为 YYYY-MM-DD。")
            return
        delta_days = (target_date - date.today()).days
        if delta_days < 0 or delta_days > 15:
            messagebox.showwarning("日期超出预报范围", "全国地图使用 Open-Meteo 未来 16 天预报数据。请将日期设为今天至未来 15 天内。")
            return
        win = tk.Toplevel(self)
        win.title("全国火烧云潜力地图 · ECMWF / GFS × 朝霞 / 晚霞")
        win.geometry("1480x1000")
        win.minsize(1100, 760)
        outer = ttk.Frame(win, padding=10)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text=f"全国火烧云潜力地图　{target_date.isoformat()}", font=("Microsoft YaHei UI", 14, "bold")).pack(side="left")
        # 网格间距由用户在 GUI 中选择；更小的间距会显著增加天气 API 请求量。
        grid_control = ttk.Frame(outer)
        grid_control.pack(fill="x", pady=(6, 2))
        ttk.Label(grid_control, text="全国地图网格间距：").pack(side="left")
        grid_step_var = tk.StringVar(value="1.25°")
        grid_step_box = ttk.Combobox(
            grid_control, textvariable=grid_step_var,
            values=["0.50°", "0.75°", "1.00°", "1.25°", "1.50°", "2.00°", "2.50°", "3.00°"],
            state="readonly", width=9
        )
        grid_step_box.pack(side="left", padx=(0, 14))
        ttk.Label(grid_control, text="地图评分：").pack(side="left")
        score_mode_var = tk.StringVar(value="常规评分")
        score_mode_box = ttk.Combobox(grid_control, textvariable=score_mode_var,
                                      values=["常规概率", "上限概率"], state="readonly", width=9)
        score_mode_box.pack(side="left", padx=(0, 10))
        ttk.Label(
            grid_control,
            text="间距越小地图越细，但查询更慢、API 请求更多；建议先试 0.75°–1.25°。",
            wraplength=900
        ).pack(side="left", fill="x", expand=True)
        status = tk.StringVar(value="先选择网格间距，再点击‘开始查询并绘图’。")
        ttk.Label(outer, textvariable=status).pack(anchor="w", pady=(4, 3))
        progress = ttk.Progressbar(outer, mode="determinate", maximum=100, value=0)
        progress.pack(fill="x", pady=(0, 6))
        selected_grid_step = {"value": 1.25}
        start_map_button = ttk.Button(grid_control, text="开始查询并绘图", style="Accent.TButton")
        start_map_button.pack(side="right", padx=(10, 0))
        canvases_frame = ttk.Frame(outer)
        canvases_frame.pack(fill="both", expand=True)
        canvases = {}
        titles = [("ec_morning", "ECMWF · 朝霞"), ("ec_evening", "ECMWF · 晚霞"),
                  ("gfs_morning", "GFS · 朝霞"), ("gfs_evening", "GFS · 晚霞")]
        for i, (key, title) in enumerate(titles):
            panel = ttk.Frame(canvases_frame, padding=4)
            panel.grid(row=i // 2, column=i % 2, sticky="nsew", padx=4, pady=4)
            ttk.Label(panel, text=title, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
            canvas = tk.Canvas(panel, bg="#f7f9fb", highlightthickness=1, highlightbackground="#d8dee6", height=350)
            canvas.pack(fill="both", expand=True)
            canvases[key] = canvas
        canvases_frame.rowconfigure(0, weight=1); canvases_frame.rowconfigure(1, weight=1)
        canvases_frame.columnconfigure(0, weight=1); canvases_frame.columnconfigure(1, weight=1)
        legend = ttk.Frame(outer)
        legend.pack(fill="x", pady=(6, 0))
        leg_canvas = tk.Canvas(legend, height=42, bg="#eef2f5", highlightthickness=0)
        leg_canvas.pack(fill="x")
        ttk.Label(outer, text="颜色为 0–100 的全国初筛经验指数，不是火烧云发生概率；灰色表示数据缺失。省会点可查看该点分值与基础云况。", wraplength=1300).pack(anchor="w")

        def draw_legend():
            leg_canvas.delete("all")
            w = max(leg_canvas.winfo_width(), 600)
            x0, x1 = 40, w - 40
            n = max(1, int(x1 - x0))
            for i in range(n):
                score = 100 * i / n
                leg_canvas.create_line(x0 + i, 6, x0 + i, 24, fill=self._national_color(score))
            leg_canvas.create_text(x0, 34, text="0 · 较低", anchor="w", fill="#344054")
            leg_canvas.create_text((x0 + x1) / 2, 34, text="50 · 中等", anchor="center", fill="#344054")
            leg_canvas.create_text(x1, 34, text="100 · 较高", anchor="e", fill="#344054")
        leg_canvas.bind("<Configure>", lambda _e: draw_legend())

        map_render_data = {}
        def redraw_all_maps(_event=None):
            for key, args in list(map_render_data.items()):
                draw_map(*args)
        score_mode_box.bind("<<ComboboxSelected>>", redraw_all_maps)

        def draw_map(canvas, points, polygons, city_results, title):
            map_render_data[title] = (canvas, points, polygons, city_results, title)
            score_key = "upper_score" if score_mode_var.get() == "上限概率" else "score"
            canvas.delete("all")
            w, h = max(canvas.winfo_width(), 500), max(canvas.winfo_height(), 280)
            margin = 34
            lon_min, lon_max, lat_min, lat_max = 72.0, 135.5, 17.0, 54.5
            scale = min((w - 2 * margin) / (lon_max - lon_min), (h - 2 * margin) / (lat_max - lat_min))
            map_w, map_h = (lon_max - lon_min) * scale, (lat_max - lat_min) * scale
            ox, oy = (w - map_w) / 2, (h - map_h) / 2
            def xy(lon, lat): return ox + (lon - lon_min) * scale, oy + (lat_max - lat) * scale
            # 将稀疏规则网格双线性插值为一张细栅格图像，再一次性贴到 Canvas；
            # 插值只改善空间显示连续性，不会增加天气模型本身的信息量。
            grid_step = selected_grid_step["value"]
            regular = {}
            for p in points:
                value = p.get(score_key, p.get("score"))
                if value is None:
                    continue
                iy = round((p["lat"] - 18.0) / grid_step)
                ix = round((p["lon"] - 73.0) / grid_step)
                # 省会额外点不在规则网格上时不覆盖规则网格数据。
                if abs((18.0 + iy * grid_step) - p["lat"]) < 0.02 and abs((73.0 + ix * grid_step) - p["lon"]) < 0.02:
                    regular[(iy, ix)] = float(value)
            img_w = max(160, min(760, int(map_w)))
            img_h = max(100, min(460, int(map_h)))
            photo = tk.PhotoImage(master=canvas, width=img_w, height=img_h)
            def score_rgb(value):
                color = self._national_color(value)
                return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
            rgb_stops = [(0, (220,224,230)), (20,(242,229,160)), (40,(247,190,83)),
                         (60,(244,125,53)), (80,(220,62,58)), (100,(139,42,112))]
            def rgb_for_score(value):
                value = max(0.0, min(100.0, value))
                for j in range(len(rgb_stops)-1):
                    a, ca = rgb_stops[j]; b, cb = rgb_stops[j+1]
                    if value <= b:
                        t = (value-a)/(b-a)
                        return tuple(round(ca[k]+(cb[k]-ca[k])*t) for k in range(3))
                return rgb_stops[-1][1]
            for py in range(img_h):
                lat_v = lat_max - ((py + 0.5) / img_h) * (lat_max - lat_min)
                fy = (lat_v - 18.0) / grid_step
                iy = math.floor(fy); ty = fy - iy
                row_colors = []
                for px in range(img_w):
                    lon_v = lon_min + ((px + 0.5) / img_w) * (lon_max - lon_min)
                    fx = (lon_v - 73.0) / grid_step
                    ix = math.floor(fx); tx = fx - ix
                    vals = [regular.get((iy,ix)), regular.get((iy,ix+1)),
                            regular.get((iy+1,ix)), regular.get((iy+1,ix+1))]
                    if all(v is not None for v in vals):
                        a,b,c,d = vals
                        score_v = (a*(1-tx)*(1-ty) + b*tx*(1-ty) + c*(1-tx)*ty + d*tx*ty)
                        r,g,bv = rgb_for_score(score_v)
                        row_colors.append(f"#{r:02x}{g:02x}{bv:02x}")
                    else:
                        # 网格只在中国境内采样，国界/海岸附近经常缺少完整的四角，
                        # 这里仅在邻近 1.35 个网格间距内用最近有效格点补边，不做远距离外推。
                        nearest = None
                        nearest_d2 = 1.35 * 1.35
                        for jy in range(iy - 2, iy + 3):
                            for jx in range(ix - 2, ix + 3):
                                candidate = regular.get((jy, jx))
                                if candidate is None:
                                    continue
                                d2 = (fy - jy) ** 2 + (fx - jx) ** 2
                                if d2 < nearest_d2:
                                    nearest_d2 = d2
                                    nearest = candidate
                        if nearest is None:
                            row_colors.append("#f7f9fb")
                        else:
                            r,g,bv = rgb_for_score(nearest)
                            row_colors.append(f"#{r:02x}{g:02x}{bv:02x}")
                photo.put("{" + " ".join(row_colors) + "}", to=(0, py))
            canvas.create_image(ox, oy, image=photo, anchor="nw", tags=("heatmap",))
            canvas._heatmap_photo = photo
            # 叠加行政边界。省级 GeoJSON 轮廓若获取成功，会绘制每个省的边界。
            for poly in polygons:
                for ring in poly:
                    coords = []
                    for pt in ring:
                        lon, lat = pt[0], pt[1]
                        if lon_min - 2 <= lon <= lon_max + 2 and lat_min - 2 <= lat <= lat_max + 2:
                            px, py = xy(lon, lat); coords.extend([px, py])
                    if len(coords) >= 6:
                        canvas.create_line(*coords, fill="#536171", width=0.8, tags=("border",))
            # 省会点和标签，标签有轻微错位以减少重叠。
            for name, lat, lon in self.CHINA_CAPITALS:
                x, y = xy(lon, lat)
                res = city_results.get(name, {})
                score = res.get(score_key, res.get("score"))
                fill = self._national_color(score)
                canvas.create_oval(x-3.8, y-3.8, x+3.8, y+3.8, fill=fill, outline="#182230", width=1, tags=("city", name))
                canvas.create_text(x+5, y-5, text=name, anchor="sw", fill="#172b4d", font=("Microsoft YaHei UI", 7), tags=("citylabel", name))
                tip = (f"{name} · {title}\n常规概率：{'缺失' if res.get('score') is None else res.get('score')} / 100"
                       f"\n上限概率：{'缺失' if res.get('upper_score') is None else res.get('upper_score')} / 100"
                       f"\n总云 {self._pct(res.get('total'))} / 低云 {self._pct(res.get('low'))} / 中云 {self._pct(res.get('mid'))} / 高云 {self._pct(res.get('high'))}\n降水概率 {self._pct(res.get('precip'))}")
                canvas.tag_bind(name, "<Button-1>", lambda _e, text=tip: messagebox.showinfo("省会点详情", text, parent=win))
            canvas.create_text(w/2, h-8, text=f"中国区域示意投影 · {score_mode_var.get()}（经验指数，非概率）", fill="#667085", font=("Microsoft YaHei UI", 8))

        def worker():
            try:
                win.after(0, lambda: status.set("读取中国省级行政区边界……"))
                geo_url = "https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json"
                req = urllib.request.Request(geo_url, headers={"User-Agent": "FireCloudForecast/1.1.1"})
                with urllib.request.urlopen(req, timeout=25) as response:
                    geo = json.loads(response.read().decode("utf-8"))
                polygons = self._geojson_polygons(geo)
                if not polygons:
                    raise ValueError("中国行政区 GeoJSON 没有返回可用多边形。")
                # 使用 GUI 选择的网格间距；更小间距会增加点位数与 API 请求量。
                grid_step = selected_grid_step["value"]
                grid = []
                lat = 18.0
                while lat <= 53.0 + 1e-9:
                    lon = 73.0
                    while lon <= 134.5 + 1e-9:
                        if self._point_in_china(lon, lat, polygons):
                            grid.append((round(lat, 4), round(lon, 4)))
                        lon += grid_step
                    lat += grid_step
                cities = [(lat, lon) for _, lat, lon in self.CHINA_CAPITALS if self._point_in_china(lon, lat, polygons)]
                coords = list(dict.fromkeys(grid + cities))
                if len(coords) < 50:
                    raise ValueError("中国边界筛选后的网格点过少，请检查网络或行政区数据格式。")
                all_results = {}
                batch_count = max(1, math.ceil(len(coords) / 30))
                progress_state = {"done": 0, "total": batch_count * 2}
                def batch_progress(model_name, done_in_model, total_in_model):
                    progress_state["done"] += 1
                    done = progress_state["done"]
                    total = progress_state["total"]
                    pct = 100 * done / max(1, total)
                    win.after(0, lambda d=done, t=total, m=model_name, v=pct:
                              (progress.configure(value=v),
                               status.set(f"正在获取 {m} 天气数据……批次 {d}/{t}（缓存命中也会计入）")))
                    self._set_main_progress(pct, f"全国地图：{model_name} 数据批次 {done}/{total}")
                for model_label, model_id, prefix in [("ECMWF", "ecmwf_ifs025", "ec"), ("GFS", "ncep_gfs_global", "gfs")]:
                    win.after(0, lambda text=f"查询 {model_label} 天气数据（间距 {grid_step:g}°，共 {len(coords)} 个网格/城市点）……": status.set(text))
                    locs = self._fetch_national_weather(coords, model_id, progress_callback=lambda d, t, m=model_label: batch_progress(m, d, t))
                    for direction, event_name in [("morning", "sunrise"), ("evening", "sunset")]:
                        point_results, city_results = [], {}
                        for (lat, lon), loc in zip(coords, locs):
                            try:
                                event_dt, _az = (find_sunrise if event_name == "sunrise" else find_sunset)(target_date, 8.0, lat, lon)
                                if event_dt is None:
                                    score, vals = None, {}
                                else:
                                    event_utc = (event_dt - timedelta(hours=8)).replace(tzinfo=None)
                                    hourly = loc.get("hourly", {})
                                    times = hourly.get("time", [])
                                    if not times:
                                        score, vals = None, {}
                                    else:
                                        idx = min(range(len(times)), key=lambda i: abs((datetime.fromisoformat(times[i]) - event_utc).total_seconds()))
                                        if abs((datetime.fromisoformat(times[idx]) - event_utc).total_seconds()) > 5400:
                                            score, vals = None, {}
                                        else:
                                            score, vals = self._national_score(hourly, idx)
                                rec = {"lat": lat, "lon": lon, "score": score, **vals}
                                point_results.append(rec)
                                for name, clat, clon in self.CHINA_CAPITALS:
                                    if abs(lat-clat) < 0.0001 and abs(lon-clon) < 0.0001:
                                        city_results[name] = rec
                            except Exception:
                                point_results.append({"lat": lat, "lon": lon, "score": None})
                        all_results[f"{prefix}_{direction}"] = (point_results, city_results)
                def finish():
                    status.set(f"完成：间距 {grid_step:g}°，共 {len(coords)} 个网格/省会坐标 · {datetime.now().strftime('%H:%M:%S')} 更新。插值仅改善显示连续性。")
                    progress.configure(value=100)
                    self._set_main_progress(100, "全国地图绘制完成")
                    for key, title in titles:
                        pts, city_res = all_results[key]
                        draw_map(canvases[key], pts, polygons, city_res, title)
                    start_map_button.configure(state="normal")
                    win.update_idletasks()
                win.after(0, finish)
            except Exception as exc:
                def report_error(e=exc):
                    status.set("全国地图生成失败。可以稍后重试；相同采样数据成功获取后会在一小时内复用缓存。")
                    progress.configure(value=0)
                    self._set_main_progress(0, "全国地图任务失败")
                    start_map_button.configure(state="normal")
                    messagebox.showerror("全国地图失败", str(e), parent=win)
                win.after(0, report_error)

        def start_map_query():
            selected_grid_step["value"] = float(grid_step_var.get().replace("°", ""))
            start_map_button.configure(state="disabled")
            progress.configure(value=0)
            self._set_main_progress(0, "开始查询全国地图数据……")
            status.set(f"准备查询：网格间距 {selected_grid_step['value']:g}°……")
            import threading
            threading.Thread(target=worker, daemon=True).start()

        start_map_button.configure(command=start_map_query)

    def open_local_2day_forecast(self):
        """一键查询 GUI 当前坐标的未来四个日出/日落事件，ECMWF 与 GFS 共八项。"""
        try:
            lat = parse_float(self.vars["lat"].get(), "纬度")
            lon = parse_float(self.vars["lon"].get(), "经度")
            elevation_m = parse_float(self.vars["elevation"].get(), "海拔") if self.vars.get("elevation") and self.vars["elevation"].get().strip() else 0.0
            if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ValueError("纬度范围为 -90 到 90，经度范围为 -180 到 180。")
            local_now = datetime.now().astimezone()
            offset_td = local_now.utcoffset() or timedelta(0)
            utc_offset = offset_td.total_seconds() / 3600.0
            place = self.vars.get("place", tk.StringVar(value="本地坐标")).get().strip() or "本地坐标"
        except Exception as exc:
            messagebox.showerror("输入错误", str(exc), parent=self)
            return

        win = tk.Toplevel(self)
        win.title(f"{place} · 未来两日 EC/GFS 朝晚霞简报")
        win.geometry("1120x900")
        win.minsize(900, 720)
        outer = ttk.Frame(win, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=f"{place} · 本地坐标未来朝霞 / 晚霞", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        ttk.Label(outer, text=f"坐标 {lat:.4f}, {lon:.4f} · 系统本地时区 UTC{utc_offset:+g} · 按时间列出最近 4 个未来事件；每个事件分别比较 ECMWF 与 GFS。", wraplength=1060).pack(anchor="w", pady=(3, 8))
        status = tk.StringVar(value="准备获取 ECMWF 与 GFS 数据……")
        ttk.Label(outer, textvariable=status).pack(anchor="w", pady=(0, 3))
        progress = ttk.Progressbar(outer, mode="determinate", maximum=100, value=0, length=400)
        progress.pack(fill="x", pady=(0, 8))
        self._set_main_progress(0, "开始本地未来两日预报……")
        # 可滚动卡片区，避免云量/评分文字被固定行高挤出可视区域。
        body_shell = ttk.Frame(outer)
        body_shell.pack(fill="both", expand=True)
        body_canvas = tk.Canvas(body_shell, bg="#eef2f5", highlightthickness=0)
        body_scroll = ttk.Scrollbar(body_shell, orient="vertical", command=body_canvas.yview)
        body_canvas.configure(yscrollcommand=body_scroll.set)
        body_scroll.pack(side="right", fill="y")
        body_canvas.pack(side="left", fill="both", expand=True)
        cards = ttk.Frame(body_canvas)
        cards.columnconfigure(0, weight=1); cards.columnconfigure(1, weight=1)
        body_window = body_canvas.create_window((0, 0), window=cards, anchor="nw")
        cards.bind("<Configure>", lambda _e: body_canvas.configure(scrollregion=body_canvas.bbox("all")))
        body_canvas.bind("<Configure>", lambda e: body_canvas.itemconfigure(body_window, width=e.width))
        ttk.Label(cards, text="ECMWF IFS", font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w", padx=6, pady=2)
        ttk.Label(cards, text="GFS Global", font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=1, sticky="w", padx=6, pady=2)
        card_refs = {}
        for i in range(4):
            cards.rowconfigure(i + 1, minsize=180, weight=0)
            for col, model_label in enumerate(("ECMWF", "GFS")):
                frame = tk.Frame(cards, bg="#ffffff", bd=1, relief="solid", padx=10, pady=8)
                frame.grid(row=i + 1, column=col, sticky="nsew", padx=5, pady=5)
                title_var = tk.StringVar(value=f"事件 {i+1} · {model_label}")
                score_var = tk.StringVar(value="火烧云潜力评分：等待数据")
                comment_var = tk.StringVar(value="云量与云层结构等待数据……")
                tk.Label(frame, textvariable=title_var, bg="#ffffff", fg="#172b4d",
                         font=("Microsoft YaHei UI", 11, "bold"), anchor="w", justify="left").pack(anchor="w", fill="x")
                swatch = tk.Frame(frame, width=100, height=24, bg="#bfc5cc", highlightbackground="#667085", highlightthickness=1)
                swatch.pack(anchor="w", pady=(5, 4))
                swatch.pack_propagate(False)
                tk.Label(frame, textvariable=score_var, bg="#ffffff", fg="#172b4d",
                         font=("Microsoft YaHei UI", 12, "bold"), anchor="w", justify="left").pack(anchor="w", fill="x")
                tk.Label(frame, textvariable=comment_var, bg="#ffffff", fg="#243447",
                         font=("Microsoft YaHei UI", 9), wraplength=500, justify="left", anchor="nw").pack(anchor="w", fill="x", pady=(5, 0))
                card_refs[(i, model_label)] = (title_var, score_var, comment_var, swatch)
        ttk.Button(outer, text="关闭", command=win.destroy).pack(anchor="e", pady=(8, 0))

        # 取当前时刻之后最近四个事件；每个事件分别使用两个模型评分，合计 8 项。
        events = []
        today = local_now.date()
        now_naive = local_now.replace(tzinfo=None)
        for day_offset in range(5):
            d = today + timedelta(days=day_offset)
            for key, label, fn in (("morning", "朝霞", find_sunrise), ("evening", "晚霞", find_sunset)):
                event_dt, _az = fn(d, utc_offset, lat, lon)
                if event_dt is not None and event_dt > now_naive:
                    events.append({"date": d, "key": key, "label": label, "dt": event_dt, "azimuth": _az})
        events.sort(key=lambda e: e["dt"])
        events = events[:4]
        if len(events) < 4:
            status.set("无法计算足够的未来日出/日落事件（可能位于极昼/极夜地区）。")
            return
        import threading
        def worker():
            try:
                model_specs = [("ECMWF", "ecmwf_ifs025"), ("GFS", "ncep_gfs_global")]
                results = {}
                # 低请求量折中：每个模型把四个事件的稀疏路径合并为一次天气请求。
                # 距离点 7 个；每个截面中心+左右各 20/40 km，共 5 点，四事件共 140 坐标/模型。
                # 云底剖面复用该模型本地逐小时剖面，AOD 复用下方一次本地 CAMS 查询，避免逐事件额外请求。
                path_distances = [0, 50, 100, 175, 250, 350, 450]
                path_layouts = {}
                batch_path_coords = []
                for event_index, event in enumerate(events):
                    azimuth = event.get("azimuth")
                    if azimuth is None:
                        path_layouts[event_index] = {"error": "缺少该事件太阳方位，无法建立光路采样。"}
                        continue
                    centers = [destination_point(lat, lon, azimuth, d) for d in path_distances]
                    all_coords, index_map = [], []
                    for clat, clon in centers:
                        pts = [(clat, clon)]
                        for off in (-40.0, -20.0, 20.0, 40.0):
                            bearing = (azimuth + (90.0 if off > 0 else -90.0)) % 360
                            pts.append(destination_point(clat, clon, bearing, abs(off)))
                        start_idx = len(batch_path_coords) + len(all_coords)
                        all_coords.extend(pts)
                        index_map.append((start_idx, start_idx + len(pts)))
                    path_layouts[event_index] = {"centers": centers, "index_map": index_map,
                                                 "all_coords": all_coords, "distances": path_distances}
                    batch_path_coords.extend(all_coords)

                def local_profile_for_event(hourly, idx, elevation, model_id):
                    levels = ([1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]
                              if model_id == "ecmwf_ifs025" else
                              [1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200])
                    approx = {1000:110,975:320,950:500,925:800,900:1000,850:1500,800:1900,750:2500,
                              700:3000,650:3600,600:4200,550:4900,500:5600,450:6300,400:7200,
                              350:8100,300:9200,250:10400,200:11800,150:13500,100:15800,50:19300}
                    profile_levels = []
                    for level in levels:
                        def val(prefix):
                            arr = hourly.get(f"{prefix}_{level}hPa", [])
                            value = arr[idx] if idx < len(arr) else None
                            return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None
                        height = val("geopotential_height")
                        if height is None:
                            height = approx.get(level)
                        profile_levels.append({"pressure_hpa": level, "height_m_asl": height,
                                               "cloud_cover": val("cloud_cover"),
                                               "relative_humidity": val("relative_humidity")})
                    estimate = estimate_cloud_base_from_profile(profile_levels, elevation or 0.0)
                    return estimate

                def score_future_event_path(event, model_id, event_aod, layout, path_locs, local_profile):
                    event_utc = (event["dt"] - timedelta(hours=utc_offset)).replace(tzinfo=None)
                    if layout.get("error"):
                        raise ValueError(layout["error"])
                    distances, centers = layout["distances"], layout["centers"]
                    all_coords, index_map = layout["all_coords"], layout["index_map"]
                    # index_map 指向合并请求的全局下标；按每个截面提取五个横向样本。
                    samples = []
                    for dist, center, (start_idx, end_idx) in zip(distances, centers, index_map):
                        records = []
                        for subcoord, loc in zip(batch_path_coords[start_idx:end_idx], path_locs[start_idx:end_idx]):
                            hourly_path = loc.get("hourly", {})
                            ts = hourly_path.get("time", [])
                            if not ts:
                                continue
                            ti = min(range(len(ts)), key=lambda j: abs((datetime.fromisoformat(ts[j]) - event_utc).total_seconds()))
                            if abs((datetime.fromisoformat(ts[ti]) - event_utc).total_seconds()) > 5400:
                                continue
                            rec = {"lat": subcoord[0], "lon": subcoord[1]}
                            for key in ("cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "visibility", "wind_speed_10m", "precipitation_probability", "precipitation"):
                                arr = hourly_path.get(key, [])
                                value = arr[ti] if ti < len(arr) else None
                                rec[key] = float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None
                            records.append(rec)
                        if not records:
                            raise ValueError(f"{dist} km 路径点缺少接近事件时刻的天气数据。")
                        def mean_key(key):
                            vals = [r[key] for r in records if r.get(key) is not None]
                            return sum(vals) / len(vals) if vals else None
                        transverse = records[1:5]
                        transverse_low = [r.get("cloud_cover_low") for r in transverse if r.get("cloud_cover_low") is not None]
                        offsets = [-40, -20, 20, 40]
                        samples.append({
                            "distance_km": dist, "lat": center[0], "lon": center[1],
                            "cloud_cover": mean_key("cloud_cover"), "cloud_cover_low": mean_key("cloud_cover_low"),
                            "center_cloud_cover_low": records[0].get("cloud_cover_low"),
                            "cross_track_low_cover": transverse_low,
                            "cross_track_offsets_km": offsets[:len(transverse_low)],
                            "cross_track_low_ge70_count": sum(v >= 70 for v in transverse_low),
                            "cross_track_low_ge70_fraction": (sum(v >= 70 for v in transverse_low) / len(transverse_low)) if transverse_low else None,
                            "cloud_cover_mid": mean_key("cloud_cover_mid"), "cloud_cover_high": mean_key("cloud_cover_high"),
                            "visibility_m": mean_key("visibility"), "wind_ms": mean_key("wind_speed_10m"),
                            "precipitation_probability": mean_key("precipitation_probability"),
                            "precipitation": mean_key("precipitation"), "area_sample_count": len(records),
                        })
                    direction_key = event["key"]
                    manual_base = parse_float(self.vars[f"{direction_key}_cloud_base_km"].get(), "云底高度")
                    aod_text = self.vars[f"{direction_key}_aod"].get().strip()
                    manual_aod = parse_float(aod_text, "手动 AOD") if aod_text else None
                    if manual_aod is not None and manual_aod < 0:
                        raise ValueError("AOD 不能小于 0。")
                    base_mode = self.vars["cloud_base_mode"].get()
                    effective_aod = manual_aod if manual_aod is not None else event_aod
                    profile_base = local_profile.get("estimated_base_km") if local_profile else None
                    profile_status = (local_profile.get("status", "本地剖面") if local_profile else "本地剖面缺测，使用手动回退高度")
                    profiles = [{"distance_km": d, "estimated_base_km": profile_base,
                                 "status": "本地剖面近似复用：未额外请求远端剖面" if profile_base is not None else profile_status,
                                 "levels": []} for d in distances]
                    aod_samples = [{"distance_km": d, "aod": effective_aod, "time": None} for d in distances]
                    geometry = analyze_cloud_base_geometry(
                        samples, profiles, aod_samples, event["date"], utc_offset, lat, lon,
                        base_mode, manual_base, manual_aod=manual_aod, weighted_aod=effective_aod,
                        scale_height_km=0.5,
                        event_type="sunrise" if direction_key == "morning" else "sunset",
                        ground_elevation_m=data.get("elevation_m", 0.0))
                    local_low_above_ground = has_local_low_cloud_above_ground(local_profile.get("levels") if local_profile else None)
                    inferred = infer_from_cloud_path(samples, effective_aod,
                                                     local_low_cloud_above_ground=local_low_above_ground)
                    local_geometry = next((r for r in geometry.get("rows", []) if r.get("distance_km") == 0), None)
                    reach = local_geometry.get("reach_km") if local_geometry else None
                    if not isinstance(reach, (int, float)) or reach <= 0:
                        reach = max((r.get("reach_km") or 0 for r in geometry.get("rows", [])), default=0)
                    boundaries = estimate_path_cloud_boundaries(samples, reach)
                    inferred = apply_path_score_pair(inferred, geometry, boundaries)
                    inferred = apply_low_cloud_screen_constraint(inferred, samples=samples, source="online_path",
                                                                 max_relevant_distance_km=reach,
                                                                 local_low_cloud_above_ground=local_low_above_ground)
                    return inferred, {"target_cloud": inferred.get("target_cloud"), "total": inferred.get("total"),
                                      "low": inferred.get("low"), "mid": inferred.get("mid"), "high": inferred.get("high"),
                                      "color_score": inferred.get("color_score"),
                                      "color_score_label": inferred.get("color_score_label"),
                                      "color_rgb": inferred.get("color_rgb"),
                                      "color_name": inferred.get("color_name"),
                                      "aod": effective_aod, "reach_km": reach,
                                      "path_samples": len(samples), "path_area_points": 5,
                                      "profile_source": "本地剖面近似复用" if profile_base is not None else "手动高度回退",
                                      "aod_source": "手动 AOD" if manual_aod is not None else "本地 CAMS AOD",
                                      "clear_gap_start_km": boundaries.get("clear_gap_start_km"),
                                      "cloud_edge_distance_km": boundaries.get("cloud_edge_distance_km")}

                # CAMS AOD 与天气模式无关：只请求一次，供四个事件共用，失败则明确按缺测处理。
                aod_times, aod_values = [], []
                try:
                    aod_params = {"latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}",
                                  "hourly": "aerosol_optical_depth", "domains": "cams_global",
                                  "timezone": "UTC", "forecast_days": 5, "timeformat": "iso8601"}
                    aod_url = "https://air-quality-api.open-meteo.com/v1/air-quality?" + urllib.parse.urlencode(aod_params)
                    aod_req = urllib.request.Request(aod_url, headers={"User-Agent": "FireCloudForecast/1.4.12"})
                    aod_payload = request_json(aod_req, timeout=20, retries=0, cache_key=aod_url)
                    aod_loc = aod_payload[0] if isinstance(aod_payload, list) else aod_payload
                    aod_hourly = aod_loc.get("hourly", {})
                    aod_times = aod_hourly.get("time", [])
                    aod_values = aod_hourly.get("aerosol_optical_depth", [])
                except Exception:
                    aod_times, aod_values = [], []

                def aod_for_event(event_utc):
                    if not aod_times or not aod_values:
                        return None
                    try:
                        j = min(range(len(aod_times)), key=lambda k: abs((datetime.fromisoformat(aod_times[k]) - event_utc).total_seconds()))
                        gap = abs((datetime.fromisoformat(aod_times[j]) - event_utc).total_seconds())
                        value = aod_values[j] if j < len(aod_values) else None
                        return float(value) if gap <= 7200 and value is not None and math.isfinite(float(value)) and float(value) >= 0 else None
                    except Exception:
                        return None

                for model_index, (model_label, model_id) in enumerate(model_specs):
                    model_start = model_index * 50
                    win.after(0, lambda m=model_label: status.set(f"正在请求 {m} 逐小时云量数据……（路径数据按模型合并查询；遇到限流不自动连续重试）"))
                    self._set_main_progress(model_start + 5, f"本地未来两日：正在获取 {model_label} 数据……")
                    params = {
                        "latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}",
                        "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,precipitation_probability,precipitation,visibility,wind_speed_10m," + ",".join(
                            f"{prefix}_{level}hPa" for level in ([1000,925,850,700,600,500,400,300,250,200,150,100,50] if model_id == "ecmwf_ifs025" else [1000,975,950,925,900,850,800,750,700,650,600,550,500,450,400,350,300,250,200])
                            for prefix in ("cloud_cover", "relative_humidity", "geopotential_height")), 
                        "models": model_id, "timezone": "UTC", "forecast_days": 4, "timeformat": "iso8601",
                    }
                    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
                    req = urllib.request.Request(url, headers={"User-Agent": "FireCloudForecast/1.4.24"})
                    # 不在 429 后循环重试，避免把配额不足变成更多请求。
                    payload = request_json(req, timeout=45, retries=0, cache_key=url)
                    loc = payload[0] if isinstance(payload, list) else payload
                    hourly = loc.get("hourly", {})
                    times = hourly.get("time", [])
                    # 每个模型只发起一次合并路径天气请求，避免每个事件分别请求。
                    path_locs = []
                    path_error = None
                    win.after(0, lambda m=model_label: status.set(f"{m} 本地数据已返回，正在获取四个事件合并后的路径天气（仅 1 次请求）……"))
                    try:
                        if not batch_path_coords:
                            raise ValueError("没有可用的路径坐标。")
                        cloud_hourly = "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility,wind_speed_10m,precipitation_probability,precipitation"
                        params_path = {
                            "latitude": ",".join(f"{x[0]:.5f}" for x in batch_path_coords),
                            "longitude": ",".join(f"{x[1]:.5f}" for x in batch_path_coords),
                            "hourly": cloud_hourly, "models": model_id, "timezone": "UTC",
                            "forecast_days": 4, "past_hours": 36, "timeformat": "iso8601",
                        }
                        path_url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params_path)
                        path_req = urllib.request.Request(path_url, headers={"User-Agent": "FireCloudForecast/1.4.24"})
                        path_payload = request_json(path_req, timeout=45, retries=0, cache_key=path_url)
                        path_locs = path_payload if isinstance(path_payload, list) else [path_payload]
                        if len(path_locs) != len(batch_path_coords):
                            raise ValueError(f"合并路径天气接口返回 {len(path_locs)} 个位置，预期 {len(batch_path_coords)} 个。")
                    except Exception as exc:
                        path_error = str(exc)
                    for i, event in enumerate(events):
                        event_utc = (event["dt"] - timedelta(hours=utc_offset)).replace(tzinfo=None)
                        if not times:
                            score, vals = None, {}
                        else:
                            idx = min(range(len(times)), key=lambda j: abs((datetime.fromisoformat(times[j]) - event_utc).total_seconds()))
                            if abs((datetime.fromisoformat(times[idx]) - event_utc).total_seconds()) > 5400:
                                score, vals = None, {}
                            else:
                                # 本地两日预测优先使用 GUI 中选定/手动修改的观测点海拔。
                                loc_elevation = elevation_m
                                if not isinstance(loc_elevation, (int, float)) or not math.isfinite(loc_elevation):
                                    loc_elevation = 0.0
                                event_aod = aod_for_event(event_utc)
                                try:
                                    if path_error:
                                        raise RuntimeError("合并路径查询失败：" + path_error)
                                    layout = path_layouts.get(i, {"error": "路径配置缺失"})
                                    # 使用本地模式已返回的气压层资料推算本地云底；不再为每个事件单独查询远端剖面。
                                    local_profile = local_profile_for_event(hourly, idx, loc_elevation, model_id)
                                    inferred, geometry_info = score_future_event_path(
                                        event, model_id, event_aod, layout, path_locs, local_profile)
                                    score = inferred.get("regular_score")
                                    vals = {
                                        "total": inferred.get("total"), "low": inferred.get("low"),
                                        "mid": inferred.get("mid"), "high": inferred.get("high"),
                                        "precip": (hourly.get("precipitation_probability", [])[idx]
                                                   if idx < len(hourly.get("precipitation_probability", [])) else None),
                                        "upper_score": inferred.get("upper_score"),
                                        "target_cloud": inferred.get("target_cloud"),
                                        "aod": geometry_info.get("aod"), **geometry_info,
                                    }
                                except Exception as path_exc:
                                    score, vals = None, {"path_error": str(path_exc)}
                        results[(i, model_label)] = (score, vals)
                    self._set_main_progress(model_start + 50, f"本地未来两日：{model_label} 数据处理完成")
                    win.after(0, lambda v=model_start + 50: progress.configure(value=v))
                def finish():
                    progress.configure(value=100, maximum=100)
                    self._set_main_progress(100, "本地未来两日预报完成")
                    for i, event in enumerate(events):
                        event_title = f"{event['date'].strftime('%m-%d')} {event['label']} · {event['dt'].strftime('%H:%M')}"
                        for model_label in ("ECMWF", "GFS"):
                            score, vals = results[(i, model_label)]
                            title_var, score_var, comment_var, swatch = card_refs[(i, model_label)]
                            title_var.set(event_title + " · " + model_label)
                            color_rgb = vals.get("color_rgb")
                            color = self._hex(color_rgb) if isinstance(color_rgb, (tuple, list)) and len(color_rgb) == 3 else self._national_color(score)
                            swatch.configure(bg=color)
                            if score is None:
                                score_var.set("评分：路径数据不足，暂不评分")
                                err = vals.get("path_error")
                                comment_var.set(("路径采样失败：" + err) if err else "该时刻附近没有可用的逐小时预报数据。")
                                continue
                            upper_score = vals.get("upper_score")
                            color_score = vals.get("color_score")
                            score_var.set(f"发生概率估计*：{score}%　｜　有利情景上限：{upper_score if upper_score is not None else '—'}%　｜　色彩指数：{color_score if color_score is not None else '—'}/100")
                            total, low, mid, high, precip = (vals.get(k) for k in ("total", "low", "mid", "high", "precip"))
                            if score >= 75: lead = "常规判断：当前证据较有利，值得重点留意。"
                            elif score >= 55: lead = "常规判断：有一定机会，建议临近再确认。"
                            elif score >= 35: lead = "常规判断：条件一般，可作为备选。"
                            else: lead = "常规判断：当前证据偏弱；可同时参考上限评分。"
                            known_layers = [("低云", low), ("中云", mid), ("高云", high)]
                            known_layers = [(name, value) for name, value in known_layers if value is not None]
                            if not known_layers:
                                cloud_tendency = "云型倾向：云层分项缺测，无法判断。"
                            else:
                                dominant_name, dominant_value = max(known_layers, key=lambda item: item[1])
                                if dominant_name == "低云" and dominant_value >= 45:
                                    cloud_tendency = "云型倾向：低云占比较高，可能遮挡天空或压低霞光观感。"
                                elif dominant_name == "中云" and dominant_value >= 35:
                                    cloud_tendency = "云型倾向：中云较突出，若云隙与云底高度合适，可能有利于霞光显色。"
                                elif dominant_name == "高云" and dominant_value >= 35:
                                    cloud_tendency = "云型倾向：高云较突出，可能出现高空染色；若云层过薄，视觉效果也可能偏弱。"
                                elif max(value for _, value in known_layers) < 20:
                                    cloud_tendency = "云型倾向：低/中/高云量都偏少，可供着色的云体可能不足。"
                                else:
                                    cloud_tendency = f"云型倾向：{dominant_name}相对占优，需结合云层形态和实际云底判断。"
                            aod_value = vals.get("aod")
                            aod_text = "—" if aod_value is None else f"{aod_value:.2f}"
                            details = (
                                f"{lead}\n"
                                f"云量：总云 {self._pct(total)} ｜ 低云 {self._pct(low)} ｜ 中云 {self._pct(mid)} ｜ 高云 {self._pct(high)}\n"
                                f"降水概率：{self._pct(precip)} ｜ AOD：{aod_text}\n"
                                f"云底绝对海拔：{('约 '+format(vals.get('cloud_base_km'), '.2f')+' km ASL' if vals.get('cloud_base_km') is not None else '不可用')}；理论照明距离：{('约 '+format(vals.get('reach_km'), '.0f')+' km' if vals.get('reach_km') is not None else '不可用')}\n"
                                f"色彩倾向：{vals.get('color_name') or '待估'}；色彩评分是独立的经验指数。\n"
                                f"{cloud_tendency}\n"
                                "发生评分与色彩评分使用统一核心；颜色色块来自云况/能见度/AOD和低云屏障，不再用发生评分直接着色。\n"
                                "两日预测仍使用本地剖面近似复用，远端云底与光路信息少于指定日期详细路径；云隙项按当前路径证据判断。\n"
                                "注：发生评分不是经历史样本校准的统计概率；色彩评分也不是精确的色彩饱和度/红霞面积预测。"
                            )
                            comment_var.set(details)
                    status.set("完成：共 8 项（4 个未来事件 × ECMWF/GFS）；每个模型合并 1 次路径天气请求。云底剖面/AOD 复用本地值，精度与单日详细路径仍可能有差异；分数不是发生概率。")
                win.after(0, finish)
            except Exception as exc:
                msg = str(exc)
                self._set_main_progress(0, "本地未来两日预报失败")
                win.after(0, lambda m=msg: status.set("获取失败：" + m))
                win.after(0, lambda m=msg: messagebox.showerror("本地两日预报失败", m, parent=win))
        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _pct(value):
        return "—" if value is None else f"{value:.0f}%"

    @staticmethod
    def _fetch_national_weather(coords, model_id, progress_callback=None):
        """分批查询全国天气；遇到 429 时先退避，再自动拆小批次重试。可回报外层批次进度。"""
        all_locations = []
        # 60 个位置 × 16 天逐小时数据的响应体较大；用较小批次降低单次请求负担。
        batch_size = 30

        def fetch_batch(batch):
            cache_key = (model_id, tuple((round(x[0], 4), round(x[1], 4)) for x in batch))
            cached = _NATIONAL_WEATHER_CACHE.get(cache_key)
            if cached and time.time() - cached[0] < _NATIONAL_CACHE_TTL_SECONDS:
                return list(cached[1])

            params = {
                "latitude": ",".join(f"{x[0]:.4f}" for x in batch),
                "longitude": ",".join(f"{x[1]:.4f}" for x in batch),
                "hourly": "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,precipitation_probability",
                "models": model_id, "timezone": "UTC", "forecast_days": 16, "timeformat": "iso8601",
            }
            url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
            last_429 = None
            for attempt in range(3):
                req = urllib.request.Request(url, headers={"User-Agent": "FireCloudForecast/1.3.1"})
                try:
                    payload = request_json(req, timeout=50, cache_key=url)
                    locs = payload if isinstance(payload, list) else [payload]
                    if len(locs) != len(batch):
                        raise ValueError(f"全国地图天气接口返回 {len(locs)} 个位置，预期 {len(batch)} 个。")
                    _NATIONAL_WEATHER_CACHE[cache_key] = (time.time(), locs)
                    return locs
                except urllib.error.HTTPError as exc:
                    if exc.code != 429:
                        raise
                    last_429 = exc
                    retry_after = exc.headers.get("Retry-After")
                    try:
                        delay = min(90, max(5, int(retry_after))) if retry_after else (8, 20, 35)[attempt]
                    except (TypeError, ValueError):
                        delay = (8, 20, 35)[attempt]
                    if attempt < 2:
                        time.sleep(delay)

            # 若大批次持续被拒绝，拆成两半后再请求；单点仍被限流时，保留原始错误提示。
            if len(batch) > 1:
                midpoint = len(batch) // 2
                left = fetch_batch(batch[:midpoint])
                right = fetch_batch(batch[midpoint:])
                combined = left + right
                _NATIONAL_WEATHER_CACHE[cache_key] = (time.time(), combined)
                return combined
            raise last_429 or RuntimeError("天气接口请求失败，请稍后重试。")

        batches = [coords[start:start + batch_size] for start in range(0, len(coords), batch_size)]
        for batch_index, batch in enumerate(batches, start=1):
            all_locations.extend(fetch_batch(batch))
            if progress_callback:
                try:
                    progress_callback(batch_index, len(batches))
                except Exception:
                    pass
        return all_locations



    def show_path_cloud_map(self, direction_key):
        """在经纬网地理俯视底图上显示现有空间采样中的低/中/高云分布。"""
        result = getattr(self, "direction_results", {}).get(direction_key)
        if not result:
            messagebox.showinfo("尚无数据", "请先查询朝霞与晚霞，再查看地图云层分布。")
            return
        raw_details = result.get("raw_details", [])
        records = [rec for group in raw_details for rec in group.get("records", [])]
        if not records:
            messagebox.showinfo("空间数据不足", "本次查询没有保留可绘制的周边空间采样点，请重新查询后再试。")
            return
        title = "朝霞地图俯视云层分布" if direction_key == "morning" else "晚霞地图俯视云层分布"
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("1120x780")
        win.minsize(760, 540)
        outer = ttk.Frame(win, padding=10)
        outer.pack(fill="both", expand=True)
        note = ("俯视云图：低云保持蓝色；中高云使用橙→紫连续色带，颜色由中云/高云相对占比决定。"
                "无明显云层不着色。底图为经纬网和采样位置，不是卫星云图；圆形覆盖仅为稀疏采样示意。")
        ttk.Label(outer, text=note, wraplength=1080).pack(anchor="w", pady=(0, 6))
        map_row = ttk.Frame(outer)
        map_row.pack(fill="both", expand=True)
        canvas = tk.Canvas(map_row, bg="#f4f7f5", highlightthickness=1, highlightbackground="#cbd5e1")
        canvas.pack(side="left", fill="both", expand=True)
        height_bar = tk.Canvas(map_row, width=118, bg="#f4f7f5", highlightthickness=0)
        height_bar.pack(side="right", fill="y", padx=(8, 0))
        def draw_height_bar(_event=None):
            height_bar.delete("all")
            bh = height_bar.winfo_height()
            x0, x1 = 8, 28
            y0, y1 = 42, max(120, bh-70)
            # 连续混合色带：中云偏橙，高云偏紫；低云独立显示为蓝色。
            c0, c1 = (246, 189, 96), (139, 101, 189)
            steps = max(30, int(y1-y0))
            for i in range(steps):
                t = i / max(1, steps-1)
                rgb = tuple(round(c0[j]*(1-t)+c1[j]*t) for j in range(3))
                col = "#%02x%02x%02x" % rgb
                yy = y0 + (y1-y0)*i/steps
                yy2 = y0 + (y1-y0)*(i+1)/steps + 1
                height_bar.create_rectangle(x0, yy, x1, yy2, fill=col, outline=col)
            height_bar.create_text(58, 18, text="中高云色带", font=("Microsoft YaHei UI", 9, "bold"), fill="#334155")
            height_bar.create_text(34, y0, text="高云占比高", anchor="w", font=("Microsoft YaHei UI", 8), fill="#334155")
            height_bar.create_text(34, y1, text="中云占比高", anchor="w", font=("Microsoft YaHei UI", 8), fill="#334155")
            height_bar.create_rectangle(8, y1+22, 28, y1+36, fill="#8ec5ed", outline="#8ec5ed")
            height_bar.create_text(34, y1+29, text="低云", anchor="w", font=("Microsoft YaHei UI", 8), fill="#334155")
        height_bar.bind("<Configure>", draw_height_bar)
        legend = ttk.Frame(outer)
        legend.pack(fill="x", pady=(6, 0))
        for color, label in (("#8ec5ed", "低云（独立蓝色）"), ("#d99c76", "中高云混合色带见右侧"), ("#d94841", "观测点/日出日落方向")):
            sw = tk.Canvas(legend, width=18, height=12, highlightthickness=0, bg="#f4f7f5")
            sw.create_rectangle(1, 1, 17, 11, fill=color, outline=color)
            sw.pack(side="left", padx=(7, 3))
            ttk.Label(legend, text=label).pack(side="left", padx=(0, 12))

        # 根据当前数据范围自动设定地理视野；经度按 cos(latitude) 修正比例，避免高纬度变形。
        lats = [r["lat"] for r in records if isinstance(r.get("lat"), (int, float))]
        lons = [r["lon"] for r in records if isinstance(r.get("lon"), (int, float))]
        if not lats or not lons:
            messagebox.showinfo("坐标缺失", "采样点没有有效经纬度，无法绘制地图。")
            win.destroy()
            return
        # 若常见城市距采样路径不超过 120 km，也纳入视野范围，便于显示真实城市位置。
        center_samples_for_bounds = [r for r in result.get("samples", [])
                                     if isinstance(r.get("lat"), (int,float)) and isinstance(r.get("lon"), (int,float))]
        def _city_distance_km(lat1, lon1, lat2, lon2):
            mean_lat = math.radians((lat1 + lat2) / 2)
            return math.hypot((lon2-lon1)*111.32*math.cos(mean_lat), (lat2-lat1)*111.32)
        for _city_name, city_lat, city_lon in self.MAP_CITIES:
            if any(_city_distance_km(city_lat, city_lon, sm["lat"], sm["lon"]) <= 120
                   for sm in center_samples_for_bounds):
                lats.append(city_lat); lons.append(city_lon)
        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)
        pad_lat = max(0.15, (max_lat-min_lat)*0.10)
        pad_lon = max(0.15, (max_lon-min_lon)*0.10)
        min_lat -= pad_lat; max_lat += pad_lat
        min_lon -= pad_lon; max_lon += pad_lon
        origin = result.get("origin", (sum(lats)/len(lats), sum(lons)/len(lons)))
        azimuth = result.get("azimuth", 0.0)
        bounds = result.get("boundaries", {})

        def draw(_event=None):
            canvas.delete("all")
            w, h = canvas.winfo_width(), canvas.winfo_height()
            left, right, top, bottom = 64, 26, 28, 48
            pw, ph = max(100, w-left-right), max(100, h-top-bottom)
            lat_mid = (min_lat+max_lat)/2
            coslat = max(0.15, math.cos(math.radians(lat_mid)))
            lon_span = max(1e-5, (max_lon-min_lon)*coslat)
            lat_span = max(1e-5, max_lat-min_lat)
            scale = min(pw/lon_span, ph/lat_span)
            used_w, used_h = lon_span*scale, lat_span*scale
            xoff = left+(pw-used_w)/2
            yoff = top+(ph-used_h)/2
            def xy(lat, lon):
                return xoff+(lon-min_lon)*coslat*scale, yoff+(max_lat-lat)*scale
            # 经纬网底图
            lat_step = 0.25 if lat_span < 2 else (0.5 if lat_span < 5 else 1.0)
            lon_step = 0.25 if (max_lon-min_lon) < 2 else (0.5 if (max_lon-min_lon) < 5 else 1.0)
            first_lat = math.ceil(min_lat/lat_step)*lat_step
            v = first_lat
            while v <= max_lat:
                x1,y=xy(v,min_lon); x2,_=xy(v,max_lon)
                canvas.create_line(x1,y,x2,y,fill="#d8e0df",dash=(2,4))
                canvas.create_text(left-8,y,text=f"{v:.2f}°",anchor="e",fill="#52616b",font=("Microsoft YaHei UI",8))
                v += lat_step
            first_lon = math.ceil(min_lon/lon_step)*lon_step
            v = first_lon
            while v <= max_lon:
                x,y1=xy(max_lat,v); _,y2=xy(min_lat,v)
                canvas.create_line(x,y1,x,y2,fill="#d8e0df",dash=(2,4))
                canvas.create_text(x,top+ph+16,text=f"{v:.2f}°",anchor="n",fill="#52616b",font=("Microsoft YaHei UI",8))
                v += lon_step
            # 绘制采样路径（中心点连线），仅表示采样方向，不是光线的三维轨迹。
            center_samples = sorted(result.get("samples", []), key=lambda r: r.get("distance_km", 0))
            path_points = [xy(r["lat"], r["lon"]) for r in center_samples if isinstance(r.get("lat"), (int, float)) and isinstance(r.get("lon"), (int, float))]
            if len(path_points) >= 2:
                canvas.create_line(*[coord for point in path_points for coord in point], fill="#64748b", dash=(4, 3), width=1.5)

            # 城市标注：固定标出用户输入的本地名称，再挑选沿路径附近、彼此间距足够的城市。
            # 这使用脚本内置的常见城市坐标，不会为地图标签额外访问地名 API。
            ox, oy = xy(origin[0], origin[1])
            place_name = self.vars.get("place").get().strip() if getattr(self, "vars", None) and self.vars.get("place") else "本地"
            canvas.create_oval(ox-5, oy-5, ox+5, oy+5, fill="#d94841", outline="white", width=1.5)
            canvas.create_text(ox+8, oy-10, text=f"{place_name}（本地）", anchor="sw", fill="#9f2525",
                               font=("Microsoft YaHei UI", 9, "bold"), tags=("citylabel", "originlabel"))
            def approx_km(lat1, lon1, lat2, lon2):
                mean_lat = math.radians((lat1+lat2)/2)
                dx = (lon2-lon1)*111.32*math.cos(mean_lat)
                dy = (lat2-lat1)*111.32
                return math.hypot(dx, dy)
            candidates = []
            for city_name, city_lat, city_lon in self.MAP_CITIES:
                if city_name == place_name:
                    continue
                nearest = None
                for sample in center_samples:
                    if not isinstance(sample.get("lat"), (int,float)) or not isinstance(sample.get("lon"), (int,float)):
                        continue
                    off_km = approx_km(city_lat, city_lon, sample["lat"], sample["lon"])
                    if nearest is None or off_km < nearest[0]:
                        nearest = (off_km, float(sample.get("distance_km", 0)), sample)
                if nearest:
                    candidates.append((nearest[0], nearest[1], city_name, city_lat, city_lon))
            # 优先沿线 120 km 内的城市；至少再标一个最近城市，避免地图只剩本地标签。
            candidates.sort(key=lambda x: (x[0] > 120, x[0], x[1]))
            selected = []
            for item in candidates:
                off_km, along_km, city_name, city_lat, city_lon = item
                if off_km > 180 and selected:
                    continue
                if all(abs(along_km - old[1]) >= 70 for old in selected):
                    selected.append(item)
                if len(selected) >= 4:
                    break
            if not selected and candidates:
                selected = [candidates[0]]
            for off_km, along_km, city_name, city_lat, city_lon in selected:
                cx, cy = xy(city_lat, city_lon)
                # 若城市离路径有一定距离，用细虚线连到路径最近采样点，避免误以为城市正好位于路径上。
                nearest_sample = min(center_samples, key=lambda sm: approx_km(city_lat, city_lon, sm.get("lat", city_lat), sm.get("lon", city_lon))) if center_samples else None
                if nearest_sample and off_km > 12:
                    sx, sy = xy(nearest_sample["lat"], nearest_sample["lon"])
                    canvas.create_line(cx, cy, sx, sy, fill="#6b7280", dash=(2,3), width=1)
                canvas.create_oval(cx-3, cy-3, cx+3, cy+3, fill="#344054", outline="white", width=1)
                label = city_name if off_km <= 12 else f"{city_name}（偏离路径约{off_km:.0f}km）"
                canvas.create_text(cx+5, cy-5, text=label, anchor="sw", fill="#263548",
                                   font=("Microsoft YaHei UI", 8, "bold"), tags=("citylabel", city_name))
            # 低云单独蓝色；中高云使用连续混合色。颜色反映中云/高云的相对占比，不是精确云高。
            def _blend_rgb(a, b, t):
                t = max(0.0, min(1.0, t))
                rgb = tuple(round(a[i] * (1.0-t) + b[i] * t) for i in range(3))
                return "#%02x%02x%02x" % rgb
            mid_rgb, high_rgb = (246, 189, 96), (139, 101, 189)
            for rec in records:
                x, y = xy(rec["lat"], rec["lon"])
                radius = max(3.0, min(22.0, 12.0 * scale / 111.0))
                low = rec.get("cloud_cover_low")
                mid = rec.get("cloud_cover_mid")
                high = rec.get("cloud_cover_high")
                if isinstance(low, (int, float)) and low >= 25:
                    rr = radius * (0.55 + 0.45 * min(100.0, low) / 100.0)
                    canvas.create_oval(x-rr, y-rr, x+rr, y+rr, fill="#8ec5ed", outline="", stipple="gray50")
                mid_v = float(mid) if isinstance(mid, (int, float)) else 0.0
                high_v = float(high) if isinstance(high, (int, float)) else 0.0
                nonlow_total = mid_v + high_v
                if nonlow_total >= 25:
                    ratio_high = high_v / nonlow_total if nonlow_total > 0 else 0.0
                    fill = _blend_rgb(mid_rgb, high_rgb, ratio_high)
                    amount = min(100.0, nonlow_total / 2.0)
                    rr = radius * (0.55 + 0.45 * amount / 100.0)
                    stipple = "gray50" if nonlow_total < 65 else "gray25"
                    canvas.create_oval(x-rr, y-rr, x+rr, y+rr, fill=fill, outline="", stipple=stipple)
            # 绘制观测点与太阳方位箭头
            ox,oy = xy(origin[0],origin[1])
            canvas.create_oval(ox-5,oy-5,ox+5,oy+5,fill="#d94841",outline="white",width=1.5)
            canvas.create_text(ox+8,oy-9,text="观测点",anchor="sw",fill="#9f2525",font=("Microsoft YaHei UI",9,"bold"))
            # 方位角按地理北顺时针，长度只作方向提示。
            arrow_len = min(pw,ph)*0.18
            ar = math.radians(azimuth)
            ex = ox + math.sin(ar)*arrow_len
            ey = oy - math.cos(ar)*arrow_len
            canvas.create_line(ox,oy,ex,ey,fill="#d94841",width=2,arrow="last")
            canvas.create_text(ex,ey,text=f"日出/日落方向 {azimuth:.0f}°",anchor="s",fill="#9f2525",font=("Microsoft YaHei UI",9,"bold"))
            # 沿路径边界仅标记对应采样点位置，避免误画成连续真实云缘。
            sample_by_dist = {r.get("distance_km"):r for r in result.get("samples",[])}
            for key,color,label in (("fire_cloud_range_km","#0f766e","火烧云范围"),("cloud_edge_distance_km","#b45309","云边距")):
                d = bounds.get(key)
                if d is None or d not in sample_by_dist:
                    continue
                sm = sample_by_dist[d]
                x,y = xy(sm["lat"],sm["lon"])
                canvas.create_oval(x-5,y-5,x+5,y+5,fill=color,outline="white",width=1)
                canvas.create_text(x+7,y+8,text=f"{label} {d}km",anchor="nw",fill=color,font=("Microsoft YaHei UI",8,"bold"))
            canvas.create_text(left+pw-4,top+4,text="北 ↑",anchor="ne",fill="#334155",font=("Microsoft YaHei UI",10,"bold"))
        canvas.bind("<Configure>", draw)
        win.after(80, draw)

    def show_path_cloud_profile(self, direction_key):
        """绘制沿太阳方位的距离-高度剖面；高度带着色，云量/RH决定是否绘制。"""
        result = getattr(self, "direction_results", {}).get(direction_key)
        if not result:
            messagebox.showinfo("尚无数据", "请先查询朝霞与晚霞，再绘制路径云层剖面。")
            return
        profiles = result.get("profiles", [])
        samples = result.get("samples", [])
        profile_by_distance = {p.get("distance_km"): p for p in profiles}
        chart_profiles = []
        used_fallback = False
        for sm in sorted(samples, key=lambda x: x.get("distance_km", 0)):
            prof = profile_by_distance.get(sm.get("distance_km"), {})
            if prof.get("levels"):
                chart_profiles.append(prof)
                continue
            # API 剖面缺失时，利用已有低/中/高云量绘制粗略高度带，保证图仍可用。
            used_fallback = True
            levels = []
            for key, height, pressure in (("cloud_cover_low", 1.0, 850), ("cloud_cover_mid", 4.5, 600), ("cloud_cover_high", 8.5, 300)):
                amount = sm.get(key)
                if isinstance(amount, (int, float)):
                    levels.append({"height_agl_km": height, "cloud_cover": amount,
                                   "relative_humidity": None, "pressure_hpa": pressure,
                                   "cloudy_candidate": amount >= 25})
            chart_profiles.append({"distance_km": sm.get("distance_km"), "estimated_base_km": None,
                                   "status": "分层云量高度带近似（剖面数据缺失）", "levels": levels,
                                   "fallback_layer_bands": True})
        valid = [p for p in chart_profiles if p.get("levels")]
        if len(valid) < 1:
            messagebox.showinfo("剖面数据不足", "本次既没有有效气压层剖面，也没有可用的低/中/高云量数据，暂时无法绘图。")
            return
        title = "朝霞路径云层剖面" if direction_key == "morning" else "晚霞路径云层剖面"
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("1080x700")
        win.minsize(780, 500)
        outer = ttk.Frame(win, padding=10)
        outer.pack(fill="both", expand=True)
        note = ("横轴：沿太阳方位的距离；纵轴：模式估算的离地高度。颜色表示高度带，颜色深浅近似表示云量/RH强弱。"
                "云层块是离散数据的可视化近似，不是卫星实测云体。" +
                ("部分点缺少气压层剖面，已用低/中/高云量的典型高度带补画，属于粗略示意。" if used_fallback else ""))
        ttk.Label(outer, text=note, wraplength=1020).pack(anchor="w", pady=(0, 6))
        canvas = tk.Canvas(outer, bg="#fbfcfe", highlightthickness=1, highlightbackground="#cbd5e1")
        canvas.pack(fill="both", expand=True)
        legend = ttk.Frame(outer)
        legend.pack(fill="x", pady=(7, 0))
        for color, label in (("#509dde", "低云 0–2 km"), ("#f4a636", "中云 2–6 km"), ("#a678da", "高云 6 km以上"), ("#d83c3c", "推算云底")):
            swatch = tk.Canvas(legend, width=18, height=12, highlightthickness=0, bg="#fbfcfe")
            swatch.create_rectangle(1, 1, 17, 11, fill=color, outline=color)
            swatch.pack(side="left", padx=(6, 3))
            ttk.Label(legend, text=label).pack(side="left", padx=(0, 12))

        def draw(_event=None):
            canvas.delete("all")
            width, height = canvas.winfo_width(), canvas.winfo_height()
            left, right, top, bottom = 66, 24, 24, 54
            plot_w, plot_h = max(100, width-left-right), max(100, height-top-bottom)
            sample_by_dist = {x.get("distance_km"): x for x in samples}
            profs = sorted(valid, key=lambda p: p.get("distance_km", 0))
            distances = [p["distance_km"] for p in profs]
            max_dist = max(distances) if distances else 1
            plot_dist = max_dist + ((distances[-1]-distances[-2])/2 if len(distances) > 1 else 0)
            all_heights = [lv.get("height_agl_km", 0) for p in profs for lv in p.get("levels", [])
                           if isinstance(lv.get("height_agl_km"), (int, float))]
            max_h = min(18.0, max(4.0, math.ceil(max(all_heights or [10]))))
            def xmap(d): return left + (d/plot_dist)*plot_w if plot_dist else left
            def ymap(h): return top + plot_h - (max(0.0, min(max_h, h))/max_h)*plot_h
            # axes and grid
            for h in range(0, int(max_h)+1, 2):
                y = ymap(h)
                canvas.create_line(left, y, left+plot_w, y, fill="#e3e8ef")
                canvas.create_text(left-10, y, text=f"{h} km", anchor="e", fill="#334155", font=("Microsoft YaHei UI", 9))
            tick_step = 25 if max_dist <= 250 else (50 if max_dist <= 500 else 100)
            for d in range(0, int(max_dist)+1, tick_step):
                x = xmap(d)
                canvas.create_line(x, top, x, top+plot_h, fill="#edf0f4")
                canvas.create_text(x, top+plot_h+17, text=str(d), fill="#334155", font=("Microsoft YaHei UI", 8))
            canvas.create_line(left, top, left, top+plot_h, fill="#475569", width=1.5)
            canvas.create_line(left, top+plot_h, left+plot_w, top+plot_h, fill="#475569", width=1.5)
            canvas.create_text(left+plot_w/2, height-12, text="沿太阳方位距离（km）", fill="#1f2937", font=("Microsoft YaHei UI", 10))
            canvas.create_text(16, top+plot_h/2, text="离地高度", angle=90, fill="#1f2937", font=("Microsoft YaHei UI", 10))
            # sample cells extend halfway to adjacent samples, so irregular distances remain legible
            for i, prof in enumerate(profs):
                d = prof.get("distance_km", 0)
                x0d = (distances[i-1]+d)/2 if i else max(0, d-(distances[1]-d)/2 if len(distances)>1 else 0)
                x1d = (d+distances[i+1])/2 if i+1 < len(distances) else (d+(d-distances[i-1])/2 if i else max_dist)
                x0, x1 = xmap(x0d), xmap(x1d)
                levels = sorted([lv for lv in prof.get("levels", []) if isinstance(lv.get("height_agl_km"), (int, float))], key=lambda a:a["height_agl_km"])
                for j, lv in enumerate(levels):
                    h = lv.get("height_agl_km", 0)
                    cc, rh = lv.get("cloud_cover"), lv.get("relative_humidity")
                    is_cloudy = (isinstance(cc, (int, float)) and cc >= 25) or (isinstance(rh, (int, float)) and rh >= 85)
                    if not is_cloudy:
                        continue
                    if j > 0:
                        h0 = (levels[j-1].get("height_agl_km", h)+h)/2
                    else:
                        h0 = max(0, h-0.35)
                    if j+1 < len(levels):
                        h1 = (h+levels[j+1].get("height_agl_km", h))/2
                    else:
                        h1 = min(max_h, h+0.35)
                    if h1 <= 0 or h0 > max_h:
                        continue
                    color = _cloud_height_color(h, cc, rh)
                    canvas.create_rectangle(x0, ymap(min(max_h,h1)), x1, ymap(max(0,h0)), fill=color, outline="#ffffff", width=0.5)
                # show model-estimated base as a red point; not direct observation
                if prof.get("estimated_base_km") is not None:
                    base = prof["estimated_base_km"]
                    if 0 <= base <= max_h:
                        canvas.create_oval(xmap(d)-3, ymap(base)-3, xmap(d)+3, ymap(base)+3, fill="#d83c3c", outline="white")
            # label the two heuristic boundaries, if they exist
            bounds = result.get("boundaries", {})
            for key, color, label, yoff in (("fire_cloud_range_km", "#0f766e", "火烧云范围", 10), ("cloud_edge_distance_km", "#b45309", "云边距", 26)):
                d = bounds.get(key)
                if d is not None:
                    x = xmap(d)
                    canvas.create_line(x, top, x, top+plot_h, fill=color, dash=(3, 3), width=1.5)
                    canvas.create_text(min(left+plot_w-5, max(left+5, x+4)), top+yoff, text=f"{label} {d}km", anchor="nw", fill=color, font=("Microsoft YaHei UI", 8))
            if not any((lv.get("cloudy_candidate") for p in profs for lv in p.get("levels", []))):
                canvas.create_text(left+plot_w/2, top+plot_h/2, text="剖面未识别到明显云层（云量/RH阈值）", fill="#64748b")
        canvas.bind("<Configure>", draw)
        win.after(80, draw)

    def show_direction_details(self, direction_key):
        """按需弹窗显示某一方向的详细云况，不在主界面常驻展开。"""
        result = getattr(self, "direction_results", {}).get(direction_key)
        if not result:
            messagebox.showinfo("尚无数据", "请先选择 ECMWF/GFS 模式并查询朝霞与晚霞。")
            return
        title = "朝霞采样点具体云况" if direction_key == "morning" else "晚霞采样点具体云况"
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("900x680")
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        text_box = tk.Text(frame, wrap="none", font=("Consolas", 9), padx=8, pady=8)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=text_box.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=text_box.xview)
        text_box.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        text_box.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text_box.insert("1.0", result["details"])
        text_box.configure(state="disabled")
        ttk.Button(frame, text="关闭", command=win.destroy).grid(row=2, column=0, sticky="e", pady=(8, 0))

    def _on_aod_edited(self, *_args):
        if self._setting_aod_programmatically:
            return
        # 用户在 AOD 框中修改数值后，后续预测优先采用手动值。
        self.aod_manual_override = bool(self.vars.get("aod") and self.vars["aod"].get().strip())
        if self.aod_manual_override:
            self.aod_status_var.set("已启用手动 AOD；点击“恢复网页 AOD”可取消覆盖")

    def _set_aod_field(self, value):
        self._setting_aod_programmatically = True
        try:
            self.vars["aod"].set("" if value is None else f"{value:.3f}")
        finally:
            self._setting_aod_programmatically = False

    def use_web_aod(self):
        if self.web_aod_value is None:
            messagebox.showinfo("提示", "当前还没有成功获取网页 AOD。请先点击“查询朝霞与晚霞”。")
            return
        self.aod_manual_override = False
        self._set_aod_field(self.web_aod_value)
        valid = f"；有效时刻 {self.web_aod_valid_time} UTC" if self.web_aod_valid_time else ""
        self.aod_status_var.set(
            f"网页默认：CAMS Global，AOD={self.web_aod_value:.3f}{valid}"
        )

    @staticmethod
    def _fetch_cloud_base_profiles(coords, distances, sunset_utc, model_id, local_ground_elevation_m=None):
        """读取沿日落方向中心采样点的气压层云量/RH/位势高度，推算最低可能云底。"""
        if model_id == "ecmwf_ifs025":
            levels = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]
        else:
            levels = [1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200]
        variables = []
        for level in levels:
            variables.extend([
                f"cloud_cover_{level}hPa",
                f"relative_humidity_{level}hPa",
                f"geopotential_height_{level}hPa",
            ])
        params = {
            "latitude": ",".join(f"{x[0]:.5f}" for x in coords),
            "longitude": ",".join(f"{x[1]:.5f}" for x in coords),
            "hourly": ",".join(variables),
            "models": model_id,
            "timezone": "UTC",
            "forecast_days": 16,
            "past_hours": 36,
            "timeformat": "iso8601",
        }
        url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "FireCloudForecast/0.9 (personal non-commercial tool)"})
        payload = request_json(req, timeout=18, cache_key=url)
        locations = payload if isinstance(payload, list) else [payload]
        if len(locations) != len(coords):
            raise ValueError(f"云底剖面接口返回 {len(locations)} 个位置，预期 {len(coords)} 个。")

        # 仅用于没有位势高度返回值时的近似回退；高度为海拔，不是离地高度。
        approx_height_asl_m = {
            1000: 110, 975: 320, 950: 500, 925: 800, 900: 1000,
            850: 1500, 800: 1900, 750: 2500, 700: 3000, 650: 3600,
            600: 4200, 550: 4900, 500: 5600, 450: 6300, 400: 7200,
            350: 8100, 300: 9200, 250: 10400, 200: 11800,
            150: 13500, 100: 15800, 50: 19300,
        }
        results = []
        for profile_index, (dist, coord, loc) in enumerate(zip(distances, coords, locations)):
            hourly = loc.get("hourly", {})
            times = hourly.get("time", [])
            if not times:
                results.append({"distance_km": dist, "estimated_base_km": None, "status": "接口无时间序列", "levels": []})
                continue
            idx = min(range(len(times)), key=lambda i: abs(
                (datetime.fromisoformat(times[i]) - sunset_utc).total_seconds()
            ))
            valid_dt = datetime.fromisoformat(times[idx])
            gap = abs((valid_dt - sunset_utc).total_seconds())
            if gap > 5400:
                results.append({"distance_km": dist, "estimated_base_km": None, "status": "无接近日落时刻的剖面", "levels": []})
                continue
            ground_elevation = loc.get("elevation")
            # 第一个点就是观测地点；优先采用用户在 GUI/Excel 中确认的海拔。
            if profile_index == 0 and isinstance(local_ground_elevation_m, (int, float)) and math.isfinite(float(local_ground_elevation_m)):
                ground_elevation = float(local_ground_elevation_m)
                elevation_note = f"用户地点海拔 {ground_elevation:.0f} m（用于本地云层高度换算）"
            elif not isinstance(ground_elevation, (int, float)) or not math.isfinite(ground_elevation):
                ground_elevation = 0.0
                elevation_note = "地形海拔缺失，按 0 m 近似"
            else:
                elevation_note = f"地形海拔 {ground_elevation:.0f} m"
            profile_levels = []
            for level in levels:
                def get_value(prefix):
                    series = hourly.get(f"{prefix}_{level}hPa", [])
                    value = series[idx] if idx < len(series) else None
                    return value if isinstance(value, (int, float)) and math.isfinite(value) else None
                height = get_value("geopotential_height")
                if height is None:
                    height = approx_height_asl_m.get(level)
                profile_levels.append({
                    "pressure_hpa": level,
                    "height_m_asl": height,
                    "cloud_cover": get_value("cloud_cover"),
                    "relative_humidity": get_value("relative_humidity"),
                })
            estimate = estimate_cloud_base_from_profile(profile_levels, ground_elevation)
            results.append({
                "distance_km": dist,
                "lat": coord[0], "lon": coord[1],
                "time": valid_dt.strftime("%Y-%m-%d %H:%M UTC"),
                "ground_elevation_m": ground_elevation,
                "elevation_note": elevation_note,
                **estimate,
            })
        return {"samples": results, "levels": levels}

    @staticmethod
    def _fetch_aod_path(coords, distances, sunset_utc):
        """获取 CAMS Global 550 nm AOD，按日落时刻取样并按距离加权。"""
        latitudes = ",".join(f"{x[0]:.5f}" for x in coords)
        longitudes = ",".join(f"{x[1]:.5f}" for x in coords)
        params = {
            "latitude": latitudes,
            "longitude": longitudes,
            "hourly": "aerosol_optical_depth",
            "domains": "cams_global",
            "timezone": "UTC",
            "forecast_days": 5,
            "timeformat": "iso8601",
        }
        url = "https://air-quality-api.open-meteo.com/v1/air-quality?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "FireCloudForecast/0.8"})
        payload = request_json(req, timeout=18, cache_key=url)
        locations = payload if isinstance(payload, list) else [payload]
        if len(locations) != len(coords):
            raise ValueError(f"CAMS AOD 返回 {len(locations)} 个采样点，预期 {len(coords)} 个。")

        samples = []
        weighted_pairs = []
        valid_times = []
        for dist, coord, loc in zip(distances, coords, locations):
            hourly = loc.get("hourly", {})
            times = hourly.get("time", [])
            values = hourly.get("aerosol_optical_depth", [])
            if not times or not values:
                samples.append({"distance_km": dist, "aod": None, "time": None})
                continue
            idx = min(range(len(times)), key=lambda i: abs(
                (datetime.fromisoformat(times[i]) - sunset_utc).total_seconds()
            ))
            valid_dt = datetime.fromisoformat(times[idx])
            gap = abs((valid_dt - sunset_utc).total_seconds())
            value = values[idx] if idx < len(values) else None
            # CAMS Global AOD 为 3 小时分辨率；超过 2 小时不使用，避免拿相差过远的时刻代替。
            if gap > 2 * 3600 or value is None:
                value = None
            elif isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                weight = 1.0 / (1.0 + dist / 100.0)
                weighted_pairs.append((float(value), weight))
                valid_times.append(valid_dt.strftime("%Y-%m-%d %H:%M"))
            else:
                value = None
            samples.append({"distance_km": dist, "aod": value,
                            "time": valid_dt.strftime("%Y-%m-%d %H:%M")})
        weighted_aod = (
            sum(v*w for v, w in weighted_pairs) / sum(w for _, w in weighted_pairs)
            if weighted_pairs else None
        )
        # CAMS 全球 AOD 通常为 3 小时数据；显示所采用的有效时刻中位值。
        valid_time = sorted(valid_times)[len(valid_times)//2] if valid_times else None
        return {"samples": samples, "weighted_aod": weighted_aod, "valid_time": valid_time}

    @staticmethod
    def _fetch_model_update_info(model_id):
        """读取 Open-Meteo 对应模型的静态元数据，显示起报时间和数据上线时间。"""
        # Open-Meteo 的模型更新时间不是 /v1/model-updates 查询接口；
        # 官方文档使用 /data/{model}/static/meta.json。
        candidate_ids = [model_id]
        if model_id == "ncep_gfs_global":
            candidate_ids.extend(["gfs_seamless", "ncep_gfs025"])
        elif model_id == "ecmwf_ifs025":
            candidate_ids.extend(["ecmwf_ifs", "ecmwf_ifs025_seamless"])

        payload = None
        last_error = None
        tried = set()
        for candidate_id in candidate_ids:
            if candidate_id in tried:
                continue
            tried.add(candidate_id)
            urls = [
                f"https://api.open-meteo.com/data/{candidate_id}/static/meta.json",
                f"https://customer-api.open-meteo.com/data/{candidate_id}/static/meta.json",
            ]
            for url in urls:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "FireCloudForecast/0.5.1"})
                    with urllib.request.urlopen(req, timeout=8) as response:
                        candidate_payload = json.loads(response.read().decode("utf-8"))
                    if isinstance(candidate_payload, dict) and any(
                        key in candidate_payload for key in (
                            "last_run_initialisation_time", "last_run_availability_time",
                            "last_run_modification_time"
                        )
                    ):
                        payload = candidate_payload
                        break
                    last_error = ValueError(f"元数据中没有更新时间字段：{candidate_id}")
                except Exception as exc:
                    last_error = exc
            if payload is not None:
                break

        if payload is None:
            return {
                "display": "数据更新时间：暂时无法读取",
                "detail": f"模型更新时间元数据读取失败（{last_error}）",
            }

        def format_epoch(value):
            try:
                dt = datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(timezone(timedelta(hours=8)))
                return dt.strftime("%Y-%m-%d %H:%M")
            except (TypeError, ValueError, OSError, OverflowError):
                return None

        init_time = format_epoch(payload.get("last_run_initialisation_time"))
        available_time = format_epoch(payload.get("last_run_availability_time"))
        modified_time = format_epoch(payload.get("last_run_modification_time"))

        # 用户关心的是这批预报数据何时能在 Open-Meteo 上使用，优先显示 availability。
        # modification 仅代表下载/转换完成，availability 缺失时才作为次选并明确标注。
        if available_time:
            display = f"数据更新时间：{available_time}（北京时间）"
        elif modified_time:
            display = f"数据处理完成：{modified_time}（北京时间）"
        elif init_time:
            display = f"模式起报时间：{init_time}（北京时间）"
        else:
            display = "数据更新时间：暂时无法读取"

        details = []
        if init_time:
            details.append(f"模式起报 {init_time}")
        if available_time:
            details.append(f"数据上线 {available_time}")
        if modified_time:
            details.append(f"下载/转换完成 {modified_time}")
        detail = "，".join(details) + "（北京时间）" if details else "模型更新时间字段不完整"
        return {
            "display": display,
            "detail": detail,
            "initialisation": init_time,
            "availability": available_time,
            "modification": modified_time,
        }

    @staticmethod
    def _fmt_pct(value):
        return "无数据" if value is None else f"{value:.0f}%"

    @staticmethod
    def _hex(rgb):
        return "#%02x%02x%02x" % tuple(int(clamp(c, 0, 255)) for c in rgb)

    def _draw_peak_color_swatch(self, direction_key):
        result = self.direction_results.get(direction_key, {})
        inferred = result.get("inferred", {}) if isinstance(result, dict) else {}
        canvas = getattr(self, f"{direction_key}_peak_swatch", None)
        if canvas is None:
            return
        canvas.delete("all")
        rgb = inferred.get("color_rgb", (230, 135, 85))
        fill = self._hex(rgb) if isinstance(rgb, (tuple, list)) and len(rgb) == 3 else "#e68755"
        canvas.create_rectangle(2, 2, 36, 28, fill=fill, outline="#cbd5e1", width=1)
        canvas.create_rectangle(3, 3, 35, 27, outline="")

    def _draw_colorbar(self, direction_key):
        """在朝霞/晚霞摘要下显示对应的预测色块与连续色彩倾向条。"""
        canvas = (self.morning_color_canvas if direction_key == "morning"
                  else self.evening_color_canvas)
        direction_result = self.direction_results.get(direction_key, {})
        result = direction_result.get("inferred")
        dynamic = direction_result.get("dynamic_light_curve", {}).get("samples", [])
        canvas.delete("all")
        width = max(canvas.winfo_width(), 380)
        canvas.configure(height=136)
        if dynamic:
            # 主图显示本次预测的相对亮度（峰值归一化为100）；原始指数仍保留在峰值标签和报告中。
            x0, x1, y0, y1 = 38, width-12, 25, 76
            canvas.create_text(8, 5, text="云色时序（不考虑云量）", anchor="nw",
                               fill="#243447", font=("Microsoft YaHei UI", 9, "bold"))
            def xx(i): return x0+(x1-x0)*i/max(1,len(dynamic)-1)
            def yy(v): return y1-(y1-y0)*max(0,min(100,float(v)))/100.0
            canvas.configure(background="white")
            for val in (0,20,40,60,80,100):
                y=yy(val); canvas.create_line(x0,y,x1,y,fill="#d9dee7",width=1)
                canvas.create_text(x0-7,y,text=str(val),anchor="e",fill="#475569",font=("Microsoft YaHei UI",7))
            # 白底图直接使用模型估算色，不做额外增艳/提亮，避免视觉效果偏离输入条件。
            for i in range(len(dynamic)-1):
                x_a,x_b=xx(i),xx(i+1)
                y_a,y_b=yy(dynamic[i]["relative_brightness"]),yy(dynamic[i+1]["relative_brightness"])
                c=tuple(max(0,min(255,int(v))) for v in dynamic[i].get("color",(190,125,105)))
                # 面积填色仅作轻量辅助；曲线线段使用原始估算 RGB。
                bg=(255,255,255)
                fill_rgb=tuple(int(bg[k]*0.08+c[k]*0.92) for k in range(3))
                fill_hex="#%02x%02x%02x"%fill_rgb
                line_hex="#%02x%02x%02x"%c
                canvas.create_polygon(x_a,y1,x_a,y_a,x_b,y_b,x_b,y1,
                                      fill=fill_hex,outline="",smooth=False)
                canvas.create_line(x_a,y_a,x_b,y_b,fill=line_hex,width=3)
            # 分段线因每分钟颜色不同，Tk Canvas 在色段接缝处偶尔会露出白色细缝；
            # 用同色小圆点覆盖接缝，保持曲线连续，不改变任何采样值。
            for i, point in enumerate(dynamic):
                px, py = xx(i), yy(point["relative_brightness"])
                c = tuple(max(0,min(255,int(v))) for v in point.get("color",(190,125,105)))
                hx = "#%02x%02x%02x" % c
                canvas.create_oval(px-1.6,py-1.6,px+1.6,py+1.6,fill=hx,outline=hx,width=0)
            # 标出日出/日落时刻，并显示横轴钟点刻度；刻度标签直接使用实际本地时间。
            event_i=min(range(len(dynamic)),key=lambda i:abs(dynamic[i].get("minutes_from_event",0)))
            ex=xx(event_i); canvas.create_line(ex,y0,ex,y1,fill="#64748b",dash=(2,3),width=1)
            span=dynamic[-1]["minutes_from_event"]-dynamic[0]["minutes_from_event"]
            target_ticks=6
            for tick in range(target_ticks+1):
                wanted=dynamic[0]["minutes_from_event"]+span*tick/target_ticks
                ti=min(range(len(dynamic)),key=lambda i:abs(dynamic[i]["minutes_from_event"]-wanted))
                tx=xx(ti)
                canvas.create_line(tx,y1,tx,y1+3,fill="#64748b",width=1)
                m=dynamic[ti]["minutes_from_event"]
                label=dynamic[ti]["datetime"].strftime("%H:%M")
                if abs(m)<0.51:
                    label += " 日出" if direction_key=="morning" else " 日落"
                canvas.create_text(tx,y1+5,text=label,anchor="n",fill="#334155",font=("Microsoft YaHei UI",7))
            peak=direction_result.get("dynamic_light_curve",{}).get("peak")
            if peak:
                pi=min(range(len(dynamic)),key=lambda i:abs((dynamic[i]["datetime"]-peak["datetime"]).total_seconds()))
                px,py=xx(pi),yy(peak["relative_brightness"])
                canvas.create_oval(px-3,py-3,px+3,py+3,fill="#ffffff",outline="#ffffff")
                peak_txt=f"相对峰值 {peak['relative_brightness']:.1f}/100 · 原始指数 {peak['brightness']:.1f} · {peak['datetime'].strftime('%H:%M')}"
                canvas.create_text(x0+3,126,text=peak_txt,anchor="w",fill="#334155",font=("Microsoft YaHei UI",8))
            return
        canvas.configure(background="white")
        if not dynamic:
            # 显示层兜底：当单次查询万里无云、没有可参与受光估算的云层时，
            # 不要退回“预测色块 + colorbar”布局；保留与正常情况一致的曲线坐标框。
            # 这里只画零亮度参考线和时间轴，不伪造动态采样，也不修改任何评分/天气算法。
            canvas.configure(background="white")
            x0, x1, y0, y1 = 38, width-12, 25, 76
            canvas.create_text(8, 5, text="云色时序（不考虑云量）", anchor="nw",
                               fill="#243447", font=("Microsoft YaHei UI", 9, "bold"))
            def empty_yy(v): return y1-(y1-y0)*max(0,min(100,float(v)))/100.0
            for val in (0,20,40,60,80,100):
                y=empty_yy(val)
                canvas.create_line(x0,y,x1,y,fill="#d9dee7",width=1)
                canvas.create_text(x0-7,y,text=str(val),anchor="e",fill="#475569",
                                   font=("Microsoft YaHei UI",7))
            baseline_y=empty_yy(0)
            canvas.create_line(x0,baseline_y,x1,baseline_y,fill="#94a3b8",width=2)
            # 有事件时间时显示对应时间刻度；没有则使用通用刻度，纯属空图显示。
            event_dt=direction_result.get("event_dt")
            if event_dt:
                is_morning = direction_key == "morning"
                start_dt=event_dt-timedelta(minutes=30)
                end_dt=event_dt+timedelta(minutes=30)
                for tick in range(7):
                    tx=x0+(x1-x0)*tick/6
                    tick_dt=start_dt+(end_dt-start_dt)*tick/6
                    canvas.create_line(tx,y1,tx,y1+3,fill="#64748b",width=1)
                    label=tick_dt.strftime("%H:%M")
                    if abs((tick_dt-event_dt).total_seconds()) <= 300:
                        label += " 日出" if is_morning else " 日落"
                    canvas.create_text(tx,y1+5,text=label,anchor="n",fill="#334155",
                                       font=("Microsoft YaHei UI",7))
            canvas.create_text((x0+x1)/2,103,text="当前无可参与动态受光估算的云层 · 相对亮度 0",
                               anchor="center",fill="#64748b",font=("Microsoft YaHei UI",9))
            canvas.create_text((x0+x1)/2,121,text="仅为显示占位，不代表新增预测采样",
                               anchor="center",fill="#94a3b8",font=("Microsoft YaHei UI",8))
            return

        if not result:
            canvas.create_text(8, 38, text="下方色条为颜色倾向；左侧峰值云色块表示主要受光云区的最鲜艳色相倾向",
                               anchor="w", fill="#5e6c84", font=("Microsoft YaHei UI", 9))
            return

        swatch_x0, swatch_x1 = 4, 90
        rgb = result.get("color_rgb", (230, 135, 85))
        canvas.create_rectangle(swatch_x0, 5, swatch_x1, 48,
                                fill=self._hex(rgb), outline="#d0d7de")
        canvas.create_text((swatch_x0 + swatch_x1) / 2, 62, text="预测色块",
                           fill="#243447", font=("Microsoft YaHei UI", 8))
        x0, x1, y0, y1 = 112, width - 14, 12, 39
        stops = [(0.0, (255, 238, 174)), (0.22, (255, 190, 83)),
                 (0.45, (255, 126, 42)), (0.67, (226, 65, 53)),
                 (0.84, (201, 66, 119)), (1.0, (112, 73, 151))]
        steps = max(80, int(x1 - x0))
        for i in range(steps):
            t = i / (steps - 1)
            for j in range(len(stops) - 1):
                if stops[j][0] <= t <= stops[j + 1][0]:
                    t0, c0 = stops[j]
                    t1, c1 = stops[j + 1]
                    f = (t - t0) / (t1 - t0)
                    color = tuple(round(c0[k] + (c1[k] - c0[k]) * f) for k in range(3))
                    break
            x = x0 + (x1 - x0) * i / steps
            canvas.create_rectangle(x, y0, x + (x1 - x0) / steps + 1, y1,
                                    fill=self._hex(color), outline="")
        marker_x = x0 + (x1 - x0) * clamp(result.get("color_pos", 0.45), 0, 1)
        canvas.create_line(marker_x, 5, marker_x, 45, fill="#202124", width=2)
        canvas.create_polygon(marker_x - 5, 5, marker_x + 5, 5, marker_x, 11,
                              fill="#202124", outline="#202124")
        canvas.create_text(x0, 52, text="淡黄白", anchor="w", fill="#243447",
                           font=("Microsoft YaHei UI", 8))
        canvas.create_text((x0 + x1) / 2, 52, text="金橙 · 橘红", anchor="center",
                           fill="#243447", font=("Microsoft YaHei UI", 8))
        canvas.create_text(x1, 52, text="洋红紫", anchor="e", fill="#243447",
                           font=("Microsoft YaHei UI", 8))
        canvas.create_text(x0, 70, text=result.get("color_name", "颜色倾向待估算"),
                           anchor="w", fill="#5e6c84", font=("Microsoft YaHei UI", 8))

    def _load_place_suggestion_records(self):
        """读取 Excel/旧 CSV 地名；返回 (name, lat, lon, elevation_m)。"""
        records = {}
        for name, lat, lon in list(self.CHINA_CAPITALS) + list(self.MAP_CITIES):
            records.setdefault(str(name), (str(name), float(lat), float(lon), None))
        base_dir = os.path.dirname(os.path.abspath(__file__))
        xlsx_path = os.path.join(base_dir, "china_city_coordinates.xlsx")
        csv_path = os.path.join(base_dir, "china_city_coordinates_common.csv")
        try:
            from openpyxl import load_workbook
            if os.path.isfile(xlsx_path):
                wb = load_workbook(xlsx_path, data_only=True, read_only=True)
                ws = wb["地名坐标表"] if "地名坐标表" in wb.sheetnames else wb.active
                rows = ws.iter_rows(values_only=True)
                headers = [str(v).strip() if v is not None else "" for v in next(rows, ())]
                def col(*names):
                    for candidate in names:
                        if candidate in headers:
                            return headers.index(candidate)
                    return None
                ni, lati, loni = col("地名", "城市名称", "name"), col("纬度", "lat"), col("经度", "lon", "longitude")
                ei = col("海拔（m）", "海拔(m)", "海拔", "elevation_m", "elevation")
                if ni is not None and lati is not None and loni is not None:
                    for row in rows:
                        if ni >= len(row) or not row[ni]:
                            continue
                        try:
                            lat, lon = float(row[lati]), float(row[loni])
                        except (TypeError, ValueError, IndexError):
                            continue
                        elev = None
                        if ei is not None and ei < len(row) and row[ei] not in (None, ""):
                            try: elev = float(row[ei])
                            except (TypeError, ValueError): pass
                        name = str(row[ni]).strip()
                        if name and -90 <= lat <= 90 and -180 <= lon <= 180:
                            records[name] = (name, lat, lon, elev)
                wb.close()
        except Exception:
            pass
        if os.path.isfile(csv_path):
            try:
                with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                    for row in csv.DictReader(f):
                        name = (row.get("地名") or row.get("城市名称") or row.get("name") or "").strip()
                        if not name: continue
                        try:
                            lat = float(row.get("纬度") or row.get("lat")); lon = float(row.get("经度") or row.get("lon"))
                        except (TypeError, ValueError): continue
                        eraw = row.get("海拔（m）") or row.get("海拔(m)") or row.get("海拔") or row.get("elevation_m")
                        try: elev = float(eraw) if eraw not in (None, "") else None
                        except (TypeError, ValueError): elev = None
                        if -90 <= lat <= 90 and -180 <= lon <= 180:
                            previous = records.get(name)
                            if elev is None and previous: elev = previous[3]
                            records[name] = (name, lat, lon, elev)
            except OSError:
                pass
        return list(records.values())

    def update_place_suggestions(self, event=None):
        """根据输入前缀显示本地 Excel/CSV 地名联想，不发起网络请求。"""
        if event is not None and event.keysym in ("Return", "Escape", "Up", "Down", "Left", "Right", "Tab"):
            if event.keysym == "Escape":
                self.place_suggestion_list.pack_forget()
            return
        query = self.vars.get("place").get().strip() if self.vars.get("place") else ""
        if len(query) < 1:
            self.place_suggestion_list.pack_forget()
            return
        q = self._normalize_city_name(query).casefold()
        records = self._load_place_suggestion_records()
        starts, contains = [], []
        for rec in records:
            name = rec[0]
            norm = self._normalize_city_name(name).casefold()
            if not norm: continue
            if norm.startswith(q) or name.casefold().startswith(query.casefold()):
                starts.append(rec)
            elif q in norm or query.casefold() in name.casefold():
                contains.append(rec)
        matches = starts + contains
        # 稳定去重并限制高度，避免超长地名表挤占设置面板。
        unique, seen = [], set()
        for rec in matches:
            if rec[0] not in seen:
                unique.append(rec); seen.add(rec[0])
            if len(unique) >= 8: break
        lb = self.place_suggestion_list
        lb.delete(0, "end")
        self._place_suggestion_records = unique
        for name, lat, lon, elev in unique:
            elev_text = f" · {elev:.0f}m" if elev is not None else " · 海拔未填"
            lb.insert("end", f"{name}  ({lat:.4f}, {lon:.4f}){elev_text}")
        if unique:
            lb.configure(height=min(6, len(unique)))
            if not lb.winfo_manager(): lb.pack(fill="x", pady=(2, 0), before=lb.master.winfo_children()[-1])
        else:
            lb.pack_forget()

    def focus_place_suggestions(self, event=None):
        lb = getattr(self, "place_suggestion_list", None)
        if lb is not None and lb.winfo_manager() and getattr(self, "_place_suggestion_records", []):
            lb.focus_set()
            lb.selection_clear(0, "end")
            lb.selection_set(0)
            lb.activate(0)
            return "break"
        return None

    def on_place_enter(self, event=None):
        lb = getattr(self, "place_suggestion_list", None)
        if lb is not None and lb.winfo_manager() and getattr(self, "_place_suggestion_records", []):
            selected = lb.curselection()
            idx = selected[0] if selected else 0
            self._apply_place_suggestion(idx)
            return "break"
        self.lookup_place_coordinates()
        return "break"

    def select_place_suggestion(self, event=None):
        lb = getattr(self, "place_suggestion_list", None)
        if lb is None: return
        selected = lb.curselection()
        if not selected and event is not None and getattr(event, "y", None) is not None:
            idx = lb.nearest(event.y)
        else:
            idx = selected[0] if selected else 0
        self._apply_place_suggestion(idx)
        return "break"

    def _apply_place_suggestion(self, idx):
        records = getattr(self, "_place_suggestion_records", [])
        if not 0 <= idx < len(records): return
        name = records[idx][0]
        self.vars["place"].set(name)
        self.place_suggestion_list.pack_forget()
        self.lookup_place_coordinates()

    @staticmethod
    def _normalize_city_name(value):
        """规范地名以匹配城市表，支持“安徽省合肥市”等常见写法。"""
        value = str(value or "").strip().replace(" ", "").replace("　", "")
        # 若输入完整省市串，剥掉省级前缀，保留末端城市名。
        value = re.sub(r"^.*?(?:省|自治区|特别行政区)", "", value)
        value = re.sub(r"^(?:中华人民共和国)", "", value)
        for suffix in ("市", "地区", "自治州", "自治县", "盟"):
            if value.endswith(suffix):
                value = value[:-len(suffix)]
                break
        return value

    def lookup_place_coordinates(self, _event=None):
        """优先从同目录 Excel 地名表读取地点、坐标和海拔；兼容旧 CSV。"""
        query = self.vars.get("place").get().strip() if self.vars.get("place") else ""
        if not query:
            return
        query_norm = self._normalize_city_name(query)
        records_by_name = {}
        for name, lat, lon in list(self.CHINA_CAPITALS) + list(self.MAP_CITIES):
            records_by_name.setdefault(name, (name, lat, lon, None))

        base_dir = os.path.dirname(os.path.abspath(__file__))
        xlsx_path = os.path.join(base_dir, "china_city_coordinates.xlsx")
        csv_path = os.path.join(base_dir, "china_city_coordinates_common.csv")
        try:
            from openpyxl import load_workbook
            if os.path.isfile(xlsx_path):
                wb = load_workbook(xlsx_path, data_only=True, read_only=True)
                ws = wb["地名坐标表"] if "地名坐标表" in wb.sheetnames else wb.active
                rows = ws.iter_rows(values_only=True)
                headers = [str(v).strip() if v is not None else "" for v in next(rows, ())]
                def col(*names):
                    for candidate in names:
                        if candidate in headers:
                            return headers.index(candidate)
                    return None
                ni = col("地名", "城市名称", "name")
                lati = col("纬度", "lat")
                loni = col("经度", "lon", "longitude")
                ei = col("海拔（m）", "海拔(m)", "海拔", "elevation_m", "elevation")
                if ni is not None and lati is not None and loni is not None:
                    for row in rows:
                        if ni >= len(row) or not row[ni]:
                            continue
                        try:
                            lat, lon = float(row[lati]), float(row[loni])
                        except (TypeError, ValueError, IndexError):
                            continue
                        elev = None
                        if ei is not None and ei < len(row) and row[ei] not in (None, ""):
                            try:
                                elev = float(row[ei])
                            except (TypeError, ValueError):
                                elev = None
                        name = str(row[ni]).strip()
                        if -90 <= lat <= 90 and -180 <= lon <= 180:
                            records_by_name[name] = (name, lat, lon, elev)
                wb.close()
        except Exception:
            pass

        # 兼容用户或旧版本仍在使用的 CSV 表。
        if os.path.isfile(csv_path):
            try:
                with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                    for row in csv.DictReader(f):
                        name = (row.get("地名") or row.get("城市名称") or row.get("name") or "").strip()
                        if not name:
                            continue
                        try:
                            lat = float(row.get("纬度") or row.get("lat"))
                            lon = float(row.get("经度") or row.get("lon"))
                        except (TypeError, ValueError):
                            continue
                        elev_raw = row.get("海拔（m）") or row.get("海拔(m)") or row.get("海拔") or row.get("elevation_m")
                        try:
                            elev = float(elev_raw) if elev_raw not in (None, "") else None
                        except (TypeError, ValueError):
                            elev = None
                        if -90 <= lat <= 90 and -180 <= lon <= 180:
                            # 旧 CSV 若没有海拔列，不得覆盖 Excel 中已维护的海拔值。
                            if elev is None and name in records_by_name:
                                elev = records_by_name[name][3]
                            records_by_name[name] = (name, lat, lon, elev)
            except OSError:
                pass

        records = list(records_by_name.values())
        exact = []
        for name, lat, lon, elev in records:
            if query_norm == self._normalize_city_name(name):
                exact.append((name, lat, lon, elev))
        if not exact:
            compact = re.sub(r"[省市区县旗盟州自治区特别行政区、,，/\\-]", "", query)
            for name, lat, lon, elev in records:
                norm = self._normalize_city_name(name)
                if compact.endswith(norm) and norm:
                    exact.append((name, lat, lon, elev))
        unique, seen = [], set()
        for rec in exact:
            if rec[0] not in seen:
                unique.append(rec); seen.add(rec[0])
        if not unique:
            # 没有匹配到 Excel 地名时，海拔默认回到 0 m，避免沿用上一个地点的高程。
            if "elevation" in self.vars:
                self.vars["elevation"].set("0")
            if hasattr(self, "place_lookup_status_var"):
                self.place_lookup_status_var.set(f"地名表未找到“{query}”；当前海拔按 0 m 默认值处理，坐标可手动输入或在 Excel 中添加地点。")
            return
        name, lat, lon, elev = unique[0]
        self.vars["lat"].set(f"{lat:.4f}")
        self.vars["lon"].set(f"{lon:.4f}")
        if "elevation" in self.vars:
            self.vars["elevation"].set("0" if elev is None else f"{elev:.0f}")
        if hasattr(self, "place_lookup_status_var"):
            elev_text = "Excel 未填写海拔，当前按 0 m 计算（可手动修改）" if elev is None else f"海拔 {elev:.0f} m"
            self.place_lookup_status_var.set(f"已匹配：{name} · 纬度 {lat:.4f}°，经度 {lon:.4f}° · {elev_text}")

    def reset_defaults(self):
        defaults = {
            "place": "合肥",
            "date": date.today().isoformat(),
            "lat": "31.8047",
            "lon": "117.2214",
            "elevation": "0",
            "weather_model": "ECMWF IFS 0.25°",
            "morning_cloud_base_km": "2.0",
            "evening_cloud_base_km": "2.0",
            "morning_aod": "",
            "evening_aod": "",
            "cloud_base_mode": "自动剖面推算",
        }
        for key, value in defaults.items():
            self.vars[key].set(value)
        self.last_fetch_time = None
        self.aod_manual_override = False
        self.morning_aod_status_var.set("AOD 留空时自动使用朝霞方向 CAMS 值")
        self.evening_aod_status_var.set("AOD 留空时自动使用晚霞方向 CAMS 值")

    def _read_inputs(self):
        try:
            parsed_date = datetime.strptime(self.vars["date"].get().strip(), "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("日期格式请使用 YYYY-MM-DD，例如 2026-10-23。")
        data = {
            "lat": parse_float(self.vars["lat"].get(), "纬度"),
            "lon": parse_float(self.vars["lon"].get(), "经度"),
            "elevation_m": (parse_float(self.vars["elevation"].get(), "海拔") if self.vars.get("elevation") and self.vars["elevation"].get().strip() else 0.0),
            # 当前版本以中国标准时间为默认；自动识别任意地点时区尚未实现。
            "utc_offset": 8.0,
            "cloud_base": 2500.0,
            "cloud_top": 5000.0,
            "edge_distance": 120.0,
            "aod": 0.15,
            "aerosol_height": 1000.0,
            "wind": 5.0,
            "sunset_speed": 18.0,
            "date": parsed_date,
            "place": self.vars["place"].get().strip() or "未命名地点",
            "cloud_type": "未知",
            "cloud_amount": "未知",
            "obstruction": "未知",
        }
        if not -90 <= data["lat"] <= 90:
            raise ValueError("纬度必须在 -90 到 90 之间。")
        if not -180 <= data["lon"] <= 180:
            raise ValueError("经度必须在 -180 到 180 之间。")
        return data

    def calculate(self):
        """兼容旧版内部调用；v0.5 的主流程只使用自动云况预报。"""
        messagebox.showinfo("提示", "请选择 ECMWF/GFS 模式，然后点击对应的‘查询朝霞’或‘查询晚霞’。")

    @staticmethod
    def _set_text(widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        # 仅突出摘要中的中云/高云字段，不改变数据与评分。
        try:
            import re
            widget.tag_configure("mid_high_cloud", foreground="#c62828", font=("Microsoft YaHei UI", 10, "bold"))
            widget.tag_configure("tintable_ratio", foreground="#111111", font=("Microsoft YaHei UI", 10, "bold"))
            widget.tag_configure("model_time", foreground="#111111", font=("Microsoft YaHei UI", 9, "bold"))
            for match in re.finditer(r"(?<!\S)(?:中|高)\s*\d+(?:\.\d+)?%", value):
                start = match.start()
                widget.tag_add("mid_high_cloud", f"1.0+{start}c", f"1.0+{match.end()}c")
            for match in re.finditer(r"可染色云量占比：[^\n]+", value):
                widget.tag_add("tintable_ratio", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
            for match in re.finditer(r"(?:模型数据上线|模型数据处理完成|模式起报时间|模型数据时间)：[^\n]+", value):
                widget.tag_add("model_time", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        except Exception:
            pass
        widget.configure(state="disabled")

    def _export_live_curve_comparison(self, source_xlsx, result, out_dir):
        """若输入是调试导出的 Excel，则把原单次曲线与实况模拟曲线逐分钟并排导出。"""
        try:
            from openpyxl import load_workbook, Workbook
            from openpyxl.styles import Font, PatternFill, Alignment
            from openpyxl.utils import get_column_letter
        except ImportError:
            return None
        try:
            src = load_workbook(source_xlsx, data_only=True, read_only=True)
            if "单次模式曲线" not in src.sheetnames:
                src.close()
                return None
            src_ws = src["单次模式曲线"]
            src_rows = list(src_ws.iter_rows(min_row=2, values_only=True))
            src.close()
            single = {}
            for row in src_rows:
                if not row or not row[0]:
                    continue
                key = str(row[0]).strip()[:19]
                single[key] = row
            dynamic = result.get("dynamic_light_curve", {}).get("samples", [])
            wb = Workbook()
            ws = wb.active
            ws.title = "曲线逐点对照"
            ws.append(["时间", "单次亮度", "实况亮度", "实况-单次", "单次太阳高度角", "实况太阳高度角",
                       "单次RGB", "实况RGB"])
            for point in dynamic:
                dt = point.get("datetime")
                if not isinstance(dt, datetime):
                    continue
                key = dt.strftime("%Y-%m-%d %H:%M:%S")
                original = single.get(key)
                single_b = original[4] if original and len(original) > 4 else None
                single_alt = original[2] if original and len(original) > 2 else None
                single_rgb = "" if not original or len(original) < 8 else f"{original[5]},{original[6]},{original[7]}"
                live_b = point.get("brightness")
                live_alt = point.get("solar_altitude_deg")
                color = point.get("color") or (0, 0, 0)
                live_rgb = f"{color[0]},{color[1]},{color[2]}"
                delta = (float(live_b) - float(single_b)) if isinstance(single_b, (int, float)) and isinstance(live_b, (int, float)) else None
                ws.append([key, single_b, live_b, delta, single_alt, live_alt, single_rgb, live_rgb])
            summary = wb.create_sheet("峰值摘要")
            summary.append(["项目", "单次模式", "实况模拟", "差异/说明"])
            single_peak = None
            if single:
                try:
                    best = max((r for r in single.values() if isinstance(r[4], (int, float))), key=lambda r:r[4])
                    single_peak = (best[4], str(best[0]))
                except Exception:
                    pass
            live_peak = result.get("dynamic_light_curve", {}).get("peak")
            live_peak_pair = (live_peak.get("brightness"), live_peak.get("datetime").strftime("%Y-%m-%d %H:%M:%S")) if live_peak else (None, None)
            summary.append(["峰值亮度", single_peak[0] if single_peak else None, live_peak_pair[0],
                            (round(float(live_peak_pair[0])-float(single_peak[0]), 2) if single_peak and live_peak_pair[0] is not None else "无法比较")])
            summary.append(["峰值时间", single_peak[1] if single_peak else None, live_peak_pair[1], "时间戳按秒对齐；若事件时刻有微秒，显示时已取整到秒"])
            summary.append(["曲线定义", "单次模式原始结果", "实况模拟重新计算", "输入层高按低/中/高云固定高度带近似映射；云层输入信息不同仍可能产生差异"])
            for sheet in wb.worksheets:
                sheet.freeze_panes = "A2"
                sheet.auto_filter.ref = sheet.dimensions
                for cell in sheet[1]:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="334155")
                    cell.alignment = Alignment(vertical="center", wrap_text=True)
                for col_idx in range(1, sheet.max_column+1):
                    vals = [str(sheet.cell(row=r, column=col_idx).value or "") for r in range(1, min(sheet.max_row, 80)+1)]
                    sheet.column_dimensions[get_column_letter(col_idx)].width = min(44, max(12, max((len(v) for v in vals), default=12)+2))
            out_path = os.path.join(out_dir, "动态曲线逐点对照.xlsx")
            wb.save(out_path)
            return out_path
        except Exception:
            # 对照表是调试附加产物，不应阻止正常实况模拟图生成。
            return None

    # ---------------- Excel 实况模拟 ----------------
    def run_live_simulation(self):
        """读取本地 Excel 实况云层区间，离线生成现场模拟图和剖面图。"""
        xlsx = filedialog.askopenfilename(
            title="选择实况模拟 Excel 文件", filetypes=[("Excel 工作簿", "*.xlsx"), ("所有文件", "*.*")]
        )
        if not xlsx:
            return
        try:
            data = self._read_live_simulation_excel(xlsx)
            result = self._calculate_live_simulation(data)
            out_dir = os.path.join(os.path.dirname(xlsx), f"实况模拟结果_{data['date'].isoformat()}_{data['direction_cn']}")
            os.makedirs(out_dir, exist_ok=True)
            scene_path = os.path.join(out_dir, "实况视觉模拟.jpg")
            profile_path = os.path.join(out_dir, "实况云层剖面.jpg")
            curve_path = os.path.join(out_dir, "动态亮度-色彩曲线.jpg")
            report_path = os.path.join(out_dir, "实况模拟分析.txt")
            self._draw_live_scene(data, result, scene_path)
            self._draw_live_profile(data, result, profile_path)
            self._draw_dynamic_light_curve(data, result, curve_path)
            palette_path = os.path.join(out_dir, "理想色彩与当前大气条件对照.jpg")
            self._draw_color_reference_palette(data, palette_path)
            with open(report_path, "w", encoding="utf-8-sig") as f:
                f.write(self._live_simulation_report(data, result, xlsx))
            comparison_path = self._export_live_curve_comparison(xlsx, result, out_dir)
            comparison_line = f"\n{comparison_path}" if comparison_path else "\n（输入文件没有‘单次模式曲线’工作表，未生成逐点对照表）"
            messagebox.showinfo(
                "实况模拟完成",
                f"方向：{data['direction_cn']}\n日期：{data['date']}\n发生概率估计：{result['inferred']['regular_score']}%\n有利情景上限：{result['inferred']['upper_score']}%\n相对亮度峰值：100/100；原始亮度指数：{result['inferred'].get('dynamic_peak_brightness', '—')}（{result['inferred'].get('dynamic_peak_time', '—')}）\n\n已生成：\n{scene_path}\n{profile_path}\n{curve_path}\n{palette_path}\n{report_path}"
            )
            try:
                import subprocess, sys
                if os.name == "nt":
                    os.startfile(out_dir)
            except Exception:
                pass
        except Exception as exc:
            messagebox.showerror("实况模拟失败", f"{exc}\n\n请检查 Excel 工作表名称、列名和数据格式。")

    @staticmethod
    def _parse_height_ranges(value):
        """解析例如“0~1000米，2000~6000米，8000~10000米”的多层高度。"""
        if value is None or not str(value).strip():
            return []
        text = str(value).replace("～", "~").replace("—", "~").replace("–", "~").replace("－", "~")
        # 兼容逗号、分号、顿号、换行分隔多个高度层。
        chunks = re.split(r"[,，;；、\n]+", text)
        out = []
        for chunk in chunks:
            chunk = chunk.strip().replace("米", "").replace("m", "").replace("M", "").replace(" ", "")
            if not chunk:
                continue
            m = re.fullmatch(r"(-?\d+(?:\.\d+)?)~(-?\d+(?:\.\d+)?)", chunk)
            if not m:
                raise ValueError(f"无法识别高度范围：{chunk}。请写成 0~1000米，多个高度层用逗号或分号分隔。")
            lo, hi = float(m.group(1)), float(m.group(2))
            if lo < 0 or hi <= lo:
                raise ValueError(f"高度范围无效：{chunk}；高度必须非负，且上限大于下限。")
            out.append((lo, hi))
        return out

    def _read_live_simulation_excel(self, path):
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise RuntimeError("读取 Excel 需要 openpyxl。请在运行本脚本的 Python 环境执行：python -m pip install openpyxl")
        wb = load_workbook(path, data_only=True, read_only=True)
        if "基本信息" not in wb.sheetnames or "云层区间" not in wb.sheetnames:
            raise ValueError("Excel 必须包含‘基本信息’和‘云层区间’两个工作表。请使用随版本提供的模板。")
        meta = {}
        info_ws = wb["基本信息"]
        info_rows = list(info_ws.iter_rows(values_only=True))
        # 标准模板采用“字段 | 值”逐行布局；同时兼容用户把字段名放在首行列标题、值放在第二行的布局。
        if info_rows:
            header = [str(v).strip() if v is not None else "" for v in info_rows[0]]
            if len(info_rows) > 1:
                for key, value in zip(header, info_rows[1]):
                    if key:
                        meta[key] = value
        for row in info_rows[1:]:
            if row and row[0] is not None:
                meta[str(row[0]).strip()] = row[1] if len(row) > 1 else None
        def get(key, default=None):
            v = meta.get(key, default)
            return default if v is None or str(v).strip() == "" else v
        direction_raw = str(get("类型", "晚霞")).strip()
        direction = "morning" if direction_raw in ("朝霞", "日出", "morning", "sunrise") else "evening" if direction_raw in ("晚霞", "日落", "evening", "sunset") else None
        if direction is None:
            raise ValueError("基本信息中的‘类型’请填写‘朝霞’或‘晚霞’。")
        try:
            dval = get("日期", date.today().isoformat())
            if isinstance(dval, datetime): dval = dval.date()
            if isinstance(dval, date): sim_date = dval
            else: sim_date = date.fromisoformat(str(dval).strip()[:10])
            lat, lon = float(get("纬度", 31.8047)), float(get("经度", 117.2214))
            elevation_m = float(get("海拔_m", get("观测点海拔_m", get("海拔（m）", get("海拔", 0.0)))))
            utc_offset = float(get("时区 UTC偏移", 8))
            aod_raw = get("AOD", None)
            aod = None if aod_raw is None else float(aod_raw)
            az_raw = get("太阳方位角_deg", None)
            event_azimuth = None if az_raw is None or str(az_raw).strip() == "" else float(az_raw)
        except Exception as exc:
            raise ValueError(f"基本信息中的日期、经纬度、时区或 AOD 格式不正确：{exc}")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180 or not -12 <= utc_offset <= 14:
            raise ValueError("经纬度或 UTC 时区偏移超出有效范围。")
        if not -500 <= elevation_m <= 9000:
            raise ValueError("海拔应在 -500 至 9000 米之间。")
        if aod is not None and aod < 0: raise ValueError("AOD 不能小于 0。")
        ws = wb["云层区间"]
        rows = list(ws.iter_rows(values_only=True))
        if not rows: raise ValueError("‘云层区间’工作表没有数据。")
        headers = [str(x).strip() if x is not None else "" for x in rows[0]]
        aliases = {
            "距离起点_km": ["距离起点_km", "起始距离(km)", "起始距离_km", "距离起点"],
            "距离终点_km": ["距离终点_km", "结束距离(km)", "结束距离_km", "距离终点"],
            "云量_percent": ["云量_percent", "云量(%)", "云量", "云量百分比"],
            "云高度范围_m": ["云高度范围_m", "云高度范围", "高度范围(m)", "高度范围"],
        }
        col = {}
        for target, opts in aliases.items():
            idx = next((headers.index(o) for o in opts if o in headers), None)
            if idx is None: raise ValueError(f"‘云层区间’缺少列：{target}。请勿修改模板的列名。")
            col[target] = idx
        zones = []
        for line_no, row in enumerate(rows[1:], start=2):
            if not row or all(v is None or str(v).strip()=="" for v in row): continue
            try:
                start = float(row[col["距离起点_km"]]); end = float(row[col["距离终点_km"]])
                cover = float(row[col["云量_percent"]]); layers = self._parse_height_ranges(row[col["云高度范围_m"]])
            except Exception as exc:
                raise ValueError(f"云层区间第 {line_no} 行数据错误：{exc}")
            if start < 0 or end <= start: raise ValueError(f"第 {line_no} 行距离区间无效：终点必须大于起点，且距离不能为负。")
            if not 0 <= cover <= 100: raise ValueError(f"第 {line_no} 行云量必须在 0~100% 之间。")
            if not layers and cover > 0:
                raise ValueError(f"第 {line_no} 行云量大于 0%，必须至少填写一个高度范围；若该段明确无云，请将云量填 0%，高度范围留空。")
            zones.append({"start":start, "end":end, "cover":cover, "layers":layers, "row":line_no})
        event_raw = get("事件时刻", None)
        event_datetime = None
        if event_raw is not None:
            try:
                event_datetime = datetime.fromisoformat(str(event_raw).strip())
            except Exception:
                raise ValueError("基本信息中的‘事件时刻’格式不正确，请使用 YYYY-MM-DD HH:MM:SS；也可留空使用程序计算的日出/日落时刻。")
        wb.close()
        if not zones: raise ValueError("至少需要填写一条云层区间记录。")
        zones.sort(key=lambda z:(z["start"], z["end"], min((lo for lo,hi in z["layers"]), default=-1)))
        for i,z in enumerate(zones):
            if i and z["start"] < zones[i-1]["end"] - 1e-6:
                prev = zones[i-1]
                same_distance_band = abs(z["start"]-prev["start"]) <= 1e-6 and abs(z["end"]-prev["end"]) <= 1e-6
                vertical_overlap = any(max(a0,b0) < min(a1,b1)-1e-6
                                       for a0,a1 in z["layers"] for b0,b1 in prev["layers"])
                # Debug Excel 可在同一距离区间用多行分别表达低/中/高云量；仅允许完全相同距离段且高度层不重叠。
                if not (same_distance_band and not vertical_overlap):
                    raise ValueError(f"距离区间重叠：第 {prev['row']} 行与第 {z['row']} 行。只有完全相同距离段且高度层互不重叠时，才允许分多行填写不同云层。")
            if i and z["start"] > zones[i-1]["end"] + 1e-6:
                raise ValueError(f"距离区间不连续：第 {zones[i-1]['row']} 行结束于 {zones[i-1]['end']:g} km，第 {z['row']} 行从 {z['start']:g} km 开始。请补上中间区间；明确无云时填云量 0%。")
        if zones[0]["start"] > 1e-6:
            raise ValueError("距离区间必须从 0 km 开始；请补上 0 到第一个区间起点的记录。")
        return {"direction":direction, "direction_cn":"朝霞" if direction=="morning" else "晚霞", "date":sim_date,
                "place":str(get("地点", "未命名地点")), "lat":lat, "lon":lon, "elevation_m":elevation_m,
                "utc_offset":utc_offset, "aod":aod, "visibility_km":(float(get("能见度_km")) if get("能见度_km", None) is not None else None),
                "event_datetime":event_datetime, "event_azimuth":event_azimuth, "zones":zones, "source_excel":path}

    @staticmethod
    def _analyze_solar_path_barriers(zones):
        """旧版静态屏障判定停用。

        旧逻辑只扫描 0/1/2°，并把远处同高度带的云区相交直接换算成固定扣分，
        会把连续高云误判为强屏障。v1.4.25 将时间变化的遮挡交给动态光路曲线评估；
        此处不再仅凭高云高度带相交扣分。低云约束仍由独立的
        apply_low_cloud_screen_constraint 处理。
        """
        return {
            "penalty": 0.0,
            "upper_penalty": 0.0,
            "severity": "改用动态光路评估",
            "barriers": [],
            "note": ("已停用旧版静态高云屏障扣分：它仅扫描 0°/1°/2°，并可能把同一连续高云层的几何相交误判为强屏障。"
                     "本版使用逐分钟太阳高度角、云层高度与距离估算动态受光；低云硬约束仍单独保留。"
                     "云量不是云光学厚度，动态曲线属于经验几何估算，不是三维云体或辐射传输模拟。")
        }

    @staticmethod
    def _dynamic_cloud_color(solar_altitude_deg):
        """根据太阳高度角生成基础色相；大气灰化由独立的 atmospheric_color_quality 控制。

        注意：这里不再把太阳仍在地平线上方直接画成灰色。太阳高度角主要控制暖色到红色的色相变化，
        而空气浑浊度由能见度/AOD/可用湿度数据另行控制饱和度。
        """
        def blend(a, b, t):
            t=max(0.0,min(1.0,float(t)))
            return tuple(int(round(a[i]*(1-t)+b[i]*t)) for i in range(3))
        # 基础色带采用可辨识的暖色，避免先天低饱和导致曲线发灰。
        # 这是太阳高度对应的参考色相，不代表每次实景必然出现这些 RGB。
        pale_gold=(255,202,105)
        warm_orange=(255,126,48)
        red=(245,48,35)
        dark_red=(132,17,31)
        night=(35,22,39)
        alt=float(solar_altitude_deg)
        if alt >= 0.5:
            return pale_gold
        if alt >= -0.5:
            return blend(pale_gold, warm_orange, (0.5-alt)/1.0)
        if alt >= -2.0:
            return blend(warm_orange, red, (-0.5-alt)/1.5)
        if alt >= -4.5:
            return blend(red, dark_red, (-2.0-alt)/2.5)
        return blend(dark_red, night, min(1.0,(-4.5-alt)/3.0))

    def _build_dynamic_light_curve(self, data, zones, sun_event, single_mode=False):
        """按分钟扫描日出/日落前后动态受光，输出 0–100 亮度指数与示意色。

        单次单模式与 Excel 实况模拟共用同一套计算：球形地球地平线几何、距离权重、受光比例、
        最强异层遮挡衰减，以及能见度/AOD 修正。两种模式的输入云区/高度层来源不同，但算法定义一致。
        亮度是相对指数，不是实测辐射亮度。
        """
        if not sun_event or not sun_event[0]:
            return {"samples": [], "peak": None, "note": "无法计算日出/日落时刻，未生成动态曲线。"}
        event_dt=sun_event[0]
        is_morning=data.get("direction")=="morning"
        # 朝霞、晚霞统一只展示太阳事件前后各 30 分钟，共 1 小时。
        start_dt=event_dt-timedelta(minutes=30)
        end_dt=event_dt+timedelta(minutes=30)
        earth_r=6371.0088
        cloud_layers=[]
        # 即使 Excel 是 0–200 km 一个宽区间，也要在内部细分距离采样，
        # 否则只用区间中点会漏掉更远高云在日落后仍被照亮的时间窗口。
        spatial_step_km=5.0
        for zi,z in enumerate(zones):
            width=max(0.1,z["end"]-z["start"])
            n=max(1,int(math.ceil(width/spatial_step_km)))
            for seg_i in range(n):
                seg_start=z["start"]+width*seg_i/n
                seg_end=z["start"]+width*(seg_i+1)/n
                dist=(seg_start+seg_end)/2.0
                seg_width=max(0.1,seg_end-seg_start)
                for lo,hi in z["layers"]:
                    if hi <= 1500 or hi <= lo:
                        continue
                    mid=(lo+hi)/2000.0
                    # 单次单模式与实况模拟使用完全相同的距离权重，避免同一类曲线因入口模式不同而改变定义。
                    # 采用实况模拟已使用的 1/(1+d/350)；这是经验权重，不是严格辐射传输或视角立体角模型。
                    spatial_weight = 1.0/(1.0+dist/350.0)
                    weight=seg_width*max(0.25,(hi-lo)/1000.0)*spatial_weight
                    cloud_layers.append({"zone_index":zi,"zone":z,"dist":dist,
                                         "segment_start":seg_start,"segment_end":seg_end,
                                         "lo_km":lo/1000.0,"hi_km":hi/1000.0,
                                         "mid_km":mid,"weight":weight,"cover":z["cover"]})
        total_weight=sum(x["weight"] for x in cloud_layers)
        if total_weight <= 0:
            return {"samples": [], "peak": None, "note": "没有可参与动态受光估算的 1.5 km 以上云层。"}
        vis=data.get("visibility_km")
        # 缺少能见度数据时不主动假定空气浑浊；否则会无依据地把所有曲线压灰。
        visibility_factor=1.0 if vis is None else max(0.35,min(1.0,0.35+0.65*min(float(vis),30.0)/30.0))
        aod=data.get("aod")
        # AOD 对亮度仍做温和消光；颜色灰化另用独立的 atmospheric_color_quality，高 AOD 下采用强非线性去饱和。
        aod_factor=math.exp(-0.35*max(0.0,float(aod))) if aod is not None else 1.0
        if vis is None:
            visibility_color_quality=1.0
        else:
            # 能见度 30 km 及以上近似不灰化；低能见度才逐渐降低饱和度。
            visibility_color_quality=max(0.0,min(1.0,(float(vis)-2.0)/28.0))
        aod_color_quality=aod_color_quality_factor(aod)
        atmospheric_color_quality=max(0.0,min(1.0,visibility_color_quality*aod_color_quality))
        # 如果输入中恰有近地相对湿度，则只作为温和灰化因素；缺测不推断水汽含量。
        rh=data.get("relative_humidity", data.get("humidity"))
        if rh is not None:
            try:
                rh=float(rh)
                atmospheric_color_quality *= max(0.78,min(1.0,1.0-max(0.0,rh-70.0)*0.007))
            except (TypeError, ValueError):
                pass
        atmospheric_color_quality=max(0.0,min(1.0,atmospheric_color_quality))
        samples=[]
        count=int((end_dt-start_dt).total_seconds()//60)
        for minute in range(count+1):
            dt=start_dt+timedelta(minutes=minute)
            solar_alt,solar_az=solar_position(dt,data["utc_offset"],data["lat"],data["lon"])
            weighted_light=0.0
            color_weight=0.0
            transmitted_color_weight=0.0
            # 分母只统计当前太阳几何条件下“有机会被直射照亮”的云层权重。
            # 旧版把此刻已处于地球阴影中的中低云也放进固定分母，导致高云仍受光时
            # 亮度被大量不发光云层稀释，出现“峰值仅 16/100”的假低值。
            eligible_weight=0.0
            for target in cloud_layers:
                z=target["zone"]
                dist=target["dist"]
                # 目标位于日落/日出方向时，局地地平线相对观测点旋转约 d/R。
                local_alt=solar_alt+math.degrees(dist/earth_r)
                base_h=target["lo_km"]; top_h=target["hi_km"]
                def shadow_threshold(h_km):
                    return -math.degrees(math.acos(max(-1.0,min(1.0,earth_r/(earth_r+max(0.0,h_km))))))
                base_thr=shadow_threshold(base_h)
                top_thr=shadow_threshold(top_h)
                if local_alt >= base_thr:
                    lit_fraction=1.0
                elif local_alt <= top_thr:
                    lit_fraction=0.0
                else:
                    denom=max(1e-6,base_thr-top_thr)
                    lit_fraction=max(0.0,min(1.0,(local_alt-top_thr)/denom))
                if lit_fraction <= 0 or target["cover"] <= 0:
                    continue
                # 分母按“实际受光比例”计权：仅有极薄边缘进入阳光的云层，
                # 不能像整层完全受光一样贡献完整分母，否则日出/日落附近会被人为压低峰值。
                eligible_weight += target["weight"] * lit_fraction
                # 检查太阳方向更远处的“不同高度层”是否与从目标云层延伸出去的光线相交。
                # 同一高度带的连续高云不再被当作硬屏障，避免旧版误判；异层相交只做渐进衰减。
                attenuation=1.0
                target_band=(target["lo_km"],target["hi_km"])
                target_alt=solar_alt+math.degrees(dist/earth_r)
                # 关键修正：cloud_layers 按 5 km 细分，一个宽云区会产生许多相邻采样段。
                # 旧逻辑把这些同属一个 Excel 距离区间的采样段重复当成独立遮挡物相乘，
                # 轻易把亮度压到个位数/十几。这里先按原始云区聚合，每个区间最多扣减一次，
                # 同一区间不同高度层只保留最强的有效遮挡，避免“重复计数”。
                zone_opacity={}
                for blocker in cloud_layers:
                    if blocker["zone_index"] == target["zone_index"]:
                        continue
                    if blocker["dist"] <= dist+2.5:
                        continue
                    overlap=max(0.0,min(target_band[1],blocker["hi_km"])-max(target_band[0],blocker["lo_km"]))
                    target_thick=max(0.1,target_band[1]-target_band[0])
                    # 高度带大幅重叠通常是同一片连续高云，不把它再次认定为独立屏障。
                    if overlap/target_thick >= 0.60:
                        continue
                    dx=max(0.0,blocker["dist"]-dist)
                    if dx <= 1.0:
                        continue
                    ray_h=target["mid_km"]+dx*math.tan(math.radians(target_alt))+dx*dx/(2.0*earth_r)
                    # 将云层边界与 20% 云量门槛做渐进过渡，避免一跨过硬阈值亮度就跳变。
                    if ray_h < blocker["lo_km"]:
                        vertical_gap=blocker["lo_km"]-ray_h
                    elif ray_h > blocker["hi_km"]:
                        vertical_gap=ray_h-blocker["hi_km"]
                    else:
                        vertical_gap=0.0
                    vertical_factor=max(0.0,min(1.0,1.0-vertical_gap/0.75))
                    cover_factor=max(0.0,min(1.0,(float(blocker["cover"])-8.0)/32.0))
                    if vertical_factor > 0.0 and cover_factor > 0.0:
                        opacity=min(0.70,cover_factor*0.65*vertical_factor)
                        zi=blocker["zone_index"]
                        zone_opacity[zi]=max(zone_opacity.get(zi,0.0),opacity)
                # 这些区间是沿同一观测方向的简化采样，不是独立、可无限相乘的真实遮光片。
                # 旧版将多个区间的 opacity 连乘，容易让普通高云层被重复衰减到 16/100 甚至更低。
                # 动态曲线只对最强的一个异层遮挡做有限衰减；严格的低云屏障仍由独立评分规则处理。
                strongest_opacity=max(zone_opacity.values(), default=0.0)
                # 两种模式统一采用实况模拟原有的有限异层遮挡衰减，避免同一曲线在不同入口下
                # 一边考虑光路遮挡、一边完全忽略遮挡。只取最强一处，不对多个采样区间连乘。
                attenuation = max(0.55, 1.0-strongest_opacity)
                contribution=target["weight"]*(target["cover"]/100.0)*lit_fraction
                weighted_light += contribution*attenuation
                color_weight += contribution
                transmitted_color_weight += contribution*attenuation
            # 亮度定义为“当前可受光云层中的受光覆盖指数”，而不是所有高度层的平均值。
            # 这样太阳落低后，未受光的低层/中层不会把仍被照亮的高云峰值压得过低。
            raw_brightness=100.0*weighted_light/max(1e-9,eligible_weight)
            brightness=max(0.0,min(100.0,raw_brightness*visibility_factor*aod_factor))
            base_color=self._dynamic_cloud_color(solar_alt)
            # 分清三个概念：太阳高度决定基础色相；云层受光/遮挡主要影响亮度；
            # 灰化只由能见度、AOD，以及确实存在的湿度输入影响。不能把云量遮挡直接当成雾霾。
            transmission=(transmitted_color_weight/max(1e-9,color_weight)) if color_weight>0 else 0.0
            air_transmission=max(0.0,min(1.0,visibility_factor*aod_factor))
            color_quality=atmospheric_color_quality
            # 颜色只由太阳高度角的基础色相 + 大气灰化共同决定。
            # 云层遮挡/受光不足只反映在亮度纵轴，不再把曲线颜色额外压灰或压暗。
            # 即使当前没有有效受光云层，也保留该时刻的大气色彩估计，避免用灰色代替遮挡。
            r,g,b=[v/255.0 for v in base_color]
            h,l,sat=colorsys.rgb_to_hls(r,g,b)
            # AOD 色彩衰减采用分段非线性曲线：AOD=0.8 时接近中性灰；低能见度和实际输入的高湿度仍可进一步降低饱和度。
            sat *= 0.08+0.92*color_quality
            rr,gg,bb=colorsys.hls_to_rgb(h,max(0.0,min(1.0,l)),max(0.0,min(1.0,sat)))
            color=(int(round(rr*255)),int(round(gg*255)),int(round(bb*255)))
            samples.append({"datetime":dt,"minutes_from_event":(dt-event_dt).total_seconds()/60.0,
                            "solar_altitude_deg":solar_alt,"solar_azimuth_deg":solar_az,
                            # brightness 保留原始经验指数，供颜色和跨日期条件比较使用。
                            "brightness":round(brightness,1),"color":color,
                            "optical_transmission":round(color_quality,3),
                            "atmospheric_color_quality":round(atmospheric_color_quality,3),
                            "path_transmission":round(transmission,3),
                            "air_transmission":round(air_transmission,3)})
        # 新增本次预测内部的相对亮度：本时窗峰值=100，其余时刻按原始亮度比例缩放。
        # 原始 brightness 不覆盖，避免不同日期的绝对条件强弱信息丢失。
        raw_peak=max((float(x["brightness"]) for x in samples), default=0.0)
        for point in samples:
            point["relative_brightness"] = round(
                100.0 * float(point["brightness"]) / raw_peak, 1
            ) if raw_peak > 0 else 0.0
        peak=max(samples,key=lambda x:x["brightness"]) if samples else None
        return {"samples":samples,"peak":peak,"raw_peak_brightness":round(raw_peak,1),
                "note":"逐分钟估算；brightness 是保留的原始受光覆盖经验指数，relative_brightness 是本次预测窗口内归一化的相对亮度（窗口峰值=100）。相对亮度适合观察同一次预测内的起伏，不可用于比较不同日期的绝对强弱。原始指数结合受光几何、有限的最强异层光路衰减及 AOD/能见度修正；色彩灰化与亮度衰减分开处理。它不是实测辐射亮度或真实色度预测。"}

    def _draw_color_reference_palette(self, data, path):
        """输出理想通透空气参考色与当前大气修正色，便于检查灰化是否过度。"""
        if Image is None:
            raise RuntimeError("生成色彩参考图需要 Pillow：python -m pip install pillow")
        from PIL import ImageDraw
        W,H=1500,620
        img=Image.new("RGB",(W,H),(248,249,251)); d=ImageDraw.Draw(img)
        title=self._jpg_font(34,True); label=self._jpg_font(23); small=self._jpg_font(18)
        d.text((55,30),"火烧云色彩参考｜理想空气 vs 当前大气条件",font=title,fill=(30,41,59))
        aod=data.get("aod"); vis=data.get("visibility_km")
        rh=data.get("relative_humidity",data.get("humidity"))
        d.text((58,82),f"AOD：{'缺测' if aod is None else f'{float(aod):.3f}'}    能见度：{'缺测' if vis is None else f'{float(vis):.1f} km'}    相对湿度：{'缺测' if rh is None else f'{float(rh):.0f}%'}",font=label,fill=(71,85,105))
        vis_q=1.0 if vis is None else max(0.0,min(1.0,(float(vis)-2.0)/28.0))
        aod_q=aod_color_quality_factor(aod)
        quality=max(0.0,min(1.0,vis_q*aod_q))
        if rh is not None:
            try: quality *= max(0.78,min(1.0,1.0-max(0.0,float(rh)-70.0)*0.007))
            except (TypeError,ValueError): pass
        d.text((58,140),"理想参考色：高透明度、低 AOD、无额外湿度惩罚（用于检查基础色带）",font=label,fill=(55,65,81))
        d.text((58,330),"当前条件色：按本次输入的 AOD / 能见度 / 可用湿度修正饱和度",font=label,fill=(55,65,81))
        swatches=[(0.5,"太阳高度 ≥ +0.5°"),(-0.5,"约 -0.5°"),(-2.0,"约 -2°"),(-4.5,"约 -4.5°")]
        margin=58; gap=18; sw=(W-2*margin-3*gap)//4
        for i,(alt,caption) in enumerate(swatches):
            x=margin+i*(sw+gap); ideal=self._dynamic_cloud_color(alt)
            rr,gg,bb=[v/255.0 for v in ideal]; hh,ll,ss=colorsys.rgb_to_hls(rr,gg,bb)
            rr,gg,bb=colorsys.hls_to_rgb(hh,ll,ss*(0.08+0.92*quality))
            current=(int(round(rr*255)),int(round(gg*255)),int(round(bb*255)))
            d.rounded_rectangle((x,180,x+sw,280),radius=10,fill=ideal)
            d.rounded_rectangle((x,370,x+sw,470),radius=10,fill=current)
            d.text((x+3,292),caption,font=small,fill=(55,65,81))
            d.text((x+3,482),f"RGB {ideal[0]},{ideal[1]},{ideal[2]}",font=small,fill=(55,65,81))
            d.text((x+3,512),f"RGB {current[0]},{current[1]},{current[2]}",font=small,fill=(55,65,81))
        d.text((58,570),f"本次大气色彩保留系数：{quality:.2f}。此图用于算法调试，不是对真实云彩 RGB 的精确预报。",font=small,fill=(71,85,105))
        img.save(path,"JPEG",quality=95,subsampling=0,optimize=True)

    def _draw_dynamic_light_curve(self, data, result, path):
        """绘制时间-亮度二维曲线，线段颜色随估算色相由灰暖色转红、暗红。"""
        if Image is None:
            raise RuntimeError("生成动态曲线需要 Pillow：python -m pip install pillow")
        from PIL import ImageDraw
        samples=result.get("dynamic_light_curve",{}).get("samples",[])
        if not samples:
            raise RuntimeError("当前案例无法生成动态亮度曲线：缺少太阳时刻或可用高云层。")
        W,H=1800,1000
        img=Image.new("RGB",(W,H),(255,255,255)); d=ImageDraw.Draw(img)
        title_font=self._jpg_font(32,True); label_font=self._jpg_font(20); small_font=self._jpg_font(16)
        d.text((55,25),f"{data['place']}｜{data['date']}｜{data['direction_cn']} 动态亮度-色彩曲线",font=title_font,fill=(30,41,59))
        d.text((58,72),"横轴：相对日出/日落时刻（分钟）｜纵轴：本次预测相对亮度（峰值=100）｜颜色按原始受光指数示意：金黄/橙红 → 红 → 暗红",font=label_font,fill=(71,85,105))
        left,right,top,bottom=130,1720,145,850
        def X(i): return left+(right-left)*i/max(1,len(samples)-1)
        def Y(v): return bottom-(bottom-top)*max(0,min(100,v))/100.0
        # 横向网格及坐标标注
        for v in range(0,101,20):
            yy=Y(v); d.line((left,yy,right,yy),fill=(220,226,234),width=1)
            d.text((left-18,yy),str(v),font=label_font,fill=(71,85,105),anchor="rm")
        d.line((left,top,left,bottom),fill=(100,116,139),width=2)
        d.line((left,bottom,right,bottom),fill=(100,116,139),width=2)
        # 白底导出图直接使用模型估算 RGB，不做饱和度/明度增强，避免人为美化色彩。
        for i in range(len(samples)-1):
            x0,x1=X(i),X(i+1); y0,y1=Y(samples[i]["relative_brightness"]),Y(samples[i+1]["relative_brightness"])
            c=tuple(max(0,min(255,int(v))) for v in samples[i].get("color",(190,125,105)))
            fill=tuple(int(255*0.08+c[k]*0.92) for k in range(3))
            d.polygon([(x0,bottom),(x0,y0),(x1,y1),(x1,bottom)],fill=fill)
            d.line((x0,y0,x1,y1),fill=c,width=5)
        # 不同颜色的逐分钟线段在像素边界可能出现细小白缝；覆盖接点以保证视觉连续。
        for i, sample in enumerate(samples):
            px, py = X(i), Y(sample["relative_brightness"])
            c = tuple(max(0,min(255,int(v))) for v in sample.get("color",(190,125,105)))
            d.ellipse((px-2,py-2,px+2,py+2),fill=c)
        # 连续色带同样使用模型估算色，不做增强；曲线下方面积仅做 8% 白色混合，避免大面积浅色填充造成整体发灰的视觉错觉。
        band_top,band_bottom=890,914
        for i,sample in enumerate(samples):
            xa=int(X(i)); xb=int(X(i+1)) if i+1<len(samples) else right
            if xb<=xa: xb=xa+1
            c=tuple(max(0,min(255,int(v))) for v in sample.get("color",(190,125,105)))
            d.rectangle((xa,band_top,xb,band_bottom),fill=c)
        d.text((left,band_bottom+8),"时刻色彩（模型估算 RGB；遮挡与空气透明度会影响色彩）",font=small_font,fill=(71,85,105))
        # 横轴使用实际本地钟点，首尾和事件时刻均标注；其他刻度约每 10 分钟显示。
        for i,sample in enumerate(samples):
            m=sample["minutes_from_event"]
            is_event=abs(m)<0.51
            if i not in (0,len(samples)-1) and not is_event and abs(m/10-round(m/10))>0.03:
                continue
            xx=X(i); d.line((xx,bottom,xx,bottom+8),fill=(170,179,194),width=1)
            label=sample["datetime"].strftime("%H:%M")
            if is_event: label += " 日出" if data.get("direction")=="morning" else " 日落"
            d.text((xx,bottom+18),label,font=small_font,fill=(71,85,105),anchor="mt")
        # 峰值点和说明
        peak=result.get("dynamic_light_curve",{}).get("peak")
        if peak:
            pi=min(range(len(samples)),key=lambda i:abs((samples[i]["datetime"]-peak["datetime"]).total_seconds()))
            px,py=X(pi),Y(peak["relative_brightness"])
            d.ellipse((px-7,py-7,px+7,py+7),fill=peak["color"],outline=(255,255,255),width=2)
            peak_label=f"相对峰值 {peak['relative_brightness']:.1f}/100｜原始指数 {peak['brightness']:.1f}｜{peak['datetime'].strftime('%H:%M')}｜太阳高度 {peak['solar_altitude_deg']:.2f}°"
            d.rounded_rectangle((left+20,top+15,left+710,top+62),radius=10,fill=(245,247,250),outline=(203,213,225),width=1)
            d.text((left+35,top+26),peak_label,font=label_font,fill=(30,41,59))
        d.text((W/2,950),"本地时间（HH:MM）",font=label_font,fill=(51,65,85),anchor="mt")
        d.text((25,115),"相对亮度",font=label_font,fill=(51,65,85))
        img.save(path,"JPEG",quality=95,subsampling=0,optimize=True)

    @staticmethod
    def _filter_live_zones_for_observer_elevation(zones, elevation_m, radius_km=80.0):
        """按“先验证云洞、再决定是否启用”的规则处理高海拔机位。

        所有云层高度均为绝对海拔 ASL。规则：
        1) 海拔为0，或本地0–80 km参考云底高于机位：完整沿用原算法；
        2) 若机位高于本地参考云底，先用0–80 km内高于机位的云层重估新云底和理论极限云距；
        3) 仅当新极限云距内、80 km以外存在明确的宽云洞（云量<=15%、有效宽度>=15 km，且路径上云洞两侧均有云区证据），
           才正式移除所有低于机位的云层并重新评分；否则回退原始0 m逻辑。
        """
        try:
            elevation_m = float(elevation_m or 0.0)
        except (TypeError, ValueError):
            elevation_m = 0.0
        original = [dict(z, layers=list(z.get("layers", []))) for z in zones]
        if not math.isfinite(elevation_m) or elevation_m <= 0:
            return original, {
                "enabled": False, "radius_km": radius_km, "removed_layers": 0,
                "removed_zone_km": 0.0, "candidate_cloud_base_m": None,
                "candidate_reach_km": None, "gap_evidence": None,
                "note": "观测点海拔为0 m或无效，完整沿用原算法。"
            }

        local_zones = [z for z in original if float(z["start"]) < radius_km and float(z["end"]) > 0]
        local_layers = [(lo, hi) for z in local_zones for lo, hi in z.get("layers", [])]
        if not local_layers:
            return original, {
                "enabled": False, "radius_km": radius_km, "removed_layers": 0,
                "removed_zone_km": 0.0, "candidate_cloud_base_m": None,
                "candidate_reach_km": None, "gap_evidence": None,
                "note": "0–80 km内没有可用于估算本地云底的高度层，回退原始0 m算法。"
            }
        local_base_m = min(lo for lo, hi in local_layers)
        if local_base_m > elevation_m:
            return original, {
                "enabled": False, "radius_km": radius_km, "removed_layers": 0,
                "removed_zone_km": 0.0, "local_base_m": local_base_m,
                "candidate_cloud_base_m": None, "candidate_reach_km": None,
                "gap_evidence": None,
                "note": f"本地参考云底 {local_base_m:.0f} m 高于机位 {elevation_m:.0f} m，保持原算法，不启用海拔过滤。"
            }

        # 只用0–80 km采样重估新云底：暂时排除低于机位的层，并裁切穿过机位高度的层。
        candidate_layers = []
        for z in local_zones:
            for lo, hi in z.get("layers", []):
                if hi <= elevation_m:
                    continue
                candidate_layers.append((max(lo, elevation_m), hi))
        if not candidate_layers:
            return original, {
                "enabled": False, "radius_km": radius_km, "removed_layers": 0,
                "removed_zone_km": 0.0, "local_base_m": local_base_m,
                "candidate_cloud_base_m": None, "candidate_reach_km": None,
                "gap_evidence": None,
                "note": f"机位 {elevation_m:.0f} m 高于0–80 km内全部云层，无法从近场采样重估新云底，回退原始0 m算法。"
            }
        candidate_base_m = min(lo for lo, hi in candidate_layers)
        candidate_reach_km = math.sqrt(8.0 * 6371.0088 * max(0.0, candidate_base_m / 1000.0))

        # 在新极限云距内寻找80 km以外的明确云洞。云洞宽度按与[80 km, 新极限云距]的交集计算；
        # 云洞后方云区可以延伸到极限云距以外，用来确认它是云层间隙而非路径终点。
        cloud_zones = [z for z in original if float(z.get("cover", 0.0)) > 15.0 and z.get("layers")]
        gap_evidence = []
        for z in original:
            start, end = float(z["start"]), float(z["end"])
            cover = float(z.get("cover", 0.0))
            if cover > 15.0 or end <= radius_km or start >= candidate_reach_km:
                continue
            clipped_start = max(start, radius_km)
            clipped_end = min(end, candidate_reach_km)
            if clipped_end - clipped_start < 15.0:
                continue
            has_left_cloud = any(float(c["start"]) < start and float(c["end"]) > 0 for c in cloud_zones)
            has_right_cloud = any(float(c["start"]) >= end - 1e-6 for c in cloud_zones)
            if has_left_cloud and has_right_cloud:
                gap_evidence.append({"start_km": clipped_start, "end_km": clipped_end,
                                     "width_km": clipped_end-clipped_start, "cover": cover})

        if not gap_evidence:
            return original, {
                "enabled": False, "radius_km": radius_km, "removed_layers": 0,
                "removed_zone_km": 0.0, "local_base_m": local_base_m,
                "candidate_cloud_base_m": candidate_base_m,
                "candidate_reach_km": candidate_reach_km, "gap_evidence": [],
                "note": (f"机位 {elevation_m:.0f} m 高于本地参考云底 {local_base_m:.0f} m；"
                         f"近场重估云底 {candidate_base_m:.0f} m、极限云距约 {candidate_reach_km:.0f} km，"
                         f"但80 km以外至极限云距内未找到明确云洞，回退原始0 m算法。")
            }

        # 有明确漏光云洞证据后，正式移除所有低于机位的云层（不局限于80 km）；
        # 穿过机位高度的云层只保留高于机位的部分。其后由原评分/云洞/距离流程统一重算。
        out = []
        removed_layers = 0
        removed_zone_km = 0.0
        for z in original:
            layers = []
            for lo, hi in z.get("layers", []):
                if hi <= elevation_m:
                    removed_layers += 1
                    continue
                if lo < elevation_m < hi:
                    layers.append((elevation_m, hi))
                    removed_layers += 1
                else:
                    layers.append((lo, hi))
            cover = float(z.get("cover", 0.0))
            if z.get("layers") and not layers:
                cover = 0.0
                removed_zone_km += float(z["end"]) - float(z["start"])
            out.append({**z, "cover": cover, "layers": layers, "elevation_filter_applied": True})
        out.sort(key=lambda z: (z["start"], z["end"], min((lo for lo, hi in z["layers"]), default=-1)))
        gap_summary = "; ".join(f"{g['start_km']:.0f}–{g['end_km']:.0f} km（{g['width_km']:.0f} km）" for g in gap_evidence)
        return out, {
            "enabled": True, "radius_km": radius_km, "removed_layers": removed_layers,
            "removed_zone_km": round(removed_zone_km, 3), "local_base_m": local_base_m,
            "candidate_cloud_base_m": candidate_base_m, "candidate_reach_km": candidate_reach_km,
            "gap_evidence": gap_evidence,
            "note": (f"机位 {elevation_m:.0f} m 高于本地参考云底 {local_base_m:.0f} m；"
                     f"0–80 km重估云底 {candidate_base_m:.0f} m、极限云距约 {candidate_reach_km:.0f} km；"
                     f"在80 km以外发现明确云洞：{gap_summary}。已移除所有低于机位的云层并重新计算。")
        }


    @staticmethod
    def _live_zone_band_cover(zones, band_lo, band_hi):
        """按原始距离采样计算某高度带云量，用于海拔过滤前后诊断。"""
        if not zones:
            return 0.0
        max_dist = max(z["end"] for z in zones)
        step = 5.0
        distances = []
        d = 0.0
        while d < max_dist:
            distances.append(d + min(step / 2, (max_dist - d) / 2))
            d += step
        if not distances or distances[-1] < max_dist:
            distances.append(max_dist)
        vals = []
        for dist in distances:
            matching = [z for z in zones if z["start"] <= dist < z["end"] or
                        (abs(dist - max_dist) < 1e-6 and z["end"] == max_dist)]
            layer_covers = [(lo, hi, float(z["cover"])) for z in matching
                            for lo, hi in z["layers"] if hi > band_lo and lo < band_hi]
            cover = sum(item[2] for item in layer_covers) / len(layer_covers) if layer_covers else 0.0
            weight = 1.0 / (1.0 + dist / 100.0)
            vals.append((cover, weight))
        return sum(v*w for v,w in vals) / sum(w for _,w in vals) if vals else 0.0

    def _calculate_live_simulation(self, data):
        """把手填区间转换为路径采样；海拔过滤后复用原评分核心。"""
        original_zones = data["zones"]
        original_band_cover = {
            "low": self._live_zone_band_cover(original_zones, 0, 2000),
            "mid": self._live_zone_band_cover(original_zones, 2000, 6000),
            "high": self._live_zone_band_cover(original_zones, 6000, 100000),
        }
        zones, elevation_filter = self._filter_live_zones_for_observer_elevation(
            original_zones, data.get("elevation_m", 0.0), 80.0
        )
        data["original_zones"] = original_zones
        data["zones"] = zones
        max_dist = max(z["end"] for z in zones)
        step = 5.0
        distances = []
        d = 0.0
        while d < max_dist:
            distances.append(d + min(step/2, (max_dist-d)/2))
            d += step
        if not distances or distances[-1] < max_dist: distances.append(max_dist)
        samples = []
        for dist in distances:
            matching = [z for z in zones if z["start"] <= dist < z["end"] or (abs(dist-max_dist)<1e-6 and z["end"]==max_dist)]
            if not matching:
                samples.append({"distance_km":dist,"cover":0.0,"layers":[],"layer_covers":[],"zone":None})
            else:
                # 同一距离段允许多行表示不同高度层；合并采样，避免只取第一行而丢失其他云层。
                layer_covers = [(lo, hi, float(z["cover"])) for z in matching for lo, hi in z["layers"]]
                layers = [(lo, hi) for lo, hi, _ in layer_covers]
                cover = sum(float(z["cover"]) for z in matching) / len(matching)
                samples.append({"distance_km":dist,"cover":cover,"layers":layers,
                                "layer_covers":layer_covers,"zone":matching[0],"zones":matching})
        sample_weights = [1.0 / (1.0 + sm["distance_km"] / 100.0) for sm in samples]
        def band_cover(band_lo, band_hi):
            vals=[]
            for sm, weight in zip(samples, sample_weights):
                matching_cover = [cover for lo, hi, cover in sm.get("layer_covers", [])
                                  if hi > band_lo and lo < band_hi]
                vals.append((sum(matching_cover)/len(matching_cover) if matching_cover else 0.0, weight))
            return sum(v*w for v,w in vals)/sum(w for _,w in vals) if vals else None
        low = band_cover(0, 2000)
        mid = band_cover(2000, 6000)
        high = band_cover(6000, 100000)
        # 距离加权云量；沿用现有路径分析“近处权重大、远处权重小”的衰减形式。
        weighted_total = sum(sm["cover"]*w for sm,w in zip(samples,sample_weights)) / sum(sample_weights) if sample_weights else 0
        # 采用起点附近最低云层作为几何参考；若首段不是从0km开始，明确由首个观测区间代表路径起始参考。
        first_candidates = [z for z in zones if z["start"] <= 0 < z["end"] and z["layers"]]
        first = first_candidates[0] if first_candidates else next((z for z in zones if z["start"] <= 0 < z["end"]), zones[0])
        first_layers = [(lo, hi) for z in first_candidates for lo, hi in z["layers"]]
        all_layers = [(lo,hi) for z in zones for lo,hi in z["layers"]]
        if not all_layers: raise ValueError("所有距离区间都标记为无云，没有可模拟的云层。")
        # 同一距离段可能分多行输入不同高度层；几何云底必须取起点处所有有效层的最低云底。
        local_base_m = min((lo for lo,hi in first_layers), default=min(lo for lo,hi in all_layers))
        # 高度层从 0m 起时，不能把云底物理上判为恰好 0m；用该层中点作几何近似并在报告中注明。
        geometry_base_m = local_base_m
        if geometry_base_m <= 0:
            first_layer = next(iter(first_layers), all_layers[0])
            geometry_base_m = (first_layer[0] + first_layer[1]) / 2
        max_top_m = max(hi for lo,hi in all_layers)
        effective_km = max(0.0, geometry_base_m/1000.0)
        reach_km = math.sqrt(8 * 6371.0088 * effective_km) if effective_km > 0 else 0
        # 仅将明确填写为 0% 的连续距离段视为晴空，不把未观测数据当作晴空。
        internal_gaps=[]
        for i,z in enumerate(zones):
            # 不是只有 0% 才算云洞：<=15% 且连续宽度>=15 km 才作为有效云洞候选，
            # 避免一个很窄的空档或小幅云量波动误触发“有云洞”。
            if z["cover"] <= 15 and (z["end"]-z["start"]) >= 15 and i > 0 and i < len(zones)-1:
                left_cloud=any(q["cover"] > 15 for q in zones[:i])
                right_cloud=any(q["cover"] > 15 for q in zones[i+1:])
                if left_cloud and right_cloud: internal_gaps.append((z["start"],z["end"]))
        if internal_gaps:
            gap_start,gap_end=internal_gaps[0]
            next_cloud=gap_end
            gap_status="effective_gap_candidate"
        else:
            gap_start=gap_end=next_cloud=None
            # Excel 区间是用户明确输入的连续路径；若多个区间连续且云量一直>15%，视为无有效云洞。
            all_contiguous = all(abs(zones[i]["end"]-zones[i+1]["start"]) < 0.01 for i in range(len(zones)-1))
            has_cloud_throughout = len(zones) >= 3 and all(float(z["cover"]) > 15 for z in zones)
            gap_status = "continuous_cloud" if all_contiguous and has_cloud_throughout else "unknown"
        inferred={"low":low,"mid":mid,"high":high,"total":weighted_total,"aod":data["aod"]}
        geometry={"rows":[{"distance_km":0,"effective_base_km":effective_km,"reach_km":reach_km}]}
        boundaries={"clear_gap_start_km":gap_start,"clear_gap_end_km":gap_end,"cloud_edge_distance_km":next_cloud,
                    "gap_status":gap_status,"gap_evidence_points":len(zones),"gap_evidence_span_km":max_dist,
                    "fire_cloud_range_km":max_dist}
        inferred=apply_path_score_pair(inferred,geometry,boundaries)
        barrier_analysis = self._analyze_solar_path_barriers(zones)
        # 太阳光路屏障是独立的关键约束：不让远处晴空段把连续高密遮挡带平均掉。
        inferred["pre_barrier_regular_score"] = inferred["regular_score"]
        inferred["pre_barrier_upper_score"] = inferred["upper_score"]
        inferred["solar_barrier_penalty"] = barrier_analysis["penalty"]
        inferred["solar_barrier_upper_penalty"] = barrier_analysis["upper_penalty"]
        inferred["regular_score"] = int(round(max(0, inferred["regular_score"]-barrier_analysis["penalty"])))
        inferred["upper_score"] = int(round(max(inferred["regular_score"], inferred["upper_score"]-barrier_analysis["upper_penalty"])))
        inferred["score"] = inferred["regular_score"]
        inferred = apply_low_cloud_screen_constraint(inferred, zones=zones, source="excel_live", max_relevant_distance_km=reach_km)
        if data.get("event_datetime") is not None:
            # Debug Excel 保存单次模式原始事件时刻与方位角，确保同一时间窗口/太阳方向。
            # 兼容旧版调试 Excel：若缺少方位角，则按该事件时刻重新计算，绝不把 None 传给绘图。
            event_dt = data["event_datetime"]
            event_azimuth = data.get("event_azimuth")
            if event_azimuth is None:
                _, event_azimuth = solar_position(event_dt, data["utc_offset"], data["lat"], data["lon"])
            sunset_info=(event_dt, event_azimuth)
        else:
            sunset_info=(find_sunrise(data["date"],data["utc_offset"],data["lat"],data["lon"]) if data["direction"]=="morning"
                         else find_sunset(data["date"],data["utc_offset"],data["lat"],data["lon"]))
        dynamic_curve = self._build_dynamic_light_curve(data, zones, sunset_info)
        peak = dynamic_curve.get("peak")
        inferred["dynamic_peak_brightness"] = peak["brightness"] if peak else None  # 原始经验指数
        inferred["dynamic_peak_relative_brightness"] = peak["relative_brightness"] if peak else None
        inferred["dynamic_peak_time"] = peak["datetime"].strftime("%H:%M") if peak else None
        return {"samples":samples,"max_distance":max_dist,"max_height_m":max_top_m,"inferred":inferred,"geometry":geometry,
                "boundaries":boundaries,"sun_event":sunset_info,"low":low,"mid":mid,"high":high,"total":weighted_total,
                "local_base_m":local_base_m,"reach_km":reach_km,"solar_barriers":barrier_analysis,
                "dynamic_light_curve":dynamic_curve,"elevation_filter":elevation_filter,
                "original_band_cover":original_band_cover}

    def _draw_live_scene(self, data, result, path):
        if Image is None: raise RuntimeError("生成 JPG 需要 Pillow：python -m pip install pillow")
        from PIL import ImageDraw
        import random
        W,H=1800,1000
        rng=random.Random(f"{data['date']}-{data['direction']}-{data['place']}")
        evening=data["direction"]=="evening"
        img=Image.new("RGB",(W,H),(40,60,95)); pix=img.load()
        for y in range(H):
            t=y/H
            if t<0.72:
                q=t/0.72; top=(34,55,91); mid=(130,150,177)
                col=tuple(int(top[i]*(1-q)+mid[i]*q) for i in range(3))
            else:
                q=(t-0.72)/0.28
                warm=(255,117,57) if evening else (255,166,105)
                if data["aod"] is not None:
                    warm,_ = adjust_color_for_aod(warm, data["aod"])
                col=tuple(int(mid[i]*(1-q)+warm[i]*q) for i in range(3))
            # 能见度较低时，模拟地平线附近的灰化/低对比度；不是物理辐射传输结果。
            vis=data.get("visibility_km")
            if vis is not None and vis < 20 and t > 0.55:
                haze=max(0.0,min(0.35,(20-vis)/20*0.35))*((t-0.55)/0.45)
                col=tuple(int(c*(1-haze)+150*haze) for c in col)
            for x in range(W): pix[x,y]=col
        draw=ImageDraw.Draw(img,"RGBA")
        # 视觉上将高度层投影到不同天空高度；云量百分比控制横向云块覆盖密度。
        max_h=max(1000,result["max_height_m"])
        max_dist=max(1,result["max_distance"])
        for zi,z in enumerate(data["zones"]):
            x0=int(70+(W-140)*z["start"]/max_dist); x1=int(70+(W-140)*z["end"]/max_dist)
            zone_w=max(1,x1-x0)
            for li,(lo,hi) in enumerate(z["layers"]):
                hmid=(lo+hi)/2
                ymid=int(710-(hmid/max_h)*530)
                thickness=max(20,int((hi-lo)/max_h*330))
                cover=z["cover"]
                is_low_intersecting_layer = (lo < 2000 and hi > 0)
                cloud_color=(156,170,187,190) if is_low_intersecting_layer else ((235,210,203,170) if hmid>=6000 else ((255,174,137,190) if hmid>=2000 else (156,170,187,190)))
                # 低云层本身保持灰蓝；所有中/高云先继承统一低云颜色系数，再叠加具体光路屏障。
                # 这样即使二维屏障几何没有命中，也不会出现评分大幅下降但视觉颜色完全不变。
                if hmid >= 2000 and not is_low_intersecting_layer:
                    attenuation = float(result.get("inferred", {}).get("low_cloud_color_factor", 1.0) or 0.0)
                    for barrier in result.get("solar_barriers", {}).get("barriers", []):
                        bd = barrier.get("distance", (float("inf"), float("inf")))
                        target_d = barrier.get("target_distance_km")
                        target_h = barrier.get("target_height_km")
                        if (target_d is not None and target_h is not None and
                                abs((z["start"] + z["end"]) / 2 - target_d) <= max(20.0, zone_w / max(1, W-140) * max_dist) and
                                (z["start"] + z["end"]) / 2 < bd[0] and
                                abs(hmid/1000.0 - target_h) <= max(1.5, (hi-lo)/2000.0)):
                            sev = float(barrier.get("severity", 0))
                            attenuation = min(attenuation, 0.12 if sev >= 24 else (0.30 if sev >= 12 else 0.55))
                    if attenuation < 1.0:
                        neutral=(116,119,126)
                        cloud_color=tuple(int(neutral[i]*(1-attenuation)+cloud_color[i]*attenuation) for i in range(3))+(cloud_color[3],)
                # 用确定性云团排列近似覆盖率；统一云量用于该距离段的全部高度层。
                count=max(0,int((zone_w/70)*(cover/100)))
                for _ in range(count):
                    cx=rng.randint(x0-25,max(x0-24,x1+25)); cy=ymid+rng.randint(-max(5,thickness//3),max(5,thickness//3))
                    cw=rng.randint(45,115); ch=rng.randint(14,max(15,min(52,thickness//2+20)))
                    alpha=int(85+cover*1.35)
                    col=(*cloud_color[:3],min(235,alpha))
                    for dx,dy,scale in [(-.34,.08,.75),(-.12,-.15,.95),(.13,-.22,1.0),(.36,.02,.8),(.04,.15,1.1)]:
                        draw.ellipse((cx+dx*cw-cw*.28*scale,cy+dy*ch-ch*.65*scale,cx+dx*cw+cw*.28*scale,cy+dy*ch+ch*.65*scale),fill=col)
            if zi>0:
                draw.line((x0,150,x0,760),fill=(255,255,255,100),width=2)
            draw.text((x0+5,765),f"{z['start']:g}–{z['end']:g} km  云量{z['cover']:g}%",font=self._jpg_font(18,True),fill=(255,255,255,235),stroke_width=1,stroke_fill=(25,35,50,180))
        # 地平线、太阳与前景轮廓
        draw.ellipse((W*0.77-32,735-32,W*0.77+32,735+32),fill=(255,206,129,245))
        draw.polygon([(0,810),(180,775),(350,815),(560,770),(760,820),(970,780),(1180,815),(1400,770),(1600,800),(1800,775),(1800,1000),(0,1000)],fill=(28,39,54,245))
        title=f"{data['place']}｜{data['date']}｜{data['direction_cn']} 实况视觉模拟"
        draw.text((55,30),title,font=self._jpg_font(32,True),fill=(255,255,255,255),stroke_width=1,stroke_fill=(20,30,45,200))
        ev=result["sun_event"]
        if ev and ev[0]:
            az_text = f"{ev[1]:.1f}°" if len(ev) > 1 and isinstance(ev[1], (int, float)) else "未知"
            draw.text((55,80),f"太阳时刻约 {ev[0].strftime('%H:%M')}｜方位角 {az_text}（当地时）",font=self._jpg_font(20),fill=(255,255,255,235))
        inf=result["inferred"]
        aod="缺测" if data["aod"] is None else f"{data['aod']:.2f}"
        vis_text="缺测" if data.get("visibility_km") is None else f"{data['visibility_km']:.1f}km"
        screen = inf.get("low_cloud_screen")
        screen_text = (f"低云屏障 {screen['start_km']:.0f}-{screen['end_km']:.0f}km" if screen else "低云屏障未命中")
        color_factor = float(inf.get("low_cloud_color_factor", 1.0) or 0.0)
        summary=f"发生概率估计 {inf['regular_score']}%   有利情景上限 {inf['upper_score']}%   {screen_text}   暖色系数 {color_factor:.2f}   AOD {aod}   能见度 {vis_text}"
        draw.rounded_rectangle((45,875,1755,950),radius=16,fill=(12,22,38,215),outline=(255,255,255,110),width=2)
        draw.text((70,895),summary,font=self._jpg_font(24,True),fill=(255,255,255,255))
        img.save(path,"JPEG",quality=94,subsampling=0,optimize=True)

    def _draw_live_profile(self, data, result, path):
        if Image is None: raise RuntimeError("生成 JPG 需要 Pillow：python -m pip install pillow")
        from PIL import ImageDraw
        W,H=1800,1050; img=Image.new("RGB",(W,H),(250,252,255)); d=ImageDraw.Draw(img)
        d.text((55,25),f"{data['place']}｜{data['date']}｜{data['direction_cn']} 实况云层剖面",font=self._jpg_font(32,True),fill=(23,43,77))
        d.text((58,72),"横轴：沿日出/日落方向的距离（km）；纵轴：离地高度（km）；同一距离段的云量统一作用于所有高度层",font=self._jpg_font(19),fill=(71,85,105))
        left,right,top,bottom=115,1730,140,900; maxd=max(1,result['max_distance']); maxh=max(10,math.ceil(result['max_height_m']/1000+1))
        X=lambda x:left+(right-left)*x/maxd; Y=lambda y:bottom-(bottom-top)*y/maxh
        for h in range(0,maxh+1,2):
            y=Y(h); d.line((left,y,right,y),fill=(221,229,239),width=2); d.text((left-18,y-10),str(h),font=self._jpg_font(17),fill=(71,85,105),anchor="rm")
        for z in data['zones']:
            x0,x1=X(z['start']),X(z['end']); cover=z['cover']
            for lo,hi in z['layers']:
                mid=(lo+hi)/2
                base=(70,150,220) if mid<2000 else ((239,159,45) if mid<6000 else (150,125,220))
                strength=cover/100
                color=tuple(int(248*(1-strength)+c*strength) for c in base)
                y0,y1=Y(lo/1000),Y(hi/1000)
                d.rectangle((x0,min(y0,y1),x1,max(y0,y1)),fill=color,outline=base,width=2)
                d.text(((x0+x1)/2,(y0+y1)/2),f"{cover:g}%",font=self._jpg_font(16,True),fill=(25,35,50),anchor="mm")
        d.rectangle((left,top,right,bottom),outline=(71,85,105),width=2)
        step=25 if maxd<=250 else (50 if maxd<=500 else 100)
        ticks=list(range(0,int(maxd)+1,step))
        if not ticks or ticks[-1]<maxd: ticks.append(int(maxd))
        for x in ticks:
            xx=X(x); d.line((xx,top,xx,bottom),fill=(232,237,244),width=1); d.text((xx,bottom+12),str(x),font=self._jpg_font(15),fill=(71,85,105),anchor="mt")
        d.text((W/2,965),"距离（km）",font=self._jpg_font(20),fill=(51,65,85),anchor="mt")
        d.text((18,110),"高度（km）",font=self._jpg_font(17),fill=(51,65,85))
        d.text((60,1010),f"发生概率估计 {result['inferred']['regular_score']}%｜有利情景上限 {result['inferred']['upper_score']}%｜AOD {'缺测' if data['aod'] is None else format(data['aod'],'.2f')}",font=self._jpg_font(20,True),fill=(23,43,77))
        img.save(path,"JPEG",quality=95,subsampling=0,optimize=True)

    def _live_simulation_report(self, data, result, source_path):
        inf=result['inferred']; ev=result['sun_event']
        az_text = f"{ev[1]:.2f}°" if ev and ev[1] is not None else "未知"
        aod_text = "缺测" if data["aod"] is None else f"{data['aod']:.3f}"
        low_text = "未知" if result["low"] is None else f"{result['low']:.1f}%"
        mid_text = "未知" if result["mid"] is None else f"{result['mid']:.1f}%"
        high_text = "未知" if result["high"] is None else f"{result['high']:.1f}%"
        visibility = data.get("visibility_km")
        visibility_text = "缺测" if visibility is None else f"{visibility:.1f} km"
        lines=["火烧云实况模拟分析报告",f"Excel：{source_path}",f"地点：{data['place']}",f"日期：{data['date']}",f"类型：{data['direction_cn']}",f"坐标：{data['lat']:.5f}, {data['lon']:.5f}",f"观测点海拔：{data.get('elevation_m', 0.0):.0f} m（云层高度范围按海拔绝对高度 ASL 解释）",f"当地时区：UTC{data['utc_offset']:+g}",
               f"太阳时刻：{ev[0].strftime('%H:%M:%S') if ev and ev[0] else '无法计算'}",f"太阳方位角：{az_text}",f"AOD：{aod_text}",f"海拔过滤：{result.get('elevation_filter', {}).get('note', '未提供过滤诊断')}",
               f"海拔判定细节：原始本地云底 {result.get('elevation_filter', {}).get('local_base_m', '未估算')} m；新云底 {result.get('elevation_filter', {}).get('candidate_cloud_base_m', '未重估')} m；新极限云距 {result.get('elevation_filter', {}).get('candidate_reach_km', '未重估')} km；明确云洞证据 {result.get('elevation_filter', {}).get('gap_evidence', '无/未触发')}",
               f"发生概率估计：{inf['regular_score']}%（光路屏障前 {inf.get('pre_barrier_regular_score', inf['regular_score'])}%，屏障扣分 {inf.get('solar_barrier_penalty',0):.1f} 个百分点）",f"有利情景上限：{inf['upper_score']}%（光路屏障前 {inf.get('pre_barrier_upper_score', inf['upper_score'])}%，屏障扣分 {inf.get('solar_barrier_upper_penalty',0):.1f} 个百分点）",f"太阳光路屏障判断：{result.get('solar_barriers',{}).get('severity','未知')}",f"连续低云硬约束：{('未命中' if not inf.get('low_cloud_screen') else inf.get('low_cloud_screen'))}",f"低云颜色约束：{inf.get('low_cloud_color_note', '无')}",f"估算低层云量：{low_text}",f"估算中层云量：{mid_text}",f"估算高层云量：{high_text}",
               f"过滤前高度带云量（低/中/高）：{result.get('original_band_cover', {}).get('low', 0):.1f}% / {result.get('original_band_cover', {}).get('mid', 0):.1f}% / {result.get('original_band_cover', {}).get('high', 0):.1f}%；过滤后：{low_text} / {mid_text} / {high_text}",
               f"距离加权云量：{result['total']:.1f}%",f"参考起点云层下界：{result['local_base_m']:.0f} m",f"几何代表云底：{result['geometry']['rows'][0]['effective_base_km']*1000:.0f} m（若高度层从0m起，则用该层中点近似）",f"理论照明距离（简化几何）：{result['reach_km']:.1f} km",f"能见度：{visibility_text}", "", "太阳光路屏障明细："]
        barrier_info = result.get("solar_barriers", {})
        lines.append(barrier_info.get("note", "未提供光路屏障说明"))
        if barrier_info.get("barriers"):
            for b in barrier_info["barriers"]:
                layers = ", ".join(f"{lo:g}~{hi:g}m" for lo,hi in b["layers"])
                lines.append(f"{b['distance'][0]:g}~{b['distance'][1]:g} km｜云量 {b['cover']:g}%｜命中高度带 {layers}｜目标云高 {b['target_height_km']:.2f} km｜太阳高度角情景 {b['sun_altitude_deg']:.1f}°｜光线估算高度 {b['ray_height_km']:.2f} km｜屏障强度 {b['severity']:.1f}")
        else:
            lines.append("旧版静态高云屏障扣分已停用；未对高云高度带相交自动施加固定扣分。动态曲线仍是几何近似，不等于确认无遮挡。")
        lines += ["", "云层区间："]
        for z in data['zones']:
            heights="，".join(f"{lo:g}~{hi:g}m" for lo,hi in z['layers'])
            lines.append(f"{z['start']:g}~{z['end']:g} km｜云量 {z['cover']:g}%｜高度 {heights}")
        dynamic = result.get("dynamic_light_curve", {})
        peak = dynamic.get("peak")
        lines += ["", "动态受光曲线：", dynamic.get("note", "未生成动态曲线。")]
        if peak:
            lines.append(f"相对亮度峰值：{peak['relative_brightness']:.1f}/100；原始亮度指数峰值：{peak['brightness']:.1f}；时间 {peak['datetime'].strftime('%H:%M:%S')}；太阳高度角 {peak['solar_altitude_deg']:.2f}°")
        lines += ["", "评分说明：基础分沿用 apply_path_score_pair；旧版静态高云屏障扣分已停用，避免仅凭同高度云层相交就固定扣分。低云约束仍由独立规则处理；动态曲线逐分钟评估太阳高度、云层距离/高度及异层光路相交。", "高度分层：云层区间的高度均按海拔绝对高度 ASL（相对平均海平面）解释；低层<2km，中层2–6km，高层≥6km。观测点海拔单独记录，不将高度范围自动解释为离地高度。区间内云量百分比原样作用于该区间所有高度层，不把多层云量相加。", "重要限制：动态曲线是二维区间和球形地球几何下的原始受光指数估算；图中相对亮度将本次时间窗口的峰值归一化为100；云量百分比不等于不透明度，未建模真实三维云体、云光学厚度、云粒子散射及精确云层形态。曲线颜色为示意色，不是实际 RGB 预报；未填写的距离区间视为未提供观测，而非已确认晴空。评分是经验指数，不是经过校准的真实概率。"]
        return "\n".join(lines)

    # ---------------- 案例保存、加载与现场验证 ----------------
    def _case_model_code(self):
        label = self.vars.get("weather_model").get() if self.vars.get("weather_model") else "ECMWF IFS 0.25°"
        return "EC" if label.startswith("ECMWF") else "GFS"

    def _case_folder_for_current(self):
        """案例目录按日期、当前填写的地名和天气模式唯一标识。"""
        date_text = self.vars.get("date").get().strip() if self.vars.get("date") else date.today().isoformat()
        try:
            d = date.fromisoformat(date_text)
            date_label = d.strftime("%Y年%m月%d日")
        except ValueError:
            date_label = date.today().strftime("%Y年%m月%d日")

        place_text = self.vars.get("place").get().strip() if self.vars.get("place") else "未命名地点"
        place_text = place_text or "未命名地点"
        # 过滤 Windows 文件夹名中的非法字符，同时避免名称末尾为空格或句点。
        place_label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", place_text)
        place_label = re.sub(r"\s+", " ", place_label).strip(" .") or "未命名地点"
        # 形式示例：2026年10月25日_合肥_EC；同日、同地、同模型重复保存会覆盖预测文件。
        return os.path.join(self.case_root, f"{date_label}_{place_label}_{self._case_model_code()}")

    @staticmethod
    def _json_safe(value):
        """递归把结果转为 JSON 可保存的普通 Python 数据。"""
        if isinstance(value, dict):
            return {str(k): FireCloudApp._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [FireCloudApp._json_safe(v) for v in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            if isinstance(value, float) and not math.isfinite(value):
                return None
            return value
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _svg_write(path, width, height, body):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
               '<rect width="100%" height="100%" fill="#fbfcfe"/>' + body + '</svg>')
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)

    def _save_profile_svg(self, result, direction_key, path):
        """将当前案例的云层剖面数据导出为独立 SVG 图像，不依赖第三方绘图库。"""
        profiles = result.get("profiles", [])
        samples = result.get("samples", [])
        pmap = {p.get("distance_km"): p for p in profiles}
        series = []
        for sm in sorted(samples, key=lambda x: x.get("distance_km", 0)):
            p = pmap.get(sm.get("distance_km"), {})
            levels = p.get("levels") or []
            if not levels:
                for key, h in (("cloud_cover_low", 1.0), ("cloud_cover_mid", 4.5), ("cloud_cover_high", 8.5)):
                    v = sm.get(key)
                    if isinstance(v, (int, float)):
                        levels.append({"height_agl_km": h, "cloud_cover": v, "cloudy_candidate": v >= 25})
            series.append((sm.get("distance_km", 0), levels, p.get("estimated_base_km")))
        if not any(levels for _, levels, _ in series):
            self._svg_write(path, 1200, 680, '<text x="40" y="70" font-size="24" fill="#334155">本次没有可绘制的高度剖面数据</text>')
            return
        W, H = 1200, 680
        left, right, top, bottom = 90, 1160, 70, 590
        max_d = max([float(d or 0) for d, _, _ in series] + [1.0])
        max_h = max([float(l.get("height_agl_km", 0) or 0) for _, ls, _ in series for l in ls] + [12.0])
        max_h = max(12.0, min(20.0, max_h + 1.0))
        def X(d): return left + (right-left) * float(d or 0) / max_d
        def Y(h): return bottom - (bottom-top) * float(h or 0) / max_h
        body = [f'<text x="{left}" y="35" font-size="23" font-weight="bold" fill="#172b4d">{xml_escape("朝霞" if direction_key=="morning" else "晚霞")}路径云层高度剖面</text>']
        for h in range(0, int(max_h)+1, 2):
            y=Y(h); body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#dbe3ec"/>')
            body.append(f'<text x="{left-12}" y="{y+5:.1f}" text-anchor="end" font-size="14" fill="#475569">{h} km</text>')
        ticks = sorted(set([0, int(max_d/4), int(max_d/2), int(max_d*3/4), int(max_d)]))
        for d in ticks:
            x=X(d); body.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" stroke="#e2e8f0"/>')
            body.append(f'<text x="{x:.1f}" y="{bottom+27}" text-anchor="middle" font-size="14" fill="#475569">{d} km</text>')
        for d, levels, base in series:
            x=X(d); band=max(7, min(23, (right-left)/max(1,len(series))*0.68))
            for lv in levels:
                h=float(lv.get("height_agl_km", 0) or 0); cover=lv.get("cloud_cover")
                if cover is None: continue
                if h < 2: color="#509dde"
                elif h < 6: color="#f4a636"
                else: color="#a678da"
                opacity=max(.18,min(.9,float(cover)/100.0))
                y=Y(h)
                body.append(f'<rect x="{x-band/2:.1f}" y="{y-7:.1f}" width="{band:.1f}" height="14" rx="3" fill="{color}" fill-opacity="{opacity:.2f}" stroke="{color}" stroke-opacity=".75"/>')
            if isinstance(base,(int,float)):
                y=Y(base); body.append(f'<path d="M{x-5:.1f},{y-5:.1f} L{x+5:.1f},{y+5:.1f} M{x+5:.1f},{y-5:.1f} L{x-5:.1f},{y+5:.1f}" stroke="#d83c3c" stroke-width="2"/>')
        body.append(f'<text x="{(left+right)/2}" y="650" text-anchor="middle" font-size="16" fill="#334155">沿太阳方位距离（离散采样）</text>')
        body.append('<rect x="780" y="18" width="14" height="14" fill="#509dde"/><text x="800" y="30" font-size="13" fill="#334155">低云</text><rect x="860" y="18" width="14" height="14" fill="#f4a636"/><text x="880" y="30" font-size="13" fill="#334155">中云</text><rect x="940" y="18" width="14" height="14" fill="#a678da"/><text x="960" y="30" font-size="13" fill="#334155">高云</text><path d="M1040 18 l10 10 m0 -10 l-10 10" stroke="#d83c3c" stroke-width="2"/><text x="1058" y="30" font-size="13" fill="#334155">推算云底</text>')
        self._svg_write(path, W, H, ''.join(body))

    def _save_scene_svg(self, result, direction_key, path):
        inferred = result.get("inferred", {})
        sm = next((x for x in result.get("samples", []) if x.get("distance_km") == 0), {})
        boundary = result.get("boundaries", {})
        aod = result.get("aod")
        def pct(v): return "未知" if v is None else f"{v:.0f}%"
        lines = [
            ("朝霞现场视觉预估" if direction_key == "morning" else "晚霞现场视觉预估", 30, "#172b4d", True),
            (f"发生概率估计：{inferred.get('regular_score', '—')}%    有利情景上限：{inferred.get('upper_score', '—')}%", 21, "#1d4ed8", True),
            (f"本地云量：总云 {pct(sm.get('cloud_cover'))}｜低云 {pct(sm.get('cloud_cover_low'))}｜中云 {pct(sm.get('cloud_cover_mid'))}｜高云 {pct(sm.get('cloud_cover_high'))}", 18, "#334155", False),
            (f"火烧云范围：{boundary.get('fire_cloud_range_km', '未知')} km；云边距：{boundary.get('cloud_edge_distance_km', '未知')} km", 18, "#334155", False),
            (f"晴空间隔：{boundary.get('clear_gap_start_km', '未知')}–{boundary.get('clear_gap_end_km', '未知')} km（若未识别则不能视为不存在）", 17, "#334155", False),
            (f"AOD：{'缺测' if aod is None else f'{aod:.3f}'}；彩虹机会指数：{result.get('rainbow', {}).get('score', '暂不可估')}", 17, "#334155", False),
            ("说明：这是基于天气模式数据的经验性视觉预估，不是现场实拍，也不是发生概率。", 15, "#64748b", False),
        ]
        body=[]; y=65
        for txt, size, color, bold in lines:
            body.append(f'<text x="45" y="{y}" font-size="{size}" fill="{color}"'+(' font-weight="bold"' if bold else '')+f'>{xml_escape(txt)}</text>')
            y += 70 if size >= 25 else 58
        self._svg_write(path, 1200, 560, ''.join(body))

    @staticmethod
    def _jpg_font(size, bold=False):
        """加载 Windows 常见中文字体；若不存在则退回 Pillow 默认字体。"""
        if ImageFont is None:
            return None
        candidates = ([r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf"] if bold else
                      [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc"])
        # Linux/macOS 常见中文字体回退，方便在非 Windows 环境预览输出图。
        candidates += (["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"] if bold else
                       ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"])
        for name in candidates:
            try:
                return ImageFont.truetype(name, size=size)
            except Exception:
                pass
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size=size)
        except Exception:
            return ImageFont.load_default()

    @staticmethod
    def _num_pct(value):
        return None if not isinstance(value, (int, float)) else max(0, min(100, float(value)))

    def _save_scene_jpg(self, result, direction_key, path, place=""):
        """输出可直接查看的示意天空图：按预报低/中/高云量绘制云层与暖色受光倾向。"""
        if Image is None:
            raise RuntimeError("生成 JPG 图片需要 Pillow，请在运行脚本的 Python 环境执行：python -m pip install pillow")
        W, H = 1600, 900
        img = Image.new("RGB", (W, H), (35, 58, 94))
        pix = img.load()
        # 天空渐变：上方蓝灰，靠近地平线逐渐转为日出/日落暖色。
        for y in range(H):
            t = y / H
            if t < 0.69:
                q = t / 0.69
                top = (32, 53, 86); mid = (102, 128, 164)
                c = tuple(int(top[i] * (1-q) + mid[i] * q) for i in range(3))
            else:
                q = (t - 0.69) / 0.31
                base = (102, 128, 164); warm = (246, 143, 93)
                c = tuple(int(base[i] * (1-q) + warm[i] * q) for i in range(3))
            for x in range(W):
                pix[x, y] = c
        draw = ImageDraw.Draw(img, "RGBA")
        f_title = self._jpg_font(36, True); f_sub = self._jpg_font(23); f_layer = self._jpg_font(25, True); f_small = self._jpg_font(19)
        title = "朝霞天空现场视觉模拟" if direction_key == "morning" else "晚霞天空现场视觉模拟"
        place = (place or "未命名地点").strip()
        draw.text((48, 28), title, font=f_title, fill=(255,255,255,245), stroke_width=1, stroke_fill=(20,35,55,180))
        draw.text((50, 80), f"{place}  |  基于天气模式云量的示意图，不是实拍或精确云体预测", font=f_sub, fill=(255,255,255,235))
        samples = result.get("samples", [])
        sm = next((x for x in samples if x.get("distance_km") == 0), samples[0] if samples else {})
        layers = [
            ("高云", sm.get("cloud_cover_high"), 180, 0.18, (244, 188, 180)),
            ("中云", sm.get("cloud_cover_mid"), 350, 0.36, (250, 164, 128)),
            ("低云", sm.get("cloud_cover_low"), 535, 0.56, (119, 132, 154)),
        ]
        # 稳定伪随机排布：相同案例每次生成的示意图保持一致。
        seed = sum(ord(ch) for ch in (place + direction_key))
        import random
        rng = random.Random(seed)
        for layer_idx, (label, amount, ybase, spread, tint) in enumerate(layers):
            cover = self._num_pct(amount)
            cover = 0.0 if cover is None else cover
            # 云量越高，云团越密、横向覆盖越大；云的形状仅为示意。
            count = int(cover / 100 * 25)
            cloud_col = tint
            if layer_idx == 2:
                cloud_col = (112, 125, 147) if direction_key == "morning" else (122, 118, 139)
            elif layer_idx == 1:
                cloud_col = (249, 153, 122) if direction_key == "morning" else (255, 142, 111)
            else:
                cloud_col = (255, 203, 186) if direction_key == "morning" else (255, 188, 171)
            if layer_idx < 2:
                # colorbar 与场景图共用低云约束结果：高/中云暖色受光潜力同步衰减。
                factor = float(result.get("inferred", {}).get("low_cloud_color_factor", 1.0) or 0.0)
                neutral = (116, 119, 126)
                cloud_col = tuple(int(neutral[k] * (1-factor) + cloud_col[k] * factor) for k in range(3))
            for i in range(count):
                x = rng.randint(-80, W-30)
                yy = int(ybase + rng.uniform(-55, 55))
                ww = rng.randint(95, 235) * (0.7 + cover / 200)
                hh = rng.randint(22, 55) * (0.8 + cover / 250)
                alpha = int(95 + 115 * cover / 100)
                # 云团由多个椭圆组成，避免规则矩形块。
                for dx, dy, scale in [(-.35,.1,.72),(-.12,-.18,.9),(.15,-.23,1.0),(.38,.02,.78),(.08,.13,1.15)]:
                    bx = x + dx*ww; by = yy + dy*hh
                    draw.ellipse((bx-ww*.25*scale, by-hh*.65*scale, bx+ww*.25*scale, by+hh*.65*scale),
                                 fill=(*cloud_col, alpha))
            draw.rounded_rectangle((42, ybase-68, 330, ybase-18), radius=14, fill=(17, 30, 50, 145), outline=(255,255,255,90), width=1)
            text_amount = "缺测" if amount is None else f"{float(amount):.0f}%"
            draw.text((58, ybase-62), f"{label}  {text_amount}", font=f_layer, fill=(255,255,255,250))
        # 地平线和远山轮廓
        draw.polygon([(0, 765),(130,735),(245,760),(390,710),(520,748),(680,720),(850,760),(1030,720),(1200,755),(1390,710),(1600,745),(1600,900),(0,900)], fill=(36,47,62,210))
        draw.line((0,765,W,765), fill=(255,225,197,190), width=3)
        inf = result.get("inferred", {})
        score = inf.get("regular_score", "—"); upper = inf.get("upper_score", "—")
        aod = result.get("aod")
        summary = f"发生概率估计 {score}%   有利情景上限 {upper}%   AOD {'缺测' if aod is None else format(aod,'.3f')}"
        draw.rounded_rectangle((40, 812, 1110, 866), radius=14, fill=(15,25,42,180))
        draw.text((58, 822), summary, font=f_sub, fill=(255,255,255,245))
        img.save(path, "JPEG", quality=94, subsampling=0, optimize=True)

    def _save_profile_jpg(self, result, direction_key, path, place=""):
        """输出细化距离—高度云层剖面 JPG，使用已有逐点气压层数据。"""
        if Image is None:
            raise RuntimeError("生成 JPG 图片需要 Pillow，请在运行脚本的 Python 环境执行：python -m pip install pillow")
        profiles = result.get("profiles", [])
        samples = result.get("samples", [])
        pmap = {p.get("distance_km"): p for p in profiles}
        series = []
        for sm in sorted(samples, key=lambda x: x.get("distance_km", 0)):
            p = pmap.get(sm.get("distance_km"), {})
            levels = list(p.get("levels") or [])
            if not levels:
                for key, h in (("cloud_cover_low", 1.0), ("cloud_cover_mid", 4.5), ("cloud_cover_high", 8.5)):
                    v = sm.get(key)
                    if isinstance(v, (int, float)):
                        levels.append({"height_agl_km": h, "cloud_cover": v, "relative_humidity": None})
            series.append((float(sm.get("distance_km") or 0), levels, p.get("estimated_base_km")))
        if not any(levels for _, levels, _ in series):
            # 即便无剖面，仍生成一张清楚标注缺数据的 JPG，方便案例目录结构一致。
            img = Image.new("RGB", (1600, 900), "white")
            d = ImageDraw.Draw(img); d.text((60,60), "没有可绘制的云层高度剖面数据", font=self._jpg_font(34, True), fill=(60,70,85))
            img.save(path, "JPEG", quality=92); return
        W,H = 1800, 1050
        img = Image.new("RGB", (W,H), (250,252,255)); d = ImageDraw.Draw(img)
        f_title=self._jpg_font(34,True); f_axis=self._jpg_font(20); f_small=self._jpg_font(16); f_legend=self._jpg_font(19)
        title = "朝霞路径云层精细剖面" if direction_key == "morning" else "晚霞路径云层精细剖面"
        d.text((65,25), title, font=f_title, fill=(23,43,77))
        d.text((67,72), f"{place or '未命名地点'}｜横轴：沿太阳方位距离（km）｜纵轴：离地高度（km）", font=f_axis, fill=(71,85,105))
        left,right,top,bottom=110,1735,135,900
        max_dist=max([x[0] for x in series]+[1.0]); max_h=max(12.0,min(20.0,max([float(lv.get('height_agl_km') or 0) for _,ls,_ in series for lv in ls]+[12.0])+1))
        def X(v): return left+(right-left)*v/max_dist
        def Y(v): return bottom-(bottom-top)*max(0,min(max_h,v))/max_h
        for h in range(0,int(max_h)+1,2):
            y=Y(h); d.line((left,y,right,y), fill=(220,228,238), width=2); d.text((left-18,y-12),f"{h}",font=f_axis,fill=(71,85,105),anchor="rm")
        step=25 if max_dist<=250 else (50 if max_dist<=500 else 100)
        ticks=list(range(0,int(max_dist)+1,step))
        if not ticks or ticks[-1] < max_dist: ticks.append(int(max_dist))
        for dist in ticks:
            x=X(dist); d.line((x,top,x,bottom),fill=(232,237,244),width=1); d.text((x,bottom+18),str(dist),font=f_small,fill=(71,85,105),anchor="mt")
        n=len(series)
        for i,(dist,levels,base) in enumerate(series):
            if n>1:
                prev=series[i-1][0] if i else max(0,dist-(series[i+1][0]-dist)/2)
                nxt=series[i+1][0] if i<n-1 else dist+(dist-series[i-1][0])/2
                x0=X((prev+dist)/2) if i else X(max(0,dist-(series[1][0]-dist)/2))
                x1=X((dist+nxt)/2) if i<n-1 else X(min(max_dist,dist+(dist-series[i-1][0])/2))
            else: x0,x1=left,right
            x0=max(left,x0); x1=min(right,max(x0+2,x1))
            for lv in levels:
                h=lv.get("height_agl_km"); cc=lv.get("cloud_cover"); rh=lv.get("relative_humidity")
                if not isinstance(h,(int,float)): continue
                if isinstance(cc,(int,float)) and cc<15 and not (isinstance(rh,(int,float)) and rh>=80): continue
                if h<2: color=(66,153,225)
                elif h<6: color=(245,158,11)
                else: color=(167,139,250)
                strength=max(0.18,min(0.95,(float(cc)/100 if isinstance(cc,(int,float)) else 0.25)))
                # 将云量强度编码为颜色明度，并保留不同高度带的辨识色。
                pale=tuple(int(245*(1-strength)+c*strength) for c in color)
                hh=float(h); y0=Y(max(0,hh-0.28)); y1=Y(min(max_h,hh+0.28))
                d.rectangle((x0,min(y0,y1),x1,max(y0,y1)), fill=pale, outline=color, width=1)
            if isinstance(base,(int,float)) and 0<=base<=max_h:
                x=X(dist); y=Y(base); d.line((x-6,y-6,x+6,y+6),fill=(210,45,45),width=3); d.line((x+6,y-6,x-6,y+6),fill=(210,45,45),width=3)
        d.rectangle((left,top,right,bottom), outline=(71,85,105), width=2)
        d.text((W/2,980),"沿太阳方位的离散采样距离（km）",font=f_axis,fill=(51,65,85),anchor="mt")
        d.text((18,105),"高度（km）",font=f_small,fill=(51,65,85))
        # 图例
        legends=[((66,153,225),"低云/低层"),((245,158,11),"中云/中层"),((167,139,250),"高云/高层"),((210,45,45),"推算云底")]
        lx=120
        for color,label in legends:
            d.rectangle((lx,930,lx+28,950),fill=color,outline=color); d.text((lx+37,928),label,font=f_legend,fill=(51,65,85)); lx+=260
        d.text((W-65,1020),"云层高度与覆盖来自数值模式/启发式推算，不是实测云体",font=f_small,fill=(100,116,139),anchor="rs")
        img.save(path,"JPEG",quality=95,subsampling=0,optimize=True)

    def _build_summary_report(self):
        """生成用户需要的简要、直白分析记录；按分析项目快速回顾结论。"""
        lines = [f"火烧云预测简要记录｜{self.vars['place'].get()}｜{self.vars['date'].get()}",
                 f"天气模式：{self.vars['weather_model'].get()}｜模式数据时间：{self.last_fetch_time or '未记录'}", ""]
        for key, name in (("morning", "朝霞"), ("evening", "晚霞")):
            r = getattr(self, "direction_results", {}).get(key, {})
            if not r: continue
            inf=r.get("inferred",{}); b=r.get("boundaries",{}); samples=r.get("samples",[])
            sm=next((x for x in samples if x.get("distance_km")==0),samples[0] if samples else {})
            geo=r.get("geometry",{}).get("rows",[])
            g0=next((x for x in geo if x.get("distance_km")==0),{})
            def pct(v): return "未知/缺测" if not isinstance(v,(int,float)) else f"{v:.0f}%"
            def km(v): return "未知/未识别" if not isinstance(v,(int,float)) else f"约 {v:.0f} km"
            score=inf.get("regular_score"); upper=inf.get("upper_score")
            reasons=inf.get("reasons",[])
            low_reason=next((x for x in reasons if "低云" in x and ("遮挡" in x or "风险" in x)), "低云遮挡：模型证据不足，需现场确认。")
            vis_values=[x.get("visibility_m") for x in samples if isinstance(x.get("visibility_m"),(int,float))]
            mean_vis=(sum(vis_values)/len(vis_values)/1000) if vis_values else None
            aod=r.get("aod")
            lines.append(f"【{name}】")
            lines.append(f"总体判断：发生概率估计 {score if score is not None else '未知'}%；有利情景上限 {upper if upper is not None else '未知'}%。该数值为经验估计，尚未经历史样本校准，不等同于严格统计概率。")
            lines.append(f"云量结构：低云 {pct(sm.get('cloud_cover_low'))}；中云 {pct(sm.get('cloud_cover_mid'))}；高云 {pct(sm.get('cloud_cover_high'))}；总云量 {pct(sm.get('cloud_cover'))}。")
            lines.append(f"低云遮挡：{low_reason}")
            lines.append(f"云层延伸/云边界：本地连续中高云范围 {km(b.get('fire_cloud_range_km'))}；太阳方向推算云边距 {km(b.get('cloud_edge_distance_km'))}。")
            gap_start=b.get('clear_gap_start_km'); gap_end=b.get('clear_gap_end_km')
            gap=(f"约 {gap_start}–{gap_end} km" if gap_start is not None and gap_end is not None else "未识别到可判读的晴空间隔（不代表一定没有）")
            lines.append(f"云隙/挡光：{gap}；远端云层能否被实际照亮，还受云体厚度、云顶高度和三维云缘影响。")
            lines.append(f"云底与理论照明：本地推算云底绝对海拔 {km(g0.get('cloud_base_km'))}；理论照明距离 {km(g0.get('reach_km'))}。云底来自模式剖面推算，不是实测。")
            lines.append(f"空气透明度：AOD {'缺测' if aod is None else f'{aod:.3f}'}；沿线模式能见度均值 {'缺测' if mean_vis is None else f'{mean_vis:.1f} km'}。低能见度或较高 AOD 可能削弱远处霞光与色彩饱和度。")
            color_note=next((x for x in reasons if any(t in x for t in ("色彩","AOD","饱和","透明度"))), None)
            lines.append("色彩饱和度潜力："+(color_note if color_note else "当前数据不能精确预测真实颜色；主要取决于空气透明度、云层厚度与受光角度。"))
            if isinstance(score,(int,float)):
                if score>=70: verdict="当前条件相对有利，值得重点关注；仍需临场核对云形、云隙和低云遮挡。"
                elif score>=45: verdict="存在一定机会，但条件不完整或不确定性较高，建议重点观察太阳方向的云隙和受光云层。"
                else: verdict="当前模型证据偏弱，不宜单凭本次预报期待明显火烧云；局地云形变化仍可能改变结果。"
            else: verdict="关键评分缺失，暂时无法给出可靠的总结判断。"
            lines.append("直白结论："+verdict)
            lines.append("")
        lines.append("说明：本摘要用于快速回顾；具体采样点、气压层、几何计算与评分理由见 analysis_report_detailed.txt / analysis_report.csv。")
        return "\n".join(lines)

    def _export_single_mode_live_excel(self, folder, direction_key, result):
        """将本次单次模式采样导出为可直接导入实况模拟的 Excel，并附原曲线供对照。

        自动模式的低/中/高云量分别映射为 0–2、2–6、6–12 km 的近似高度带；
        这是为了复用实况模拟的输入格式，不代表真实云底/云顶。
        """
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment
            from openpyxl.utils import get_column_letter
        except ImportError as exc:
            raise RuntimeError("生成调试 Excel 需要 openpyxl。请执行：python -m pip install openpyxl") from exc

        samples = sorted(result.get("samples", []), key=lambda q: float(q.get("distance_km", 0)))
        if not samples:
            return None
        direction_cn = "朝霞" if direction_key == "morning" else "晚霞"
        place = str(self.vars["place"].get()).strip() or "未命名地点"
        date_text = str(self.vars["date"].get()).strip()
        try:
            event_date = date.fromisoformat(date_text[:10])
        except Exception:
            event_date = date.today()
        lat = float(self.vars["lat"].get())
        lon = float(self.vars["lon"].get())
        # 沿用单次模式最终采用的 AOD；不额外请求数据。
        aod = result.get("aod")
        if aod is None:
            aod = result.get("inferred", {}).get("aod")
        vis_values = [float(x["visibility_m"]) / 1000.0 for x in samples
                      if isinstance(x.get("visibility_m"), (int, float)) and x.get("visibility_m") > 0]
        visibility_km = sum(vis_values) / len(vis_values) if vis_values else None

        # 与单次模式构造 curve_zones 的规则完全一致：以相邻采样点中点作为距离区间边界。
        distance_rows = []
        for i, sm in enumerate(samples):
            d = float(sm.get("distance_km", 0))
            prev_d = float(samples[i-1].get("distance_km", d)) if i > 0 else d
            next_d = float(samples[i+1].get("distance_km", d)) if i+1 < len(samples) else d
            start = max(0.0, (prev_d + d) / 2.0) if i > 0 else 0.0
            end = (d + next_d) / 2.0 if i+1 < len(samples) else d + max(5.0, (d-prev_d)/2.0 if i > 0 else 12.5)
            if end <= start:
                end = start + 5.0
            distance_rows.append((start, end, sm))

        wb = Workbook()
        ws = wb.active
        ws.title = "基本信息"
        ws.append(["字段", "值"])
        metadata = [
            ("地点", place), ("日期", event_date.isoformat()), ("类型", direction_cn),
            ("纬度", lat), ("经度", lon),
            ("海拔_m", (parse_float(self.vars["elevation"].get(), "海拔") if self.vars.get("elevation") and self.vars["elevation"].get().strip() else 0.0)),
            ("时区 UTC偏移", 8),
            ("事件时刻", result.get("event_dt").strftime("%Y-%m-%d %H:%M:%S") if isinstance(result.get("event_dt"), datetime) else ""),
            ("太阳方位角_deg", result.get("azimuth")),
            ("AOD", aod), ("能见度_km", visibility_km),
            ("来源模式", "单次单模式自动天气采样"),
            ("高度映射说明", "云高度范围按海拔绝对高度 ASL：低云 0~2000m；中云 2000~6000m；高云 6000~12000m。为曲线对照使用的近似层，不是实测云底/云顶。"),
            ("距离分段说明", "区间边界按相邻沿线采样距离的中点生成，与单次模式构造动态曲线输入的规则一致。"),
            ("对照说明", "本工作簿可直接用于实况模拟。曲线仍可能因太阳事件时间、输入高度近似及实况模式其他处理不同而出现差异；请同时查看‘单次模式曲线’工作表。"),
        ]
        for row in metadata:
            ws.append(list(row))

        cloud_ws = wb.create_sheet("云层区间")
        cloud_ws.append(["距离起点_km", "距离终点_km", "云量_percent", "云高度范围_m", "来源云层", "原始采样距离_km"])
        bands = [
            ("cloud_cover_low", 0.0, 2000.0, "低云"),
            ("cloud_cover_mid", 2000.0, 6000.0, "中云"),
            ("cloud_cover_high", 6000.0, 12000.0, "高云"),
        ]
        for start, end, sm in distance_rows:
            wrote = False
            for key, lo, hi, label in bands:
                cover = sm.get(key)
                if isinstance(cover, (int, float)) and math.isfinite(float(cover)) and float(cover) > 0:
                    cloud_ws.append([round(start, 4), round(end, 4), round(float(cover), 4),
                                     f"{lo:g}~{hi:g}米", label, sm.get("distance_km")])
                    wrote = True
            if not wrote:
                cloud_ws.append([round(start, 4), round(end, 4), 0, None, "无云层覆盖", sm.get("distance_km")])

        curve_ws = wb.create_sheet("单次模式曲线")
        curve_ws.append(["时间", "相对事件分钟", "太阳高度角_deg", "太阳方位角_deg", "原始亮度指数", "本次相对亮度_峰值100", "示意色_R", "示意色_G", "示意色_B"])
        dynamic_samples = result.get("dynamic_light_curve", {}).get("samples", [])
        for point in dynamic_samples:
            dt = point.get("datetime")
            if isinstance(dt, datetime):
                dt_text = dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                dt_text = str(dt or "")
            color = point.get("color") or (0, 0, 0)
            curve_ws.append([dt_text, point.get("minutes_from_event"), point.get("solar_altitude_deg"),
                             point.get("solar_azimuth_deg"), point.get("brightness"), point.get("relative_brightness"),
                             color[0], color[1], color[2]])

        note_ws = wb.create_sheet("调试说明")
        note_ws.append(["项目", "说明"])
        notes = [
            ("用途", "用同一份单次模式沿线云量输入实况模拟，比较动态亮度/色彩曲线。"),
            ("关键限制", "单次模式只提供低/中/高云量，不提供可靠云底云顶；本文件按固定高度带近似映射。"),
            ("云量处理", "低、中、高云量分别写成相同距离范围内的独立行，云量各自保留，避免把三层云量错误合并成一个百分比。"),
            ("曲线数值", "同时保存原始亮度经验指数和本次窗口内归一化的相对亮度（本次峰值=100）。相对亮度用于观察同一次预测内的时间变化；跨日期比较请参考原始亮度指数。"),
            ("无额外请求", "生成文件只使用已获取并保存在案例中的采样数据，不发起天气 API 请求。"),
        ]
        for row in notes:
            note_ws.append(list(row))

        # 轻量格式化，便于直接查看和编辑。
        for sheet in wb.worksheets:
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions if sheet.max_row > 1 else "A1"
            for cell in sheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="334155")
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            for col_idx in range(1, sheet.max_column + 1):
                values = [str(sheet.cell(row=r, column=col_idx).value or "") for r in range(1, min(sheet.max_row, 80) + 1)]
                width = min(58, max(12, max((len(v) for v in values), default=12) + 2))
                sheet.column_dimensions[get_column_letter(col_idx)].width = width
            sheet.row_dimensions[1].height = 28
        out_path = os.path.join(folder, f"{direction_key}_live_simulation_debug.xlsx")
        wb.save(out_path)
        return out_path

    def save_case(self):
        if not getattr(self, "direction_results", None):
            messagebox.showinfo("尚无分析结果", "请先完成一次单日单模式的朝霞/晚霞详细查询，再保存案例。")
            return
        folder = self._case_folder_for_current()
        if os.path.isdir(folder):
            # 不删除整个目录：保留已录入的现场实况，只更新预测部分文件。
            pass
        try:
            os.makedirs(folder, exist_ok=True)
            model_label = self.vars["weather_model"].get()
            state = {
                "schema_version": 1, "app_version": APP_TITLE,
                "saved_at": datetime.now().astimezone().isoformat(),
                "place": self.vars["place"].get(), "date": self.vars["date"].get(),
                "latitude": self.vars["lat"].get(), "longitude": self.vars["lon"].get(),
                "elevation_m": (parse_float(self.vars["elevation"].get(), "海拔")
                                if self.vars.get("elevation") and self.vars["elevation"].get().strip() else 0.0),
                "weather_model": model_label, "model_code": self._case_model_code(),
                "cloud_base_mode": self.vars["cloud_base_mode"].get(),
                "aod_inputs": {k: self.vars[k].get() for k in ("morning_aod", "evening_aod")},
                "cloud_base_inputs": {k: self.vars[k].get() for k in ("morning_cloud_base_km", "evening_cloud_base_km")},
                "last_fetch_time": self.last_fetch_time,
                # 原始 API 响应单独放在 weather_raw.json，避免状态文件重复膨胀。
                "direction_results": self._json_safe({k: {kk: vv for kk, vv in v.items() if kk != "raw_weather"}
                                                       for k, v in self.direction_results.items()}),
                "last_report": self.last_report,
                "note": "历史案例加载时原样显示已保存结果；不会自动重新请求天气 API。",
            }
            with open(os.path.join(folder, "analysis_state.json"), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            # 完整保存沿线天气 API 返回值；若当前旧案例没有原始响应，至少保留所有已解析采样数据。
            raw = {k: {"raw_weather": v.get("raw_weather", []), "samples": v.get("samples", []),
                       "profiles": v.get("profiles", []), "raw_details": v.get("raw_details", [])}
                   for k, v in self.direction_results.items()}
            with open(os.path.join(folder, "weather_raw.json"), "w", encoding="utf-8") as f:
                json.dump(self._json_safe(raw), f, ensure_ascii=False, indent=2)
            with open(os.path.join(folder, "analysis_report.csv"), "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["方向", "分析项目", "结果", "直白解释", "数据/推断属性"])
                for key, name in (("morning", "朝霞"), ("evening", "晚霞")):
                    r = self.direction_results.get(key, {})
                    if not r.get("inferred"):
                        continue
                    inf = r.get("inferred", {}); s0 = next((x for x in r.get("samples", []) if x.get("distance_km") == 0), {})
                    b = r.get("boundaries", {}); geo = r.get("geometry", {}).get("rows", [])
                    near_geo = next((x for x in geo if x.get("distance_km") == 0), {})
                    aod = r.get("aod")
                    rows = [
                        ("本地总云量", s0.get("cloud_cover"), "本地天空总云量预报，不等于可见火烧云比例", "天气模式预报"),
                        ("本地低云量", s0.get("cloud_cover_low"), "低层云覆盖情况；是否遮挡还取决于云底、方位和实际云形", "天气模式预报"),
                        ("本地中云量", s0.get("cloud_cover_mid"), "中层云量，可能参与受光", "天气模式预报"),
                        ("本地高云量", s0.get("cloud_cover_high"), "高层云量，可能参与受光但不直接代表色彩饱和度", "天气模式预报"),
                        ("本地估算云底绝对海拔_km_ASL", near_geo.get("cloud_base_asl_km", near_geo.get("cloud_base_km")), "气压层离地云底估算值加观测点海拔，不是实测", "模式推算"),
                        ("本地估算云底离地高度_km_AGL", near_geo.get("cloud_base_agl_km"), "绝对云底海拔减去观测点海拔", "派生值"),
                        ("本地有效云底绝对海拔_km_ASL", near_geo.get("effective_base_km"), "绝对云底海拔减气溶胶等效高度后的几何估算", "模式推算"),
                        ("本地理论照明距离_km", near_geo.get("reach_km"), "几何理论距离，不代表该距离内一定有云", "公式估算"),
                        ("火烧云范围_km", b.get("fire_cloud_range_km"), "沿太阳方向离散采样得到的中高云连续范围", "路径采样估算"),
                        ("云边距_km", b.get("cloud_edge_distance_km"), "离散采样推算的云边界距离", "路径采样估算"),
                        ("晴空间隔起点_km", b.get("clear_gap_start_km"), "未识别到不代表不存在；采样稀疏时可能漏检", "路径采样估算"),
                        ("晴空间隔终点_km", b.get("clear_gap_end_km"), "未识别到不代表不存在；采样稀疏时可能漏检", "路径采样估算"),
                        ("AOD", aod, "气溶胶光学厚度；缺测时不做 AOD 修正", "CAMS/手动输入"),
                        ("常规评分", inf.get("regular_score"), "当前预报证据下的偏保守评分，不是发生概率", "经验评分"),
                        ("上限评分", inf.get("upper_score"), "有利情景下的潜在表现，不是发生概率", "经验评分"),
                        ("彩虹机会指数", r.get("rainbow", {}).get("score"), "经验指数，未校准为真实概率", "经验指数"),
                    ]
                    for title, value, explain, source in rows:
                        if isinstance(value, float): value = round(value, 3)
                        w.writerow([name, title, "未知/缺测" if value is None else value, explain, source])
                    w.writerow([name, "现场遮挡判断", "待现场验证", "预测云量不能单独确认实际地平线遮挡", "需现场问卷" ])
                    self._save_profile_svg(r, key, os.path.join(folder, f"{key}_cloud_profile.svg"))
                    self._save_scene_svg(r, key, os.path.join(folder, f"{key}_scene_estimate.svg"))
                    # JPG 是用户可直接双击查看的主图；SVG 继续保留作矢量备份。
                    self._save_profile_jpg(r, key, os.path.join(folder, f"{key}_cloud_profile.jpg"), self.vars["place"].get())
                    self._save_scene_jpg(r, key, os.path.join(folder, f"{key}_scene_estimate.jpg"), self.vars["place"].get())
                    # DEBUG：同时导出可直接用于实况模拟的分层 Excel，不增加天气请求。
                    self._export_single_mode_live_excel(folder, key, r)
            # 详细日志保留完整查询报告；简要日志用于快速回顾结论。
            detailed_report = self.last_report or "本次没有生成详细文本报告。"
            with open(os.path.join(folder, "analysis_report_detailed.txt"), "w", encoding="utf-8-sig") as f:
                f.write(detailed_report)
            with open(os.path.join(folder, "analysis_summary.txt"), "w", encoding="utf-8-sig") as f:
                f.write(self._build_summary_report())
            # 兼容旧版文件名，analysis_report.txt 仍保留详细日志内容。
            with open(os.path.join(folder, "analysis_report.txt"), "w", encoding="utf-8-sig") as f:
                f.write(detailed_report)
            self.loaded_case_path = folder
            messagebox.showinfo("案例已保存", f"案例已保存到：\n{folder}\n\n再次保存同一天同一模式会更新预测文件，并保留已填写的现场实况记录。")
        except Exception as exc:
            messagebox.showerror("保存案例失败", f"保存过程中发生错误：\n{exc}")

    def load_case(self):
        folder = filedialog.askdirectory(title="选择要加载的案例文件夹", initialdir=self.case_root if os.path.isdir(self.case_root) else os.path.dirname(os.path.abspath(__file__)))
        if not folder:
            return
        state_path = os.path.join(folder, "analysis_state.json")
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            # 只恢复已保存的显示状态，不触发网络请求或重算评分。旧案例缺少海拔字段时回到 0 m，避免沿用上一个地点。
            if "elevation" in self.vars and state.get("elevation_m") in (None, ""):
                self.vars["elevation"].set("0")
            for key, state_key in (("place", "place"), ("date", "date"), ("lat", "latitude"), ("lon", "longitude"),
                                   ("elevation", "elevation_m"), ("weather_model", "weather_model"), ("cloud_base_mode", "cloud_base_mode")):
                if state.get(state_key) is not None and key in self.vars:
                    self.vars[key].set(str(state[state_key]))
            for key, value in state.get("aod_inputs", {}).items():
                if key in self.vars: self.vars[key].set(str(value))
            for key, value in state.get("cloud_base_inputs", {}).items():
                if key in self.vars: self.vars[key].set(str(value))
            self.direction_results = state.get("direction_results", {})
            # 旧案例兼容：旧版保存的结果没有统一低云颜色系数时，按当前规则补算；不联网。
            for _direction_key, _saved_result in self.direction_results.items():
                _inf = _saved_result.get("inferred", {}) if isinstance(_saved_result, dict) else {}
                if isinstance(_inf, dict) and "low_cloud_color_factor" not in _inf:
                    _samples = _saved_result.get("samples", [])
                    _local = next((x for x in _samples if x.get("distance_km") == 0), _samples[0] if _samples else {})
                    if _inf.get("low") is None and _local.get("cloud_cover_low") is not None:
                        _inf["low"] = _local.get("cloud_cover_low")
                    if _inf.get("color_rgb_before_low_cloud") is not None:
                        _inf["color_rgb"] = _inf["color_rgb_before_low_cloud"]
                    if _inf.get("color_pos_before_low_cloud") is not None:
                        _inf["color_pos"] = _inf["color_pos_before_low_cloud"]
                    apply_low_cloud_screen_constraint(_inf, samples=_samples, source="loaded_case")
            self.last_report = state.get("last_report", "")
            self.last_fetch_time = state.get("last_fetch_time")
            for key, prefix, empty in (("morning", "morning", "朝霞暂无保存结果"), ("evening", "evening", "晚霞暂无保存结果")):
                r = self.direction_results.get(key, {})
                self._set_text(getattr(self, f"{prefix}_summary"), r.get("summary", empty))
                inf = r.get("inferred", {})
                getattr(self, f"{prefix}_score_label").configure(text=f"{inf.get('regular_score', '—')}%" if inf.get("regular_score") is not None else "—")
                getattr(self, f"{prefix}_upper_label").configure(text=f"上限 {inf.get('upper_score', '—')}%")
                rainbow = r.get("rainbow", {})
                getattr(self, f"{prefix}_rainbow_label").configure(
                    text=self._rainbow_indicator_text(rainbow, r.get("event_dt"), key))
                self._draw_colorbar(key)
                aod = r.get("aod")
                getattr(self, f"{prefix}_aod_status_var").set("案例中记录：AOD 缺测" if aod is None else f"案例中记录：AOD={aod:.3f}")
            self.loaded_case_path = folder
            messagebox.showinfo("案例已加载", f"已从本地加载案例：\n{folder}\n\n没有发起天气请求。点击‘高度剖面’可查看当前案例的剖面数据；图片文件也保存在案例目录中。")
        except Exception as exc:
            messagebox.showerror("加载案例失败", f"无法读取案例状态文件：\n{exc}")

    def record_field_observation(self):
        folder = getattr(self, "loaded_case_path", None)
        if not folder or not os.path.isfile(os.path.join(folder, "analysis_state.json")):
            if getattr(self, "direction_results", None):
                folder = self._case_folder_for_current()
                if not os.path.isfile(os.path.join(folder, "analysis_state.json")):
                    if messagebox.askyesno("先保存预测案例", "当前预测还没有保存成案例。是否先保存案例，再填写现场实况？"):
                        self.save_case()
                        folder = getattr(self, "loaded_case_path", None)
                    else:
                        return
            else:
                messagebox.showinfo("提示", "请先加载一个案例，或完成并保存一次单日详细分析。")
                return
        if not folder or not os.path.isfile(os.path.join(folder, "analysis_state.json")):
            return
        state_path = os.path.join(folder, "analysis_state.json")
        try:
            with open(state_path, "r", encoding="utf-8") as f: state = json.load(f)
        except Exception as exc:
            messagebox.showerror("读取案例失败", str(exc)); return
        old = {}
        obs_path = os.path.join(folder, "field_observation.json")
        if os.path.isfile(obs_path):
            try:
                with open(obs_path, "r", encoding="utf-8") as f: old = json.load(f)
            except Exception: pass
        old_records = old.get("observations", {}) if isinstance(old.get("observations"), dict) else {}
        # 兼容试用版早期单条记录格式。
        if not old_records and old.get("direction"):
            old_records = {old.get("direction"): old}
        default_direction = old.get("last_direction", "晚霞")
        selected_old = old_records.get(default_direction, {})
        win = tk.Toplevel(self); win.title("记录现场实况并对照预测"); win.geometry("650x690"); win.transient(self)
        outer = ttk.Frame(win, padding=14); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=f"案例：{os.path.basename(folder)}", style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Label(outer, text="尽量在日出/日落后填写。选择‘不确定/未观察’不会被当成预测错误。", style="Sub.TLabel", wraplength=600).pack(anchor="w", pady=(0, 10))
        fields = [
            ("direction", "记录哪个时段", ["晚霞", "朝霞"]),
            ("actual_fire_cloud", "是否实际出现可观赏的火烧云", ["明显出现", "有但较弱", "没有出现", "未观察/不确定"]),
            ("actual_color_saturation", "实际色彩饱和度", ["很鲜艳", "中等", "偏淡", "基本无色彩", "未观察/不确定"]),
            ("actual_low_cloud_obstruction", "低云/近地云是否遮挡太阳方向", ["明显遮挡", "部分遮挡", "没有明显遮挡", "未观察/不确定"]),
            ("actual_cloud_gap", "太阳方向是否观察到明显云隙/晴空带", ["有明显云隙", "没有明显云隙", "不确定/看不到远处"]),
            ("actual_cloud_boundary", "远处云边界（可估距离）", ["0–50 km", "50–100 km", "100–200 km", "200 km 以外", "无法估计"]),
            ("actual_pollution_haze", "现场空气通透度/雾霾", ["通透", "轻度灰霾", "明显灰霾/雾", "未观察/不确定"]),
            ("actual_cloud_type", "主要受光云层形态", ["高云/卷云为主", "中高云混合", "低云/层云为主", "云型复杂/无法判断", "未观察/不确定"]),
            ("actual_photo", "是否拍到可供后续核对的照片", ["有照片", "无照片"]),
        ]
        controls = {}
        for key, label, options in fields:
            row=ttk.Frame(outer); row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=30).pack(side="left", anchor="w")
            initial = default_direction if key == "direction" else selected_old.get(key, options[0])
            var=tk.StringVar(value=initial); controls[key]=var
            ttk.Combobox(row, textvariable=var, values=options, state="readonly", width=28).pack(side="right", fill="x", expand=True)
        ttk.Label(outer, text="补充备注（可写云层方向、持续时间、拍摄位置、照片文件名等）").pack(anchor="w", pady=(8, 2))
        note=tk.Text(outer, height=4, wrap="word"); note.pack(fill="x")
        note.insert("1.0", selected_old.get("notes", ""))
        def save_obs():
            obs={k:v.get() for k,v in controls.items()}; obs["notes"]=note.get("1.0","end").strip()
            obs["recorded_at"]=datetime.now().astimezone().isoformat(); obs["case_folder"]=os.path.basename(folder)
            observations = dict(old_records)
            direction_key = "morning" if obs["direction"] == "朝霞" else "evening"
            pred = state.get("direction_results", {}).get(direction_key, {})
            inf=pred.get("inferred", {})
            actual=obs.get("actual_fire_cloud")
            score=inf.get("regular_score")
            # 仅生成可解释的粗分类差异提示，不把主观现场判断伪装成严格统计误差。
            comparisons=[]
            if actual not in (None,"未观察/不确定"):
                if actual=="明显出现" and isinstance(score,(int,float)) and score < 40:
                    comparisons.append("预测评分偏低但现场明显出现：需复核是否低估了有利云层/云隙。")
                elif actual=="没有出现" and isinstance(score,(int,float)) and score >= 65:
                    comparisons.append("预测评分较高但现场未出现：需复核路径云况、低云遮挡、云层厚度或模型误差。")
                elif actual=="有但较弱" and isinstance(score,(int,float)) and score >= 75:
                    comparisons.append("预测潜力较高但现场表现较弱：需检查云隙、低云遮挡和云层实际受光情况。")
            if obs.get("actual_low_cloud_obstruction")=="明显遮挡":
                comparisons.append("现场确认低云明显遮挡：重点复核本地低云量、云底高度和遮挡判断。")
            if obs.get("actual_cloud_gap")=="有明显云隙":
                comparisons.append("现场观察到云隙：可与预测中的晴空间隔起止距离核对。")
            if obs.get("actual_pollution_haze")=="明显灰霾/雾":
                comparisons.append("现场通透度较差：检查 AOD 数据及气溶胶对色彩判断的影响。")
            obs["comparison_notes"]=comparisons
            observations[obs["direction"]] = obs
            data = {"schema_version": 2, "case_folder": os.path.basename(folder),
                    "last_direction": obs["direction"], "observations": observations}
            try:
                with open(obs_path,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)
                with open(os.path.join(folder,"field_comparison.csv"),"w",encoding="utf-8-sig",newline="") as f:
                    w=csv.writer(f); w.writerow(["方向","预测常规分","预测上限分","现场火烧云","现场饱和度","低云遮挡","云隙观察","云边界估计","空气通透度","主要云型","照片","差异提示","备注"])
                    for direction, record in observations.items():
                        rk = "morning" if direction == "朝霞" else "evening"
                        ri = state.get("direction_results", {}).get(rk, {}).get("inferred", {})
                        w.writerow([direction,ri.get("regular_score"),ri.get("upper_score"),record.get("actual_fire_cloud"),record.get("actual_color_saturation"),record.get("actual_low_cloud_obstruction"),record.get("actual_cloud_gap"),record.get("actual_cloud_boundary"),record.get("actual_pollution_haze"),record.get("actual_cloud_type"),record.get("actual_photo"),"；".join(record.get("comparison_notes", [])),record.get("notes","")])
                win.destroy()
                messagebox.showinfo("现场记录已保存", "已写入 field_observation.json 与 field_comparison.csv。\n朝霞与晚霞可以分别记录，后续再次保存预测案例不会删除实况记录。\n这些规则只做初步提示，积累更多案例后再统计校准更可靠。")
            except Exception as exc:
                messagebox.showerror("保存现场记录失败", str(exc))
        actions=ttk.Frame(outer); actions.pack(fill="x", pady=(10,0))
        ttk.Button(actions,text="保存现场记录",style="Accent.TButton",command=save_obs).pack(side="right")
        ttk.Button(actions,text="取消",command=win.destroy).pack(side="right",padx=(0,6))

    def save_report(self):
        report = getattr(self, "last_report", "")
        if not report:
            messagebox.showinfo("提示", "请先计算预报。")
            return
        path = filedialog.asksaveasfilename(
            title="保存分析报告",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile=f"朝霞晚霞分析_{date.today().isoformat()}.txt",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(report)
            messagebox.showinfo("保存成功", f"报告已保存：\n{path}")
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))


if __name__ == "__main__":
    app = FireCloudApp()
    app.mainloop()
