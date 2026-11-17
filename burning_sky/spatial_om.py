# -*- coding: utf-8 -*-
"""Experimental *optional* Open-Meteo OM spatial pressure-profile adapter.

This changes neither cloud physics nor published probabilities by itself. It can
replace the remote pressure-level point sampling for a web Pro query only when
all required evidence is present. Failure raises SpatialUnavailable and the
caller falls back to the established Open-Meteo point API.

The reader follows the Open-Meteo official layout and python-omfiles example.
It is intentionally not a complete 3-D cloud or irradiance solver.
"""
from __future__ import annotations
from datetime import datetime, timezone
from functools import lru_cache
import math
import os
from pathlib import Path
import tempfile
import urllib.request
import json

ALLOWED_MODELS = {
    'ecmwf_ifs025': 'ecmwf_ifs025',
    'ncep_gfs_global': 'ncep_gfs025', # DIFFERENT resolution from some seamless API runs; marked as experimental.
}
BASE = 'https://openmeteo.s3.amazonaws.com/data_spatial'
# Basic range-limited request budget to prevent large automatic downloads.
MAX_COORDINATES = 120
MAX_CROP_CELLS = 12000
MAX_TIME_DELTA_SECONDS = 5400
MIN_USABLE_CLOUD_LEVELS = 4


class SpatialUnavailable(RuntimeError):
    pass


