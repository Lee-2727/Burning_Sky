# -*- coding: utf-8 -*-
"""Offline, coarse *candidate* areas and eight viewing sectors from cached OM.

Not an operational probability map: 2-D cloud-cover fields cannot establish
3-D cloud-ray clearance, opacity, or all cities' individual event-time weather.
No HTTP calls, even when the user clicks plot repeatedly.
"""
from __future__ import annotations
import math
from datetime import timezone,timedelta
import numpy as np
from burning_sky.algorithm import solar_position
from desktop.spatial_v2_visual import normalize_utc, _layer_grid, _setup_chinese_fonts

BEIJING=timezone(timedelta(hours=8))


def _earth_unit(lat,lon):
    la=np.radians(np.asarray(lat,dtype=float));lo=np.radians(np.asarray(lon,dtype=float))
    return np.stack((np.cos(la)*np.cos(lo),np.cos(la)*np.sin(lo),np.sin(la)),axis=-1)


def _destination(lat,lon,bearing,km):
    lat1,lon1,b,arc=(math.radians(lat),math.radians(lon),math.radians(bearing),km/6371.0)
    lat2=math.asin(max(-1.,min(1.,math.sin(lat1)*math.cos(arc)+math.cos(lat1)*math.sin(arc)*math.cos(b))))
    lon2=lon1+math.atan2(math.sin(b)*math.sin(arc)*math.cos(lat1),math.cos(arc)-math.sin(lat1)*math.sin(lat2))
    return math.degrees(lat2),((math.degrees(lon2)+180)%360)-180


class CloudField:
    def __init__(self,data,max_nearest_km=44):
        from scipy.spatial import cKDTree
        self.lat,self.lon,self.low=_layer_grid(data,'低云')
        _,_,self.mid=_layer_grid(data,'中云')
        _,_,self.high=_layer_grid(data,'高云')
        self.valid=self.lat.shape==self.lon.shape and self.low.shape==self.lat.shape
        self.mask=np.isfinite(self.lat)&np.isfinite(self.lon)&np.isfinite(self.low)&np.isfinite(self.mid)&np.isfinite(self.high)
        if not self.mask.any():raise ValueError('快照没有完整的低中高云格点，不能分析')
        self.positions=np.column_stack((self.lat[self.mask],self.lon[self.mask]))
        self.values=np.column_stack((self.low[self.mask],self.mid[self.mask],self.high[self.mask]))
        self.tree=cKDTree(_earth_unit(*self.positions.T))
        self.max_nearest_km=max_nearest_km
        self.source=data.get('spatial_source') or {}
        self.time=normalize_utc(self.source.get('valid_time'))
        if self.time is None:raise ValueError('缺少 OM 有效时间，不计算区域候选')

    def lookup(self,lat,lon):
        q=_earth_unit(np.asarray(lat,dtype=float),np.asarray(lon,dtype=float))
        distance,idx=self.tree.query(q.reshape(-1,3),k=1)
        km=6371.0*2*np.arcsin(np.minimum(1.,distance/2))
        val=self.values[idx].astype(float).copy()
        val[km>self.max_nearest_km,:]=np.nan
        shape=np.shape(lat)
        return val.reshape((*shape,3))


def _solar_at_valid(field,lat,lon):
    local=field.time.astimezone(BEIJING).replace(tzinfo=None)
    return solar_position(local,8.,float(lat),float(lon))


def trace_horizontal_support(field,lat,lon,sun_bearing,*,upstream_km=700,step_km=50):
    """Full-distance 2-D *proxy*, not evidence that a particular beam passes."""
    ds=np.arange(step_km,upstream_km+0.01,step_km)
    points=[_destination(lat,lon,sun_bearing,float(d)) for d in ds]
    samples=field.lookup(np.array([p[0] for p in points]),np.array([p[1] for p in points]))
    valid=np.isfinite(samples).all(axis=1)
    if not valid.any():return {'support':None,'coverage':0.,'upstream_cover':None,'max_upstream_cover':None}
    # Aggregate cloudy area as an *uncertain* proxy, not independent ray extinction.
    covers=np.maximum(samples[valid,1],samples[valid,2])/100.
    # One dense distant cloud segment must not disappear into a 700km mean.
    # This is a conservative screening index, not verified optical attenuation.
    shade=.65*float(np.mean(covers))+.35*float(np.max(covers))
    return {'support':round(max(0.,1-shade),4),
            'coverage':round(float(valid.mean()),4),
            'upstream_cover':round(float(100*covers.mean()),2),
            'max_upstream_cover':round(float(100*covers.max()),2)}


