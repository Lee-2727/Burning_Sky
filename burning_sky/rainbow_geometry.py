"""Conservative rainbow candidate evaluator. Shared spherical geometry, rainbow-specific gates.

Cloud-band cover is an *unresolved obstruction proxy*, not a pressure sounding.
Indices are uncalibrated, never statistical occurrence probabilities.
"""
import math
from datetime import datetime, timedelta
from .algorithm import solar_position
from .spherical_raytrace import _ray_height_km, _earth_occluded, _cell_intersection

RAIN_DISTANCES = (10., 25., 45., 70.)
RAIN_BEARING_OFFSETS = (-40., -20., 0., 20., 40.)
SUN_DISTANCES = (0., 15., 35., 65., 100., 150.)


def _num(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def _at(hourly, key, index):
    arr=hourly.get(key) or []
    return _num(arr[index]) if 0<=index<len(arr) else None


def _hour_index(hourly, dt):
    times=hourly.get('time') or []
    try:
        indices=[(abs((datetime.fromisoformat(t)-dt).total_seconds()),i) for i,t in enumerate(times)]
        seconds,i=min(indices)
        return i if seconds<=1800 else None
    except (ValueError,TypeError):return None


def _rain_evidence(hourly, index):
    """Keep precipitation evidence separate from the final visibility index."""
    amount=_at(hourly,'precipitation',index)
    prob=_at(hourly,'precipitation_probability',index)
    humidity=_at(hourly,'relative_humidity_2m',index)
    prev=_at(hourly,'precipitation',index-1)
    prev2=_at(hourly,'precipitation',index-2)
    if amount is None and prob is None:
        return {'state':'missing','signal':None,'amount_mm':None,'probability_pct':None,'humidity_pct':humidity,'previous_hour_mm':prev,'previous_two_hours_mm':prev2}
    current=max(0.,amount or 0.)
    recent=max(0.,prev or 0.)+0.5*max(0.,prev2 or 0.)
    # Probabilistic forecasts do not prove suspended droplets; retain as candidate evidence.
    present=current>=0.03
    likely=(prob is not None and prob>=45 and (humidity is None or humidity>=70))
    residual=recent>=0.1 and humidity is not None and humidity>=85
    if present:
        signal=min(1.,0.40+0.35*min(1.,current/1.5)+0.25*min(1.,(prob or 0)/85))
        state='current_rain'
    elif likely:
        signal=min(.55,.20+.35*min(1.,(prob or 0)/100))
        state='forecast_rain_possible'
    elif residual:
        signal=min(.32,.12+.20*min(1.,recent/1.5))
        state='recent_rain_uncertain_droplets'
    else:
        signal=0.;state='no_droplet_evidence'
    return {'state':state,'signal':round(signal,4),'amount_mm':amount,'probability_pct':prob,
            'humidity_pct':humidity,'previous_hour_mm':prev,'previous_two_hours_mm':prev2}


def _rain_signal(hourly,index):
    return _rain_evidence(hourly,index)['signal']


def _light_status(cloud_hourlies, indices, alt, target_height_km=1.0):
    """First test a contiguous typical gap, then use exact spherical ray vs band cells.

    Missing coverage is unknown, not clear. Geometry uses fixed broad layer bands
    because the rainbow path has no retrieved pressure-level sounding.
    """
    if alt<=0:return '无地面直射',0.
    if len(cloud_hourlies)!=len(SUN_DISTANCES) or any(i is None for i in indices):return '光路数据不足',0.
    samples=[]
    for d,h,i in zip(SUN_DISTANCES,cloud_hourlies,indices):
        vals=[_at(h,k,i) for k in ('cloud_cover_low','cloud_cover_mid','cloud_cover_high')]
        if any(v is None for v in vals):return '光路数据不足',0.
        samples.append((d,vals))
    # A typical gap is at least 35 km with every band under 15% cloud cover.
    gap=0.;gap_end=0.
    for j in range(len(samples)-1):
        a,vs=samples[j];b,vs2=samples[j+1]
        if max(vs)<15 and max(vs2)<15:
            gap+=b-a;gap_end=b
        else:break
    if gap>=35 and gap_end>=SUN_DISTANCES[-1]:
        if not _earth_occluded(target_height_km,alt,gap_end):return '典型云洞直射（150km内）',1.0
    # Strict ray tracing: use broad band proxies with 35% cloud-cover threshold.
    bands=((0.,2.,0),(2.,6.,1),(6.,12.,2))
    blocked=0;checked=0
    for j,(d,vs) in enumerate(samples):
        left=max(0.,(samples[j-1][0]+d)/2) if j else 0.
        right=(d+samples[j+1][0])/2 if j+1<len(samples) else d
        if _earth_occluded(target_height_km,alt,right):return '地球遮挡',0.
        for bottom,top,k in bands:
            if vs[k]>=35:
                cell={'start_km':left,'end_km':right,'bottom_km':bottom,'top_km':top}
                if _cell_intersection(target_height_km,alt,cell,end_distance_km=SUN_DISTANCES[-1]):blocked+=1
            checked+=1
    if blocked:return '球面光路被云遮挡',0.
    return '球面云隙直射（150km粗层近似）',1.0


def evaluate(lat,lon,event_utc,rain_sites,light_paths,environment=None,minute_range=(-240,90)):
    """Regional rainbow opportunity: no fixed-viewer rainbow-arc exclusion.

    The sampling origin is only the center of the search grid.  Results describe
    illuminated rain *areas*, not a fixed observer's visible rainbow or a
    statistically calibrated occurrence probability.
    """
    timeline=[];best=None
    environment=environment or [{} for _ in rain_sites]
    for minutes in range(minute_range[0],minute_range[1]+1,15):
        dt=event_utc+timedelta(minutes=minutes)
        alt,az=solar_position(dt,0,lat,lon)
        row={'time':dt.isoformat(),'solar_altitude_deg':round(alt,2),
             'solar_azimuth_deg':round(az,2),'sampled_rain_sites':len(rain_sites),
             'details':[],'rejections':{'missing_hour':0,'missing_rain':0,'no_rain':0,'no_direct_sun':0}}
        # Near-horizon negative solar altitudes are allowed as a weak geometric
        # candidate; below -2 degrees there is no reliable direct beam.
        # No artificial noon scan: time windows are anchored to solar events.
        if not -2.0<=alt<=53:
            row.update(score=0,sector_coverage_pct=None,candidate_area_fraction_pct=0,
                       status='太阳高度角不满足主虹/副虹地面筛选')
            timeline.append(row);continue
        # No arbitrary per-sector 20% threshold; preserve all illuminated rain candidates.
        valid=[];sector_values={o:[] for o in RAIN_BEARING_OFFSETS}
        primary_candidates=[];secondary_candidates=[]
        for site,path,env in zip(rain_sites,light_paths,environment):
            dist,offset,rlat,rlon,hourly=site
            idx=_hour_index(hourly,dt)
            detail={'distance_km':dist,'offset_deg':offset,'latitude':rlat,'longitude':rlon}
            if idx is None:
                row['rejections']['missing_hour']+=1;detail['reason']='缺少对应小时';row['details'].append(detail);continue
            evidence=_rain_evidence(hourly,idx)
            detail.update(evidence)
            if evidence['signal'] is None:
                row['rejections']['missing_rain']+=1;detail['reason']='缺少降水数据';row['details'].append(detail);continue
            if evidence['signal']==0:
                row['rejections']['no_rain']+=1;detail['reason']='未发现雨滴证据';row['details'].append(detail);continue
            indices=[_hour_index(h,dt) for h in path]
            # Height sensitivity, NOT a claim of resolved droplet height.
            trials=[]
            for h in (0.3,1.0,2.0):
                status,trans=_light_status(path,indices,alt,target_height_km=h)
                trials.append({'target_height_km':h,'status':status,'transmission_proxy':trans})
            light=max(x['transmission_proxy'] for x in trials)
            detail['light_trials']=trials
            detail['light']=max(trials,key=lambda x:x['transmission_proxy'])['status']
            if light<=0:
                row['rejections']['no_direct_sun']+=1;detail['reason']='没有证实太阳直射';row['details'].append(detail);continue
            wind=_at(hourly,'wind_speed_10m',idx)
            vis=_at(hourly,'visibility',idx)
            vis_source='selected_weather_model' if vis is not None else 'missing'
            if vis is None:
                vis=_at(env.get('visibility_hourly',{}),'visibility',_hour_index(env.get('visibility_hourly',{}),dt))
                if vis is not None:vis_source='best_match_visibility_fallback'
            # Air quality uses its own hourly time grid, never the weather index.
            aq=env.get('air_quality_hourly',{})
            aq_idx=_hour_index(aq,dt)
            pm25=_at(aq,'pm2_5',aq_idx) if aq_idx is not None else None
            pm10=_at(aq,'pm10',aq_idx) if aq_idx is not None else None
            aod=_at(aq,'aerosol_optical_depth',aq_idx) if aq_idx is not None else None
            # Direct normal irradiance is a local proxy, not proof of sunshine at the rain site.
            dni=_at(env.get('visibility_hourly',{}),'direct_normal_irradiance',_hour_index(env.get('visibility_hourly',{}),dt))
            detail.update({'visibility_source':vis_source,'pm2_5':pm25,'pm10':pm10,
                           'aod':aod,'dni':dni,'air_quality_source':'open_meteo_air_quality' if aq_idx is not None else 'missing'})

            humidity=evidence['humidity_pct']
            amount=evidence['amount_mm']
            pop=evidence['probability_pct']
            # Conditional expected precipitation: PoP is not additional rainfall.
            # If actual model amount is zero, keep PoP as uncertainty, not invented millimetres.
            equivalent_rain_mm=(max(0.,amount) if amount is not None else None)
            if equivalent_rain_mm is None and pop is not None:
                equivalent_rain_mm=round(.25*max(0.,min(1.,pop/100.)),3)
            rain_factor=min(1.,.55+.45*math.sqrt(max(0.,equivalent_rain_mm or 0.)/.5))
            pop_factor=1. if pop is None else (.78+.22*max(0.,min(1.,pop/85.)))
            humidity_factor=1. if humidity is None else max(.72,min(1.,.78+.22*(humidity/85.)))
            # Visibility under rainfall cannot distinguish aerosol extinction from precipitation.
            # Penalize only very low visibility; missing visibility is UNKNOWN, not an automatic penalty.
            visibility_factor=1. if vis is None else max(.55,min(1.,vis/8000.))
            wind_factor=1. if wind is None else max(.70,1.-max(0.,wind-7.)*.025)
            # Hourly PM is NOT a WHO daily health assessment. It is an aerosol proxy.
            # Apply small bounded penalties only when a measured/predicted field exists.
            aerosol_factor=1.0
            if aod is not None:aerosol_factor=min(aerosol_factor,max(.72,1.-max(0.,aod-.25)*.22))
            if pm25 is not None:aerosol_factor=min(aerosol_factor,max(.78,1.-max(0.,pm25-15.)*.003))
            if pm10 is not None:aerosol_factor=min(aerosol_factor,max(.82,1.-max(0.,pm10-45.)*.0015))

            evidence_factor=max(0.,min(1.,evidence['signal'] or 0.))
            # Conditional occurrence proxy, not an empirically calibrated probability.
            # Use a saturating union of partly independent evidences instead of multiplying
            # every factor and accidentally treating a confirmed sun path as only 65% light.
            existence=min(1.,.40*evidence_factor+.28*rain_factor+.18*pop_factor+.14*humidity_factor)
            probability_proxy=existence*light*visibility_factor*wind_factor*aerosol_factor
            sun_primary=max(0.,min(1.,(42.-alt)/25.))
            sun_secondary=max(0.,min(1.,(51.-alt)/30.))
            primary=probability_proxy*(.78+.22*sun_primary) if alt<42. else 0.
            # Secondary requires more contrast; distinguish formation potential from visual quality.
            secondary=primary*(.36+.20*sun_secondary)*(.90 if vis is None else max(.55,min(1.,vis/12000.))) if alt<51. else 0.
            if evidence['state']!='current_rain': secondary*=.80
            quality=min(1.,(.40*rain_factor+.30*visibility_factor+.30*light)*wind_factor*aerosol_factor)
            secondary_quality=quality*(.50+.20*sun_secondary)
            detail.update({'primary_index':round(primary*100,1),'secondary_index':round(secondary*100,1),
                           'primary_quality':round(quality*100,1),'secondary_quality':round(secondary_quality*100,1),
                           'wind_mps':wind,'visibility_m':vis,'light_proxy':light,
                           'quality_index':round(100*quality,1),'equivalent_rain_mm':equivalent_rain_mm,
                           'score_components':{'rain_evidence':round(evidence_factor,3),'rain_amount':round(rain_factor,3),
                             'precip_probability':round(pop_factor,3),'humidity':round(humidity_factor,3),
                             'light':round(light,3),'visibility':round(visibility_factor,3),
                             'wind':round(wind_factor,3),'aerosol':round(aerosol_factor,3),'existence_proxy':round(existence,3),
                             'solar_primary':round(sun_primary,3),'solar_secondary':round(sun_secondary,3)},
                           'uncertainty_flags':(['visibility_missing'] if vis is None else [])+
                             (['precip_amount_missing'] if amount is None else [])+
                             (['pm_aod_missing'] if pm25 is None and pm10 is None and aod is None else [])+
                             ['cloud_layer_height_proxy','not_calibrated'],
                           'reason':'有雨滴证据且粗层直射通过'})
            row['details'].append(detail)
            valid.append(detail)
            primary_candidates.append(primary)
            secondary_candidates.append(secondary)
            sector_values[offset].append(quality)
        # An area opportunity should not vanish because every sector scores < 20.
        sector_max={str(k):round(100*max(v),1) if v else 0 for k,v in sector_values.items()}
        row['sector_quality_indices']=sector_max
        row['candidate_area_fraction_pct']=round(100*len(valid)/max(1,len(rain_sites)))
        row['illuminated_candidate_count']=len(valid)
        row['candidate_bearing_offsets_deg']=[k for k,v in sector_values.items() if v]
        row['primary_index']=round(100*max(primary_candidates,default=0))
        row['primary_quality']=round(max((d['primary_quality'] for d in valid),default=0))
        row['secondary_quality']=round(max((d['secondary_quality'] for d in valid),default=0))
        row['secondary_index']=round(100*max(secondary_candidates,default=0))
        row['double_rainbow_index']=min(row['primary_index'],row['secondary_index'])
        row['score']=row['primary_index']
        row['sector_coverage_pct']=None  # Cannot infer actual arc coverage from this grid.
        row['status']='发现受光雨区候选' if valid else '未发现满足条件的受光雨区'
        timeline.append(row)
        # Compare continuous evidence, then distinct sites, not rounded score alone.
        ranking=(len(valid)>0,row['primary_index'],row['secondary_index'],len(valid),sum(d['quality_index'] for d in valid))
        if best is None or ranking>best[0]:best=(ranking,row)
    winner=best[1] if best else {'score':0,'details':[],'candidate_area_fraction_pct':0,'sector_coverage_pct':None}
    score=winner['score']
    label='未检出' if not winner.get('illuminated_candidate_count') else ('弱候选' if score<25 else '有候选区域' if score<50 else '较明显候选区域')
    return {'score':score,'label':label,'time':winner.get('time'),
            'sector_coverage_pct':None,'candidate_area_fraction_pct':winner.get('candidate_area_fraction_pct',0),
            'candidate_bearing_offsets_deg':winner.get('candidate_bearing_offsets_deg',[]),
            'completeness_pct':None,'rainbow_completeness':'未计算：区域雨区分析不能代替固定视点的虹弧完整度',
            'primary_rainbow_potential':bool(winner.get('primary_index',0)),
            'secondary_rainbow_potential':bool(winner.get('secondary_index',0)),
            'primary_index':winner.get('primary_index',0),
            'secondary_index':winner.get('secondary_index',0),
            'primary_probability_pct':winner.get('primary_index',0),
            'secondary_probability_pct':winner.get('secondary_index',0),
            'primary_quality':winner.get('primary_quality',0),
            'secondary_quality':winner.get('secondary_quality',0),
            'probability_kind':'uncalibrated heuristic probability-like score',
            'double_rainbow_index':winner.get('double_rainbow_index',0),
            'light_method':'逐候选雨区太阳方向粗层球面光路；0.3/1/2km高度敏感性测试',
            'timeline':timeline,
            'note':'Debug33：概率式启发分值，未经实况校准；通过的粗层光路给满分，缺失能见度不惩罚；能见度受雨雾和气溶胶共同影响，不能等同空气纯净度。质量分独立输出；未计算真实虹弧完整度。'}
