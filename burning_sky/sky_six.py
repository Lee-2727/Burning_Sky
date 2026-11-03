# -*- coding: utf-8 -*-
"""Six observer-view sky sectors, with explicit coverage/uncertainty semantics.

All six sectors are *view angles*, not six points on the ground.
This diagnostic requires meteorology at three bearings; it does not synthesize
missing side data from the centre-line. Cloud cover is a fractional proxy.
"""
from __future__ import annotations
import math
from datetime import datetime, timedelta
from .algorithm import solar_position
from .spherical_raytrace import destination, EARTH_RADIUS_KM, profile_cloud_cells

AZ_BINS = [(-60,-20),(-20,20),(20,60)]
EL_BINS = [(45,90),(0,45)]

def apparent_elevation(distance_km, height_km, observer_km=0.0):
    if distance_km <= 0: return 90.0 if height_km > observer_km else 0.0
    theta=distance_km/EARTH_RADIUS_KM
    r=EARTH_RADIUS_KM+height_km; r0=EARTH_RADIUS_KM+observer_km
    return math.degrees(math.atan2(r*math.cos(theta)-r0,r*math.sin(theta)))

def target_ray_height(target_height_km, solar_alt_deg, upstream_km):
    theta=upstream_km/EARTH_RADIUS_KM
    a=math.radians(solar_alt_deg)
    den=math.cos(theta+a)
    if den<=0:return float('inf')
    return (EARTH_RADIUS_KM+target_height_km)*math.cos(a)/den-EARTH_RADIUS_KM

def _intersects(target_h, sun_alt, cell, target_d, max_d):
    a=max(target_d+0.05,float(cell['start_km']))
    b=min(max_d,float(cell['end_km']))
    if b<=a:return False
    # Ray is parameterized by distance from target, not from observer.
    local_a=a-target_d;local_b=b-target_d
    critical=max(local_a,min(local_b,-math.radians(sun_alt)*EARTH_RADIUS_KM))
    hs=[target_ray_height(target_h,sun_alt,v) for v in (local_a,local_b,critical)]
    return min(hs)<=cell['top_km'] and max(hs)>=cell['bottom_km']

def _earth_blocked(target_h, sun_alt, max_upstream_km):
    if sun_alt>=0:return False
    closest=min(max_upstream_km,-math.radians(sun_alt)*EARTH_RADIUS_KM)
    return target_ray_height(target_h,sun_alt,closest)<0

def multi_target_profile(samples, profiles, event_dt, utc_offset, origin_lat, origin_lon,
                         direction='morning', target_limit_km=100.0, step_minutes=3):
    """Check multiple targets and cloud heights along the original solar-axis profile.

    The source profiles are a 1-D section. A clear ray is only verified within
    sampled profile coverage, and is not an assertion of full solar visibility.
    """
    cells=profile_cloud_cells(profiles)
    valid=sorted((p for p in profiles if p.get('levels') and isinstance(p.get('distance_km'),(int,float))),
                 key=lambda p:p['distance_km'])
    if not valid:return {'targets':[],'times':[],'clear_count':0,'note':'缺少气压层剖面'}
    end=max(float(p['distance_km']) for p in valid)
    target_limit=min(max(0.,float(target_limit_km)),end)
    # Use each available profile's actual cloud heights; do not invent 4/8 km targets.
    targets=[]
    for p in valid:
        d=float(p['distance_km'])
        if d>target_limit+1e-6:continue
        cloudy=[lv for lv in p['levels'] if isinstance(lv.get('height_agl_km'),(int,float))
                and isinstance(lv.get('cloud_cover'),(int,float)) and lv['cloud_cover']>=25
                and float(lv['height_agl_km'])>=1.5]
        for lv in cloudy:
            h=float(lv['height_agl_km'])
            # Cap the number of levels per target; retain distinct heights.
            if targets and len(targets)>500:break
            targets.append({'distance_km':d,'height_km':h,'cover':float(lv['cloud_cover']),
                            'elevation_deg':apparent_elevation(d,h),
                            'latitude':p.get('lat'),'longitude':p.get('lon')})
    minutes=range(-30,0,step_minutes) if direction=='morning' else range(1,31,step_minutes)
    times=[event_dt+timedelta(minutes=m) for m in minutes]
    for target in targets:
        d=target['distance_km'];h=target['height_km']
        lat=target['latitude'];lon=target['longitude']
        if not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)):
            lat,lon=destination(origin_lat,origin_lon,0,d)
        flags=[]
        for dt in times:
            alt,_=solar_position(dt,utc_offset,lat,lon)
            if _earth_blocked(h,alt,end-d):flags.append(False);continue
            # Exclude target's own cell, test only the actual upstream segment.
            blocked=any(_intersects(h,alt,c,d,end) for c in cells
                        if abs(float(c['sample_distance_km'])-d)>1e-6)
            flags.append(not blocked)
        target['illuminated']=flags
        target['any_illuminated']=any(flags)
    return {'targets':targets,'times':[t.isoformat() for t in times],
            'clear_count':sum(t['any_illuminated'] for t in targets),
            'target_limit_km':target_limit,'profile_end_km':end,
            'note':'一维多目标气压层光路；仅验证已有剖面范围，不代表完整三维遮挡或太阳方向以外的云。'}