def compass_candidate(data,lat,lon,*,direction='evening',target_distances=(45,95,145)):
    field=CloudField(data)
    bins=[]
    headings=[('北',0),('东北',45),('东',90),('东南',135),
              ('南',180),('西南',225),('西',270),('西北',315)]
    for name,bearing in headings:
        points=[]
        for dist in target_distances:
            tl,tn=_destination(lat,lon,bearing,dist)
            sample=field.lookup(np.array([tl]),np.array([tn]))[0]
            if not np.isfinite(sample).all():continue
            altitude,solar_az=_solar_at_valid(field,tl,tn)
            if (direction=='evening' and solar_az<180) or (direction=='morning' and solar_az>=180):continue
            support=trace_horizontal_support(field,tl,tn,solar_az)
            if support['coverage']<0.7:continue
            cover=float(max(sample[1],sample[2])/100.)
            low=float(sample[0]/100.)
            score=100.*cover*support['support']*(1-.55*low)
            points.append({'value':score,'coverage':support['coverage'], 'upstream_cover':support['upstream_cover'],
                           'altitude':altitude})
        bins.append({'name':name,'bearing':bearing,'relative_score':round(float(np.mean([p['value'] for p in points])),1) if points else None,
                     'candidate_targets':len(points), 'coverage':round(float(np.mean([p['coverage'] for p in points])),3) if points else 0.})
    return {'sectors':bins,'model':field.source.get('domain'),'run':field.source.get('reference_time'),
            'valid':field.source.get('valid_time'),'direction':direction,
            'sun_azimuth_deg':_solar_at_valid(field,lat,lon)[1],
            'note':'纯二维云场相对候选指数，不是发生概率或完整太阳光路。缺测扇区不排行。'}




def trace_coarse_spherical_ray(field, latitude, longitude, sun_altitude_deg,
                               sun_bearing_deg, target_height_km, *,
                               upstream_km=700, step_km=35):
    """No-I/O regional *screen*, not a 3-D optical-clearance certificate.

    Follow a straight solar ray over a spherical Earth, sampling preloaded OM
    horizontal low/mid/high clouds at the *ray's actual height*. Unlike an
    average over the full 700-km ground track, a dense distant cloud belt in
    the intersected altitude band can reject the candidate. The vertical
    bands are deliberately approximate; never label the result strict/verified.
    """
    if (not math.isfinite(float(sun_altitude_deg)) or
        not math.isfinite(float(target_height_km)) or target_height_km<=0):
        return {'support':None,'coverage':0.,'reason':'invalid_geometry',
                'exited_cloud_layer':False,'max_intersected_cover':None}
    distances=np.arange(float(step_km),float(upstream_km)+0.1,float(step_km),dtype=float)
    r=6371.0+float(target_height_km)
    a=math.radians(float(sun_altitude_deg))
    # Exact radial separation of a straight ray from a spherical surface.
    height=np.sqrt(r*r+distances*distances+2*r*distances*math.sin(a))-6371.0
    earth=np.where(height<=0)[0]
    # Check the exact closest approach too: a narrow Earth intersection
    # could otherwise be missed between coarse sample distances.
    closest=-r*math.sin(a)
    tangent_blocked=(0.<=closest<=float(upstream_km) and r*math.cos(a)<=6371.)
    if earth.size or tangent_blocked:
        return {'support':0.,'coverage':1.,'reason':'earth_blocked',
                'exited_cloud_layer':False,'max_intersected_cover':None}
    # Once the ray climbs above this *conservative cloud-top proxy*, and is
    # ascending, later layers from the same proxy cannot intersect it.
    ascending=distances + r*math.sin(a) > 0
    exit_idx=np.where((height>=17.)&ascending)[0]
    exited=bool(exit_idx.size)
    if exited:
        end=int(exit_idx[0])+1
        distances=distances[:end];height=height[:end]
    positions=[_destination(latitude,longitude,float(sun_bearing_deg),float(d)) for d in distances]
    points=field.lookup(np.array([v[0] for v in positions]),np.array([v[1] for v in positions]))
    good=np.isfinite(points).all(axis=1)
    coverage=float(good.mean()) if len(good) else 0.
    # A hole in the *data* is not a hole in the clouds. If coverage is poor,
    # decline to paint a positive candidate. A single missing cell among ~20
    # is treated conservatively as a possible opaque crossing.
    if coverage<0.90:
        return {'support':None,'coverage':round(coverage,3),'reason':'upstream_missing',
                'exited_cloud_layer':exited,'max_intersected_cover':None}
    cloud=np.clip(points/100.,0.,1.)
    def overlap(z,lower,upper,margin=0.75):
        return np.maximum(0.,np.minimum(1.,np.minimum((z-(lower-margin))/margin,
                          ((upper+margin)-z)/margin)))
    # Fixed physical-height *proxies* only: OM's 2-D cloud-cover product does
    # not reveal the true cloud base/top within its low/mid/high categories.
    bands=np.stack((overlap(height,0.,3.1),overlap(height,2.6,8.5),
                    overlap(height,6.0,16.0)),axis=1)
    intercepted=np.max(np.where(good[:,None],cloud*bands,1.),axis=1)
    # A long, dense cloud belt matters much more than one isolated coarse
    # pixel. Adjacent pixels are correlated: do NOT multiply independent
    # (1-cloud_cover) probabilities along the entire track.
    def strongest_belt(samples,width):
        n=min(len(samples),width)
        return float(np.max(np.convolve(samples,np.ones(n)/n,mode='valid'))) if n else 1.
    peak3=strongest_belt(intercepted,3)
    peak5=strongest_belt(intercepted,5)
    mean=float(np.mean(intercepted))
    obstruction=min(1.,0.55*peak3+0.25*peak5+0.20*mean)
    support=(1.-obstruction)**2 * coverage**3
    # Reaching 700 km below the plausible cloud-top envelope does not prove
    # the upstream ray is clear beyond it; discount rather than invent a pass.
    if not exited:support*=0.60
    return {'support':round(float(np.clip(support,0.,1.)),4),
            'coverage':round(coverage,3),
            'reason':'screened_to_cloud_top' if exited else 'screened_700km_unverified_beyond',
            'exited_cloud_layer':exited,
            'max_intersected_cover':round(100.*float(np.max(intercepted)),1),
            'strongest_3cell_belt':round(100.*peak3,1)}