def _as_utc(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    raise SpatialUnavailable('无法解析空间数据的预报时间')


def metadata_url(model_id):
    domain = ALLOWED_MODELS.get(model_id)
    if domain is None:
        raise SpatialUnavailable('该预报模式暂不支持空间气压层试验')
    return f'{BASE}/{domain}/latest.json'


@lru_cache(maxsize=8)
def _get_metadata_cached(domain, refresh_bucket):
    url = f'{BASE}/{domain}/latest.json'
    request = urllib.request.Request(url, headers={'User-Agent': 'BurningSkies-SpatialPilot/0.1'})
    with urllib.request.urlopen(request, timeout=8) as response:
        info = json.load(response)
    if not isinstance(info, dict) or not info.get('reference_time') or not info.get('valid_times'):
        raise SpatialUnavailable('最新空间模型清单缺少起报或有效时次')
    if info.get('completed') is not True:
        raise SpatialUnavailable('空间模型尚未完整发布')
    return info


def resolve_valid_file(model_id, event_utc, metadata=None):
    domain = ALLOWED_MODELS.get(model_id)
    if domain is None:
        raise SpatialUnavailable('当前模型不在空间网格试验范围内')
    now = _as_utc(event_utc)
    if metadata is None:
        import time
        metadata = _get_metadata_cached(domain, int(time.time() // 600))
    valid = [_as_utc(v) for v in metadata.get('valid_times', [])]
    if not valid:
        raise SpatialUnavailable('空间文件没有列出有效时间')
    selected = min(valid, key=lambda dt: abs((dt-now).total_seconds()))
    delta = abs((selected-now).total_seconds())
    if delta > MAX_TIME_DELTA_SECONDS:
        raise SpatialUnavailable(f'空间云场距目标时刻 {round(delta/60)} 分钟，拒绝拿错时次代替')
    reference = _as_utc(metadata['reference_time'])
    if reference > selected:
        raise SpatialUnavailable('空间起报时刻晚于有效时刻')
    root = f'{BASE}/{domain}/{reference:%Y/%m/%d/%H%M}Z'
    url = f'{root}/{selected:%Y-%m-%dT%H%M}.om'
    return {
        'model_domain': domain, 'reference_time': reference.isoformat(),
        'valid_time': selected.isoformat(), 'delta_seconds': delta,
        'url': url, 'variables': set(metadata.get('variables', [])),
    }


def _pixel_indices(lat_grid, lon_grid, locations):
    import numpy as np
    lats = np.asarray(lat_grid, dtype=float)
    lons = np.asarray(lon_grid, dtype=float)
    if lats.shape != lons.shape or lats.ndim != 2:
        raise SpatialUnavailable('OM 地理坐标与二维数据形状不符')
    selected = []
    for lat, lon in locations:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise SpatialUnavailable('输入坐标超出 WGS84 范围')
        # Check longitude cyclic distance, including grids with 0..360 longitude.
        lon_error = (lons - lon + 180) % 360 - 180
        score = (lats-lat)**2 + (lon_error*np.cos(np.radians(lat)))**2
        try:
            idx = int(np.nanargmin(score))
        except ValueError as exc:
            raise SpatialUnavailable('OM 网格不包含有效地理坐标') from exc
        row, col = np.unravel_index(idx, score.shape)
        if math.sqrt(float(score[row, col]))*111.3 > 45:
            raise SpatialUnavailable('目标坐标不在模型网格范围内，不能外推')
        selected.append((int(row), int(col)))
    return selected


def _finite(x):
    try:
        a = float(x)
        return a if math.isfinite(a) else None
    except (TypeError, ValueError):
        return None


def _fetch_grid(root, points, cloud_variables, all_names):
    """One rectangle slice per pressure variable, not global-array downloads."""
    import numpy as np
    ys, xs = zip(*points)
    ya, yb = min(ys), max(ys)+1
    xa, xb = min(xs), max(xs)+1
    cells = (yb-ya)*(xb-xa)
    if cells > MAX_CROP_CELLS:
        raise SpatialUnavailable(f'网格切片范围 {cells} 单元过大，拒绝自动批量下载')
    extracted = {}
    for key in cloud_variables:
        if key not in all_names:
            continue
        try:
            child = root.get_child_by_name(key)
            if not child.is_array:
                continue
            arr = np.asarray(child.read_array((slice(ya,yb), slice(xa,xb))))
            if arr.shape != (yb-ya, xb-xa):
                continue
            extracted[key] = [_finite(arr[y-ya,x-xa]) for y,x in points]
        except (AttributeError, KeyError, ValueError):
            continue
    return extracted, cells


def _terrain_elevations(coords):
    # One metadata request for all coordinates. Reject missing terrain rather
    # than shifting vertical cloud structures by an ungrounded 0 m.
    from urllib.parse import urlencode
    query = urlencode({
        'latitude': ','.join(f'{c[0]:.5f}' for c in coords),
        'longitude': ','.join(f'{c[1]:.5f}' for c in coords),
    })
    req = urllib.request.Request('https://api.open-meteo.com/v1/elevation?'+query,
                                 headers={'User-Agent':'BurningSkies-SpatialPilot/0.1'})
    with urllib.request.urlopen(req, timeout=12) as response:
        payload = json.load(response)
    heights = payload.get('elevation') if isinstance(payload, dict) else None
    if not isinstance(heights, list) or len(heights)!=len(coords):
        raise SpatialUnavailable('地形高程服务返回不完整；不使用空间气压层')
    out=[_finite(x) for x in heights]
    if any(x is None for x in out):
        raise SpatialUnavailable('地形高程缺测；不使用空间气压层')
    return out


def fetch_pressure_profiles(coords, distances, event_utc, model_id, *,
                            estimate_cloud_base, analyze_vertical_layers,
                            local_ground_elevation_m=None,
                            metadata=None, reader_factory=None, mesh_factory=None,
                            ground_elevations=None):
    """Return original engine's ``{'samples':..., 'levels':...}`` contract.

    ``reader_factory`` and ``mesh_factory`` are injection points for offline tests.
    The verified WKT grid, not assumptions about row order, is used to locate
    samples. Missing values are never treated as clear sky.
    """
    if not coords or len(coords) != len(distances) or len(coords) > MAX_COORDINATES:
        raise SpatialUnavailable('空间采样点数无效或超过安全上限')
    info = resolve_valid_file(model_id, event_utc, metadata)
    names = info['variables']
    # Require actual native pressure cloud fields. Horizontal low/mid/high
    # percentages do NOT supply the vertical evidence for a strict ray check.
    import re
    levels = sorted({int(v.group(1)) for name in names
                     if (v := re.fullmatch(r'cloud_cover_(\d+)hPa', name))}, reverse=True)
    if len(levels) < MIN_USABLE_CLOUD_LEVELS:
        raise SpatialUnavailable('空间清单没有足够的原生气压层云量；不利用二维云量伪造垂直云层')
    want = []
    for level in levels:
        want += [f'cloud_cover_{level}hPa',
                 f'relative_humidity_{level}hPa',
                 f'geopotential_height_{level}hPa']
    if reader_factory is None:
        try:
            import fsspec
            from omfiles import OmFileReader
        except ImportError as exc:
            raise SpatialUnavailable('缺少 omfiles / fsspec；请安装可选空间数据依赖') from exc
        cache_dir = Path(tempfile.gettempdir()) / 'burning_sky_om_cache'
        cache_dir.mkdir(parents=True, exist_ok=True)
        source = fsspec.open('blockcache::'+info['url'], mode='rb',
                            blockcache={'cache_storage':str(cache_dir)},
                            https={'block_size':65536})
        reader_factory = lambda: OmFileReader(source)
    if mesh_factory is None:
        try:
            from omfiles.grids import OmGrid
        except ImportError as exc:
            raise SpatialUnavailable('缺少 omfiles 的 grids 扩展') from exc
        mesh_factory = lambda wkt,shape: OmGrid(wkt, shape).get_meshgrid()
    try:
        with reader_factory() as root:
            if not root.is_group:
                raise SpatialUnavailable('空间 OM 文件不是分层数据结构')
            representative = root.get_child_by_name(f'cloud_cover_{levels[0]}hPa')
            shape = representative.shape
            if len(shape) != 2:
                raise SpatialUnavailable('空间气压层云量不是二维数组')
            crs = root.get_child_by_name('crs_wkt').read_scalar()
            lon_grid, lat_grid = mesh_factory(crs,shape)
            pixels = _pixel_indices(lat_grid,lon_grid,coords)
            data, cells = _fetch_grid(root,pixels,want,names)
    except SpatialUnavailable:
        raise
    except Exception as exc:
        raise SpatialUnavailable(f'OM 文件读取失败：{type(exc).__name__}: {str(exc)[:160]}') from exc
    usable_levels = [p for p in levels if f'cloud_cover_{p}hPa' in data]
    heights = [p for p in usable_levels if f'geopotential_height_{p}hPa' in data]
    if len(usable_levels) < MIN_USABLE_CLOUD_LEVELS or len(heights) < MIN_USABLE_CLOUD_LEVELS:
        raise SpatialUnavailable('实际 OM 文件缺少足够的云量或位势高度气压层，保持 API 回退')
    # Model-level height is above sea level. Terrain must be accounted for;
    # setting every remote site's elevation to zero corrupts the ray path.
    if ground_elevations is None:
        ground_elevations = _terrain_elevations(coords)
    if len(ground_elevations)!=len(coords):
        raise SpatialUnavailable('远端地形高程数量不符')
    out=[]
    for i, (dist,coord) in enumerate(zip(distances,coords)):
        profile_levels=[]
        count_valid_cloud=0
        for pressure in usable_levels:
            def get(prefix):
                vals=data.get(f'{prefix}_{pressure}hPa')
                return vals[i] if vals else None
            c=get('cloud_cover')
            if c is not None: count_valid_cloud+=1
            height=get('geopotential_height')
            profile_levels.append({'pressure_hpa':pressure,'height_m_asl':height,
                                   'height_source':'model_geopotential' if height is not None else 'missing',
                                   'cloud_cover':c,'relative_humidity':get('relative_humidity')})
        if count_valid_cloud < MIN_USABLE_CLOUD_LEVELS:
            raise SpatialUnavailable(f'{dist} km 空间云量有效气压层不足')
        ground = float(local_ground_elevation_m) if i==0 and local_ground_elevation_m is not None else _finite(ground_elevations[i])
        if ground is None:
            raise SpatialUnavailable(f'{dist} km 无有效地形高程')
        vertical=analyze_vertical_layers(profile_levels)
        formatted=vertical['levels']
        estimate=estimate_cloud_base(formatted,ground)
        out.append({'distance_km':dist,'lat':coord[0],'lon':coord[1],
                    'time':info['valid_time'], 'ground_elevation_m':ground,
                    'elevation_note':'空间原生气压层；使用地形高程 API 校准离地高度',
                    'vertical_structure':vertical,**estimate,
                    'spatial_model_domain':info['model_domain'],
                    'spatial_run_time':info['reference_time']})
    return {'samples':out,'levels':usable_levels,'spatial_source':{
        'domain':info['model_domain'],'valid_time':info['valid_time'],
        'reference_time':info['reference_time'],'cells_read':cells,
        'points':len(coords),'pressure_levels':len(usable_levels),
        'experimental_gfs_domain_mismatch':model_id=='ncep_gfs_global'}}
