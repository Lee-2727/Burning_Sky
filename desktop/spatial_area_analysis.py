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
            'note':'纯二维云场相对候选指数，不是发生概率或完整太阳光路。缺测扇区不排行。'}


def national_candidate(data,*,direction='evening',stride=1,max_cells=10000):
    field=CloudField(data)
    lat,lon=field.lat,field.lon
    ny,nx=lat.shape
    # Bound computation when a high-res model or wider snapshot is used.
    stride=max(int(stride),math.ceil(math.sqrt(ny*nx/max_cells)))
    chosen=np.zeros(lat.shape,dtype=bool);chosen[::stride,::stride]=True
    chosen&=field.mask
    cells=np.argwhere(chosen)
    score=np.full(lat.shape,np.nan,dtype=float)
    classes=np.zeros(lat.shape,dtype=np.uint8)
    coverage=np.full(lat.shape,np.nan,dtype=float)
    in_window=0;verified_coverage=0
    for y,x in cells:
        la,lo=float(lat[y,x]),float(lon[y,x])
        alt,az=_solar_at_valid(field,la,lo)
        if not (-9<=alt<=6) or (direction=='morning' and az>=180) or (direction=='evening' and az<180):
            continue
        in_window+=1
        cloud=float(max(field.mid[y,x],field.high[y,x])/100.)
        if cloud<0.10:continue
        ray=trace_horizontal_support(field,la,lo,az)
        coverage[y,x]=ray['coverage']
        if ray['coverage']<0.70:continue
        verified_coverage+=1
        low=field.low[y,x]/100.
        score[y,x]=round(100*cloud*ray['support']*(1-0.55*low),2)
        # These *are independent heuristic labels*, not mutually exclusive physics.
        # A: cloud boundary based on nearby high+middle cloud contrast.
        cy=max(0,y-2);dy=min(ny,y+3);cx=max(0,x-2);dx=min(nx,x+3)
        hood=np.maximum(field.mid[cy:dy,cx:dx],field.high[cy:dy,cx:dx])
        if np.isfinite(hood).any() and float(np.nanmax(hood)-np.nanmin(hood))>=35:
            classes[y,x]|=1
        # B: broad possible upstream opening; the entire 700km path was tested
        # as sampled *horizontal averages*, not verified solar transmittance.
        if (ray['coverage']>=0.999 and ray['upstream_cover'] is not None
            and ray['upstream_cover']<=45 and ray['max_upstream_cover']<=60):
            classes[y,x]|=2
        # C: local low-cloud break with target middle/high cloud present.
        low_hood=field.low[cy:dy,cx:dx]
        if np.isfinite(low_hood).any() and 100*low+25<=float(np.nanmean(low_hood)):
            classes[y,x]|=4
    return {'score':score,'classes':classes,'coverage':coverage,'latitude':lat,'longitude':lon,
            'source':field.source,'in_twilight_cells':in_window,'evaluated_cells':verified_coverage,
            'direction':direction,'stride':stride,
            'note':'单一 OM 有效时次、二维粗网格候选区域，不是全国各地当日实际日出/落预报；空白含时差与缺测。'}


def render_compass_figure(result):
    from matplotlib.figure import Figure
    _setup_chinese_fonts()
    fig=Figure(figsize=(9,5.5),dpi=110,facecolor='#f9fafb');ax=fig.add_subplot(111)
    sectors=result['sectors']
    vals=[r['relative_score'] if r['relative_score'] is not None else 0 for r in sectors]
    colors=['#ECA179' if r['relative_score'] is not None else '#DEE1E6' for r in sectors]
    bars=ax.bar([x['name'] for x in sectors],vals,color=colors,edgecolor='none')
    for i,row in enumerate(sectors):
        if row['relative_score'] is None: ax.text(i,4,'缺测',ha='center',fontsize=9,color='#6b7280')
        else: ax.text(i,row['relative_score']+1,str(row['relative_score']),ha='center',fontsize=9)
    ax.set_ylim(0,110);ax.set_ylabel('云场相对候选指数（非概率）')
    ax.set_title('八方位云场候选优势 · '+('朝霞' if result['direction']=='morning' else '晚霞')+'\n'+str(result['model'])+' | '+str(result['valid']),loc='left')
    ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    fig.text(.09,.025,result['note'],color='#64748b',fontsize=9)
    fig.subplots_adjust(left=.09,right=.97,bottom=.16,top=.79)
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