def _national_local_sun_ok(altitude,direction):
    # User's fixed window: evening only AFTER sunset, morning only BEFORE
    # sunrise. -0.833 degrees is the same apparent-horizon threshold used by
    # web_national_map._local_event. Solar altitude is evaluated per cell.
    return -9.0 <= float(altitude) <= -0.833


def national_candidate(data,*,direction='evening',stride=1,max_cells=10000,longitude_bounds=None,latitude_bounds=None):
    """Regional dawn/dusk *candidate* heat map, using cached OM only.

    Screen candidate middle/high targets at two approximate altitudes against
    the coarse 3-layer solar-ray corridor. 0.25-degree cloud-cover cannot
    certify beam clearance or calibrated event probability. No new HTTP calls.
    """
    if direction not in ('morning','evening'):raise ValueError('direction must be morning/evening')
    field=CloudField(data)
    lat,lon=field.lat,field.lon
    ny,nx=lat.shape
    stride=max(int(stride),math.ceil(math.sqrt(ny*nx/max_cells)))
    chosen=np.zeros(lat.shape,dtype=bool);chosen[::stride,::stride]=True
    chosen&=field.mask
    if longitude_bounds is not None:
        chosen &= (lon>=longitude_bounds[0])&(lon<longitude_bounds[1])
    if latitude_bounds is not None:
        chosen &= (lat>=latitude_bounds[0])&(lat<=latitude_bounds[1])
    cells=np.argwhere(chosen)
    score=np.full(lat.shape,np.nan,dtype=float)
    classes=np.zeros(lat.shape,dtype=np.uint8)
    coverage=np.full(lat.shape,np.nan,dtype=float)
    in_window=0;checked=0;earth_blocked=0;unknown=0
    for y,x in cells:
        la,lo=float(lat[y,x]),float(lon[y,x])
        alt,az=_solar_at_valid(field,la,lo)
        if not _national_local_sun_ok(alt,direction):continue
        in_window+=1
        mid=float(np.clip(field.mid[y,x]/100.,0.,1.))
        high=float(np.clip(field.high[y,x]/100.,0.,1.))
        total=1.-(1.-mid)*(1.-high)
        if total<0.10:continue
        # These are representative target-layer heights, not measured bases.
        # Both must trace the *actual* curved-Earth solar-ray altitude, rather
        # than mixing all clouds at all heights in a single 700-km mean.
        rays=[]
        for amount,height in ((mid,5.5),(high,9.0)):
            if amount>=0.07:
                rays.append((amount,trace_coarse_spherical_ray(field,la,lo,alt,az,height)))
        if not rays:continue
        coverage[y,x]=min(r['coverage'] for _,r in rays)
        available=sum(w for w,r in rays if r['support'] is not None)
        required=sum(w for w,_ in rays)
        if required<=0 or available/required<0.8:
            unknown+=1
            continue
        support=sum(w*(r['support'] or 0.) for w,r in rays if r['support'] is not None)/required
        if all(r['reason']=='earth_blocked' for _,r in rays):earth_blocked+=1
        checked+=1
        low=float(np.clip(field.low[y,x]/100.,0.,1.))
        # Not a new Burning Sky score. This ONLY scales regional relative
        # potential with evidence of a plausible illuminated cloud corridor.
        # Later civil twilight becomes dimmer, and impossible Earth-blocked
        # rays receive zero, not red patches.
        twilight=max(0.,min(1.,(alt+9.)/5.5)) if alt < -3.5 else 1.
        score[y,x]=round(float(np.clip(100.*total*support*(1.-0.55*low)*twilight,0.,100.)),2)
        cy=max(0,y-2);dy=min(ny,y+3);cx=max(0,x-2);dx=min(nx,x+3)
        hood=np.maximum(field.mid[cy:dy,cx:dx],field.high[cy:dy,cx:dx])
        if np.isfinite(hood).any() and float(np.nanmax(hood)-np.nanmin(hood))>=35:
            classes[y,x]|=1
        # Only classify broad candidate openings when both target-layer rays
        # have enough data and the coarse *height-aware* obstruction is low.
        if support>=0.55 and coverage[y,x]>=0.95:
            classes[y,x]|=2
        low_hood=field.low[cy:dy,cx:dx]
        if np.isfinite(low_hood).any() and 100*low+25<=float(np.nanmean(low_hood)):
            classes[y,x]|=4
    return {'score':score,'classes':classes,'coverage':coverage,'latitude':lat,'longitude':lon,
            'source':field.source,'in_twilight_cells':in_window,'evaluated_cells':checked,
            'earth_blocked_cells':earth_blocked,'unknown_ray_cells':unknown,
            'direction':direction,'stride':stride,
            'ray_method':'coarse_spherical_height_bands_v1',
            'note':'仅日出前/日落后；复用 OM 网格进行 700 km 分层球面粗筛；不是全国各地当日实际精细预报；不确定光路不算晴空；相对指数非实测概率。'}


