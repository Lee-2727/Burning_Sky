# -*- coding: utf-8 -*-
"""Debug17: real 2-D sector sampling. Experimental geometric illumination.
Not a calibrated forecast or a pressure-level cloud-ray solution.
"""
from __future__ import annotations
import math
from datetime import timedelta
from .spherical_raytrace import destination, _ray_height_km, _earth_occluded
from .algorithm import solar_position

def grid(radius_km, bearing, radial_bins=8, angular_bins=9):
    """Equal-area polar cells, uniform angle; each point represents equal sector area."""
    radius_km = max(1.0, float(radius_km))
    for i in range(radial_bins):
        r0=radius_km*math.sqrt(i/radial_bins)
        r1=radius_km*math.sqrt((i+1)/radial_bins)
        d=radius_km*math.sqrt((i+.5)/radial_bins)
        for j in range(angular_bins):
            a0=-60+120*j/angular_bins
            a1=-60+120*(j+1)/angular_bins
            offset=(a0+a1)/2
            yield dict(i=i,j=j,r0=r0,r1=r1,a0=a0,a1=a1,d=d,
                       offset=offset,bearing=(bearing+offset)%360)

def _nearest_hour(hourly, event_dt):
    from datetime import datetime
    times=hourly.get('time',[])
    if not times:return None
    # Provider uses UTC; event_dt is local and is converted by caller.
    return min(range(len(times)),key=lambda k:abs((datetime.fromisoformat(times[k])-event_dt).total_seconds()))

def _v(hourly, name, idx):
    arr=hourly.get(name,[])
    return arr[idx] if idx is not None and idx<len(arr) else None

def evaluate(cells, weather, event_dt, utc_offset, lat, lon, direction, aod=0.2,
             radial_bins=8, angular_bins=9):
    """Cloud existence and approximate target illumination; unresolved upstream kept separate.

    Uses only broad cloud-cover layers. It does not assert full-path clear skies.
    """
    if len(cells)!=len(weather):raise ValueError("采样点数与天气返回位置数不一致")
    start=-30 if direction=='morning' else 0
    stop=0 if direction=='morning' else 30
    times=[event_dt+timedelta(minutes=m) for m in range(start,stop+1,3)]
    outputs=[]
    for cell,record in zip(cells,weather):
        h=record.get('hourly',{})
        event_utc=event_dt-timedelta(hours=utc_offset)
        idx=_nearest_hour(h,event_utc)
        mid=_v(h,'cloud_cover_mid',idx)
        high=_v(h,'cloud_cover_high',idx)
        low=_v(h,'cloud_cover_low',idx)
        if mid is None or high is None:
            outputs.append(dict(cell, state='unknown', colors=[None]*len(times), mid=mid,high=high))
            continue
        # These are approximate reference altitudes, NOT retrieved cloud bases.
        target_h=(8.0 if high>=mid and high>=5 else 4.0)
        cover=max(mid,high)
        if cover<5:
            outputs.append(dict(cell,state='no_cloud',colors=[None]*len(times),mid=mid,high=high))
            continue
        colors=[]
        for dt in times:
            alt,az=solar_position(dt,utc_offset,record.get('latitude',lat),record.get('longitude',lon))
            # Local solar altitude is computed at each target; horizon test uses
            # the target's altitude above the reference sphere.
            if _earth_occluded(target_h,alt,350):
                colors.append(None)
                continue
            # Illumination candidate, not verified through upstream 3-D cloud geometry.
            depth=(dt-event_dt).total_seconds()/60
            phase=abs(depth)/30
            aodv=max(0.,min(1.5,float(aod or .2)))
            haze=max(.25,1-.58*aodv)
            if direction=='morning':
                # Later pre-sunrise -> brighter warmer; earlier -> pink/purple.
                base=(245,105+100*(1-phase),115+80*phase)
            else:
                base=(245,105+100*(1-phase),115+80*phase)
            colors.append(tuple(int(max(0,min(255,200*(1-haze)+v*haze))) for v in base))
        state='candidate' if any(c is not None for c in colors) else 'unlit'
        outputs.append(dict(cell,state=state,colors=colors,mid=mid,high=high))
    # Never report candidates as verified illuminated.
    return {'cells':outputs,'times':[t.isoformat() for t in times],
            'candidate_area_pct':round(100*sum(x['state']=='candidate' for x in outputs)/len(outputs),1) if outputs else 0,
            'verified_scale_pct':None,
            'note':'候选受光图：已获取真实二维中高云量，但缺少二维气压层云体与完整上游光路，不能当作已验证霞云规模。'}

