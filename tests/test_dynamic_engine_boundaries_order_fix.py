"""Regression: both real forecast entry paths must define cloud boundaries before dynamic sourcing.

No network, Tk window or stored weather data is needed. The forecast is driven
through its actual data and geometry setup up to the horizon planner.
"""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import engine


class ReachedSourcePlanner(Exception):
    pass


class Value:
    def __init__(self, value): self.value = value
    def get(self): return self.value


class FakeApp:
    def __init__(self, event_utc):
        self.event_utc = event_utc
        self.vars = {
            'weather_model': Value('GFS Global'),
            'cloud_base_mode': Value('手动指定'),
            'aod_source': Value('手动输入'),
            'morning_cloud_base_km': Value('8.5'),
            'evening_cloud_base_km': Value('8.5'),
            'morning_aod': Value('0.1'),
            'evening_aod': Value('0.1'),
        }
        self.skip_rainbow_for_overview = True
        self.audited_steps = []

    def _read_inputs(self):
        return {'date': date(2026, 11, 20), 'utc_offset': 8,
                'lat': 31.8047, 'lon': 117.2214, 'elevation_m': 30.}

    def _set_main_progress(self, pct, message):
        self.audited_steps.append(message)

    def _fetch_cloud_base_profiles(self, coords, distances, event_utc, model_id,
                                   local_ground_elevation_m=None):
        return {'samples': [
            {'distance_km': d, 'lat': lat, 'lon': lon, 'estimated_base_km': 8.0,
             'levels': [
                 {'height_agl_km': 7.0, 'cloud_cover': 0.0, 'pressure_hpa': 400},
                 {'height_agl_km': 8.0, 'cloud_cover': 80.0 if d <= 250 else 0.0, 'pressure_hpa': 350},
                 {'height_agl_km': 9.0, 'cloud_cover': 0.0, 'pressure_hpa': 300},
             ]} for (lat, lon), d in zip(coords, distances)
        ]}

    def _fetch_selected_aod_path(self, coords, distances, event_utc, source):
        return {'samples': [{'distance_km': d, 'aod': 0.1} for d in distances],
                'weighted_aod': 0.1, 'valid_time': None}

    def _fetch_weather_locations(self, coords, model_id):
        fields = {
            'cloud_cover': 70.0, 'cloud_cover_low': 0.0,
            'cloud_cover_mid': 10.0, 'cloud_cover_high': 65.0,
            'visibility': 30000.0, 'wind_speed_10m': 3.0,
            'precipitation_probability': 0.0, 'precipitation': 0.0,
            'wind_gusts_10m': 5.0, 'wind_direction_10m': 100.0,
        }
        return [{'hourly': {'time': [self.event_utc.isoformat()],
                            **{key: [value] for key, value in fields.items()}}}
                for _ in coords]


@pytest.mark.parametrize('direction,local_event', [
    ('morning', datetime(2026, 11, 20, 6, 30)),
    ('evening', datetime(2026, 11, 20, 17, 30)),
])
def test_forecast_entry_has_boundaries_before_dynamic_plan(monkeypatch, direction, local_event):
    fake = FakeApp(local_event.replace(hour=local_event.hour - 8)
                   if local_event.hour >= 8 else datetime(2026, 11, 19, local_event.hour + 16, local_event.minute))
    payload = {}

    def planner(profiles, event_dt, utc_offset, lat, lon, solar_direction,
                reach_km, target_limit_km=None, **kwargs):
        payload.update(direction=solar_direction, target_limit_km=target_limit_km,
                       count=len(profiles), reach=reach_km)
        raise ReachedSourcePlanner

    monkeypatch.setattr(engine, '_get_model_metadata_signature', lambda model: None)
    monkeypatch.setattr(engine, 'find_sunrise', lambda *args: (local_event, 90.))
    monkeypatch.setattr(engine, 'find_sunset', lambda *args: (local_event, 270.))
    monkeypatch.setattr(engine, 'analyze_cloud_base_geometry', lambda samples, *args, **kwargs: {
        'rows': [{'distance_km': 0, 'reach_km': 500}], 'farthest': None})
    monkeypatch.setattr(engine, 'apply_high_elevation_nearby_low_cloud_filter',
                        lambda *args, **kwargs: {'applied': False})
    monkeypatch.setattr(engine, 'infer_from_cloud_path', lambda *args, **kwargs: {})
    with patch('burning_sky.sky_six.dynamic_solar_source_horizon', side_effect=planner), \
         patch('burning_sky.spatial_pipeline.calibrate_path_samples', return_value={'status': 'mock'}), \
         patch('burning_sky.cloud_height_reconstruction.reconstruct', return_value={'counts': {}}):
        with pytest.raises(ReachedSourcePlanner):
            engine.FireCloudApp._fetch_weather_task(fake, direction,
                                                   raise_errors=True, notify_success=False)
    assert payload['direction'] == direction
    assert payload['count'] > 6
    # This came from the forecast's actual sampled cloud field, NOT an unbound local.
    assert payload['target_limit_km'] is not None
    assert payload['reach'] == 500
