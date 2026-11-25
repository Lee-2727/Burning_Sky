"""Every minute/region is retained; a single representative ray may not replace the sweep."""
from datetime import datetime
from unittest.mock import patch
from burning_sky.sky_six import adaptive_equivalent_gaps
from burning_sky.local_cloud_illumination import local_illumination

DT=datetime(2026,11,20,6,40)

def _profiles():
    distances=(0,25,50,75,*range(100,751,50))
    return [{'distance_km':float(d),'levels':[
      {'height_agl_km':8.,'cloud_cover':90. if d<=50 else 0.,'pressure_hpa':350},
      {'height_agl_km':10.,'cloud_cover':0.,'pressure_hpa':250}]}
      for d in distances]

def test_every_minute_and_all_bottom_origins_are_retained():
    profiles=_profiles()
    with patch('burning_sky.sky_six.solar_position',return_value=(-.8,100.)):
        result=adaptive_equivalent_gaps(profiles,DT,8,0,0,'morning',50.,
                    source_scan_km=700.,use_horizontal_fusion=False)
    bottom=result['verified_target_bottom_points']
    for minute in range(-30,1):
        rows=[p for p in bottom if p['minute_offset']==minute]
        assert len(rows)>=4, (minute,rows)
        assert abs(sum(p['area_width_km'] for p in rows)-50.)<1e-5
        # A blocked minute is recorded as a normal output, not skipped.
        assert all(p.get('status') for p in rows)
    expected={(m,round(p['position_km'],4)) for m in range(-30,1)
              for p in bottom if p['minute_offset']==m and p['status']=='verified_bottom'}
    traces={(t['minute_offset'],round(t['target_bottom_position_km'],4))
            for t in result['verified_ray_traces'] if t.get('surface')=='cloud_bottom'}
    assert traces==expected, (len(traces),len(expected),expected-traces)
    with patch('burning_sky.local_cloud_illumination.solar_position',return_value=(-.8,100.)):
        light=local_illumination(profiles,DT,'morning',8,0,0,50.,
             verified_target_bands=result['verified_target_bands'],
             verified_target_bottom_points=bottom)
    assert len(light['times'])==31
