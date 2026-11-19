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
    # GFS OM remains readable only by the separate read-only national-map /
    # diagnostic tools. Forecast cloud geometry never consumes this product.
    'ncep_gfs_global': 'ncep_gfs025',
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


def _fetch_grid(root, points, cloud_variables, all_names, *, include_raster=False):
    """One rectangle slice per pressure variable, not global-array downloads."""
    import numpy as np
    ys, xs = zip(*points)
    ya, yb = min(ys), max(ys)+1
    xa, xb = min(xs), max(xs)+1
    cells = (yb-ya)*(xb-xa)
    if cells > MAX_CROP_CELLS:
        raise SpatialUnavailable(f'网格切片范围 {cells} 单元过大，拒绝自动批量下载')
    extracted = {}
    rasters = {}
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
            if include_raster:
                rasters[key] = [[_finite(v) for v in row] for row in arr]
        except (AttributeError, KeyError, ValueError):
            continue
    if include_raster:
        return extracted, cells, rasters, (ya, yb, xa, xb)
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


def _gfs_cloud_level_groups(names):
    """Pressure class proxy for GFS025, *not* native low/mid/high layers.

    GFS025 spatial files have pressure-level variables but are not guaranteed
    to publish the three surface cloud layer products.  A level is never
    considered clear simply because its native cloud_cover field is missing.
    The proxy is deliberately labeled as pressure-layer maximum, not satellite
    cloud or exact altitude based cloud cover.
    """
    import re
    levels = sorted({int(m.group(1)) for name in names
                     if (m := re.fullmatch(r'cloud_cover_(\d+)hPa', str(name)))},reverse=True)
    groups = {
        'cloud_cover_low': [p for p in levels if p >= 700],
        'cloud_cover_mid': [p for p in levels if 400 <= p < 700],
        'cloud_cover_high': [p for p in levels if 100 <= p < 400],
    }
    # Avoid 'clear sky' derived from a single sparse level.
    if any(len(ps) < 2 for ps in groups.values()):
        raise SpatialUnavailable('GFS025 OM 气压层云量不够完整：各低/中/高压强区间至少需 2 层；清单所含云量气压层：'+str(levels))
    # Bounded cost: use 4-5 levels per band, maintaining vertical coverage.
    chosen = {}
    for key, ps in groups.items():
        if len(ps) <= 5: chosen[key] = ps
        else:
            ix = sorted({round(i*(len(ps)-1)/4) for i in range(5)})
            selected = [ps[i] for i in ix]
            if key == 'cloud_cover_high' and 350 in ps and 350 not in selected:
                # Preserve 350hPa (~8km) evidence when present rather than
                # losing a narrow cloud layer to evenly spaced subsampling.
                # Keep the end levels, replace one interior level;
                # never issue one extra OM field request for this adjustment.
                interior = [v for v in selected[1:-1]]
                if interior:
                    replacement = min(interior, key=lambda v: abs(v-350))
                    selected.remove(replacement)
                    selected.append(350)
                    selected.sort(reverse=True)
            chosen[key] = selected
    return chosen


def _cloud_spec(info):
    """Determine field contract from the actual OM manifest, never assumptions."""
    names = set(info['variables'])
    native=('cloud_cover_low','cloud_cover_mid','cloud_cover_high')
    if all(k in names for k in native):
        return {'mode':'native_layers', 'fields':list(native),'groups':{k:[k] for k in native},
                'evidence':'2D_low_mid_high_model_cover_only'}
    if info['model_domain']=='ncep_gfs025':
        groups=_gfs_cloud_level_groups(names)
        fields=sorted({f'cloud_cover_{p}hPa' for ps in groups.values() for p in ps})
        return {'mode':'pressure_layer_proxy','fields':fields,
                'groups':{k:[f'cloud_cover_{p}hPa' for p in ps] for k,ps in groups.items()},
                'evidence':'GFS_pressure_level_max_cloud_proxy_not_native_layer'}
    missing=[k for k in native if k not in names]
    raise SpatialUnavailable('空间清单缺少二维低中高云量: '+', '.join(missing))


# Only GFS pressure-level cloud values *slightly* above the physical ceiling
# are interpreted as saturated 100% cloud. This never converts unknown or
# negative values to clear sky. Severe overflow remains unknown/NaN.
GFS_CLOUD_OVERFLOW_TOLERANCE_PCT = 1.0


def _normalize_gfs_cloud_overflow(values):
    """Round 100..101% GFS pressure cloud to 100%; preserve other anomalies."""
    import numpy as np
    arr = np.asarray(values, dtype=float)
    small_high = (np.isfinite(arr) & (arr > 100.0) &
                  (arr <= 100.0 + GFS_CLOUD_OVERFLOW_TOLERANCE_PCT))
    return np.where(small_high, 100.0, arr)


