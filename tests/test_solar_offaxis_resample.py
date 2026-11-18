"""True-bearing resampling and color-score regression, no API calls."""
from datetime import datetime
from unittest.mock import patch
import math

from burning_sky.spherical_raytrace import destination
from burning_sky.sky_six import (plan_off_axis_rays, materialize_off_axis_rays,
                                  adaptive_equivalent_gaps,dynamic_solar_source_horizon)
from burning_sky.algorithm import calculate_color_performance_score, aod_color_quality_factor

DT=datetime(2026,11,18,6,41)
LAT,LON=31.8047,117.2214

def cloud_levels(cover=0):
    return [{'height_agl_km':h,'cloud_cover': cover if h==6 else 0.,
             'pressure_hpa':500,'relative_humidity':60} for h in (3,6,10,16,22)]

def event_axis():
    return [{'distance_km':d,'lat':destination(LAT,LON,90,d)[0],
             'lon':destination(LAT,LON,90,d)[1],
             'levels':cloud_levels(80 if d==0 else 0)}
            for d in (0,25,50,100,150,200,250,300,350,400,450,500,550,600,650,700,750)]

def shifted(dt,offset,lat,lon):
    return (-1.7, 90 if abs(lat-LAT)<1e-6 and abs(lon-LON)<1e-6 and dt==DT else 95)

def get_setup(block=False):
    axis=event_axis()
    with patch('burning_sky.sky_six.solar_position',side_effect=shifted):
        plan=plan_off_axis_rays(axis,DT,8,LAT,LON,'morning',27.52,25,source_scan_km=750)
    assert plan['rays']
    data=[{'levels':cloud_levels(100 if block and (370 < i*20 < 440) else 0)}
          for i in range(len(plan['coordinates']))]
    beams=materialize_off_axis_rays(plan,data)
    return axis,plan,beams

def test_auto_resample_fills_departed_solar_rays():
    axis,plan,beams=get_setup()
    assert len(plan['coordinates'])>0
    assert all(len(ray)>=2 for ray in beams.values())
    with patch('burning_sky.sky_six.solar_position',side_effect=shifted):
        without=adaptive_equivalent_gaps(axis,DT,8,LAT,LON,'morning',27.52,
                                          source_scan_km=700,target_limit_km=25)
        with_data=adaptive_equivalent_gaps(axis,DT,8,LAT,LON,'morning',27.52,
                    source_scan_km=700,target_limit_km=25,off_axis_ray_profiles=beams)
    assert without['status']=='unresolved_data',without
    assert with_data['status']=='local_targets_verified',with_data
    assert with_data['off_axis_rays_measured']>0,with_data
    assert not with_data['off_axis_rays_missing'],with_data


def test_missing_resampled_point_never_counts_as_clear_air():
    axis,plan,beams=get_setup()
    first_ray=next(iter(beams))
    beams[first_ray]=[]
    with patch('burning_sky.sky_six.solar_position',side_effect=shifted):
        r=adaptive_equivalent_gaps(axis,DT,8,LAT,LON,'morning',27.52,
            source_scan_km=700,target_limit_km=25,off_axis_ray_profiles=beams)
    # A missing per-bottom ray may still be reconstructed from OTHER actually
    # measured stations. If not, it must remain explicitly unresolved.
    assert (r['off_axis_rays_missing']>=1 or
            r.get('bottom_unresolved_point_count',0)>=1),r
    assert (first_ray[0] in r['spatially_unverified_minutes'] or
            r.get('bottom_unresolved_point_count',0)>=1),r


def test_remote_blocker_on_real_ray_rejects_a_clear_front_segment():
    axis,plan,beams=get_setup()
    # Block along each *actual* ray at 400km, not on the old eastbound section.
    for ray in beams.values():
        for row in ray:
            if 350 <= row['distance_km'] <= 450:
                row['levels']=cloud_levels(100)
    with patch('burning_sky.sky_six.solar_position',side_effect=shifted):
        r=adaptive_equivalent_gaps(axis,DT,8,LAT,LON,'morning',27.52,
            source_scan_km=700,target_limit_km=25,off_axis_ray_profiles=beams)
    assert not r['verified_ray_traces'],r


def test_aod_050_cannot_output_62_with_good_clouds():
    obj={'low':0,'mid':50,'high':50,'total':60,'visibility_m':20000,
         'aod':.50,'low_cloud_color_factor':1.}
    output=calculate_color_performance_score(obj)
    assert output['color_score']<40,output
    assert aod_color_quality_factor(.5)==.3


def test_color_score_drops_continuously_with_aod():
    scores=[]
    for aod in (.15,.25,.35,.45,.50,.60,.80):
        score=calculate_color_performance_score({'low':10,'mid':50,'high':50,'total':65,
            'visibility_m':15000,'aod':aod,'low_cloud_color_factor':1.})['color_score']
        scores.append(score)
    assert all(a>=b for a,b in zip(scores,scores[1:])),scores
