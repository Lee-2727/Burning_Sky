# -*- coding: utf-8 -*-
"""Model-specific OM horizontal-cloud policy shared by Pro, Lite and Actions.

EC retains its existing native OM horizontal-layer calibration. GFS OM
horizontal low/mid/high cloud fields are disabled in forecast calculations;
GFS uses only its native point-API pressure-level profile.
"""
from __future__ import annotations
import math
import os
import time
from collections import OrderedDict
from datetime import datetime

from .spatial_ray_fusion import bind_horizontal_profiles

# Bound memory; duplicates within a running five-day job needn't reread OM.
_BATCH_CACHE: OrderedDict = OrderedDict()
_MAX_CACHED_BATCHES = 24
_OM_COOLDOWN: dict[str,float] = {}  # request-limit cooldown, per model


def _is_gfs_model(model_id):
    return 'gfs' in str(model_id or '').strip().lower()


def supported(model_id):
    # OM horizontal-cloud fusion is intentionally retained for EC only.
    # GFS uses its native point-API pressure-level cloud profile exclusively.
    return str(model_id) == 'ecmwf_ifs025'


def _key(coords, event_utc, model_id, spatial_version=None):
    t = event_utc.isoformat() if isinstance(event_utc, datetime) else str(event_utc)
    base = (model_id,t,tuple((round(float(lat),5),round(float(lon),5)) for lat,lon in coords))
    return base if spatial_version is None else base + (tuple(spatial_version),)


def fetch_with_spatial(owner, fallback, coords, distances, event_utc, model_id,
                       local_ground_elevation_m=None):
    """Fetch API pressure first, then attach OM horizontal layer evidence.

    Always preserve valid API pressure if OM fails. No hidden cloud-as-clear
    fallback. Replay/offline restores must not start an unrequested OM download.
    """
    out=fallback(coords,distances,event_utc,model_id,local_ground_elevation_m=local_ground_elevation_m)
    # GFS OM horizontal low/mid/high cloud fields are disabled at the source:
    # do not download, bind, replay, calibrate, or use them as ray vetoes.
    # This is deliberately model-specific; EC keeps its existing OM workflow.
    if _is_gfs_model(model_id):
        if getattr(owner, 'spatial_enabled', False):
            logs=getattr(owner,'spatial_fetch_log',None)
            if logs is None:
                logs=[]; owner.spatial_fetch_log=logs
            logs.append({'status':'gfs_om_horizontal_disabled_native_pressure_only',
                         'model':model_id,'distance_count':len(distances),
                         'reason':'GFS OM 水平低/中/高云场已停用；仅使用 GFS 原生 API 气压层剖面'})
        return out
    if not getattr(owner,'spatial_enabled',False) or not supported(model_id):
        return out
    logs=getattr(owner,'spatial_fetch_log',None)
    if logs is None:
        logs=[]; owner.spatial_fetch_log=logs
    ctx=None
    try:
        import engine as runtime_engine
        ctx=getattr(runtime_engine,'_CASE_REQUEST_CONTEXT',None)
        if isinstance(ctx,dict) and ctx.get('mode')=='replay':
            logs.append({'status':'spatial_skipped_replay','model':model_id})
            return out
        if isinstance(ctx,dict) and (ctx.get('cloud_overrides') or ctx.get('satellite_recompute')):
            logs.append({'status':'spatial_skipped_manual_overrides','model':model_id,
                         'reason':'卫星/人工云图修正优先，使用缓存气压剖面与已确认修正，不额外请求 OM 水平云场'})
            return out
    except ImportError:
        pass
    base_key=_key(coords,event_utc,model_id)
    try:
        from .spatial_om import (
            get_metadata_for_event, resolve_valid_file, fetch_horizontal_clouds
        )
        # Every explicit forecast checks the tiny S3 manifests. It downloads
        # the much larger .om raster only when the selected run/valid-time
        # identity differs from the in-process batch cache.
        manifest=get_metadata_for_event(model_id,event_utc,refresh=True)
        resolved=resolve_valid_file(model_id,event_utc,metadata=manifest)
        spatial_version=(resolved['reference_time'],resolved['valid_time'],resolved['url'])
        key=_key(coords,event_utc,model_id,spatial_version)
        horizontal=_BATCH_CACHE.get(key)
        if horizontal is None:
            until=_OM_COOLDOWN.get(model_id,0)
            if time.monotonic()<until:
                logs.append({'status':'point_api_fallback','model':model_id,
                             'distance_count':len(distances),
                             'reason':'OM data-file rate-limit cooldown active; 20-minute stop, no new .om download',
                             'manifest_kind':resolved.get('manifest_kind'),
                             'run':resolved.get('reference_time'),'valid':resolved.get('valid_time')})
                return out
            horizontal=fetch_horizontal_clouds(
                coords,distances,event_utc,model_id,metadata=manifest
            )
            _BATCH_CACHE[key]=horizontal
            if len(_BATCH_CACHE)>_MAX_CACHED_BATCHES:
                _BATCH_CACHE.popitem(last=False)
            cache_status='spatial_downloaded_new_version'
        else:
            _BATCH_CACHE.move_to_end(key)
            cache_status='spatial_cache_hit_version_verified'
        # Persist the exact auxiliary horizontal cloud evidence alongside the
        # normal URL response cache so same-day replay/calibration retains every
        # source actually used by the forecast.
        if isinstance(ctx,dict) and ctx.get('mode')=='capture' and isinstance(ctx.get('cache'),dict):
            ctx['cache'].setdefault('__burning_sky_spatial__',{})[key]=horizontal
        attached=bind_horizontal_profiles(out.get('samples',[]),horizontal)
        info=horizontal.get('spatial_source') or {}
        used=bool(attached.get('attached'))
        logs.append({'status':cache_status if used else 'point_api_fallback',
                     'model':model_id,'distance_count':len(distances),
                     'horizontal_bound_points':attached.get('attached',0),
                     'reason':attached.get('reason'),
                     'run':info.get('reference_time') or resolved.get('reference_time'),
                     'valid':info.get('valid_time') or resolved.get('valid_time'),
                     'manifest_kind':resolved.get('manifest_kind'),
                     'manifest_checks':resolved.get('manifest_checks'),
                     'manifest_last_modified':resolved.get('manifest_last_modified'),
                     'layer_origin':info.get('layer_origin'),'evidence':info.get('evidence'),
                     'pressure_source':'point_api',
                     'run_match':'not_proven',
                     'note':'每次主动查询检查 in-progress/latest 清单；仅当目标 OM run/valid-time 版本变化时读取新 .om 文件；缺测不解释为晴空'})
    except Exception as exc:
        if '429' in str(exc) or 'rate limit' in str(exc).lower() or '限流' in str(exc):
            _OM_COOLDOWN[model_id]=time.monotonic()+1200
        logs.append({'status':'point_api_fallback','model':model_id,
                     'distance_count':len(distances),
                     'reason':f'{type(exc).__name__}: {str(exc)[:240]}'})
    return out