def _invalid_cloud_mask(values):
    """A missing or out-of-range OM cloud value is unknown, never clear sky.

    Negative fill values, percent overflow, NaN and infinities all remain
    invalid. We intentionally do NOT clip these values into 0..100.
    """
    import numpy as np
    arr=np.asarray(values,dtype=float)
    return (~np.isfinite(arr)) | (arr < 0.0) | (arr > 100.0)


def _cloud_data_quality(arrays, spec):
    """Diagnostic counts for the ACTUALLY READ rectangle (not whole globe)."""
    import numpy as np
    out={}
    for name in spec['fields']:
        if name not in arrays:
            continue
        arr=np.asarray(arrays[name],dtype=float)
        gfs_approx = spec['mode'] == 'pressure_layer_proxy'
        near_cap = (np.isfinite(arr) & (arr > 100) &
                    (arr <= 100 + GFS_CLOUD_OVERFLOW_TOLERANCE_PCT)) if gfs_approx else np.zeros(arr.shape, dtype=bool)
        checked = _normalize_gfs_cloud_overflow(arr) if gfs_approx else arr
        bad=_invalid_cloud_mask(checked)
        if np.any(bad) or np.any(near_cap):
            finite=arr[np.isfinite(arr)]
            out[name]={
                'invalid_cells':int(np.count_nonzero(bad)),
                'normalized_near_100_cells':int(np.count_nonzero(near_cap)),
                'normalization_rule':'GFS pressure cloud (100%, 101%] -> 100% only' if gfs_approx else 'none',
                'total_cells':int(arr.size),
                'finite_min':float(np.min(finite)) if finite.size else None,
                'finite_max':float(np.max(finite)) if finite.size else None,
            }
    return out


def _bad_cloud_sample_fields(arrays, spec, row, col):
    """Report the ACTUAL invalid source field/value at a queried grid cell."""
    import numpy as np
    issues=[]
    for name in spec['fields']:
        raw=np.asarray(arrays[name],dtype=float)[row,col]
        if not np.isfinite(raw) or raw < 0 or raw > 100:
            issues.append(f'{name}={float(raw):.6g}')
    return issues


def _combine_cloud_arrays(arrays, spec, *, required_points=None):
    import numpy as np
    out={}
    for layer,fields in spec['groups'].items():
        layers=[]
        for name in fields:
            if name not in arrays:
                raise SpatialUnavailable('OM 实际数组缺失：'+name+'（不把缺测当晴空）')
            x=np.asarray(arrays[name],dtype=float)
            if spec['mode']=='pressure_layer_proxy':
                x=_normalize_gfs_cloud_overflow(x)
            bad=_invalid_cloud_mask(x)
            # For derived GFS bands a single bad cell must not invalidate all
            # OTHER cells in a 25km-grid rectangle. Unknown pixels are NaN.
            # Native EC behavior is deliberately kept unchanged in this patch.
            if spec['mode']!='pressure_layer_proxy' and np.any(np.isfinite(x)&((x<0)|(x>100))):
                raise SpatialUnavailable('OM 云量超出 0–100%：'+name)
            layers.append(np.where(bad,np.nan,x))
        if not layers: raise SpatialUnavailable('OM 分层数据为空：'+layer)
        stack=np.stack(layers,axis=0)
        # Only infer a layer where ALL selected pressure levels are valid.
        # Missing any level means we cannot safely bound the maximum cloud.
        valid=np.all(np.isfinite(stack),axis=0)
        with np.errstate(invalid='ignore'):
            layer_data=np.max(np.where(np.isfinite(stack),stack,-1),axis=0)
        layer_data=np.where(valid,layer_data,np.nan)
        out[layer]=layer_data
    return out


def _om_reader(info, reader_factory=None):
    if reader_factory is not None:return reader_factory()
    try:
        import fsspec
        from omfiles import OmFileReader
    except ImportError as exc:
        raise SpatialUnavailable('缺少 omfiles / fsspec 空间读取依赖') from exc
    cache_dir=Path(tempfile.gettempdir())/'burning_sky_om_cache'
    cache_dir.mkdir(parents=True,exist_ok=True)
    source=fsspec.open('blockcache::'+info['url'],mode='rb',
                       blockcache={'cache_storage':str(cache_dir)},https={'block_size':65536})
    return OmFileReader(source)


def _om_mesh(crs, shape, mesh_factory=None):
    if mesh_factory is None:
        try:
            from omfiles.grids import OmGrid
        except ImportError as exc:
            raise SpatialUnavailable('缺少 omfiles grids 扩展') from exc
        mesh_factory=lambda wkt,shape: OmGrid(wkt,shape).get_meshgrid()
    return mesh_factory(crs,shape)


