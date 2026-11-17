"""Case ZIP library: atomic local staging, optional GitHub Contents permanent storage.

GitHub credentials are server-side only (Streamlit secrets or environment).
A save is reported successful only after the archive and its metadata have both
been committed. The public Pro UI enforces a separate admin gate for writes.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

DEFAULT_DIR = Path(__file__).resolve().parents[1] / 'data' / 'case_library'
REQUIRED = {'analysis_state.json', 'request_cache.json'}
MAX_BYTES = 75 * 1024 * 1024
_SETTINGS: dict[str, str] = {}
_KEYS = ('BURNING_SKY_CASE_GITHUB_REPO', 'BURNING_SKY_CASE_GITHUB_TOKEN',
         'BURNING_SKY_CASE_GITHUB_BRANCH', 'BURNING_SKY_CASE_LIBRARY_DIR',
         'CASE_LIBRARY_ADMIN_PASSWORD')


def configure_from_mapping(mapping) -> None:
    """Read supported Streamlit Secrets without copying the token to os.environ."""
    for key in _KEYS:
        try:
            value = mapping.get(key)
        except (OSError, KeyError, AttributeError, RuntimeError):
            continue
        if value is not None and str(value).strip():
            _SETTINGS[key] = str(value).strip()


def _setting(name: str, default: str = '') -> str:
    return _SETTINGS.get(name) or os.environ.get(name, default)


def github_enabled() -> bool:
    return bool(_setting('BURNING_SKY_CASE_GITHUB_REPO') and
                _setting('BURNING_SKY_CASE_GITHUB_TOKEN'))


def admin_password() -> str:
    return _setting('CASE_LIBRARY_ADMIN_PASSWORD')


def authorized(pro_authenticated: bool = False, supplied: str = '') -> bool:
    """Never let a public Pro app write/delete private archives anonymously."""
    if not github_enabled():
        return True  # The local fallback remains explicitly temporary.
    secret = admin_password()
    if secret:
        return hmac.compare_digest(str(supplied), secret) or pro_authenticated
    return bool(pro_authenticated)


def _repo() -> str:
    repo = _setting('BURNING_SKY_CASE_GITHUB_REPO').strip('/')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('GitHub案例库必须配置为 owner/repository')
    return repo


def _branch() -> str:
    return _setting('BURNING_SKY_CASE_GITHUB_BRANCH', 'main')


def _gh_request(method: str, name: str, body=None, *, raw: bool = False, object_meta: bool = False):
    path = urllib.parse.quote('burning_sky_cases/' + name, safe='/')
    url = 'https://api.github.com/repos/' + _repo() + '/contents/' + path
    if method == 'GET':
        url += '?' + urllib.parse.urlencode({'ref': _branch()})
    headers = {
        'Authorization': 'Bearer ' + _setting('BURNING_SKY_CASE_GITHUB_TOKEN'),
        'Accept': ('application/vnd.github.raw+json' if raw else
                   'application/vnd.github.object+json' if object_meta else
                   'application/vnd.github+json'),
        'Content-Type': 'application/json',
        'User-Agent': 'BurningSky-CaseArchive/2.0',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    payload = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=payload, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            data = response.read()
            return data if raw else json.loads(data.decode('utf-8'))
    except urllib.error.HTTPError as ex:
        if ex.code == 404 and method == 'GET':
            return None
        if ex.code in (401, 403):
            msg = '案例库访问被拒绝：请确认令牌权限、仓库和分支'
        elif ex.code == 409:
            msg = '案例库提交冲突，未报告保存成功；可稍后重试'
        elif ex.code == 413:
            msg = '案例超过GitHub接口大小限制，请下载ZIP另行归档'
        else:
            msg = f'GitHub案例库 HTTP {ex.code}，保存或读取未完成'
        raise RuntimeError(msg) from ex
    except (urllib.error.URLError, TimeoutError) as ex:
        raise RuntimeError('案例库网络连接失败，未确认远端保存成功') from ex


def _gh_put(name: str, data: bytes) -> None:
    # Case IDs are random UUIDs; never overwrite an existing archive.
    _gh_request('PUT', name, {
        'message': 'Archive Burning Sky case ' + name,
        'branch': _branch(),
        'content': base64.b64encode(data).decode('ascii'),
    })


def _gh_load(name: str) -> bytes:
    if name.endswith('.zip'):
        # ZIPs may exceed 1 MiB; default JSON contents API cannot read them.
        # Always request the authenticated raw representation first.
        raw = _gh_request('GET', name, raw=True)
        if raw is None:
            raise FileNotFoundError('远端案例不存在')
        if raw[:1] == b'{':
            try:
                obj = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return raw
            if isinstance(obj, dict) and obj.get('encoding') == 'base64':
                return base64.b64decode(obj.get('content', ''))
            if isinstance(obj, dict) and ('encoding' in obj or 'message' in obj):
                raise RuntimeError('GitHub未返回案例ZIP原始内容')
        return raw
    record = _gh_request('GET', name)
    if record is None:
        raise FileNotFoundError('远端案例不存在')
    if isinstance(record, dict) and record.get('encoding') == 'base64' and record.get('content'):
        return base64.b64decode(record['content'])
    raise RuntimeError('GitHub未返回可读取的案例元数据')


def _gh_delete(name: str) -> None:
    found = _gh_request('GET', name, object_meta=name.endswith('.zip'))
    if found is not None:
        _gh_request('DELETE', name, {
            'message': 'Delete Burning Sky case ' + name,
            'branch': _branch(), 'sha': found['sha'],
        })


def library_root() -> Path:
    return Path(_setting('BURNING_SKY_CASE_LIBRARY_DIR') or DEFAULT_DIR).expanduser().resolve()


def validate(data: bytes) -> dict:
    if not data or len(data) > MAX_BYTES:
        raise ValueError('案例ZIP大小必须在0–75 MB之间')
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = zf.infolist()
        if len(members) > 500 or sum(info.file_size for info in members) > 400 * 1024 * 1024:
            raise ValueError('案例ZIP条目或解压体积超过限制')
        names = {p.filename.rsplit('/', 1)[-1] for p in members}
        if not REQUIRED <= names:
            raise ValueError('案例ZIP缺少 analysis_state.json 或 request_cache.json')
        state_path = next((p.filename for p in members if p.filename.endswith('analysis_state.json')), None)
        request_path = next((p.filename for p in members if p.filename.endswith('request_cache.json')), None)
        state = json.loads(zf.read(state_path))
        requests = json.loads(zf.read(request_path))
        if not isinstance(state, dict) or not isinstance(requests, dict) or not requests:
            raise ValueError('案例状态或原始请求缓存无效')
        return state


def save(data: bytes, *, note: str = '') -> dict:
    state = validate(data)
    case_id = uuid.uuid4().hex
    meta = {
        'id': case_id, 'sha256': hashlib.sha256(data).hexdigest(),
        'bytes': len(data), 'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'place': str(state.get('place') or ''), 'date': str(state.get('date') or ''),
        'model': str(state.get('model_code') or state.get('weather_model') or ''),
        'note': str(note or '')[:300], 'schema_version': state.get('schema_version'),
        'storage': 'github' if github_enabled() else 'local_ephemeral',
    }
    metadata = json.dumps(meta, ensure_ascii=False).encode('utf-8')
    if github_enabled():
        # No optimistic "saved" response. Both remote writes must succeed.
        _gh_put(case_id + '.zip', data)
        try:
            _gh_put(case_id + '.json', metadata)
        except Exception:
            # A partial ZIP may remain if rollback itself fails, but is never listed.
            try:
                _gh_delete(case_id + '.zip')
            except Exception:
                pass
            raise
        # Read the metadata back to verify the remote archive is discoverable.
        try:
            confirmation = json.loads(_gh_load(case_id + '.json'))
        except Exception as ex:
            raise RuntimeError('案例已提交但远端校验失败，请检查仓库后再操作') from ex
        if confirmation.get('sha256') != meta['sha256']:
            raise RuntimeError('案例元数据校验不一致')
        return meta
    root = library_root()
    root.mkdir(parents=True, exist_ok=True)
    p_zip = root / (case_id + '.zip')
    p_meta = root / (case_id + '.json')
    tmp_zip = root / (case_id + '.zip.writing')
    tmp_meta = root / (case_id + '.json.writing')
    try:
        tmp_zip.write_bytes(data)
        os.replace(tmp_zip, p_zip)
        tmp_meta.write_bytes(metadata)
        os.replace(tmp_meta, p_meta)
    finally:
        tmp_zip.unlink(missing_ok=True)
        tmp_meta.unlink(missing_ok=True)
    return meta


def list_cases() -> list[dict]:
    if github_enabled():
        entries = _gh_request('GET', '') or []
        if not isinstance(entries, list):
            return []
        out = []
        for entry in entries:
            filename = entry.get('name', '')
            if re.fullmatch(r'[0-9a-f]{32}\.json', filename):
                try:
                    meta = json.loads(_gh_load(filename))
                    if meta.get('id') == filename[:-5]:
                        out.append(meta)
                except (OSError, ValueError, RuntimeError):
                    continue
        return sorted(out, key=lambda m: m.get('created_at', ''), reverse=True)
    root = library_root()
    if not root.exists():
        return []
    out = []
    for path in root.glob('*.json'):
        try:
            meta = json.loads(path.read_text(encoding='utf-8'))
            if re.fullmatch(r'[0-9a-f]{32}', str(meta.get('id'))) and (root/(meta['id']+'.zip')).exists():
                out.append(meta)
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda m: m.get('created_at', ''), reverse=True)


def _case_id(raw: str) -> str:
    if not re.fullmatch(r'[0-9a-f]{32}', str(raw)):
        raise ValueError('非法案例编号')
    return raw


def read(case_id: str) -> bytes:
    token = _case_id(case_id)
    if github_enabled():
        raw = _gh_load(token + '.zip')
        meta = json.loads(_gh_load(token + '.json'))
    else:
        root = library_root()
        raw = (root / (token + '.zip')).read_bytes()
        meta = json.loads((root / (token + '.json')).read_text(encoding='utf-8'))
    if hashlib.sha256(raw).hexdigest() != meta.get('sha256'):
        raise ValueError('案例校验失败：ZIP内容与远端索引不一致')
    return raw


def delete(case_id: str) -> None:
    token = _case_id(case_id)
    if github_enabled():
        # Delete the metadata first so an interrupted deletion cannot advertise
        # an archive whose ZIP has already disappeared.
        _gh_delete(token + '.json')
        _gh_delete(token + '.zip')
        return
    root = library_root()
    for suffix in ('.json', '.zip'):
        (root / (token + suffix)).unlink(missing_ok=True)