class HybridSpatialMixin:
    """Use before engine.FireCloudApp in each frontend MRO."""
    def _fetch_cloud_base_profiles(self, coords, distances, sunset_utc, model_id,
                                   local_ground_elevation_m=None):
        parent=super()._fetch_cloud_base_profiles
        return fetch_with_spatial(self,parent,coords,distances,sunset_utc,model_id,
                                  local_ground_elevation_m)


def _strip_gfs_horizontal_evidence(samples, profiles, *, manual=False):
    """Remove all legacy GFS OM-horizontal evidence from forecast inputs.

    When a pre-change case/cache already contains OM-calibrated area fields,
    restore the saved point-API baseline. If the baseline is absent, mark the
    affected layer unknown rather than silently retaining a possibly OM-edited
    value. User/manual edits remain authoritative.
    """
    fields = ('cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high')
    restored = 0
    unknowned = 0
    stripped_profiles = 0
    for sample in samples or []:
        if not isinstance(sample, dict):
            continue
        had_spatial_calibration = bool(sample.get('spatial_calibration')) or any(
            str(k).startswith('spatial_horizontal_') for k in sample)
        if not manual:
            for field in fields:
                baseline_key = 'point_api_area_' + field
                baseline = sample.get(baseline_key)
                if isinstance(baseline, (int, float)) and math.isfinite(float(baseline)):
                    sample[field] = float(baseline)
                    restored += 1
                elif had_spatial_calibration:
                    sample[field] = None
                    unknowned += 1
        for key in list(sample):
            if str(key).startswith('spatial_horizontal_') or key in (
                'spatial_calibration', 'spatial_calibration_run_unproven',
                'spatial_calibration_conflicts'):
                sample.pop(key, None)
    for profile in profiles or []:
        if not isinstance(profile, dict):
            continue
        keys = [k for k in profile if str(k).startswith('spatial_horizontal_')]
        if keys:
            stripped_profiles += 1
            for key in keys:
                profile.pop(key, None)
    return {'status':'gfs_om_horizontal_disabled',
            'restored_point_api_layers':restored,
            'unknowned_legacy_layers':unknowned,
            'stripped_profile_count':stripped_profiles,
            'manual_values_preserved':bool(manual)}


