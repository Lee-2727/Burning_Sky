# -*- coding: utf-8 -*-
"""Shared, fail-open OM horizontal calibration for Pro, Lite, Actions and Desktop.

One shared source path keeps the core physics engine intact.  Atmospheric
height evidence always comes from the existing profile API.  Horizontal OM
coverage is an additional spatial input, never an invented opaque 3-D cloud.
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


def supported(model_id):
    return str(model_id) in ('ecmwf_ifs025', 'ncep_gfs_global')


def _key(coords, event_utc, model_id):
    t = event_utc.isoformat() if isinstance(event_utc, datetime) else str(event_utc)
    return (model_id,t,tuple((round(float(lat),5),round(float(lon),5)) for lat,lon in coords))


def fetch_with_spatial(owner, fallback, coords, distances, event_utc, model_id,
                       local_ground_elevation_m=None):
    """Fetch API pressure first, then attach OM horizontal layer evidence.

    Always preserve valid API pressure if OM fails. No hidden cloud-as-clear
    fallback. Replay/offline restores must not start an unrequested OM download.
    """
    out=fallback(coords,distances,event_utc,model_id,local_ground_elevation_m=local_ground_elevation_m)
    if not getattr(owner,'spatial_enabled',False) or not supported(model_id):
        return out
    logs=getattr(owner,'spatial_fetch_log',None)
    if logs is None:
        logs=[]; owner.spatial_fetch_log=logs
    try:
        import engine as runtime_engine
        ctx=getattr(runtime_engine,'_CASE_REQUEST_CONTEXT',None)
        if isinstance(ctx,dict) and ctx.get('mode')=='replay':
            logs.append({'status':'spatial_skipped_replay','model':model_id})
            return out
    except ImportError:
        pass
    key=_key(coords,event_utc,model_id)
    until=_OM_COOLDOWN.get(model_id,0)
    if time.monotonic()<until:
        logs.append({'status':'point_api_fallback','model':model_id,'distance_count':len(distances),
                     'reason':'OM rate-limit cooldown active; 20-minute stop, no retry'})
        return out
    try:
        horizontal=_BATCH_CACHE.get(key)
        if horizontal is None:
            from .spatial_om import fetch_horizontal_clouds
            horizontal=fetch_horizontal_clouds(coords,distances,event_utc,model_id)
            _BATCH_CACHE[key]=horizontal
            if len(_BATCH_CACHE)>_MAX_CACHED_BATCHES:
                _BATCH_CACHE.popitem(last=False)
        else:
            _BATCH_CACHE.move_to_end(key)
        attached=bind_horizontal_profiles(out.get('samples',[]),horizontal)
        info=horizontal.get('spatial_source') or {}
        used=bool(attached.get('attached'))
        logs.append({'status':'spatial_horizontal_used_vertical_api' if used else 'point_api_fallback',
                     'model':model_id,'distance_count':len(distances),
                     'horizontal_bound_points':attached.get('attached',0),
                     'reason':attached.get('reason'),
                     'run':info.get('reference_time'),'valid':info.get('valid_time'),
                     'layer_origin':info.get('layer_origin'),'evidence':info.get('evidence'),
                     'pressure_source':'point_api',
                     'run_match':'not_proven',
                     'note':'OM horizontal vs point API run identity not proven; no missing data interpreted as clear'})
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


# The API's native 9-point layer cover is authoritative over a GFS OM
# pressure-level *proxy*.  The two quantities have different footprints, so
# 30 percentage points is a conservative discrepancy flag, NOT a proof that
# either product is physically incorrect.
GFS_PROXY_CONFLICT_DELTA_PP = 30.0


def calibrate_path_samples(samples, profiles, *, manual=False):
    """Apply spatial layer data without letting GFS proxies erase native cover.

    EC native OM layers retain their previous treatment.  A GFS OM pressure-
    level approximation is accepted per layer only when its discrepancy from
    the native point-API layer is <= 30 percentage points.  On a larger
    discrepancy, retain native cover in the scoring/quick-screen sample and
    in the horizontal ray-check *effective* evidence, but keep raw OM intact
    for diagnostics.  Profile pressure levels and the original total cover
    are never rewritten. This function is safe to call again after upstream
    sample extension; preserved API values remain the reference.
    """
    if manual:
        return {'status': 'manual_priority', 'corrected_distances': []}
    pmap = {round(float(p.get('distance_km', -9999)), 4): p
            for p in profiles if isinstance(p, dict)
            and isinstance(p.get('distance_km'), (float, int))}
    fields = ('cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high')
    corrected = []
    changes = []
    conflicts = []
    proxy_points = 0
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
        # Only the GFS pressure-level-derived approximation is subject to the
        # native/proxy discrepancy gate. Native 2D OM layer data are not the
        # same weak reconstructed product.
        is_proxy = (p.get('spatial_horizontal_layer_origin') == 'pressure_layer_proxy'
                    and p.get('spatial_horizontal_model_domain') == 'ncep_gfs025')
        if is_proxy:
            proxy_points += 1
        row_changes = {}
        rejected = {}
        effective = dict(vals)
        effective_sources = {field: 'om_native' if not is_proxy else 'gfs_om_pressure_proxy'
                             for field in fields}
        for field in fields:
            original_key = 'point_api_area_' + field
            old = sample.get(original_key, sample.get(field))
            if isinstance(old, (float, int)) and math.isfinite(old):
                old = float(old)
                sample[original_key] = old
            else:
                old = None
            new = float(vals[field])
            if (is_proxy and old is not None and 0 <= old <= 100 and
                    abs(old - new) > GFS_PROXY_CONFLICT_DELTA_PP):
                sample[field] = old
                effective[field] = old
                effective_sources[field] = 'native_point_api_conflict_fallback'
                row = {'distance_km': dist, 'field': field, 'api_pct': old,
                       'om_proxy_pct': new, 'delta_pp': round(abs(old-new), 3),
                       'decision': 'retain_native_api'}
                rejected[field] = row
                conflicts.append(row)
                row_changes[field] = (old, old)
            else:
                sample[field] = new
                row_changes[field] = (old, new)
        # The original OM data remain separately inspectable.  These are the
        # values the later horizontal consistency / target checks must use.
        p['spatial_horizontal_effective'] = effective
        p['spatial_horizontal_effective_sources'] = effective_sources
        if rejected:
            p['spatial_horizontal_conflict_layers'] = rejected
            sample['spatial_calibration_conflicts'] = rejected
        else:
            p.pop('spatial_horizontal_conflict_layers', None)
            sample.pop('spatial_calibration_conflicts', None)
        sample['spatial_calibration'] = ('gfs_proxy_conflict_native_fallback'
                                         if rejected else 'om_horizontal_layer_25km_cell')
        sample['spatial_calibration_run_unproven'] = True
        corrected.append(dist)
        changes.append({'distance_km': dist, 'layers': row_changes,
                        'rejected_proxy_layers': list(rejected)})
    return {
        'status': 'applied' if corrected else 'not_available',
        'corrected_distances': corrected,
        'changes': changes[:30],
        'gfs_proxy_points': proxy_points,
        'conflict_count': len(conflicts),
        'conflicts': conflicts[:40],
        'conflict_threshold_pp': GFS_PROXY_CONFLICT_DELTA_PP,
        'caveat': ('EC native 2D layers remain independently sampled. GFS OM '
                   'pressure-layer proxy conflicts >30 percentage points '
                   'retain native API layer cover; raw OM preserved, ray '
                   'cross-check uses the same effective cover. Spatial grid '
                   'cell and 9-point area footprints differ; run equality '
                   'with point API is unproven.')}
