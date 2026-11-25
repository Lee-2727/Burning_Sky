# Offline regression tests: no weather API or UI required.
from datetime import datetime
import burning_sky.local_cloud_illumination as local
import burning_sky.sky_six as strict

DT=datetime(2026,11,10,17,10)

def field(cover):
    return [{'distance_km':d,'levels':[
        {'height_agl_km':8.,'cloud_cover':cover,'pressure_hpa':350},
        {'height_agl_km':10.,'cloud_cover':cover,'pressure_hpa':270}]}
        for d in range(0,301,25)]

def run():
    # Fix the sun direction for deterministic geometry and unit-test scope.
    sun=lambda *args:(-2.0,250.0)
    local.solar_position=sun
    strict.solar_position=sun
    for cover in (10.,24.,25.,26.,80.):
        profile=field(cover)
        never=local.local_illumination(profile,DT,'evening',8,31.8,117.2,100,
                                       verified_target_bands=[])
        assert never['available'] and never['target_count']>0,cover
        assert all(row['fraction']==0 for row in never['times']),cover
    profile=field(24.)
    result=strict.adaptive_equivalent_gaps(profile,DT,8,31.8,117.2,'evening',150,
                                           target_limit_km=100)
    assert result['status']=='local_targets_verified'
    assert result['verified_target_bands']
    illuminated=local.local_illumination(profile,DT,'evening',8,31.8,117.2,100,
                                         verified_target_bands=result['verified_target_bands'])
    assert illuminated['times'][0]['fraction']>0
    selected=[x for x in result['verified_target_bands'] if x['minute_offset']==0
              and x['target_key']==result['verified_target_bands'][0]['target_key']]
    one=local.local_illumination(profile,DT,'evening',8,31.8,117.2,100,
                                verified_target_bands=selected)
    assert 0<one['times'][0]['fraction']<illuminated['times'][0]['fraction']
    assert all(row['fraction']==0 for row in one['times'][1:]), 'minute-level gate failed'
    print('PASS: target weights remain continuous below/above 25%; no independent ray = zero; individual target and minute stay isolated')

if __name__=='__main__':run()