def calibrate_path_samples(samples, profiles, *, manual=False, model_id=None):
    """Apply horizontal OM cloud calibration for EC only.

    GFS is intentionally excluded: its OM 2-D low/mid/high cloud product is
    not used anywhere in the single-direction forecast cloud field. GFS keeps
    only its native point-API pressure-level profile. EC retains the existing
    OM native-layer calibration unchanged. This function is idempotent after
    upstream profile extension.
    """
    is_gfs = _is_gfs_model(model_id) or any(
        isinstance(p, dict) and (
            p.get('spatial_horizontal_model_domain') == 'ncep_gfs025' or
            p.get('spatial_horizontal_layer_origin') == 'pressure_layer_proxy')
        for p in (profiles or []))
    if is_gfs:
        return _strip_gfs_horizontal_evidence(samples, profiles, manual=manual)
    if manual:
        # Prevent downstream horizontal 2D cross-checks from quietly using raw
        # OM evidence after a user cloud/satellite override has taken priority.
        for p in profiles:
            if isinstance(p, dict) and isinstance(p.get('spatial_horizontal_om'), dict):
                p['spatial_horizontal_effective'] = {}
                p['spatial_horizontal_effective_sources'] = {'all': 'manual_priority'}
        return {'status': 'manual_priority', 'corrected_distances': []}
    pmap = {round(float(p.get('distance_km', -9999)), 4): p
            for p in profiles if isinstance(p, dict)
            and isinstance(p.get('distance_km'), (float, int))}
    fields = ('cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high')
    corrected = []
    changes = []
    conflicts = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        try:
            dist = round(float(sample['distance_km']), 4)
        except (KeyError, ValueError, TypeError):
            continue
        p = pmap.get(dist) or {}
        vals = p.get('spatial_horizontal_om')
        if not isinstance(vals, dict) or p.get('spatial_horizontal_time_uncertainty') is not True:
            continue
        if sample.get('lat') is not None and p.get('lat') is not None:
            try:
                if (abs(float(sample['lat']) - float(p['lat'])) > .0001 or
                        abs(float(sample['lon']) - float(p['lon'])) > .0001):
                    continue
            except (TypeError, ValueError, KeyError):
                continue
        valid = all(isinstance(vals.get(k), (int, float))
                    and math.isfinite(vals[k]) and 0 <= float(vals[k]) <= 100
                    for k in fields)
        if not valid:
            continue
        row_changes = {}
        effective = dict(vals)
        effective_sources = {field: 'om_native' for field in fields}
        for field in fields:
            original_key = 'point_api_area_' + field
            old = sample.get(original_key, sample.get(field))
            if isinstance(old, (float, int)) and math.isfinite(old):
                old = float(old)
                sample[original_key] = old
            else:
                old = None
            new = float(vals[field])
            sample[field] = new
            row_changes[field] = (old, new)
        # The original OM data remain separately inspectable.  These are the
        # values the later horizontal consistency / target checks must use.
        p['spatial_horizontal_effective'] = effective
        p['spatial_horizontal_effective_sources'] = effective_sources
        p.pop('spatial_horizontal_conflict_layers', None)
        sample.pop('spatial_calibration_conflicts', None)
        sample['spatial_calibration'] = 'om_horizontal_layer_25km_cell'
        sample['spatial_calibration_run_unproven'] = True
        corrected.append(dist)
        changes.append({'distance_km': dist, 'layers': row_changes, 'rejected_proxy_layers': []})
    return {
        'status': 'applied' if corrected else 'not_available',
        'corrected_distances': corrected,
        'changes': changes[:30],
        'conflict_count': 0,
        'conflicts': [],
        'caveat': ('EC native OM horizontal layers retain the existing calibration behavior. '
                   'GFS OM horizontal cloud fields are disabled and are not used in forecast calculations.')}
