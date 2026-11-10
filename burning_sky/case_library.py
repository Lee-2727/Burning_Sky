"""Portable case ZIP library (format-preserving, atomic disk storage).

Configure BURNING_SKY_CASE_LIBRARY_DIR to a PERSISTENT mounted volume in cloud.
An ephemeral Streamlit server filesystem is never permanent storage.
Archive contents are not mutated; older versions remain available for exact review.
"""
from __future__ import annotations

import hashlib
import base64
import urllib.error
import urllib.parse
import urllib.request
import io
import json
import os
from pathlib import Path
import re
import time
import uuid
import zipfile

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "data" / "case_library"
REQUIRED = {"analysis_state.json", "request_cache.json"}
MAX_BYTES = 75 * 1024 * 1024


def github_enabled() -> bool:
    return bool(os.environ.get('BURNING_SKY_CASE_GITHUB_REPO') and
                os.environ.get('BURNING_SKY_CASE_GITHUB_TOKEN'))


def _gh_request(method, path, body=None):
    repo = os.environ['BURNING_SKY_CASE_GITHUB_REPO'].strip('/')
    # owner/repo strictly; never accept arbitrary server URLs.
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('BURNING_SKY_CASE_GITHUB_REPO 必须为 owner/repo')
    branch = os.environ.get('BURNING_SKY_CASE_GITHUB_BRANCH', 'main')
    url = 'https://api.github.com/repos/' + repo + '/contents/' + path
    if method == 'GET':
        url += '?' + urllib.parse.urlencode({'ref': branch})
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': 'Bearer ' + os.environ['BURNING_SKY_CASE_GITHUB_TOKEN'],
        'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json',
        'User-Agent': 'BurningSky-CaseLibrary/1.0',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and method == 'GET':
            return None
        raise RuntimeError(f'GitHub案例库请求失败 HTTP {exc.code}；请检查私有仓库权限、分支和配额') from exc


def _gh_folder():
    return 'burning_sky_cases'


def _gh_put(name, raw_bytes):
    branch = os.environ.get('BURNING_SKY_CASE_GITHUB_BRANCH', 'main')
    return _gh_request('PUT', _gh_folder() + '/' + name, {
        'message': 'Archive Burning Sky case ' + name,
        'branch': branch,
        'content': base64.b64encode(raw_bytes).decode('ascii'),
    })


def _gh_load(name):
    data = _gh_request('GET', _gh_folder() + '/' + name)
    if not data:
        raise FileNotFoundError('GitHub存档中未找到该案例')
    if data.get('encoding') != 'base64':
        raise ValueError('GitHub响应缺少base64内容；请检查文件大小限制')
    return base64.b64decode(data['content'])


def _gh_delete(name):
    path = _gh_folder() + '/' + name
    found = _gh_request('GET', path)
    if found:
        _gh_request('DELETE', path, {'message': 'Delete Burning Sky case ' + name,
            'branch': os.environ.get('BURNING_SKY_CASE_GITHUB_BRANCH', 'main'),
            'sha': found['sha']})


def library_root() -> Path:
    return Path(os.environ.get("BURNING_SKY_CASE_LIBRARY_DIR") or DEFAULT_DIR).expanduser().resolve()


def validate(data: bytes) -> dict:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("案例ZIP大小必须在0–75 MB之间")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = {n.rsplit('/', 1)[-1] for n in zf.namelist()}
        if not REQUIRED <= names:
            raise ValueError("案例必须包含 analysis_state.json 和 request_cache.json")
        if len(zf.namelist()) > 500:
            raise ValueError("ZIP条目异常过多")
        if sum(i.file_size for i in zf.infolist()) > 400 * 1024 * 1024:
            raise ValueError("ZIP解压后体积超出限制")
        state_name = next((n for n in zf.namelist() if n.endswith('analysis_state.json')), None)
        state = json.loads(zf.read(state_name).decode('utf-8'))
        if not isinstance(state, dict):
            raise ValueError("案例状态格式错误")
        request_name = next((n for n in zf.namelist() if n.endswith('request_cache.json')), None)
        request = json.loads(zf.read(request_name).decode('utf-8'))
        if not isinstance(request, dict) or not request:
            raise ValueError("原始请求缓存为空，不能保证离线重算；请先完整查询")
        return state


def save(data: bytes, *, note: str = "") -> dict:
    state = validate(data)
    root = library_root()
    if not github_enabled():
        root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    sha = hashlib.sha256(data).hexdigest()
    meta = {
        "id": token, "sha256": sha, "bytes": len(data), "created_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        "place": str(state.get('place') or ''), "date": str(state.get('date') or ''),
        "model": str(state.get('model_code') or state.get('weather_model') or ''),
        "note": str(note or '')[:300], "schema_version": state.get('schema_version'),
    }
    if github_enabled():
        # Use a private repo and store PAT only as a server-side Streamlit secret.
        _gh_put(token + '.zip', data)
        try:
            _gh_put(token + '.json', json.dumps(meta, ensure_ascii=False).encode('utf-8'))
        except Exception:
            try:
                _gh_delete(token + '.zip')
            except Exception:
                pass
            raise
        return meta
    path = root / (token + '.zip')
    tmp = root / (token + '.writing')
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
        mpath = root / (token + '.json')
        mtmp = root / (token + '.json.writing')
        mtmp.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        os.replace(mtmp, mpath)
    finally:
        tmp.unlink(missing_ok=True)
    return meta


def list_cases() -> list[dict]:
    if github_enabled():
        entries = _gh_request('GET', _gh_folder()) or []
        if not isinstance(entries, list):
            return []
        cases = []
        for rec in entries:
            name = rec.get('name', '')
            if re.fullmatch(r'[a-f0-9]{32}\.json', name):
                try:
                    cases.append(json.loads(_gh_load(name)))
                except (ValueError, OSError, RuntimeError):
                    continue
        return sorted(cases, key=lambda m: m.get('created_at',''), reverse=True)
    root = library_root()
    if not root.exists():
        return []
    items = []
    for p in root.glob('*.json'):
        try:
            value = json.loads(p.read_text('utf-8'))
            if (root / (value['id'] + '.zip')).exists():
                items.append(value)
        except (OSError, ValueError, KeyError):
            continue
    return sorted(items, key=lambda m: m.get('created_at', ''), reverse=True)


def _safe_id(case_id: str) -> str:
    if not re.fullmatch('[a-f0-9]{32}', str(case_id)):
        raise ValueError('非法案例ID')
    return case_id


def read(case_id: str) -> bytes:
    token = _safe_id(case_id)
    root = library_root()
    if github_enabled():
        data = _gh_load(token + '.zip')
        meta = json.loads(_gh_load(token + '.json'))
    else:
        data = (root / (token + '.zip')).read_bytes()
        meta = json.loads((root / (token + '.json')).read_text('utf-8'))
    if hashlib.sha256(data).hexdigest() != meta.get('sha256'):
        raise ValueError('案例校验失败，ZIP可能已损坏')
    return data


def delete(case_id: str) -> None:
    token = _safe_id(case_id)
    if github_enabled():
        _gh_delete(token + '.zip')
        _gh_delete(token + '.json')
        return
    root = library_root()
    # File deletion is explicit and restricted to a generated UUID filename.
    for suffix in ('.json', '.zip'):
        (root / (token + suffix)).unlink(missing_ok=True)