def _om_cloud_rectangle(root, info, spec, bounds, mesh_factory=None):
    import numpy as np
    representative=root.get_child_by_name(spec['fields'][0])
    shape=representative.shape
    if len(shape)!=2:raise SpatialUnavailable('空间云场不是二维数组')
    crs=root.get_child_by_name('crs_wkt').read_scalar()
    lon_grid,lat_grid=_om_mesh(crs,shape,mesh_factory)
    ya,yb,xa,xb=bounds(lat_grid,lon_grid)
    n=(yb-ya)*(xb-xa)
    if n<=0:raise SpatialUnavailable('网格切片为空')
    raw={}
    for name in spec['fields']:
        try:
            child=root.get_child_by_name(name)
            if not child.is_array:raise SpatialUnavailable('OM 非数组字段：'+name)
            arr=np.asarray(child.read_array((slice(ya,yb),slice(xa,xb))),dtype=float)
            if arr.shape!=(yb-ya,xb-xa):raise SpatialUnavailable('OM 网格形状错误：'+name)
            raw[name]=arr
        except SpatialUnavailable:raise
        except Exception as exc:raise SpatialUnavailable(f'OM 读取 {name} 失败: {type(exc).__name__}: {str(exc)[:130]}') from exc
    composite=_combine_cloud_arrays(raw,spec)
    lat=np.asarray(lat_grid)[ya:yb,xa:xb]
    lon=((np.asarray(lon_grid)[ya:yb,xa:xb]+180)%360)-180
    return lat,lon,composite,n,(ya,yb,xa,xb)


def _grid_payload(lat,lon,cloud,n,coverage):
    import numpy as np
    return {
        'latitude':np.asarray(lat,dtype=float).tolist(),
        'longitude':np.asarray(lon,dtype=float).tolist(),
        'layers':{name:np.asarray(layer,dtype=float).tolist() for name,layer in cloud.items()},
        'shape':[int(lat.shape[0]),int(lat.shape[1])],
        'coverage':coverage,
        'note':'Only actual cells read from OM. Missing pixels remain unknown; pressure-derived GFS bands are approximation.',
    }


def _cloud_source_info(info,spec,n,points):
    return {
        'domain':info['model_domain'],'reference_time':info['reference_time'],
        'valid_time':info['valid_time'], 'url':info['url'],
        'cells_read':n,'points':points,'fields':list(spec['fields']),
        'layer_origin':spec['mode'], 'evidence':spec['evidence'],
        'pressure_profile_source':'point_api_required',
        'model_run_equality_with_api':'unverified',
    }


def fetch_horizontal_clouds(coords, distances, event_utc, model_id, *,
                            metadata=None, reader_factory=None, mesh_factory=None):
    """EC native 2D layers or *GFS025 pressure-level-derived* 2D layer proxy.

    Both preserve the original API vertical profile.  Derived GFS layers are
    a diagnostic 2.5-D approximation: max native cloud across pressure bands,
    and must not be represented as measured native low/mid/high layers.
    """
    if not coords or len(coords)!=len(distances) or len(coords)>MAX_COORDINATES:
        raise SpatialUnavailable('二维空间云场采样点数无效或超出上限')
    info=resolve_valid_file(model_id,event_utc,metadata)
    spec=_cloud_spec(info)
    try:
        with _om_reader(info,reader_factory) as root:
            if not root.is_group:raise SpatialUnavailable('空间 OM 文件不是分层数据结构')
            child=root.get_child_by_name(spec['fields'][0]);shape=child.shape
            crs=root.get_child_by_name('crs_wkt').read_scalar()
            lons,lats=_om_mesh(crs,shape,mesh_factory)
            pixels=_pixel_indices(lats,lons,coords)
            ys,xs=zip(*pixels)
            bounds=(min(ys),max(ys)+1,min(xs),max(xs)+1)
            n=(bounds[1]-bounds[0])*(bounds[3]-bounds[2])
            if n>MAX_CROP_CELLS:
                raise SpatialUnavailable('局部网格切片范围过大，拒绝下载：'+str(n))
            ya,yb,xa,xb=bounds
            raw={}
            import numpy as np
            for field in spec['fields']:
                child=root.get_child_by_name(field)
                arr=np.asarray(child.read_array((slice(ya,yb),slice(xa,xb))),dtype=float)
                if arr.shape!=(yb-ya,xb-xa):raise SpatialUnavailable('云场矩形读取形状不符：'+field)
                raw[field]=arr
            cloud=_combine_cloud_arrays(raw,spec)
            lat=np.asarray(lats)[ya:yb,xa:xb]
            lon=((np.asarray(lons)[ya:yb,xa:xb]+180)%360)-180
            rows=[]
            for (plat,plon),(y,x),distance in zip(coords,pixels,distances):
                row={'distance_km':distance,'lat':plat,'lon':plon}
                for key,arr in cloud.items():
                    v=_finite(arr[y-ya,x-xa])
                    if v is None:
                        fields=_bad_cloud_sample_fields(raw,spec,y-ya,x-xa)
                        detail='；原始异常值：'+', '.join(fields[:6]) if fields else ''
                        raise SpatialUnavailable(f'{distance} km 的 {key} 数据无效或缺测{detail}；不把缺测当晴空')
                    row[key]=v
                rows.append(row)
            grid_view=_grid_payload(lat,lon,cloud,n,'sample_coordinate_bounding_rectangle_only')
    except SpatialUnavailable:raise
    except Exception as exc:
        raise SpatialUnavailable(f'空间二维云场读取失败: {type(exc).__name__}: {str(exc)[:160]}') from exc
    source=_cloud_source_info(info,spec,n,len(rows))
    source['invalid_cells_in_read_rectangle']=_cloud_data_quality(raw,spec)
    return {'samples':rows,'grid_view':grid_view,'spatial_source':source}