def render_compass_figure(result):
    """Eight-direction RADAR: same polar visual language as the established UI.

    Only measured directions have filled sectors. Missing data is not a zero
    score: the polygon intentionally breaks across absent directions.
    """
    from matplotlib.figure import Figure
    import numpy as np
    _setup_chinese_fonts()
    sectors=result['sectors']
    if len(sectors)!=8:raise ValueError('八方位雷达图需要恰好八个方向')
    theta=np.radians([float(s['bearing']) for s in sectors])
    values=np.array([float(s['relative_score']) if s['relative_score'] is not None else np.nan for s in sectors])
    fig=Figure(figsize=(7.8,7.4),dpi=115,facecolor='#5B3768')
    ax=fig.add_subplot(111,projection='polar',facecolor='#663873')
    ax.set_theta_zero_location('N');ax.set_theta_direction(-1)
    # Auto-fit the visual radius so a real 8-15 point difference does not
    # disappear at the centre of a permanently 0-110 chart. The displayed
    # numeric scores stay absolute 0-100 candidate indices.
    finite=values[np.isfinite(values)]
    peak=float(np.max(finite)) if finite.size else 0.
    radial_cap=(max(20.,min(110.,10.*math.ceil(1.4*peak/10.)))
                if result.get('method') in ('projected_target_clouds_from_solar_rays_v2','solar_gap_offset_cloud_weight_v3')
                else 110.)
    ax.set_ylim(0,radial_cap)
    ax.set_thetagrids(range(0,360,45),labels=['北','东北','东','东南','南','西南','西','西北'])
    ax.set_rticks([radial_cap*.25,radial_cap*.5,radial_cap*.75,radial_cap])
    ax.set_yticklabels([])
    ax.grid(color='#C7A2D0',alpha=.33,linewidth=.8)
    ax.spines['polar'].set_color('#BA91BD')
    ax.tick_params(colors='#E8D6EC',labelsize=11,pad=8)
    # Each adjacent verified *candidate* pair forms one filled wedge. Never
    # bridge missing sectors with a fabricated zero or an implied observation.
    for i in range(8):
        j=(i+1)%8
        if np.isfinite(values[i]) and np.isfinite(values[j]):
            arc=[theta[i],theta[j] if j else theta[0]+2*np.pi]
            ax.fill([arc[0],arc[0],arc[1],arc[1]],
                    [0,values[i],values[j],0],color='#F08D91',alpha=.30,linewidth=0)
    ax.plot(np.r_[theta,theta[0]+2*np.pi],np.r_[values,values[0]],
            color='#FFD45A',linewidth=2.4,zorder=5)
    good=np.isfinite(values)
    ax.scatter(theta[good],values[good],c='#FFD45A',edgecolors='#FFEFB8',s=30,zorder=6)
    for index in np.where(good)[0]:
        ax.text(theta[index],min(radial_cap*.96,values[index]+max(3.,radial_cap*.1)),f'{values[index]:.0f}',
                ha='center',va='center',fontsize=9,color='#FFF1C4',zorder=7)
    for index in np.where(~good)[0]:
        ax.text(theta[index],radial_cap*.88,'缺测',fontsize=8,color='#E3BEDB',ha='center',va='center')
    # 原白圈仅表示太阳方位的八扇区归属，并非最有利受光方向；取消。
    sun=result.get('sun_azimuth_deg')
    if sun is not None and math.isfinite(float(sun)):
        bearing=math.radians(float(sun))
        ax.plot([bearing,bearing],[0,radial_cap*.93],color='#FFD23F',linewidth=1.6,
                linestyle=(0,(4,3)),zorder=4)
        ax.scatter([bearing],[radial_cap*.93],s=125,facecolors='none',edgecolors='#FFD23F',linewidth=1.8,zorder=8)
        ax.text(bearing,radial_cap*.985,'日',color='#FFD23F',ha='center',va='center',fontsize=11,fontweight='bold')
    title='朝霞' if result['direction']=='morning' else '晚霞'
    fig.text(.07,.956,f'{title} · OM 云洞偏移 × 中高云八方位',color='#FFEBF3',fontsize=15,weight='bold')
    fig.text(.07,.915,str(result.get('model'))+' | '+str(result.get('valid'))+f' | 雷达刻度 0–{radial_cap:g}',color='#E8CFE9',fontsize=9)
    fig.text(.07,.032,'金线=云洞偏移光照优势×中高云；虚线=太阳；反烧为候选方向，非严格受光结论。',color='#F1DDF0',fontsize=9)
    fig.subplots_adjust(left=.12,right=.88,top=.87,bottom=.10)
    return fig


