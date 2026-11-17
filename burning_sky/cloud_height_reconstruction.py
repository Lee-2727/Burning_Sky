"""Experimental cloud-height completion for sparse pressure profiles.
No extra API requests. Pressure-cloud observations are never overwritten.
Cloud height estimates are uncertain proxies, not observed solid cloud walls.
"""
from __future__ import annotations
import math

BANDS = (("low",0.,2.,"cloud_cover_low"),("mid",2.,6.,"cloud_cover_mid"),("high",6.,14.,"cloud_cover_high"))

def _num(x):
    return isinstance(x,(int,float)) and math.isfinite(x)

def reconstruct(profiles, samples, threshold=25.):
    """Fill only genuinely missing layer-cover values, never override valid clear sky.

    Requires independent band-cloud evidence and RH >= 85% at a valid height.
    No pressure levels -> unknown, not an invented synthetic cloud profile.
    """
    counts={"pressure":0,"humidity":0,"skipped":0}
    records=[]
    for p in profiles:
        d=p.get("distance_km")
        s=min(samples,key=lambda x:abs(float(x.get("distance_km",0))-float(d or 0))) if samples else {}
        levels=p.get("levels") or []
        for name,lo,hi,key in BANDS:
            cover=s.get(key)
            if not _num(cover) or cover<max(10.,threshold):continue
            band=[lv for lv in levels if _num(lv.get("height_agl_km")) and lo<=lv["height_agl_km"]<hi]
            if not band:
                counts["skipped"]+=1;continue
            known=[lv for lv in band if _num(lv.get("cloud_cover"))]
            if known:
                # Any valid cloud-cover observation is authoritative, including 0%.
                # Do not override it or infer a replacement merely because it is clear.
                counts["pressure"]+=1
                continue
            humid=[lv for lv in band if lv.get("cloud_cover") is None and
                   _num(lv.get("relative_humidity")) and lv["relative_humidity"]>=85]
            if not humid:
                counts["skipped"]+=1;continue
            for lv in humid:
                lv["cloud_cover"]=min(100.,max(threshold,float(cover)))
                lv["height_source"]="humidity_reconstructed"
                lv["reconstructed_cloud"] = True
            counts["humidity"]+=1
            records.append({"distance_km":d,"layer":name,"source":"humidity_reconstructed",
                            "cover":round(float(cover),1)})
        if p.get("estimated_base_km") is None:
            cloudy=[lv["height_agl_km"] for lv in levels if
                    _num(lv.get("cloud_cover")) and lv["cloud_cover"]>=threshold and
                    _num(lv.get("height_agl_km"))]
            if cloudy:
                p["estimated_base_km"]=min(cloudy)
                p["status"]="气压层补全推算（非实测）"
    total=sum(counts.values())
    return {"counts":counts,"percentages":{k:round(v*100/total,1) if total else 0 for k,v in counts.items()},
            "unit":"采样点×云层；非准确率", "records":records,
            "warning":"仅在整层云量缺失且湿度支持时补全；已有云量含0%均保持不变。补全层参与球面光路，属于近似证据。"}