# The nationwide rectangle is deliberately requested only by an explicit user
# click; drawing an existing snapshot never opens a remote connection.
CHINA_BOUNDS=(73.0,136.0,17.5,54.5) # west,east,south,north
MAX_NATIONAL_CELLS=90000


def fetch_national_clouds_from_source(spatial_source, *,reader_factory=None,mesh_factory=None,
                                      bounds=CHINA_BOUNDS):
    """Read one run/valid's China raster, not a fake enlargement of a line slice."""
    import numpy as np
    info=dict(spatial_source or {})
    domain=info.get('domain')
    if domain not in ALLOWED_MODELS.values():raise SpatialUnavailable('全国图数据源不是受支持的 OM 模型')
    run=_as_utc(info.get('reference_time'))
    valid=_as_utc(info.get('valid_time'))
    url=f'{BASE}/{domain}/{run:%Y/%m/%d/%H%M}Z/{valid:%Y-%m-%dT%H%M}.om'
    info={'model_domain':domain,'reference_time':run.isoformat(), 'valid_time':valid.isoformat(),
          'url':url,'variables':set(info.get('available_variables') or info.get('fields') or [])}
    # For GFS, saved fields are the 2–5 pressure variables chosen for each
    # band; use precisely those same layer keys without a new manifest request.
    spec=_cloud_spec(info)
    try:
        with _om_reader(info,reader_factory) as root:
            if not root.is_group:raise SpatialUnavailable('全国 OM 文件不是数据组')
            field=root.get_child_by_name(spec['fields'][0]);shape=field.shape
            crs=root.get_child_by_name('crs_wkt').read_scalar()
            lons,lats=_om_mesh(crs,shape,mesh_factory)
            west,east,south,north=bounds
            mask=(np.isfinite(lats)&np.isfinite(lons)&(lats>=south)&(lats<=north)&
                  ((((lons+180)%360)-180)>=west)&((((lons+180)%360)-180)<=east))
            if not np.any(mask):raise SpatialUnavailable('中国区域不在当前 OM 网格中')
            ys,xs=np.where(mask)
            ya,yb,xa,xb=int(ys.min()),int(ys.max()+1),int(xs.min()),int(xs.max()+1)
            n=(yb-ya)*(xb-xa)
            if n>MAX_NATIONAL_CELLS:raise SpatialUnavailable(f'全国网格 {n} 单元超过安全限制 {MAX_NATIONAL_CELLS}')
            raw={}
            for name in spec['fields']:
                arr=np.asarray(root.get_child_by_name(name).read_array((slice(ya,yb),slice(xa,xb))),dtype=float)
                if arr.shape!=(yb-ya,xb-xa):raise SpatialUnavailable('全国 OM 数据维度有误：'+name)
                raw[name]=arr
            cloud=_combine_cloud_arrays(raw,spec)
            lat=np.asarray(lats)[ya:yb,xa:xb]
            lon=((np.asarray(lons)[ya:yb,xa:xb]+180)%360)-180
            valid_mask=mask[ya:yb,xa:xb]
            cloud={k:np.where(valid_mask,v,np.nan) for k,v in cloud.items()}
            grid_view=_grid_payload(lat,lon,cloud,n,'china_extent_actual_om_grid_only')
    except SpatialUnavailable:raise
    except Exception as exc:raise SpatialUnavailable(f'全国云场读取失败: {type(exc).__name__}: {str(exc)[:160]}') from exc
    source=_cloud_source_info(info,spec,n,0)
    source['coverage']='China extent';source['national_overlay']=True
    source['invalid_cells_in_read_rectangle']=_cloud_data_quality(raw,spec)
    return {'samples':[], 'grid_view':grid_view, 'spatial_source':source}


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
