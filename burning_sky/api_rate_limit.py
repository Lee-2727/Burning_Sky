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
_POLICY = {'manual': (7.6, 8), 'interactive': (1.6, 35), 'scheduled': (12.0, 5),
           'satellite_advanced': (15.5, 4)}
# Context-local profile prevents a Streamlit advanced page from slowing down other
# visitors or other application pages imported in the same Python process.
_ACTIVE_PROFILE = contextvars.ContextVar('burning_sky_http_profile', default=None)
_COOLDOWN_MIN = 100  # manual/scheduled workflows retain their conservative 429 pause
_COOLDOWN_MAX = 60 * 60  # never accept an unbounded Retry-After (e.g. 9 hours)
_SATELLITE_COOLDOWN = 20 * 60  # dedicated satellite-calibration pause


def set_request_profile(profile):
    if profile not in _POLICY:
        raise ValueError('unknown request profile')
    return _ACTIVE_PROFILE.set(profile)


def reset_request_profile(token):
    _ACTIVE_PROFILE.reset(token)


def _profile():
    return _ACTIVE_PROFILE.get() or _PROFILE


def _read_state():
    try:
        state=json.loads(_STATE.read_text(encoding='utf-8'))
        return state if isinstance(state,dict) else {}
    except (OSError,ValueError,TypeError):
        return {}


def cooldown_remaining(profile=None):
    """Return workflow cooldown; foreground single-mode queries are never locked out."""
    profile=profile or _profile()
    # A provider Retry-After may be extremely long. Do not turn one foreground
    # 429 into a profile-wide lockout for desktop/web single-mode queries.
    # Normal per-request pacing in _reserve remains active.
    if profile == 'interactive':
        return 0
    state=_read_state()
    cooldowns=state.get('cooldowns') or {}
    try:
        until=float(cooldowns.get(profile,0) or 0)
    except (TypeError,ValueError):
        until=0.0
    now=time.time()
    # UI countdowns obey the same hard ceiling as request admission.
    until=min(until,now+_COOLDOWN_MAX)
    return max(0,int(until-now+0.999))


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
                state=_read_state()
                now=time.time()
                recent=[float(t) for t in state.get('recent', [])
                        if now-60<float(t)<=now]
                raw_cooldowns=state.get('cooldowns') or {}
                # Cooldowns are profile-scoped. Foreground interactive entries
                # are intentionally purged; only manual/scheduled/satellite
                # workflows may be blocked by their own cooldown state.
                cooldowns={}
                for key,value in raw_cooldowns.items():
                    # Ignore and purge stale interactive cooldowns written by
                    # older releases (including long Retry-After values).
                    if str(key) == 'interactive':
                        continue
                    try:
                        until=float(value)
                    except (TypeError,ValueError):
                        continue
                    if str(key) in _POLICY and until>now:
                        cooldowns[str(key)]=min(until,now+_COOLDOWN_MAX)
                until=float(cooldowns.get(profile,0) or 0)
                if until>now:
                    raise RuntimeError(
                        f'HTTP 429: Open-Meteo 当前{profile}任务冷却中，约剩余 '
                        f'{int(until-now+0.999)} 秒；本次不发起新请求')
                wait=0.0
                if recent:
                    wait=max(wait,recent[-1]+interval-now)
                if len(recent)>=cap:
                    wait=max(wait,recent[-cap]+60-now)
                if wait<=0:
                    recent.append(now)
                    tmp=_STATE.with_suffix('.tmp')
                    tmp.write_text(json.dumps({'recent':recent,'cooldowns':cooldowns,
                        'last_429_by_profile':state.get('last_429_by_profile',{}),
                        '429_strikes_by_profile':state.get('429_strikes_by_profile',{})}),
                        encoding='utf-8')
                    os.replace(tmp,_STATE)
                    return
                print(f'[Open-Meteo 限速] 等待 {wait:.1f} 秒；档位 {profile}；'
                      f'最多每分钟 {cap} 次真实HTTP请求',flush=True)
                time.sleep(wait+0.02)
        finally:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN) if fcntl else None


def _retry_after_seconds(value, now):
    if value is None:
        return 0.0
    try:
        return max(0.0,float(value))
    except (TypeError,ValueError):
        pass
    # HTTP Retry-After may also be an HTTP date rather than a number.
    try:
        from email.utils import parsedate_to_datetime
        dt=parsedate_to_datetime(str(value))
        return max(0.0,dt.timestamp()-now)
    except Exception:
        return 0.0


def _mark_429(retry_after=None, profile=None):
    profile=profile or _profile()
    try:
        import fcntl
    except ImportError:
        fcntl = None
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, open(_STATE.with_suffix('.lock'), 'a+b') as lockfile:
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX) if fcntl else None
        try:
            state=_read_state()
            now=time.time()
            last_by=state.get('last_429_by_profile') or {}
            strikes_by=state.get('429_strikes_by_profile') or {}
            if not isinstance(last_by,dict): last_by={}
            if not isinstance(strikes_by,dict): strikes_by={}
            last=float(last_by.get(profile,0) or 0)
            strikes=(int(strikes_by.get(profile,0))+1) if now-last<30*60 else 1
            last_by[profile]=now
            strikes_by[profile]=strikes
            requested=_retry_after_seconds(retry_after,now)
            cooldowns=state.get('cooldowns') or {}
            if not isinstance(cooldowns,dict): cooldowns={}
            if profile == 'interactive':
                # Foreground single-mode queries must not inherit a long
                # server Retry-After lockout. Keep the rolling request budget
                # and minimum inter-request interval as the safety controls.
                cooldowns.pop(profile,None)
            else:
                # Satellite keeps its dedicated 20-minute stop. Manual and
                # scheduled overview jobs retain their conservative backoff.
                minimum=_SATELLITE_COOLDOWN if profile=='satellite_advanced' else _COOLDOWN_MIN
                delay=min(_COOLDOWN_MAX,max(minimum,requested))
                try: previous=float(cooldowns.get(profile,0) or 0)
                except (TypeError,ValueError): previous=0.0
                previous=min(previous,now+_COOLDOWN_MAX)
                cooldowns[profile]=max(previous,now+delay)
            tmp=_STATE.with_suffix('.tmp')
            tmp.write_text(json.dumps({'recent':[float(t) for t in state.get('recent',[])],
                'cooldowns':cooldowns,'last_429_by_profile':last_by,
                '429_strikes_by_profile':strikes_by}),encoding='utf-8')
            os.replace(tmp,_STATE)
        finally:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN) if fcntl else None


def limited_urlopen(req, *args, **kwargs):
    if _is_open_meteo(req) and _profile() in _POLICY:
        _reserve(_profile())
    try:
        return urllib.request.urlopen(req, *args, **kwargs)
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and _is_open_meteo(req):
            _mark_429(exc.headers.get("Retry-After") if exc.headers else None, profile=_profile())
        raise
