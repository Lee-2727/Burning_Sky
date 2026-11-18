# -*- coding: utf-8 -*-
"""Attach explicitly loaded *historic* OM cloud evidence during API-cache replay.

Never contacts Open-Meteo.  Joins by exact model, time, distance and coordinate.
Does not replace the vertical profiles obtained from the engine's request cache.
"""
from __future__ import annotations
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt

from burning_sky.spatial_ray_fusion import _utc


def _distance_km(a, b, c, d):
    x = sin(radians(float(a) - float(c))/2)**2 + cos(radians(float(a)))*cos(radians(float(c)))*sin(radians(float(b)-float(d))/2)**2
    return 12742 * asin(min(1., sqrt(max(0.,x))))


def bind_saved_horizontal(pressure, saved_state, event_utc, model_id):
    """Update pressure['samples'] in place; return transparent match statistics."""
    result = {'status':'archive_no_matching_om', 'attached':0, 'checked':0, 'skipped':0}
    if not isinstance(saved_state, dict):
        result['reason']='no loaded case'
        return result
    sources=[]
    for direction, previous in (saved_state.get('direction_results') or {}).items():
        if not isinstance(previous,dict) or previous.get('query_model_id')!=model_id:
            continue
        for row in previous.get('profiles') or []:
            if isinstance(row,dict) and isinstance(row.get('spatial_horizontal_om'),dict):
                sources.append(row)
    if not sources:
        result['reason']='case has no OM values for this model'
        return result
    if not isinstance(pressure,dict) or not isinstance(pressure.get('samples'),list):
        result['reason']='invalid pressure samples'
        return result
    if _utc(event_utc) is None:
        result['reason']='event time unavailable'
        return result
    for row in pressure['samples']:
        if not isinstance(row,dict):continue
        result['checked'] += 1
        try:
            d=float(row['distance_km'])
            lat=float(row['lat']); lon=float(row['lon'])
        except (KeyError,TypeError,ValueError):
            result['skipped'] += 1
            continue
        matching=[]
        for old in sources:
            try:
                if abs(float(old['distance_km'])-d)>0.02 or _distance_km(lat,lon,old['lat'],old['lon'])>0.7:
                    continue
            except (KeyError,TypeError,ValueError):
                continue
            valid=_utc(old.get('spatial_horizontal_valid_time'))
            pressure_valid=_utc(row.get('time'))
            if valid is None or pressure_valid is None or abs((valid-pressure_valid).total_seconds())>5400:
                continue
            if abs((valid-_utc(event_utc)).total_seconds())>5400:
                continue
            matching.append(old)
        if len(matching)!=1:
            result['skipped'] += 1
            continue
        old=matching[0]
        raw=old['spatial_horizontal_om']
        if not all(isinstance(raw.get(k),(float,int)) and 0<=raw[k]<=100
                   for k in ('cloud_cover_low','cloud_cover_mid','cloud_cover_high')):
            result['skipped'] += 1
            continue
        for key in ('spatial_horizontal_om','spatial_horizontal_valid_time',
                    'spatial_horizontal_run','spatial_horizontal_time_uncertainty',
                    'spatial_horizontal_layer_origin','spatial_horizontal_model_domain',
                    'spatial_horizontal_evidence'):
            if key in old:row[key]=old[key]
        if model_id=='ncep_gfs_global':
            row.setdefault('spatial_horizontal_layer_origin','pressure_layer_proxy')
            row.setdefault('spatial_horizontal_model_domain','ncep_gfs025')
        row['spatial_horizontal_replay_source']='loaded_case_zip'
        result['attached'] += 1
    result['status']='archive_om_used' if result['attached'] else 'archive_no_matching_om'
    if not result['attached']:
        result['reason']='saved profile time/distance/coordinate mismatch (not treated as clear)'
    return result
