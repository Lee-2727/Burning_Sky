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
    counts={"pressure":0,"humidity":0,"prior":0}
    records=[]
    for i,p in enumerate(profiles):
        d=p.get("distance_km")
        s=min(samples,key=lambda x:abs(float(x.get("distance_km",0))-float(d or 0))) if samples else {}
        levels=p.get("levels") or []
        if not levels:
            levels=[{"height_agl_km":h,"cloud_cover":None,"relative_humidity":None}
                    for h in (0.5,1.2,2.5,4.5,6.5,8.5,10.5,12.)]
            p["levels"]=levels
        for name,lo,hi,key in BANDS:
            cover=s.get(key)
            if not _num(cover) or cover<10:continue
            band=[lv for lv in levels if _num(lv.get("height_agl_km")) and lo<=lv["height_agl_km"]<hi]
            direct=[lv for lv in band if _num(lv.get("cloud_cover")) and lv["cloud_cover"]>=threshold]
            if direct:
                counts["pressure"]+=1
                records.append({"distance_km":d,"layer":name,"source":"pressure","cover":round(float(cover),1)})
                continue
            # A humid layer is evidence for a *possible* cloud. Preserve direct cloud-cover
            # values in a diagnostic field; only fill the derived geometric proxy.
            humid=[lv for lv in band if _num(lv.get("relative_humidity")) and lv["relative_humidity"]>=85]
            if humid:
                candidates=humid
                source="humidity"
            else:
                # Coarse 3-scenario height prior. The main engine uses central scenario;
                # uncertainty is explicitly recorded and never labeled observed.
                center={"low":1.0,"mid":4.5,"high":9.0}[name]
                candidates=sorted(band,key=lambda lv:abs(lv["height_agl_km"]-center))[:2]
                if not candidates:
                    candidates=[{"height_agl_km":center,"cloud_cover":None,"relative_humidity":None}]
                    levels.extend(candidates)
                source="prior"
            for lv in candidates:
                lv.setdefault("observed_cloud_cover",lv.get("cloud_cover"))
                lv["cloud_cover"]=max(threshold,float(cover))
                lv["height_source"]=source
            counts[source]+=1
            records.append({"distance_km":d,"layer":name,"source":source,"cover":round(float(cover),1)})
        levels.sort(key=lambda x:float(x.get("height_agl_km") or 0))
        if p.get("estimated_base_km") is None:
            cloudy=[lv["height_agl_km"] for lv in levels if _num(lv.get("cloud_cover")) and lv["cloud_cover"]>=threshold and _num(lv.get("height_agl_km"))]
            if cloudy:
                p["estimated_base_km"]=min(cloudy)
                p["status"]="云高补齐估算（非实测）"
    total=sum(counts.values())
    return {"counts":counts,"percentages":{k:round(v*100/total,1) if total else 0 for k,v in counts.items()},
            "unit":"有云的采样点×低/中/高云层；非精度或概率", "records":records,
            "warning":"估算云高参与球面光路敏感性测试；单一中心情景不代表严格证实，需多情景核验"}
