"""No actual GitHub/network traffic in these regression checks."""
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock
import pytest

from burning_sky.overview_snapshot_guard import expected_dates, count_valid
from burning_sky.overview_status_ui import status_view
from burning_sky.overview_actions_control import ActionsOverview, WORKFLOW
from burning_sky.overview_visual import render_direction
from lite.bar_cards import render as render_lite

MODELS = ('ECMWF IFS 0.25°', 'GFS Global')


def test_three_day_is_exactly_twelve_tasks():
    dates = expected_dates(date(2026, 11, 18))
    assert len(dates) == 3
    snap = {'days': [{'date': x, 'morning': {m: {'probability':0,'color_score':0,'burn_scale':0} for m in MODELS},
                      'evening': {m: {'probability':0,'color_score':0,'burn_scale':0} for m in MODELS}}
                     for x in dates]}
    assert count_valid(snap, dates) == 12
    assert status_view({'state':'completed', 'task_total':12}, snap,
                       now=datetime(2026,11,18, tzinfo=timezone(timedelta(hours=8))))[0] == 'success'
    assert render_direction(snap['days'], 'morning', '朝霞').count('11/20') == 3
    assert render_lite(snap['days'], 'morning', '朝霞').count('bs-lite-daylabel') == 3


def test_actions_dispatch_uses_exact_workflow_and_manual_mode_only():
    requests = []
    def mock_request(method, url, **kw):
        requests.append((method, url, kw))
        resp = Mock(ok=True, status_code=204, content=b'')
        if method == 'GET':
            resp.content = b'{"workflow_runs": []}'
            resp.json.return_value = {'workflow_runs': []}
        return resp
    actions = ActionsOverview('Lee-2727/Burning_Sky', 'fake-token', request=mock_request)
    actions.dispatch('fresh')
    assert requests[-1][0] == 'POST'
    assert requests[-1][1].endswith('/actions/workflows/update-overview.yml/dispatches')
    assert requests[-1][2]['json']['inputs'] == {'run_mode':'fresh'}
    assert 'fake-token' not in requests[-1][1]
    assert WORKFLOW == 'update-overview.yml'


def test_actions_never_dispatches_while_active():
    calls = []
    def mock_request(method, url, **kw):
        calls.append((method,url))
        response = Mock(ok=True, content=b'x')
        response.json.return_value = {'workflow_runs':[{'id':83,'status':'queued'}]}
        return response
    api = ActionsOverview('Lee-2727/Burning_Sky','fake',request=mock_request)
    with pytest.raises(RuntimeError, match='阻止重复启动'):
        api.dispatch('resume')
    assert all(method == 'GET' for method, _ in calls)


def test_actions_cancel_only_runs_returned_by_scheduled_workflow():
    post = []
    def mock_request(method, url, **kw):
        ret = Mock(ok=True, content=b'')
        if method == 'GET':
            ret.content = b'x'
            ret.json.return_value = {'workflow_runs':[
                {'id': 42,'status':'in_progress'}, {'id':43,'status':'completed'}]}
        else:
            post.append(url)
        return ret
    api = ActionsOverview('Lee-2727/Burning_Sky','fake',request=mock_request)
    assert api.cancel([42,43,900]) == [42]
    assert post == ['https://api.github.com/repos/Lee-2727/Burning_Sky/actions/runs/42/cancel']


def test_invalid_dispatch_modes_never_hit_network():
    api = ActionsOverview('Lee-2727/Burning_Sky','fake',request=lambda *a,**k: pytest.fail('Unexpected request'))
    with pytest.raises(ValueError): api.dispatch('anything')


def test_three_day_workflow_enabled_and_preserves_cron():
    root = Path(__file__).resolve().parents[1]
    txt = (root/'.github/workflows/update-overview.yml').read_text(encoding='utf8')
    assert "0 1,6,19 * * *" in txt
    assert 'workflow_dispatch' in txt
    assert "BURNING_SKY_OVERVIEW_RUN_MODE" in txt
    script = (root/'scripts/update_overview.py').read_text(encoding='utf8')
    assert 'range(3)' in script
    assert 'prepare_publication(' in script


def test_private_pro_scheduled_controls_do_not_require_second_password():
    root = Path(__file__).resolve().parents[1]
    code = (root/'burning_sky/overview_actions_control.py').read_text(encoding='utf8')
    app = (root/'app.py').read_text(encoding='utf8')
    assert '_authorized(' not in code
    assert 'OVERVIEW_ADMIN_PASSWORD' not in code
    assert 'BURNING_SKY_GITHUB_TOKEN' in code
    assert "api.dispatch('fresh')" in code
    assert '_render_scheduled_actions(st, _APP_DIR)' in app

def test_hourly_quota_short_circuits_remaining_tasks():
    root = Path(__file__).resolve().parents[1]
    code = (root/'scripts/update_overview.py').read_text(encoding='utf8')
    assert 'if limit_hit:' in code
    assert '[quota deferred]' in code
    assert code.index('if limit_hit:') < code.index('time.sleep(REQUEST_INTERVAL_SECONDS)')
