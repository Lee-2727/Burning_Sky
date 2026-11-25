# -*- coding: utf-8 -*-
"""Immutable China-region reference geography, separate from any forecast data.

Natural Earth 1:50m admin-1 is public domain. The source is acquired explicitly
once, validated, and cached on disk; drawing is *always offline*. Boundaries
reflect that source's cartographic conventions, not an authoritative legal map.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
SOURCE_URL = ('https://raw.githubusercontent.com/nvkelso/natural-earth-vector/'
              'master/geojson/ne_50m_admin_1_states_provinces.geojson')
CACHE = PROJECT / 'desktop_spatial_debug' / 'map_basemap' / 'china_admin1_reference.geojson'

# Reference points only; not boundaries. Use capital coordinates for legibility.
CITIES = [
    ('北京',116.407,39.904),('天津',117.201,39.084),('石家庄',114.515,38.042),
    ('太原',112.550,37.870),('呼和浩特',111.748,40.842),('沈阳',123.432,41.805),
    ('长春',125.323,43.818),('哈尔滨',126.535,45.803),('上海',121.473,31.230),
    ('南京',118.797,32.060),('杭州',120.155,30.274),('合肥',117.227,31.820),
    ('福州',119.296,26.075),('南昌',115.859,28.683),('济南',117.121,36.651),
    ('郑州',113.625,34.746),('武汉',114.305,30.593),('长沙',112.939,28.228),
    ('广州',113.264,23.129),('南宁',108.367,22.817),('海口',110.199,20.045),
    ('重庆',106.551,29.563),('成都',104.066,30.572),('贵阳',106.630,26.647),
    ('昆明',102.712,25.040),('拉萨',91.132,29.660),('西安',108.939,34.341),
    ('兰州',103.834,36.061),('西宁',101.779,36.617),('银川',106.231,38.487),
    ('乌鲁木齐',87.617,43.826),('台北',121.565,25.033),
    ('香港',114.169,22.319),('澳门',113.544,22.198),
]


# Label anchor points only. Natural Earth cache supplies the actual province lines.
PROVINCE_LABELS = [
    ('新疆',85.0,42.2),('西藏',87.1,31.9),('青海',96.4,35.6),('甘肃',102.2,38.5),
    ('内蒙古',111.1,43.7),('宁夏',106.1,37.2),('陕西',109.0,35.6),('山西',112.4,38.4),
    ('河北',115.1,38.7),('辽宁',123.3,41.7),('吉林',126.1,43.4),('黑龙江',128.1,47.3),
    ('山东',118.6,36.5),('河南',113.3,33.9),('江苏',119.2,33.1),('安徽',117.2,32.7),
    ('浙江',119.6,29.2),('江西',115.7,27.9),('湖北',112.3,31.2),('湖南',111.5,27.3),
    ('福建',118.2,26.4),('广东',113.4,23.5),('广西',108.3,23.5),('海南',109.7,19.1),
    ('四川',102.3,30.7),('重庆',107.0,29.5),('贵州',106.6,26.5),('云南',101.1,24.9),
    ('北京',116.4,40.5),('天津',117.6,39.1),('上海',121.2,31.6),
]


def _validate_and_trim(payload):
    if not isinstance(payload,dict) or payload.get('type')!='FeatureCollection':
        raise ValueError('行政区划数据不是 GeoJSON FeatureCollection')
    results=[]
    for feat in payload.get('features',[]):
        p=feat.get('properties') or {}
        admin=str(p.get('admin') or p.get('geonunit') or '')
        code=str(p.get('adm0_a3') or p.get('gu_a3') or p.get('iso_a2') or '')
        if admin not in ('China','Taiwan') and code not in ('CHN','TWN','CN','TW'):
            continue
        g=feat.get('geometry') or {}
        if g.get('type') not in ('Polygon','MultiPolygon'):
            continue
        rings=g.get('coordinates') or []
        if not rings:continue
        name=str(p.get('name_zh') or p.get('name') or p.get('name_en') or '行政区')
        results.append({'type':'Feature','properties':{'name':name},'geometry':g})
    if len(results)<20:
        raise ValueError(f'行政区划仅找到 {len(results)} 个单元，不保存不完整底图')
    return {'type':'FeatureCollection','source':'Natural Earth 1:50m Admin-1',
            'license':'public domain','features':results}


def prepare_admin_basemap(*,dest=CACHE,fetch=None):
    """Explicit one-time reference download; never invoked during chart redraw."""
    dest=Path(dest)
    if dest.is_file():
        old=json.loads(dest.read_text(encoding='utf-8'))
        return dest,len(old.get('features',[])),False
    if fetch is None:
        def fetch(url):
            req=Request(url,headers={'User-Agent':'BurningSky-Desktop-Basemap/1.0'})
            with urlopen(req,timeout=25) as response:
                # Protect against accidentally downloading a huge/unexpected resource.
                body=response.read(45_000_001)
            if len(body)>45_000_000:raise ValueError('行政区划资源超出 45 MB 安全限额')
            return json.loads(body)
    data=_validate_and_trim(fetch(SOURCE_URL))
    dest.parent.mkdir(parents=True,exist_ok=True)
    tmp=dest.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    os.replace(tmp,dest)
    return dest,len(data['features']),True


def boundary_segments(path=CACHE):
    """Read cached admin-1 segments, independent of network and cloud data."""
    path=Path(path)
    if not path.exists():return []
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError):return []
    out=[]
    for feature in data.get('features',[]):
        geometry=feature.get('geometry') or {}
        coord=geometry.get('coordinates') or []
        polygons=[coord] if geometry.get('type')=='Polygon' else coord if geometry.get('type')=='MultiPolygon' else []
        for rings in polygons:
            for ring in rings[:1]:
                if len(ring)<3:continue
                try:
                    seg=[(float(x),float(y)) for x,y,*_ in ring]
                except (TypeError,ValueError):continue
                if seg and max(x for x,y in seg)>73 and min(x for x,y in seg)<136:
                    out.append(seg)
    return out
