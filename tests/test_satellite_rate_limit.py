import json
from burning_sky import api_rate_limit as rate


def test_satellite_advanced_profile_is_slow():
    assert rate._POLICY['satellite_advanced'] == (15.5, 4)


def test_any_satellite_429_starts_twenty_minute_global_cooldown(tmp_path, monkeypatch):
    state_path = tmp_path / 'rate.json'
    monkeypatch.setattr(rate, '_STATE', state_path)
    monkeypatch.setattr(rate.time, 'time', lambda: 100000.0)
    rate._mark_429(None)
    state = json.loads(state_path.read_text('utf-8'))
    assert state['cooldown_until'] == 100000.0 + 20 * 60
