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

_LOCK = threading.RLock()
_STATE = Path(os.environ.get('BURNING_SKY_RATE_STATE', '/tmp/burning_sky_open_meteo_rate.json'))
# Explicit profiles: scheduled restores the previously working task-level pacing;
# manual uses an 8/min rolling window; interactive remains 10/min.
_PROFILE = os.environ.get('BURNING_SKY_REQUEST_PROFILE', 'interactive').lower()
_POLICY = {'manual': (7.6, 8), 'interactive': (4.0, 10)}
_MIN_INTERVAL, _MAX_PER_MINUTE = _POLICY.get(_PROFILE, (0.0, 0))
_COOLDOWN = 20 * 60


def _is_open_meteo(req):
    host = urllib.parse.urlparse(getattr(req, 'full_url', str(req))).hostname or ''
    return host == 'open-meteo.com' or host.endswith('.open-meteo.com')


def _reserve():
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
                    raise RuntimeError('HTTP 429: Open-Meteo 冷却中，本批任务停止而不是等待20分钟')
                wait = 0.0
                if recent:
                    wait = max(wait, recent[-1] + _MIN_INTERVAL - now)
                if len(recent) >= _MAX_PER_MINUTE:
                    wait = max(wait, recent[-_MAX_PER_MINUTE] + 60 - now)
                if wait <= 0:
                    recent.append(now)
                    tmp = _STATE.with_suffix('.tmp')
                    tmp.write_text(json.dumps({'recent': recent, 'cooldown_until': state.get('cooldown_until', 0)}), encoding='utf-8')
                    os.replace(tmp, _STATE)
                    return
                print(f'[Open-Meteo 限速] 等待 {wait:.1f} 秒；最多每分钟 {_MAX_PER_MINUTE} 次真实HTTP请求', flush=True)
                time.sleep(wait + 0.02)
        finally:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN) if fcntl else None


def _mark_429():
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
            state['cooldown_until'] = max(float(state.get('cooldown_until', 0)), time.time() + _COOLDOWN)
            tmp = _STATE.with_suffix('.tmp')
            tmp.write_text(json.dumps(state), encoding='utf-8')
            os.replace(tmp, _STATE)
        finally:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN) if fcntl else None


def limited_urlopen(req, *args, **kwargs):
    if _is_open_meteo(req) and _PROFILE in _POLICY:
        _reserve()
    try:
        return urllib.request.urlopen(req, *args, **kwargs)
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and _is_open_meteo(req):
            _mark_429()
        raise
