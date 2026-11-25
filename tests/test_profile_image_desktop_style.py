from burning_sky.profile_image import render_profile


def test_web_profile_renders_desktop_style_with_separate_bottom_and_face_rays():
    result={
        'place':'合肥','query_model_label':'ECMWF IFS 0.25°','direction':'evening',
        'samples':[
            {'distance_km':0,'cloud_cover_low':0,'cloud_cover_mid':80,'cloud_cover_high':0},
            {'distance_km':15,'cloud_cover_low':0,'cloud_cover_mid':80,'cloud_cover_high':0},
            {'distance_km':30,'cloud_cover_low':0,'cloud_cover_mid':0,'cloud_cover_high':0},
        ],
        'profiles':[
            {'distance_km':0,'estimated_base_km':4.0,'levels':[{'height_agl_km':4.0,'cloud_cover':80}]},
            {'distance_km':15,'estimated_base_km':4.0,'levels':[{'height_agl_km':4.0,'cloud_cover':80}]},
            {'distance_km':30,'levels':[{'height_agl_km':4.0,'cloud_cover':0}]},
        ],
        'inferred':{
            'verified_primary_ray_traces':[
                {'target_face_km':15,'target_height_km':4.0,'solar_altitude_deg':-2.0,
                 'source_check_km':100,'status':'verified_sunward_face_full_sampled_corridor'}],
            'verified_bottom_sample_traces':[
                {'minute_offset':0,'position_km':7.5,'area_width_km':15,'target_key':[0,15,4,5],
                 'status':'cloud_blocked'}],
        },
        'boundaries':{'fire_cloud_range_km':30,'cloud_edge_distance_km':100},
    }
    image=render_profile(result)
    assert image is not None
    assert image.size==(1600,900)
