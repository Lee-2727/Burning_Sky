"""Strict one-click screenshot calibration based on the *existing* Pro pipeline.

There is no UI for manual georeferencing or region selection in Lite. If the
screenshot cannot be registered with >=4 consistent landmarks and three distinct
palette references, it is rejected rather than silently applying guessed edits.
"""
from __future__ import annotations
import hashlib
import io
import json
from PIL import Image, ImageOps
from .satellite_auto_vision import recognize_cities, auto_cloud_references
from .satellite_calibration import _avg_patch, compare_samples
from .lite_forecast import number

MAX_UPLOAD_BYTES = 12 * 1024 * 1024


def photo_candidates(image_bytes: bytes, original: dict):
    if not image_bytes or len(image_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError('请上传12MB以内的卫星截图')
    Image.MAX_IMAGE_PIXELS = 20_000_000
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert('RGB')
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError('截图无法读取或尺寸过大') from exc
    if min(image.size) < 400:
        raise ValueError('截图分辨率不足，无法自动识别地图地标')
    geo = recognize_cities(image)
    marks = geo.get('landmarks') or []
    quality = geo.get('quality') or {}
    if (not geo.get('registration_candidate') or len(marks) < 4
            or number(quality.get('rms_px')) is None
            or float(quality['rms_px']) > 18
            or number(quality.get('max_px')) is None
            or float(quality['max_px']) > 30):
        raise ValueError('无法可靠定位截图中的城市，请上传包含清晰地名和较大地图范围的卫星图')
    palettes = auto_cloud_references(image)
    if any(k not in palettes for k in ('cold','band','warm')):
        raise ValueError('截图云区颜色无法可靠识别，请更换带完整红外色彩的截图')
    rgb = {k:_avg_patch(image, palettes[k]['x'],palettes[k]['y'],r=5)
           for k in ('cold','band','warm')}
    if any(c is None for c in rgb.values()):
        raise ValueError('截图颜色取样不完整')
    landmarks = [(m['city'],float(m['x']),float(m['y'])) for m in marks]
    rows = compare_samples(image,landmarks,rgb['cold'],rgb['warm'],original,band_rgb=rgb['band'])
    good = [r for r in rows if '疑似' in str(r.get('judgement',''))
            and number(r.get('suggested_high')) is not None
            and number(r.get('confidence')) is not None
            and float(r['confidence']) >= .62]
    return image, good, quality


def all_segments(good: list[dict]):
    """Select every verified candidate without an interactive region editor."""
    accepted = sorted((float(r['distance_km']),float(r['suggested_high'])) for r in good)
    dedup = []
    for d, v in accepted:
        if not 0 <= v <= 100:
            raise ValueError('校准建议的云量超出允许范围')
        if dedup and abs(d-dedup[-1][0]) < .05:
            if abs(v-dedup[-1][1]) > .1:
                raise ValueError('同一距离存在矛盾的卫星候选')
            continue
        dedup.append((d,v))
    segments, extra = [], []
    for i,(d,v) in enumerate(dedup):
        distances=[abs(d-other) for j,(other,_) in enumerate(dedup) if j!=i]
        gap = min(distances or [10.0])
        half = min(4.8,max(.25,gap*.45))
        segments.append((max(0.,d-half),d+half,None,None,v))
        extra.extend([max(0.,d-4),d,d+4])
    for x,y in zip(segments,segments[1:]):
        if y[0] < x[1] - 1e-5:
            raise ValueError('相邻校准区间重叠，无法自动处理')
    return segments,sorted({round(x,2) for x in extra})[:60]


def calibrate(original: dict, request_cache: dict, meta: dict, image_bytes: bytes,
              *, compute=None, progress=None, recover_cache=None):
    """Return a new result, never mutating the original or prior request cache.

    compute is an injectable callback for offline tests. Production delegates
    to advanced_inline._compute, preserving geometry, slow-rate policy, and
    selective request-cache replay from Pro.
    """
    image, good, quality = photo_candidates(image_bytes, original)
    if not good:
        return {'result':original,'changed':False,'candidate_count':0,
                'cache':dict(request_cache), 'quality':quality,
                'note':'未发现可可靠修正的云区，保留原始预测。'}
    segments, extra = all_segments(good)
    if compute is None:
        from .advanced_inline import _compute
        compute = _compute
    model = 'GFS' if 'gfs' in str(meta['model_label']).lower() else 'EC'
    config = {'place':meta['place'],'lat':meta['lat'],'lon':meta['lon'],
              'elev':meta['elev'],'date':meta['date'],'model':model,
              'direction':meta['direction']}
    cache = dict(request_cache or {})
    if recover_cache:
        cache.update(recover_cache)
    try:
        result, used_cache = compute(config,meta['direction'],cache=cache,
              overrides=segments,extra=extra, saved_rainbow=None,
              progress_observer=progress)
    except Exception as exc:
        # Caller can save partial request-cache after an interrupted fetch.
        try:
            exc.partial_cache = cache
        except (AttributeError, TypeError):
            pass
        raise
    metrics = result.get('inferred') if isinstance(result,dict) else None
    if not isinstance(metrics,dict) or any(number(metrics.get(k)) is None
        for k in ('regular_score','color_score','burn_scale_index')):
        raise RuntimeError('卫星重算未返回完整概率、色彩分和规模；原始结果保持不变')
    return {'result':result,'changed':True,'candidate_count':len(segments),
            'cache':used_cache,'quality':quality,'note':'卫星校准完成'}
