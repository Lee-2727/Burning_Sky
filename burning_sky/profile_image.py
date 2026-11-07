"""Raster rendering of the desktop spherical cloud profile for Streamlit."""
import json
import math
import os
from datetime import datetime
from PIL import Image, ImageDraw
from .spherical_raytrace import profile_cloud_cells, _earth_occluded, _ray_height_km
from .profile_geometry import sample_ray_curve


def render_profile(result, width=1400, height=760):
    profiles=sorted((p for p in result.get("profiles",[]) if isinstance(p.get("distance_km"),(int,float)) and p.get("levels")),key=lambda p:p["distance_km"])
    if not profiles:
        return None
    inferred=result.get("inferred") or {}
    ray_records=[]
    path=inferred.get("spherical_raytrace_debug_json")
    if path and os.path.isfile(path):
        try:
            with open(path,encoding="utf-8") as f:
                ray_records=json.load(f).get("results",[])
        except (OSError,ValueError,TypeError):
            pass
    # Desktop uses this same file and only renders verified contiguous runs.
    ordered=sorted((r for r in ray_records if isinstance(r.get("solar_altitude_deg"),(int,float)) and isinstance(r.get("time"),str)),key=lambda r:r["time"])
    runs=[];current=[]
    for ray in ordered:
        if ray.get("status")!="clear_path":
            if len(current)>=2:runs.append(current)
            current=[]
            continue
        if current:
            try:gap=(datetime.fromisoformat(ray["time"])-datetime.fromisoformat(current[-1]["time"])).total_seconds()
            except (ValueError,TypeError):gap=float("inf")
            if not 1<=gap<=61:
                if len(current)>=2:runs.append(current)
                current=[]
        current.append(ray)
    if len(current)>=2:runs.append(current)
    target=next((r.get("target_cloud_base_km") for r in ray_records if isinstance(r.get("target_cloud_base_km"),(int,float))),None)
    if target is None:
        target=next((p.get("estimated_base_km") for p in profiles if p.get("distance_km")==0 and isinstance(p.get("estimated_base_km"),(int,float))),None)
    if target is None:target=4.0
    img=Image.new("RGB",(width,height),"#fbfcfe"); d=ImageDraw.Draw(img)
    left,right,top,bottom=76,102,35,65
    pw=width-left-right;ph=height-top-bottom
    distances=[float(p["distance_km"]) for p in profiles]
    maxdist=max(distances)
    plotdist=maxdist+(distances[-1]-distances[-2])/2 if len(distances)>1 else max(1,maxdist)
    allh=[float(lv["height_agl_km"]) for p in profiles for lv in p["levels"] if isinstance(lv.get("height_agl_km"),(int,float))]
    maxh=min(22.,max(8.,target+4,math.ceil(max(allh or [10]))))
    def x(v):return left+v/plotdist*pw
    def y(v):return top+ph-max(0,min(maxh,v))/maxh*ph
    for h in range(0,int(maxh)+1,2):
        yy=y(h);d.line((left,yy,left+pw,yy),fill="#e3e8ef");d.text((8,yy-6),f"{h} km",fill="#334155")
    step=25 if maxdist<=250 else 50 if maxdist<=500 else 100
    for km in range(0,int(maxdist)+1,step):
        xx=x(km);d.line((xx,top,xx,top+ph),fill="#edf0f4");d.text((xx-8,top+ph+14),str(km),fill="#334155")
    d.line((left,top,left,top+ph),fill="#475569",width=2)
    d.line((left,top+ph,left+pw,top+ph),fill="#475569",width=2)
    for cell in profile_cloud_cells(profiles):
        if cell["top_km"]<=0 or cell["bottom_km"]>maxh:continue
        cc=cell.get("cloud_cover")
        if not isinstance(cc,(int,float)):cc=50.
        gray=int(250-205*max(0,min(100,cc))/100)
        d.rectangle((x(cell["start_km"]),y(min(maxh,cell["top_km"])),x(cell["end_km"]),y(max(0,cell["bottom_km"]))),fill=(gray,gray,gray),outline="white",width=1)
        if cell.get("reconstructed_cloud"):
            x0,x1=x(cell["start_km"]),x(cell["end_km"])
            y0,y1=y(min(maxh,cell["top_km"])),y(max(0,cell["bottom_km"]))
            def dashed(a,b):
                dx=b[0]-a[0];dy=b[1]-a[1];length=math.hypot(dx,dy)
                if length<1:return
                for k in range(0,int(length),13):
                    q=min(length,k+7)
                    d.line((a[0]+dx*k/length,a[1]+dy*k/length,
                            a[0]+dx*q/length,a[1]+dy*q/length),fill="#e11d48",width=3)
            for a,b in (((x0,y0),(x1,y0)),((x1,y0),(x1,y1)),
                        ((x1,y1),(x0,y1)),((x0,y1),(x0,y0))):dashed(a,b)
    for p in profiles:
        base=p.get("estimated_base_km")
        if isinstance(base,(int,float)) and 0<=base<=maxh:
            xx=x(p["distance_km"]);yy=y(base)
            d.ellipse((xx-3,yy-3,xx+3,yy+3),fill="#d83c3c",outline="white")
    def draw_curve(alt):
        coords=[]
        for dist,h,blocked in sample_ray_curve(target,alt,plotdist,1201):
            if not blocked and math.isfinite(h) and 0<=h<=maxh:
                coords.append((x(dist),y(h)))
            else:
                if len(coords)>1:d.line(coords,fill="#7434ac",width=3)
                coords=[]
        if len(coords)>1:d.line(coords,fill="#7434ac",width=3)
    for run in runs:
        alts=[float(r["solar_altitude_deg"]) for r in run]
        low,high=min(alts),max(alts)
        if abs(high-low)<1e-8:continue
        for col in range(1,241):
            dist=plotdist*col/241.
            if _earth_occluded(target,low,dist) or _earth_occluded(target,high,dist):continue
            a=_ray_height_km(target,low,dist);b=_ray_height_km(target,high,dist)
            if not (math.isfinite(a) and math.isfinite(b)):continue
            lo=max(0,min(a,b));hi=min(maxh,max(a,b))
            if hi<=lo:continue
            for k in range(1,9):
                yy=y(lo+(hi-lo)*k/9)
                xx=x(dist)
                d.ellipse((xx-1,yy-1,xx+1,yy+1),fill="#bb398d")
        draw_curve(low);draw_curve(high)
    if not runs:
        d.text((left+pw*.28,top+25),"No continuous verified clear-ray interval",fill="#8b3274")
    if 0<=target<=maxh:
        xx=x(0);yy=y(target)
        d.ellipse((xx-5,yy-5,xx+5,yy+5),fill="#101820",outline="white")
        d.text((xx+10,yy-16),f"Target base {target:.2f} km",fill="#111827")
    cbx=width-80;cby=top+35;cbh=max(90,ph-75)
    for k in range(100):
        gray=int(250-205*k/100)
        d.rectangle((cbx,cby+k*cbh/100,cbx+16,cby+(k+1)*cbh/100+1),fill=(gray,gray,gray))
    for v in (0,25,50,75,100):d.text((cbx+20,cby+v*cbh/100-6),str(v),fill="#334155")
    d.text((cbx-5,cby-20),"Cloud %",fill="#334155")
    for key,color,label,yoff in (("fire_cloud_range_km","#0f766e","Range",10),("cloud_edge_distance_km","#b45309","Edge",28)):
        dist=(result.get("boundaries") or {}).get(key)
        if isinstance(dist,(int,float)):
            xx=x(dist)
            d.line((xx,top,xx,top+ph),fill=color,width=2)
            d.text((min(left+pw-100,max(left+5,xx+5)),top+yoff),f"{label} {dist:.0f}km",fill=color)
    d.text((width//2-60,height-30),"Distance (km)",fill="#1f2937")
    return img