def draw(result, title='二维霞云图'):
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    from matplotlib.colors import to_rgb
    import numpy as np
    from matplotlib import colors
    fig,axes=plt.subplots(1,2,figsize=(13,6.8),subplot_kw={'aspect':'equal'})
    entries=result['cells']
    if not entries:return fig
    radius=max(x['r1'] for x in entries)
    # smooth LEFT plot via Gaussian blending of measured candidate color values.
    n=280
    xs=np.linspace(-radius,radius,n)
    ys=np.linspace(0,radius,n)
    xx,yy=np.meshgrid(xs,ys)
    rr=np.hypot(xx,yy)
    aa=np.degrees(np.arctan2(xx,yy))
    mask=(rr<=radius)&(np.abs(aa)<=60)
    rgb=np.ones((n,n,3))*.95
    for k in range(3):
        num=np.zeros((n,n));den=np.zeros((n,n))
        for e in entries:
            if e['state']!='candidate':continue
            c=next((v for v in reversed(e['colors']) if v is not None),None)
            if c is None:continue
            d=e['d'];angle=math.radians(e['offset'])
            px=d*math.sin(angle);py=d*math.cos(angle)
            # only local influence, preventing broad fill across missing cells
            dist2=(xx-px)**2+(yy-py)**2
            sigma=radius*.115
            w=np.exp(-dist2/(2*sigma*sigma))*max(.05,max(e['mid'],e['high'])/100)
            num+=w*c[k]/255;den+=w
        rgb[:,:,k]=np.where(den>.12,num/np.maximum(den,1e-10),.93)
    rgba=np.concatenate([rgb,np.where(mask,1,0)[...,None]],axis=2)
    axes[0].imshow(rgba,extent=(-radius,radius,0,radius),origin='lower',interpolation='bilinear')
    for e in entries:
        a0,a1=90-e['a1'],90-e['a0']
        face='#f0edf2' if e['state']=='unknown' else '#f4f4f4'
        w=patches.Wedge((0,0),e['r1'],a0,a1,width=e['r1']-e['r0'],
                        facecolor=face,edgecolor='#aaa0b5',linewidth=.65)
        axes[1].add_patch(w)
        if e['state']=='candidate':
            a=math.radians(e['offset']);d=e['d']
            px=d*math.sin(a);py=d*math.cos(a)
            width=max(1.2,radius*.045)
            ht=max(3.0,(e['r1']-e['r0'])*.65)
            tcols=e['colors']
            for ti,c in enumerate(tcols):
                if c is None:continue
                y0=py-ht/2+ht*ti/len(tcols)
                rect=patches.Rectangle((px-width/2,y0),width,ht/len(tcols)+.1,
                                       facecolor=tuple(v/255 for v in c),edgecolor='none')
                axes[1].add_patch(rect)
            axes[1].add_patch(patches.Rectangle((px-width/2,py-ht/2),width,ht,
                                                fill=False,edgecolor='#333',linewidth=.4))
    for ax,ttl in zip(axes,['主导云色 · 平滑候选图','采样区云色时序 · 下早上晚']):
        ax.set_xlim(-radius*1.02,radius*1.02);ax.set_ylim(-2,radius*1.03)
        ax.set_title(ttl);ax.set_xlabel('横向距离 km');ax.set_ylabel('太阳方向距离 km')
    fig.suptitle(title+' | 未验证上游云层遮挡；非正式霞云规模',fontsize=12)
    fig.tight_layout()
    return fig
