from datetime import datetime
from burning_sky.local_cloud_illumination import projection_zone_illumination, apply_to_curve

def _cloud(a,b,lo,hi):
    return {'start_km':a,'end_km':b,'bottom_km':lo,'top_km':hi,'cloud_cover':80.}

def test_double_layer_one_zone_and_face_one_third():
    c=[_cloud(0,15,6,8),_cloud(0,15,3,5),_cloud(15,30,6,8),_cloud(30,45,6,8)]
    bottom=[{'minute_offset':0,'position_km':0,'area_width_km':15,'status':'verified_bottom','target_key':[0,15,6,8]},
            {'minute_offset':0,'position_km':0,'area_width_km':15,'status':'verified_bottom','target_key':[0,15,3,5]}]
    face=[{'minute_offset':0,'target_key':[15,30,6,8],'height_band_km':[6,7]}]
    a=projection_zone_illumination(c,45,bottom,face,[0,1],include_faces=True);assert len(a['zones'])==3
    assert list(a['minute_weights'][0].values())==[1.,1./3.,0.]
    assert all(x==0 for x in a['minute_weights'][1].values())

def test_short_range_single_region():
    x=projection_zone_illumination([_cloud(0,8,7,8)],8,[{'minute_offset':0,'position_km':4,
       'status':'verified_bottom','target_key':[0,8,7,8],'area_width_km':8}],[],[0])
    assert len(x['zones'])==1 and list(x['minute_weights'][0].values())==[1]

def test_face_only_does_not_overwrite_bottom():
    cloud=[_cloud(0,15,6,8)]
    bottom=[{'minute_offset':0,'position_km':0,'status':'verified_bottom','target_key':[0,15,6,8],'area_width_km':15}]
    face=[{'minute_offset':0,'target_key':[0,15,6,8],'height_band_km':[6,7]}]
    a=projection_zone_illumination(cloud,15,bottom,face,[0],include_faces=True);assert list(a['minute_weights'][0].values())==[1]

def test_color_does_not_require_dominant_cloud():
    t=datetime(2026,11,20,6)
    curve={'samples':[{'datetime':t,'minutes_from_event':0}]}
    illumination={'available':True,'direction':'morning','potential_km':15,'target_weight':15,'target_count':1,'caveat':'',
      'times':[{'datetime':t,'minutes_from_event':0,'fraction':1/3,'strict_fraction':1/3,
                'possible_fraction':1/3,'front_face_fraction':1/3,
                'bottom_unresolved_weight_fraction':0,
                'representative_solar_altitude_deg':-2.5,'dominant':None}]}
    x=apply_to_curve(curve,illumination,target_cloud_cover_pct=75,color_fn=lambda _: (200,80,60))
    assert x['samples'][0]['illuminated_target_fraction_pct']==round(100/3,3)
    assert x['samples'][0]['color'] is not None


def test_primary_firecloud_excludes_verified_face_without_bottom():
    cloud=[_cloud(0,15,6,8)]
    face=[{'minute_offset':0,'target_key':[0,15,6,8],'height_band_km':[6,7]}]
    a=projection_zone_illumination(cloud,15,[],face,[0])
    assert list(a['minute_weights'][0].values())==[0.0]
    assert a['face_only_counts'][0]==1
    assert a['bottom_lit_counts'][0]==0



def test_local_primary_mode_is_bottom_only_even_with_verified_faces():
    from datetime import datetime
    from unittest.mock import patch
    from burning_sky.local_cloud_illumination import local_illumination
    profiles=[{'distance_km':float(d),'levels':[{'height_agl_km':8.,'cloud_cover':80.}]} for d in (0,15,30,45)]
    face=[{'minute_offset':0,'target_key':[0.,15.,8.,9.],'height_band_km':[8.1,8.5],
           'solar_altitude_deg':-2.0}]
    with patch('burning_sky.local_cloud_illumination.solar_position',return_value=(-2.,250.)):
        main=local_illumination(profiles,datetime(2026,11,19,17,10),'evening',8,31.8,117.2,15.,
                                verified_target_bands=face,verified_target_bottom_points=[],minutes=0)
        reverse=local_illumination(profiles,datetime(2026,11,19,17,10),'evening',8,31.8,117.2,15.,
                                   verified_target_bands=face,verified_target_bottom_points=[],minutes=0,
                                   include_faces=True)
    assert main['times'][0]['fraction']==0.0
    assert main['times'][0]['face_only_zone_count']==1
    assert reverse['times'][0]['fraction']>0.0
