"""Case reproducibility metadata, with byte-level integrity checks.
Old cases remain readable; missing historical conditions are explicitly unknown.
"""
import datetime, hashlib, json, os, subprocess
from pathlib import Path

def _digest(blob):
    return hashlib.sha256(blob).hexdigest()

def _revision():
    try:
        return subprocess.check_output(['git','rev-parse','HEAD'], stderr=subprocess.DEVNULL, timeout=2).decode().strip()
    except (OSError, subprocess.SubprocessError):
        return None

def _manifest(state, parameters, checksums):
    return {
        'schema_version':3,
        'saved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'application':'Burning Sky',
        'app_version':state.get('app_version'),
        'git_revision':_revision(),
        'source_case':{'place':state.get('place'),'date':state.get('date'),
                       'model_code':state.get('model_code'),'latitude':state.get('latitude'),
                       'longitude':state.get('longitude'),'elevation_m':state.get('elevation_m')},
        'effective_parameters':parameters,
        'algorithm_contract':{'primary_spherical_path_required':True,
                              'local_cloud':'continuous_face_strict_no_partial_transmission',
                              'curve_y_axis':'relative_brightness',
                              'burn_scale':'tintable_ratio_times_local_mid_high_cover'},
        'file_sha256':checksums,
        'limitations':['旧版未记录的运行时默认值无法追溯',
                       '复现旧算法输出需要保存对应源代码或Git提交',
                       '当前清单仅证明保存文件完整性，不证明历史API响应已覆盖所有请求'],
    }

def write_manifest_zip(zf,state,effective_parameters):
    checksums={}
    for name in zf.namelist():
        if not name.endswith('/') and name!='case_manifest.json':
            checksums[name]=_digest(zf.read(name))
    zf.writestr('case_manifest.json',json.dumps(_manifest(state,effective_parameters,checksums),ensure_ascii=False,indent=2))

def write_manifest_folder(folder,state,request_cache,effective_parameters):
    folder=Path(folder)
    checksums={}
    for name in ('analysis_state.json','request_cache.json','weather_raw.json'):
        path=folder/name
        if path.is_file(): checksums[name]=_digest(path.read_bytes())
    (folder/'case_manifest.json').write_text(json.dumps(_manifest(state,effective_parameters,checksums),ensure_ascii=False,indent=2),encoding='utf-8')

def check_zip_manifest(zf):
    names=set(zf.namelist())
    manifest_name=next((n for n in names if n.endswith('case_manifest.json')),None)
    if not manifest_name:
        return ['旧案例无完整参数清单；缺失条件无法自动推断']
    try:
        manifest=json.loads(zf.read(manifest_name))
        base=manifest_name[:-len('case_manifest.json')]
        warnings=[]
        for name,expected in manifest.get('file_sha256',{}).items():
            path=base+name
            if path not in names: warnings.append(f'缺少文件 {name}')
            elif _digest(zf.read(path))!=expected: warnings.append(f'校验失败 {name}')
        return warnings
    except (ValueError,TypeError,KeyError) as exc:
        return [f'案例清单解析失败：{exc}']
