"""Non-destructive cloud-cover overrides for the solar-direction sampling corridor."""
import math

def parse_overrides(text):
    rows=[]
    for lineno,line in enumerate(str(text or '').splitlines(),1):
        line=line.strip()
        if not line or line.startswith('#'): continue
        fields=[x.strip() for x in line.replace('，',',').split(',')]
        if len(fields)!=5: raise ValueError(f'第 {lineno} 行应为：起点,终点,低云,中云,高云（云量可留空）')
        try:
            a,b=map(float,fields[:2])
            if not (0<=a<b<=600): raise ValueError()
            vals=[None if x=='' else float(x) for x in fields[2:]]
            if all(x is None for x in vals) or any(x is not None and (not math.isfinite(x) or not 0<=x<=100) for x in vals): raise ValueError()
        except ValueError: raise ValueError(f'第 {lineno} 行的距离或云量无效（0–600 km，0–100%）')
        if rows and a<rows[-1][1]: raise ValueError('修正区间必须按距离升序排列，且不能重叠')
        rows.append((a,b,*vals))
    return rows

def apply_overrides(samples, overrides):
    if not overrides: return samples
    ordered=sorted(samples,key=lambda x:float(x.get('distance_km',0)))
    for i,sample in enumerate(ordered):
        if sample.get('_manual_override_applied'): continue
        sample['_manual_override_applied']=True
        d=float(sample['distance_km'])
        left=0. if i==0 else (d+float(ordered[i-1]['distance_km']))/2
        right=(d+float(ordered[i+1]['distance_km']))/2 if i+1<len(ordered) else min(600.,d+max(12.5,(d-left)))
        width=max(1e-6,right-left)
        modified=False
        for a,b,low,mid,high in overrides:
            fraction=max(0.,min(right,b)-max(left,a))/width
            if fraction<=0:continue
            for key,value in zip(('cloud_cover_low','cloud_cover_mid','cloud_cover_high'),(low,mid,high)):
                if value is None:continue
                previous=sample.get(key)
                previous=float(previous) if isinstance(previous,(int,float)) else value
                sample[key]=max(0.,min(100.,previous*(1-fraction)+value*fraction))
                modified=True
            if modified:
                sample['cloud_cover']=max(sample.get('cloud_cover_low') or 0,sample.get('cloud_cover_mid') or 0,sample.get('cloud_cover_high') or 0)
                sample['center_cloud_cover_low']=sample.get('cloud_cover_low')
                sample['cross_track_low_cover']=[sample.get('cloud_cover_low')]*6
                sample['cross_track_low_ge70_count']=6 if (sample.get('cloud_cover_low') or 0)>=70 else 0
                sample['cross_track_low_ge70_fraction']=1. if (sample.get('cloud_cover_low') or 0)>=70 else 0.
                sample['manual_cloud_override']=True
    return samples
