from datetime import datetime
from burning_sky import spherical_raytrace as s

def test_negative_altitude_does_not_short_circuit(monkeypatch):
    monkeypatch.setattr(s, 'solar_position', lambda *args: (-1.0, 270.0))
    rows=[dict(distance_km=d,cloud_cover_low=0,cloud_cover_mid=0,cloud_cover_high=0) for d in (0,25,50)]
    out=s.trace_at_time(rows,4.0,datetime(2026,11,3,18),8,31,117)
    assert out['status']=='clear_path'
    assert all(r['layer_data_complete'] for r in out['path'])

def test_earth_intersection_negative_altitude(monkeypatch):
    monkeypatch.setattr(s, 'solar_position', lambda *args: (-5.0, 270.0))
    rows=[dict(distance_km=d,cloud_cover_low=0,cloud_cover_mid=0,cloud_cover_high=0) for d in (0,25,100)]
    out=s.trace_at_time(rows,2.0,datetime(2026,11,3,18),8,31,117)
    assert out['status']=='blocked'

def test_equivalent_gap_not_full_reach():
    rows=[]
    for d in (0,25,50,75,100):
        rows.append(dict(distance_km=d,ray_height_km=4.0,layer_data_complete=True,blocked=False,
                         cloud_cover_low=0,cloud_cover_mid=0,cloud_cover_high=0))
    rows[1]['cloud_cover_mid']=80
    scan={'results':[{'status':'clear_path','time':'x','solar_altitude_deg':-1,'path':rows}]}
    gaps=s.equivalent_gaps(scan)
    assert gaps and gaps[0]['start_km']==50 and gaps[0]['end_km']==100
