"""Experimental 1-D beam-bundle occlusion metric, not calibrated radiative transfer."""
from __future__ import annotations
import math
from .spherical_raytrace import profile_cloud_cells, EARTH_RADIUS_KM

def _height_at(target_h, solar_alt_deg, relative_km):
    a=math.radians(solar_alt_deg)
    theta=relative_km/EARTH_RADIUS_KM
    den=math.cos(theta+a)
    return (EARTH_RADIUS_KM+target_h)*math.cos(a)/den-EARTH_RADIUS_KM if den>0 else float('inf')

def estimate_beam_clear_fraction(profiles, angles=(-1.5,-1.0,-0.5),
                                 heights=(5.8,9.6), half_width=.4, rays=5,
                                 max_distance=445., step_km=2.5):
    """Average geometric unobstructed fraction of test beams.

    Uses fixed reference heights/angles for reproducible comparisons. A cloudy
    cell is a geometric obstacle; no optical-depth inference is made.
    """
    cells=profile_cloud_cells(profiles)
    valid=[float(p['distance_km']) for p in profiles if p.get('levels') and isinstance(p.get('distance_km'),(int,float))]
    if len(valid)<2 or not cells:return {'coefficient':None,'reason':'insufficient pressure profiles'}
    end=min(max(valid),max_distance)
    if end<=10:return {'coefficient':None,'reason':'insufficient profile distance'}
    out=[]
    for alt in angles:
        for height in heights:
            fractions=[]
            for j in range(rays):
                h=height-half_width+2*half_width*j/max(1,rays-1)
                clear=total=0
                n=max(1,int(end/step_km))
                for i in range(n):
                    d=(i+.5)*end/n
                    ray_h=_height_at(h,alt,d)
                    if not math.isfinite(ray_h) or ray_h<0:continue
                    total+=1
                    blocked=any(c['start_km']<=d<c['end_km'] and c['bottom_km']<=ray_h<=c['top_km'] for c in cells)
                    if not blocked:clear+=1
                if total:fractions.append(clear/total)
            if fractions:out.append({'angle_deg':alt,'height_km':height,'clear_fraction':sum(fractions)/len(fractions)})
    return {'coefficient':round(sum(x['clear_fraction'] for x in out)/len(out),4) if out else None,
            'beam_results':out,'method':'fixed reference heights and angles; experimental geometric length fraction'}
