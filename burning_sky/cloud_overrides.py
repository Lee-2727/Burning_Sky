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
    """Edit point samples at their actual coordinates; never blend a clear interval
    with the cloudiness outside the user-specified boundaries."""
    if not overrides: return samples
    for sample in samples:
        if sample.get('_manual_override_applied'): continue
        sample['_manual_override_applied']=True
        d=float(sample['distance_km'])
        for a,b,low,mid,high in overrides:
            if not (a <= d < b): continue
            keys=[]
            for key,value in zip(('cloud_cover_low','cloud_cover_mid','cloud_cover_high'),(low,mid,high)):
                if value is None: continue
                sample[key]=float(value);keys.append(key)
            if keys:
                sample['cloud_cover']=max(float(sample.get(k) or 0) for k in ('cloud_cover_low','cloud_cover_mid','cloud_cover_high'))
                sample['center_cloud_cover_low']=sample.get('cloud_cover_low')
                sample['cross_track_low_cover']=[sample.get('cloud_cover_low')]*6
                sample['cross_track_low_ge70_count']=6 if (sample.get('cloud_cover_low') or 0)>=70 else 0
                sample['cross_track_low_ge70_fraction']=1. if (sample.get('cloud_cover_low') or 0)>=70 else 0.
                sample['manual_cloud_override']=True
                sample['manual_cloud_override_keys']=keys
    return samples


def apply_overrides_to_profiles(profiles, samples, overrides):
    """Synchronize pressure-level cloud cover with edited distance samples.

    Preserve pressure heights/humidity and the existing vertical shape for partial
    edits; exact 0% and 100% edits are honored for the affected altitude band.
    No network access. All geometry and tracing use the same edited profiles.
    """
    if not overrides:
        return profiles
    # Store interval metadata on every profile, including profiles whose centres
    # fall outside the edited interval: cell construction needs exact boundaries.
    for profile in profiles:
        profile['manual_override_intervals'] = [list(row) for row in overrides]
    lookup={round(float(s['distance_km']),5):s for s in samples if isinstance(s.get('distance_km'),(int,float))}
    bands=((0.,2.,'cloud_cover_low'),(2.,6.,'cloud_cover_mid'),(6.,float('inf'),'cloud_cover_high'))
    for profile in profiles:
        if not isinstance(profile.get('distance_km'),(int,float)):
            continue
        sample=lookup.get(round(float(profile['distance_km']),5))
        if not sample or not sample.get('manual_cloud_override'):
            continue
        levels=profile.get('levels') or []
        changed=False
        for lo,hi,key in bands:
            if key not in sample.get('manual_cloud_override_keys',()):
                continue
            target=sample.get(key)
            if not isinstance(target,(int,float)):
                continue
            relevant=[lv for lv in levels if isinstance(lv.get('height_agl_km'),(int,float)) and lo<=float(lv['height_agl_km'])<hi]
            if not relevant:
                continue
            originals=[float(lv['cloud_cover']) for lv in relevant if isinstance(lv.get('cloud_cover'),(int,float))]
            mean=sum(originals)/len(originals) if originals else 0.
            for lv in relevant:
                old=lv.get('cloud_cover')
                lv.setdefault('_original_cloud_cover', old)
                if not isinstance(old,(int,float)):
                    old=mean
                if target<=0:
                    new=0.
                elif target>=100:
                    new=100.
                elif mean>0:
                    new=max(0.,min(100.,float(old)*float(target)/mean))
                else:
                    new=float(target)
                lv['cloud_cover']=new
                changed=True
        if changed:
            profile['manual_cloud_override']=True
            # Derived cloud-base markers can be stale after edits; the plotting
            # and raytrace use the edited pressure levels as their source.
            profile['manual_cloud_override_note']='Pressure-level cloud covers edited; heights unchanged'
    return profiles
