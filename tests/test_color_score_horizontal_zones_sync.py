"""Color scoring is independent of cloud-area and uses the 15km zone peak."""
from datetime import datetime, timedelta
from burning_sky.algorithm import (
    calculate_color_performance_score, color_score_grade,
    horizontal_zone_color_peak,
)


def test_five_grade_boundaries():
    for n, label in [(0,'平平'),(29,'平平'),(30,'普通'),(49,'普通'),
                     (50,'绚烂'),(69,'绚烂'),(70,'史诗'),(89,'史诗'),
                     (90,'世纪'),(100,'世纪')]:
        assert color_score_grade(n) == label


def test_same_atmosphere_cloud_amount_and_gap_count_do_not_change_color_score():
    fixed = {'visibility_m':25000, 'aod':0.20}
    sparse = {**fixed,'low':0,'mid':8,'high':3,'total':11,
              'clear_gap_count':6,'largest_gap_size_km':200,
              'gap_fraction_of_reach':0.8,'low_cloud_color_factor':1.0}
    cloudy = {**fixed,'low':95,'mid':98,'high':100,'total':100,
              'clear_gap_count':0,'largest_gap_size_km':0,
              'gap_fraction_of_reach':0,'low_cloud_color_factor':0.0}
    a=calculate_color_performance_score(sparse)
    b=calculate_color_performance_score(cloudy)
    assert a['color_score']==b['color_score']
    assert a['color_score_label']==b['color_score_label']


def test_color_score_decreases_with_aod():
    low=calculate_color_performance_score({'aod':0.15,'visibility_m':25000})['color_score']
    high=calculate_color_performance_score({'aod':0.65,'visibility_m':25000})['color_score']
    assert low > high


def test_peak_uses_zone_fraction_not_legacy_cloud_winner():
    dt=datetime(2026,11,20,6,40)
    illumination={'available':True,'times':[
        {'datetime':dt,'fraction':0.2,'representative_solar_altitude_deg':-1},
        {'datetime':dt+timedelta(minutes=1),'fraction':0.7,'representative_solar_altitude_deg':-2},
        {'datetime':dt+timedelta(minutes=2),'fraction':0.1,'representative_solar_altitude_deg':-3},
    ]}
    assert horizontal_zone_color_peak(illumination)['datetime']==dt+timedelta(minutes=1)
    assert horizontal_zone_color_peak({'available':True,'times':[{'fraction':0}]}) is None
