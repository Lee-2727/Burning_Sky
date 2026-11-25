"""No fictitious sky: inner underside targets reuse only geographically valid data."""
from datetime import datetime, timedelta
from unittest.mock import patch
from burning_sky.sky_six import (_measured_station_index,_reproject_measured_ray,
    _validated_cloud_bottom,_bottom_samples,destination)
from burning_sky.local_cloud_illumination import apply_to_curve

DT=datetime(2026,11,20,6,40)

def _rows(az=114,blocker_at=None,omit=None):
    lat,lon=31.8,117.2
    target={'start_km':0.,'end_km':50.,'bottom_km':8.,'top_km':9.,'sample_distance_km':25.,'cloud_cover':80.}
    pos=_bottom_samples(target)[0]
    tlat,tlon=destination(lat,lon,100.,pos)
    count=14
    width=(705.-pos)/count
    rows=[]
    for i in range(count):
        if i==omit:continue
        dist=(i+.5)*width
        a,b=destination(tlat,tlon,az,dist)
        cloud=100. if blocker_at==i else 0.
        rows.append({'lat':a,'lon':b,'distance_km':100.+dist,'levels':[
            {'height_agl_km':h,'cloud_cover':cloud,'pressure_hpa':300} for h in [1.,4.,8.,12.,18.] ]})
    return target,pos,rows

def test_inner_sunray_reuses_only_measured_geo_samples():
    target,pos,rows=_rows()
    lat,lon=destination(31.8,117.2,100.,pos)
    index=_measured_station_index([], {(0,50.):rows})
    ray=_reproject_measured_ray(index,lat,lon,114.,pos,705.)
    assert ray is not None and len(ray)==14
    with patch('burning_sky.sky_six.solar_position',return_value=(-0.8,114.)):
        points=_validated_cloud_bottom(target,-5,DT,100.,31.8,117.2,8.,705.,
                                      [],[],{},False,{},index)
    assert points[0]['status']=='verified_bottom',points[0]
    # Other inner points must NOT automatically inherit this one valid ray.
    assert sum(p['status']=='verified_bottom' for p in points)<5

def test_missing_station_does_not_become_fictional_clear_air():
    target,pos,rows=_rows(omit=5)
    lat,lon=destination(31.8,117.2,100.,pos)
    index=_measured_station_index(rows,{})
    assert _reproject_measured_ray(index,lat,lon,114.,pos,705.) is None

def test_distant_measured_cloud_blocks_inner_target():
    target,pos,rows=_rows(blocker_at=7)
    index=_measured_station_index(rows,{})
    with patch('burning_sky.sky_six.solar_position',return_value=(-0.8,114.)):
        points=_validated_cloud_bottom(target,-5,DT,100.,31.8,117.2,8.,705.,
                                      [],[],{},False,{},index)
    assert points[0]['status']=='cloud_blocked',points[0]

def test_weak_verified_twilight_kept_but_not_major_overlay():
    dt=DT
    minute=[{'datetime':dt-timedelta(minutes=1),'minutes_from_event':-1,'color':(50,50,50)},
            {'datetime':dt,'minutes_from_event':0,'color':(50,50,50)}]
    light={'available':True,'potential_km':100.,'target_weight':90.,'target_count':1,'direction':'morning','caveat':'offline',
           'times':[{'datetime':dt-timedelta(minutes=1),'fraction':.01,'strict_fraction':.01,'possible_fraction':.02,
                    'front_face_fraction':0,'bottom_unresolved_weight_fraction':0,
                    'representative_solar_altitude_deg':-4.5,
                    'dominant':{'illumination_fraction':.01,'solar_altitude_deg':-4.5,'face_km':50.,'height_km':(8,9)}},
                    {'datetime':dt,'fraction':.8,'strict_fraction':.8,'possible_fraction':.9,
                    'front_face_fraction':0,'bottom_unresolved_weight_fraction':0,
                    'representative_solar_altitude_deg':-1.2,
                    'dominant':{'illumination_fraction':.8,'solar_altitude_deg':-1.2,'face_km':50.,'height_km':(8,9)}}]}
    ans=apply_to_curve({'samples':minute},light,color_fn=lambda alt:(200,90 if alt<-3 else 200,100),target_cloud_cover_pct=90.)
    assert ans['samples'][0]['strict_light_window'] is True
    assert ans['samples'][0]['colored_cloud_area_pct'] > 0
    assert ans['samples'][0]['major_coloration_valid'] is False
    assert ans['samples'][0]['color']!=(50,50,50)

def test_lit_cloud_colour_uses_real_inner_target_sun_angle_not_face_angle():
    from burning_sky.local_cloud_illumination import local_illumination
    from burning_sky.spherical_raytrace import profile_cloud_cells
    from burning_sky.sky_six import _bottom_sweep_strips
    profiles=[]
    for dist in [0.,25.,50.]:
        profiles.append({'distance_km':dist,'levels':[
            {'height_agl_km':8.,'cloud_cover':90. if dist<50 else 0.},
            {'height_agl_km':10.,'cloud_cover':0.}]})
    target=profile_cloud_cells(profiles,threshold=.000001,blur_horizontal_km=0.,blur_vertical_km=0.)[0]
    key=[round(float(target[k]),6) for k in ('start_km','end_km','bottom_km','top_km')]
    verified=[{'minute_offset':-1,'target_key':key,'position_km':x['position_km'],
               'area_width_km':x['area_width_km'],
               'solar_altitude_deg':-4.,'status':'verified_bottom'}
              for x in _bottom_sweep_strips(target,50.)]
    with patch('burning_sky.local_cloud_illumination.solar_position',return_value=(-0.2,100.)):
        lit=local_illumination(profiles,DT,'morning',8.,31.8,117.2,50.,minutes=1,
                               verified_target_bands=[],verified_target_bottom_points=verified)
    minute=next(p for p in lit['times'] if p['minutes_from_event']==-1)
    assert minute['dominant']['solar_altitude_deg']==-4.,minute
    assert minute['representative_solar_altitude_deg']==-4.,minute
