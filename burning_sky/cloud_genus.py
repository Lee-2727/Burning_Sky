"""Experimental cloud-genus classification from forecast pressure-layer cloud cover.

Percentages are heuristic confidence scores, NOT sky coverage or calibrated odds.
Do not use this module to change fire-cloud probability.
"""
from __future__ import annotations
import math


def _num(value):
    try:
        n=float(value)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):
        return None


def identify_cloud_genera(profiles, near_km=50.0):
    """Return up to four cloud-layer classifications for the nearest forecast column.

    We deliberately avoid combining separate horizontal columns into one imaginary
    sounding. More sophisticated T/Td and lapse-rate classification can follow.
    """
    profiles=[p for p in (profiles or []) if isinstance(p,dict) and isinstance(_num(p.get('distance_km')),(int,float)) and p.get('levels')]
    if not profiles:return []
    profile=min(profiles,key=lambda p:abs(_num(p.get('distance_km')) or 0))
    levels=[]
    for v in profile['levels']:
        h=_num(v.get('height_agl_km'))
        if h is None:
            asl=_num(v.get('height_m_asl'));ground=_num(profile.get('ground_elevation_m'))
            if asl is not None and ground is not None:h=(asl-ground)/1000
        cover=_num(v.get('cloud_cover'));rh=_num(v.get('relative_humidity'))
        if h is None or h<0 or cover is None:continue
        levels.append((h,max(0,min(100,cover)),rh))
    levels.sort()
    if not levels:return []
    # Distinct vertical bands; cloud top and thickness are estimated from model
    # pressure levels, not directly measured cloud boundaries.
    bands=[('低云',0,2.5),('中云',2.5,6),('高云',6,22)]
    results=[]
    for name,lo,hi in bands:
        candidates=[v for v in levels if lo<=v[0]<hi and v[1]>=20]
        if not candidates:continue
        runs=[];run=[]
        for v in candidates:
            if run and v[0]-run[-1][0]>2.2:
                runs.append(run);run=[]
            run.append(v)
        if run:runs.append(run)
        for run in runs:
            bottom=run[0][0];top=run[-1][0]
            mean_cover=sum(v[1] for v in run)/len(run)
            depth=max(0,top-bottom)
            mean_rh=sum(v[2] for v in run if v[2] is not None)/max(1,sum(v[2] is not None for v in run))
            # Deliberately conservative: no temperature or lapse-rate profile in
            # archived cases, so cloud genera remain hypotheses.
            if name=='低云':
                genus='层云' if bottom<0.5 and mean_cover>=65 else ('层积云' if mean_cover>=45 else '积云/层积云')
            elif name=='中云':
                genus='高层云' if depth>=1.8 and mean_cover>=65 else ('高积云' if depth<=1.5 else '高积云/高层云')
            else:
                genus='卷层云' if depth>=1.8 and mean_cover>=65 else ('卷云' if depth<=1.5 and mean_cover<70 else '卷云/卷层云')
            confidence=round(min(75,max(35,42+min(18,mean_cover*.2)+min(8,len(run)*2)- (10 if '/' in genus else 0))))
            results.append({'layer':name,'genus':genus,'confidence':confidence,
                            'cloud_cover':round(mean_cover),'bottom_km':round(bottom,2),
                            'top_km':round(top,2),'depth_km':round(depth,2),
                            'mean_rh':round(mean_rh),'sample_count':len(run)})
    # Keep strongest two high-cloud bands, to match requested web output.
    selected=[]
    for band in ('低云','中云','高云'):
        subset=sorted((r for r in results if r['layer']==band),key=lambda r:(-r['cloud_cover'],-r['sample_count']))
        selected.extend(subset[:2] if band=='高云' else subset[:1])
    return selected
