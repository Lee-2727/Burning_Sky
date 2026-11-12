"""Shared Open-Meteo request pacing; applied at the actual HTTP send boundary.

Only outbound HTTP attempts consume tokens; cached/replayed responses do not.
Works across threads and processes on the same filesystem (Linux GitHub Actions).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import contextvars

_LOCK = threading.RLock()
_STATE = Path(os.environ.get('BURNING_SKY_RATE_STATE', '/tmp/burning_sky_open_meteo_rate.json'))
# All interactive and dashboard calls use global pacing at the HTTP boundary.
# Manual overview stays on its more conservative existing budget.
_PROFILE = os.environ.get('BURNING_SKY_REQUEST_PROFILE', 'interactive').lower()
_POLICY = {'manual': (7.6, 8), 'interactive': (1.6, 35), 'scheduled': (1.6, 35),
           'satellite_advanced': (8.0, 7)}
# Context-local profile prevents a Streamlit advanced page from slowing down other
# visitors or other application pages imported in the same Python process.
_ACTIVE_PROFILE = contextvars.ContextVar('burning_sky_http_profile', default=None)
_COOLDOWN_MIN = 100  # protect against minute-limit 429; the API may say one minute
_COOLDOWN_MAX = 20 * 60


def set_request_profile(profile):
    if profile not in _POLICY:
        raise ValueError('unknown request profile')
    return _ACTIVE_PROFILE.set(profile)


def reset_request_profile(token):
    _ACTIVE_PROFILE.reset(token)


def _profile():
    return _ACTIVE_PROFILE.get() or _PROFILE


def cooldown_remaining():
    try:
        state = json.loads(_STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 0
    return max(0, int(float(state.get('cooldown_until', 0)) - time.time() + 0.999))



def _is_open_meteo(req):
    host = urllib.parse.urlparse(getattr(req, 'full_url', str(req))).hostname or ''
    return host == 'open-meteo.com' or host.endswith('.open-meteo.com')


def _reserve(profile=None):
    profile=profile or _profile()
    interval, cap = _POLICY.get(profile, (1.6,35))
    try:
        import fcntl
    except ImportError:
        fcntl = None  # Windows desktop: in-process thread lock still applies
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _STATE.with_suffix('.lock')
    with _LOCK, open(lock_path, 'a+b') as lockfile:
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX) if fcntl else None
        try:
            while True:
                try:
                    state = json.loads(_STATE.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    state = {}
                now = time.time()
                recent = [float(t) for t in state.get('recent', []) if now - 60 < float(t) <= now]
                if float(state.get('cooldown_until', 0)) > now:
                    raise RuntimeError(f'HTTP 429: Open-Meteo 限速冷却中，还需约 {int(float(state.get("cooldown_until",0))-now+0.999)} 秒；本次不发起新请求')
                wait = 0.0
                if recent:
                    wait = max(wait, recent[-1] + interval - now)
                if len(recent) >= cap:
                    wait = max(wait, recent[-cap] + 60 - now)
                if wait <= 0:
                    recent.append(now)
                    tmp = _STATE.with_suffix('.tmp')
                    tmp.write_text(json.dumps({'recent': recent, 'cooldown_until': state.get('cooldown_until', 0)}), encoding='utf-8')
                    os.replace(tmp, _STATE)
                    return
                print(f'[Open-Meteo 限速] 等待 {wait:.1f} 秒；档位 {profile}；最多每分钟 {cap} 次真实HTTP请求', flush=True)
                time.sleep(wait + 0.02)
        finally:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN) if fcntl else None


def _mark_429(retry_after=None):
    try:
        import fcntl
    except ImportError:
        fcntl = None  # Windows desktop: in-process thread lock still applies
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, open(_STATE.with_suffix('.lock'), 'a+b') as lockfile:
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX) if fcntl else None
        try:
            try:
                state = json.loads(_STATE.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                state = {}
            # Explicit server Retry-After takes precedence, otherwise first minute-limit
            # response uses a short conservative window. Repeated 429s back off.
            now=time.time()
            last=float(state.get('last_429',0))
            strikes=(int(state.get('429_strikes',0))+1) if now-last < 30*60 else 1
            state['last_429']=now
            state['429_strikes']=strikes
            try: requested=float(retry_after)
            except (ValueError,TypeError): requested=0
            delay=min(_COOLDOWN_MAX,max(_COOLDOWN_MIN,requested,100*(2**min(strikes-1,4))))
            state['cooldown_until']=max(float(state.get('cooldown_until',0)),now+delay)
            tmp = _STATE.with_suffix('.tmp')
            tmp.write_text(json.dumps(state), encoding='utf-8')
            os.replace(tmp, _STATE)
        finally:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN) if fcntl else None


def limited_urlopen(req, *args, **kwargs):
    if _is_open_meteo(req) and _profile() in _POLICY:
        _reserve(_profile())
    try:
        return urllib.request.urlopen(req, *args, **kwargs)
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and _is_open_meteo(req):
            _mark_429(exc.headers.get("Retry-After") if exc.headers else None)
        raise
