# -*- coding: utf-8 -*-
"""Offline V2.2.0 case-cloud audit. Never accesses weather APIs.

The saved calculation/score is historic and NOT recomputed here.  We examine
stored native point API, raw OM pressure proxy and the effective GFS horizontal
cover that the CURRENT calibration rule would feed to new geometry computations.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import csv
import io
import json
import math
from pathlib import Path
from zipfile import ZipFile

from burning_sky.spatial_pipeline import calibrate_path_samples

FIELDS = ('cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high')
LABELS = ('低云', '中云', '高云')
MAX_STATE_BYTES = 30 * 1024 * 1024


def read_case_zip(path):
    """Read only state JSON, without extracting untrusted paths or cache data."""
    with ZipFile(path) as z:
        states = [m for m in z.infolist() if m.filename.replace('\\', '/').endswith('/analysis_state.json')
                  or m.filename == 'analysis_state.json']
        if len(states) != 1:
            raise ValueError('ZIP 必须包含且仅包含一个 analysis_state.json')
        state_file = states[0]
        if state_file.file_size > MAX_STATE_BYTES:
            raise ValueError('案例状态文件超过 30 MB，拒绝读取')
        raw = z.read(state_file)
        if len(raw) > MAX_STATE_BYTES:
            raise ValueError('案例状态文件超过读取上限')
    state = json.loads(raw)
    if not isinstance(state, dict) or not isinstance(state.get('direction_results'), dict):
        raise ValueError('analysis_state.json 缺少 direction_results')
    return state


def _numeric(x):
    if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x):
        return float(x)
    return None


def _original(sample, key):
    n = _numeric(sample.get('point_api_area_' + key))
    if n is not None:
        return n
    # No native reference: cannot claim OM is inconsistent with the API.
    return None


def audit_state(state):
    out = {'place': state.get('place'), 'date': state.get('date'),
           'model': state.get('weather_model'), 'directions': {}, 'rows': [],
           'generated_utc': datetime.now(timezone.utc).isoformat(),
           'offline': True, 'scores_recomputed': False,
           'explanation': '只审计云量/校准输入；不重算严格光路、概率、颜色和时序，也不联网。'}
    for direction, result in state['direction_results'].items():
        if not isinstance(result, dict):
            continue
        samples = deepcopy(result.get('samples') or [])
        profiles = deepcopy(result.get('profiles') or [])
        if not samples or not profiles:
            continue
        model = str(result.get('query_model_id') or state.get('model_code') or '').lower()
        gfs = 'gfs' in model
        missing_provenance = 0
        if gfs:
            for p in profiles:
                if not isinstance(p, dict) or not isinstance(p.get('spatial_horizontal_om'), dict):
                    continue
                # Legacy exported GFS cases did not save metadata; archived GFS
                # OM layers originate from pressure proxy, not native layer fields.
                if not p.get('spatial_horizontal_layer_origin'):
                    p['spatial_horizontal_layer_origin'] = 'pressure_layer_proxy'
                    missing_provenance += 1
                if not p.get('spatial_horizontal_model_domain'):
                    p['spatial_horizontal_model_domain'] = 'ncep_gfs025'
        before = {round(float(s['distance_km']), 4): dict(s) for s in samples
                  if isinstance(s, dict) and _numeric(s.get('distance_km')) is not None}
        diag = calibrate_path_samples(samples, profiles)
        after = {round(float(s['distance_km']), 4): s for s in samples
                 if isinstance(s, dict) and _numeric(s.get('distance_km')) is not None}
        ps = {round(float(p['distance_km']), 4): p for p in profiles
              if isinstance(p, dict) and _numeric(p.get('distance_km')) is not None}
        rows = []
        for d, old in sorted(before.items()):
            sample = after[d]
            profile = ps.get(d, {})
            raw_om = profile.get('spatial_horizontal_om') or {}
            eff = profile.get('spatial_horizontal_effective') or {}
            for field, label in zip(FIELDS, LABELS):
                native = _original(old, field)
                om_value = _numeric(raw_om.get(field))
                before_value = _numeric(old.get(field))
                result_value = _numeric(sample.get(field))
                row = {'direction': direction, 'distance_km': d,
                       'layer': label, 'field': field,
                       'native_api_pct': native, 'raw_om_pct': om_value,
                       'old_calculation_pct': before_value, 'current_effective_pct': result_value,
                       'horizontal_effective_pct': _numeric(eff.get(field)),
                       'delta_pp': abs(native - om_value) if native is not None and om_value is not None else None,
                       'decision': (profile.get('spatial_horizontal_effective_sources') or {}).get(field, 'not_bound')}
                rows.append(row)
        out['directions'][direction] = {'gfs': gfs, 'rows': len(rows),
                                         'legacy_provenance_inferred': missing_provenance,
                                         'calibration': diag,
                                         'saved_probability': (result.get('inferred') or {}).get('regular_score')}
        out['rows'].extend(rows)
    return out


def audit_case_zip(path):
    result = audit_state(read_case_zip(path))
    result['source_filename'] = Path(path).name
    return result


def format_summary(audit):
    lines = [f"案例：{audit.get('source_filename') or '(未命名)'}",
             f"地点 {audit.get('place') or '?'} · 日期 {audit.get('date') or '?'} · 模式 {audit.get('model') or '?'}",
             '【离线云量一致性核对】不会请求 Open-Meteo，不会修改原 ZIP。']
    for direction, d in audit['directions'].items():
        c = d['calibration']
        lines += [f"{'朝霞' if direction == 'morning' else '晚霞' if direction == 'evening' else direction}："
                  f"分层记录 {d['rows']} 条；GFS 气压层 proxy {c.get('gfs_proxy_points',0)} 点；"
                  f"冲突 {c.get('conflict_count',0)} 项（>25个百分点）；"
                  f"旧版缺失来源标签 {d['legacy_provenance_inferred']} 点。"]
    return '\n'.join(lines) + ('\n仅复核云量输入；保存时的概率和几何结果没有重算。'
                               '完整结果需要使用案例缓存重算，或重新联网查询。')


def export_csv(audit, path):
    keys = ('direction','distance_km','layer','native_api_pct','raw_om_pct',
            'old_calculation_pct','current_effective_pct','horizontal_effective_pct','delta_pp','decision')
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(audit['rows'])


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description='离线审计 Burning Sky 案例 OM/API 云量差异')
    parser.add_argument('case_zip', help='已导出的 Burning Sky 案例 ZIP')
    parser.add_argument('--csv', help='可选：输出逐距离对照 CSV')
    args=parser.parse_args()
    data=audit_case_zip(args.case_zip)
    print(format_summary(data))
    if args.csv:
        export_csv(data,args.csv)
        print('已保存 CSV:',args.csv)