def adaptive_profile_diagnostic(profiles, event_dt, utc_offset, origin_lat, origin_lon,
                                direction='morning', target_limit_km=100.,
                                blur_horizontal_km=10., blur_vertical_km=.2):
    """Offline sensitivity study, not calibrated probability or a 3-D cloud reconstruction.

    Solar angles are derived from real times; adaptive subdivision does not extrapolate
    outside the dawn/dusk window. Cloud cover is a fractional occupancy proxy.
    """
    from .spherical_raytrace import _ray_height_km
    import bisect
    valid=sorted((p for p in profiles if p.get('levels') and isinstance(p.get('distance_km'),(int,float))),
                 key=lambda p:float(p['distance_km']))
    if not valid:return {'error':'缺少气压剖面'}
    end=float(valid[-1]['distance_km'])
    targets=[]
    for p in valid:
        d=float(p['distance_km'])
        if d>target_limit_km:continue
        for lv in p['levels']:
            c=lv.get('cloud_cover');h=lv.get('height_agl_km')
            if isinstance(c,(int,float)) and c>=25 and isinstance(h,(int,float)) and h>=1.5:
                targets.append((d,float(h),float(c)))
    # Select distinct representative targets, not arbitrary synthetic cloud heights.
    targets=sorted(targets,key=lambda x:(x[0],-x[2]))[:12]
    raw=[]
    for p in valid:
        d=float(p['distance_km'])
        for lv in p['levels']:
            h=lv.get('height_agl_km');c=lv.get('cloud_cover')
            if isinstance(h,(int,float)) and isinstance(c,(int,float)):
                raw.append((d,float(h),max(0.,min(100.,float(c)))))
    def cover_at(d,h,sx,sz):
        # Gaussian kernel regression on sampled pressure levels, with compact support.
        # This is an experiment, not evidence of a physical cloud boundary.
        total=weight=0.
        for xd,xh,c in raw:
            if abs(xd-d)>3*sx or abs(xh-h)>3*sz:continue
            w=math.exp(-.5*((xd-d)/sx)**2-.5*((xh-h)/sz)**2)
            total+=w*c;weight+=w
        return total/weight if weight>1e-7 else None
    # Avoid repeated O(N^2) regression in angular search: precompute cloud field.
    field={}
    for mode,sx,sz in [('raw',0,0),('blur_10km_200m',blur_horizontal_km,blur_vertical_km),
                       ('blur_20km_400m',2*blur_horizontal_km,2*blur_vertical_km)]:
        cells=[]
        for p in valid:
            d=float(p['distance_km'])
            for lv in p['levels']:
                h=lv.get('height_agl_km');c=lv.get('cloud_cover')
                if not isinstance(h,(int,float)) or not isinstance(c,(int,float)):continue
                cc=float(c) if mode=='raw' else cover_at(d,float(h),sx,sz)
                if cc is not None:cells.append((d,float(h),max(0.,min(100.,cc))))
        field[mode]=cells
    def evaluate(target,minute,mode):
        td,th,_=target
        dt=event_dt+timedelta(minutes=minute)
        # Target position's solar altitude, not observer's.
        lat,lon=destination(origin_lat,origin_lon,solar_position(dt,utc_offset,origin_lat,origin_lon)[1],td)
        alt,_=solar_position(dt,utc_offset,lat,lon)
        if _earth_blocked(th,alt,end-td):return {'state':'earth_blocked','risk':1.,'solar_alt':alt}
        # A finite target cloud has an uncertain edge. Exclude the first 1 km upstream
        # rather than letting the target's own pressure cell self-occlude.
        path=[(d,h,c) for d,h,c in field[mode] if d>td+1. and d<=end]
        # Profile levels represent occupancy, not solid walls. A height proximity
        # weighting avoids treating cloud at 2 km as blocking a ray at 8 km.
        opacity=0.;strong=[]
        for d,h,c in path:
            ray=target_ray_height(th,alt,d-td)
            delta=abs(ray-h)
            vertical_sigma=.25 if mode=='raw' else (.3 if mode=='blur_10km_200m' else .45)
            if delta>3*vertical_sigma:continue
            weight=math.exp(-.5*(delta/vertical_sigma)**2)
            # Correlated neighbouring pressure levels: use strongest evidence per
            # 25 km horizontal bin, avoiding multiplication of overlapping levels.
            strong.append((int(d//25),c/100*weight,d,h))
        bins={}
        for b,r,d,h in strong:
            if b not in bins or r>bins[b][0]:bins[b]=(r,d,h)
        # Heuristic cumulative attenuation, NOT physical optical depth/probability.
        risk=1.-math.prod(1.-min(.95,v[0]) for v in bins.values())
        return {'state':'candidate' if risk<.7 else 'high_obstruction_risk',
                'risk':round(risk,4),'solar_alt':round(alt,4),
                'dominant_intersections':sorted([{'distance_km':round(v[1],1),
                  'height_km':round(v[2],2),'occupancy_risk':round(v[0],3)}
                  for v in bins.values()],key=lambda q:-q['occupancy_risk'])[:3]}
    result=[]
    for target in targets:
        # Coarse time grid then bisect around state transitions; also sample midpoints
        # of blocked/blocked intervals to catch isolated narrow candidate windows.
        times=list(range(-30,0,3)) if direction=='morning' else list(range(1,31,3))
        records={}
        def at(t):
            k=round(t,5)
            if k not in records:records[k]={mode:evaluate(target,t,mode) for mode in field}
            return records[k]
        for t in times:at(t)
        def refine(a,b,depth):
            if depth>=7 or b-a<=.047:return
            mid=(a+b)/2
            ra=at(a)['raw']['state'];rb=at(b)['raw']['state'];rm=at(mid)['raw']['state']
            if ra!=rb or ra!=rm or rm!=rb or depth<2:
                refine(a,mid,depth+1);refine(mid,b,depth+1)
        for a,b in zip(times,times[1:]):refine(a,b,0)
        rows=[{'minute_offset':t,**records[t]} for t in sorted(records)]
        result.append({'target_distance_km':target[0],'target_height_km':target[1],
                       'target_cloud_cover_pct':target[2], 'scan':rows,
                       'candidate_counts':{m:sum(r[m]['state']=='candidate' for r in rows) for m in field}})
    return {'target_count':len(result),'targets':result,'profile_end_km':end,
            'method':'离线气压层经验风险与自适应时间细分；仅对已采样的一维方向有效',
            'warning':'risk为未校准经验指标；不能据此宣称真实云隙或统计透光概率。'}

def adaptive_equivalent_gaps(profiles, event_dt, utc_offset, lat, lon, direction,
                             reach_km, min_width_km=15.0):
    """Bounded, sampled-section equivalent light channels; never claim 3D certainty.

    Reuse existing pressure levels, sample time adaptively around transitions,
    and group illuminated target-distance cells into clipped contiguous spans.
    Cloud cover is an occupancy-risk proxy, not an opaque solid.
    """
    valid=sorted((p for p in (profiles or []) if p.get('levels') and
                  isinstance(p.get('distance_km'),(int,float))),key=lambda p:p['distance_km'])
    if len(valid)<2 or not isinstance(reach_km,(int,float)) or reach_km<=0:
        return {'gaps':[],'status':'unresolved_data','evaluations':0}
    end=float(valid[-1]['distance_km']);limit=min(float(reach_km),end)
    cells=profile_cloud_cells(valid)
    targets=[]
    for p in valid:
        d=float(p['distance_km'])
        if d>limit:continue
        cloud=[lv for lv in p['levels'] if isinstance(lv.get('cloud_cover'),(int,float))
               and lv['cloud_cover']>=25 and isinstance(lv.get('height_agl_km'),(int,float))
               and lv['height_agl_km']>=1.5]
        for lv in sorted(cloud,key=lambda x:-x['cloud_cover'])[:3]:
            targets.append((d,float(lv['height_agl_km']),float(lv['cloud_cover']),p))
    if not targets:return {'gaps':[],'status':'no_target_cloud','evaluations':0}
    evaluated=0;illuminated=[];diagnostics=[]
    for d,h,cover,p in targets:
        plat=p.get('lat');plon=p.get('lon')
        if not isinstance(plat,(int,float)) or not isinstance(plon,(int,float)):
            # Without actual target coordinates, a position-specific solar angle
            # cannot be verified; do not fabricate a positive path.
            continue
        cache={}
        def assess(t):
            nonlocal evaluated
            key=round(t,5)
            if key in cache:return cache[key]
            dt=event_dt+timedelta(minutes=t)
            alt,_=solar_position(dt,utc_offset,plat,plon)
            if _earth_blocked(h,alt,end-d):
                answer=(False,1.0,alt)
            else:
                # Evaluate the maximum cover in each independent 25 km segment;
                # repeated pressure levels must not multiply the same cloud.
                bins={}
                for c in cells:
                    a=max(d+1.,float(c['start_km']));b=min(end,float(c['end_km']))
                    if b<=a:continue
                    if not _intersects(h,alt,c,d,end):continue
                    strength=min(.98,max(0.,float(c['cloud_cover'])/100.))
                    index=int(((a+b)*.5)//25)
                    bins[index]=max(strength,bins.get(index,0.))
                risk=1.-math.prod(1.-v for v in bins.values())
                answer=(risk<.7,risk,alt)
            cache[key]=answer; evaluated+=1
            return answer
        times=list(range(-30,0,3)) if direction=='morning' else list(range(1,31,3))
        for t in times:assess(float(t))
        def refine(a,b,depth=0):
            if depth>=7 or b-a<=.05:return
            mid=(a+b)/2
            aa=assess(a)[0];bb=assess(b)[0];mm=assess(mid)[0]
            if aa!=bb or aa!=mm or bb!=mm or depth<2:
                refine(a,mid,depth+1);refine(mid,b,depth+1)
        for a,b in zip(times,times[1:]):refine(float(a),float(b))
        good=[(t,v) for t,v in cache.items() if v[0]]
        if good:
            best=min(good,key=lambda z:z[1][1]);illuminated.append(d)
            diagnostics.append({'distance_km':d,'height_km':h,'best_risk':round(best[1][1],3),
                                'minute_offset':best[0],'solar_altitude_deg':round(best[1][2],3)})
    distances=sorted(set(illuminated));gaps=[]
    # Adjacent target-distance cells are grouped by the original sampling grid.
    # Never extrapolate beyond the physical reach.
    grid=sorted(set(float(p['distance_km']) for p in valid if float(p['distance_km'])<=limit))
    for d in distances:
        i=grid.index(d)
        left=max(0.,(grid[i-1]+d)/2 if i else d)
        right=min(limit,(d+grid[i+1])/2 if i+1<len(grid) else limit)
        if right<=left:continue
        if gaps and left<=gaps[-1]['end_km']+1e-5:
            gaps[-1]['end_km']=round(max(gaps[-1]['end_km'],right),2)
            gaps[-1]['size_km']=round(gaps[-1]['end_km']-gaps[-1]['start_km'],2)
        else:gaps.append({'start_km':round(left,2),'end_km':round(right,2),
                          'size_km':round(right-left,2),'source':'adaptive_profile_equivalent'})
    gaps=[g for g in gaps if g['size_km']>=min_width_km]
    return {'gaps':gaps,'status':'candidate' if gaps else ('no_candidate' if evaluated else 'unresolved_data'),
            'evaluations':evaluated,'illuminated_target_count':len(illuminated),
            'diagnostics':diagnostics[:40],
            'note':'一维气压剖面经验透光风险<0.7的候选等效通道；非实测云洞或真实概率'}


def grid_points(origin_lat,origin_lon,bearing,radius_km):
    """Three angular columns x three distances + local; 10 locations maximum."""
    radius=max(1.,min(350.,float(radius_km)))
    positions=[]
    for j,offset in enumerate((-40.,0.,40.)):
        for i,fraction in enumerate((.25,.55,.85)):
            d=radius*fraction
            lat,lon=destination(origin_lat,origin_lon,bearing+offset,d)
            positions.append({'column':j,'distance_km':d,'bearing_offset':offset,'lat':lat,'lon':lon})
    return positions

def _nearest_hour(hourly,dt_utc):
    t=hourly.get('time') or []
    if not t:return None
    return min(range(len(t)),key=lambda i:abs((datetime.fromisoformat(t[i])-dt_utc).total_seconds()))

def _value(hourly,key,idx):
    v=hourly.get(key) or []
    return v[idx] if idx is not None and idx<len(v) else None

def evaluate(points,records,event_dt,utc_offset,lat,lon,direction,aod=0.2):
    """Conservative *approximate* 6-bin diagnostic from 10 actual locations.

    Broad low/mid/high cloud data do not resolve pressure-layer height or the
    three-dimensional upstream path. Mark results 'candidate' not verified.
    """
    if len(points)!=len(records):raise ValueError('采样位置数不匹配')
    minute_offsets=list(range(-30,0,3)) if direction=='morning' else list(range(1,31,3))
    times=[event_dt+timedelta(minutes=m) for m in minute_offsets]
    bins=[{'azimuth':AZ_BINS[j],'elevation':EL_BINS[k], 'targets':[], 'colors':[],
           'state':'unknown'} for k in range(2) for j in range(3)]
    for p,rec in zip(points,records):
        hourly=rec.get('hourly') or {}
        idx=_nearest_hour(hourly,event_dt-timedelta(hours=utc_offset))
        for layer,reference_h in (('cloud_cover_mid',4.5),('cloud_cover_high',8.5)):
            cover=_value(hourly,layer,idx)
            if not isinstance(cover,(int,float)) or cover<5:continue
            elev=apparent_elevation(p['distance_km'],reference_h)
            if not 0<=elev<=90:continue
            k=0 if elev>=45 else 1
            b=bins[k*3+p['column']]
            b['targets'].append({'distance_km':p['distance_km'],'height_km':reference_h,
                                 'cover':float(cover),'lat':p['lat'],'lon':p['lon']})
    for b in bins:
        if not b['targets']:
            b['state']='no_cloud';b['colors']=[None]*len(times);continue
        colors=[]
        for dt in times:
            illuminated=[]
            for target in b['targets']:
                alt,_=solar_position(dt,utc_offset,target['lat'],target['lon'])
                if not _earth_blocked(target['height_km'],alt,350):
                    illuminated.append(target)
            if not illuminated:colors.append(None);continue
            phase=abs((dt-event_dt).total_seconds())/1800
            haze=max(.25,min(1.,1-.58*float(aod or .2)))
            raw=(245,105+100*(1-phase),115+80*phase)
            colors.append(tuple(int(max(0,min(255,200*(1-haze)+v*haze))) for v in raw))
        b['colors']=colors;b['state']='candidate' if any(c is not None for c in colors) else 'unlit'
    return {'bins':bins,'times':[t.isoformat() for t in times],
            'candidate_bins':sum(b['state']=='candidate' for b in bins),
            'verified_scale_pct':None,
            'note':'六分区候选云色：左右真实云量采样，但采用参考层高；尚无二维气压层与完整上游遮挡验证，不作为正式霞云规模。'}

def draw(result,title='二维霞云图'):
    import numpy as np
    import matplotlib.pyplot as plt
    from matplotlib import rcParams
    rcParams["font.sans-serif"]=["Microsoft YaHei","SimHei","Noto Sans CJK JP","DejaVu Sans"]
    rcParams["axes.unicode_minus"]=False
    from matplotlib.patches import Rectangle
    from matplotlib.colors import to_rgb
    fig,axes=plt.subplots(1,2,figsize=(12,6))
    bins=result['bins']
    base=np.ones((240,360,3))*.93
    weights=np.zeros((240,360))
    yy,xx=np.mgrid[0:240,0:360]
    for k,b in enumerate(bins):
        if b['state']!='candidate':continue
        c=next((c for c in reversed(b['colors']) if c is not None),None)
        if c is None:continue
        col=k%3;row=k//3
        cx=60+120*col;cy=180 if row==0 else 60
        w=np.exp(-((xx-cx)**2/(2*80**2)+(yy-cy)**2/(2*67**2)))
        # Blend only within the target's own cell; do not invent colored gaps.
        x0=120*col;x1=x0+120;y0=120 if row==0 else 0;y1=y0+120
        mask=np.zeros_like(w);mask[y0:y1,x0:x1]=1
        w*=mask
        base+=w[...,None]*(np.array(c)/255-.93)
        weights+=w
    axes[0].imshow(np.clip(base,0,1),origin='lower',extent=(-60,60,0,90),interpolation='bilinear',aspect='auto')
    for k,b in enumerate(bins):
        col=k%3;row=k//3
        x0=-60+40*col;y0=45 if row==0 else 0
        ax=axes[1]
        face='#ece9ee' if b['state']=='unknown' else '#f4f4f4'
        ax.add_patch(Rectangle((x0,y0),40,45,facecolor=face,edgecolor='#888',lw=1.2))
        seq=b['colors'];cx=x0+20
        if any(c is not None for c in seq):
            for i,c in enumerate(seq):
                if c is not None:
                    ax.add_patch(Rectangle((cx-5,y0+3+(39*i/len(seq))),10,39/len(seq)+.05,
                                           facecolor=tuple(v/255 for v in c),edgecolor='none'))
            ax.add_patch(Rectangle((cx-5,y0+3),10,39,fill=False,edgecolor='#555',lw=.7))
        ax.text(x0+2,y0+41,str(k+1),fontsize=9,color='#555',va='top')
    for ax,ttl in zip(axes,('主导云色 · 分区内平滑','六分区云色时序 · 下早上晚')):
        ax.set_xlim(-60,60);ax.set_ylim(0,90)
        ax.set_xticks([-60,-20,20,60]);ax.set_yticks([0,45,90]);ax.grid(False)
        ax.set_xlabel('相对太阳方位角 °');ax.set_ylabel('观察者仰角 °');ax.set_title(ttl)
    fig.suptitle(title+'（候选受光，非完整三维光路验证）',fontsize=12)
    fig.tight_layout();return fig
