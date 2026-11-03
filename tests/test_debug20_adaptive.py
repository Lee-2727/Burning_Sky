import json, zipfile, datetime
from burning_sky.sky_six import adaptive_equivalent_gaps,grid_points

def test_no_profiles_conservative():
    r=adaptive_equivalent_gaps([],datetime.datetime(2026,11,3,6),8,31.2,121.5,'morning',200)
    assert r['gaps']==[] and r['status']=='unresolved_data'

def test_side_sample_count():
    pts=[p for p in grid_points(31.2,121.5,110,150) if p['bearing_offset']!=0]
    assert len(pts)==6