def render_national_candidate_figure(result):
    from matplotlib.figure import Figure
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable
    from desktop.spatial_v2_visual import _draw_china_basemap,_draw_admin_overlay
    _setup_chinese_fonts()
    fig=Figure(figsize=(12,7.6),dpi=110,facecolor='#f9fafb');ax=fig.add_subplot(111)
    _draw_china_basemap(ax)
    image=np.ma.masked_invalid(result['score'])
    ax.pcolormesh(result['longitude'],result['latitude'],image,shading='nearest',
                  cmap='YlOrRd',vmin=0,vmax=100,alpha=.84,zorder=3)
    colors={1:'#64ADCE',2:'#50B879',4:'#9D72C7'}
    for bit,color in colors.items():
        yy,xx=np.where((result['classes']&bit)!=0)
        if len(xx):
            ax.scatter(result['longitude'][yy,xx],result['latitude'][yy,xx],s=6,
                       c=color,alpha=.72,linewidths=0,zorder=5)
    _draw_admin_overlay(ax)
    fig.colorbar(ScalarMappable(norm=Normalize(0,100),cmap='YlOrRd'),ax=ax,
                 label='二维云场候选相对指数（非概率）',shrink=.8,pad=.02)
    ax.set(xlim=(73,136),ylim=(17.5,54.5),xlabel='经度 °E',ylabel='纬度 °N')
    ax.set_aspect('equal');ax.grid(alpha=.2)
    t=result['source'];ax.set_title(('朝霞' if result['direction']=='morning' else '晚霞')+' · 全国粗网格潜在区域（单有效时刻）\n'
        +str(t.get('domain'))+' | valid '+str(t.get('valid_time'))+' | run '+str(t.get('reference_time')),loc='left',fontsize=11)
    fig.text(.09,.042,'蓝:云边界候选  绿:完整已采样上游低云量候选  紫:低云间隙候选；颜色叠加不等于严格光路。',fontsize=9)
    fig.text(.09,.020,f"有效受光候选 {result['evaluated_cells']} 个采样格，处于该时次晨昏窗口 {result['in_twilight_cells']} 个；未显示区域≠无朝晚霞。",fontsize=9)
    fig.subplots_adjust(left=.09,right=.9,bottom=.14,top=.82)
    return fig
