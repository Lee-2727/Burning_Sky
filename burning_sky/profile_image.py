"""Streamlit cloud section using final adaptive OM-compatible strict rays."""
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
    # Only the final adaptive ray gate knows which routes remain verified AFTER
    # OM conflicts.  The legacy independent debug raytrace is candidate-only.
    gate = inferred.get('strict_primary_path_diagnostic') or {}
    verified = [r for r in (inferred.get('verified_primary_ray_traces') or [])
                if isinstance(r,dict) and isinstance(r.get('solar_altitude_deg'),(int,float))
                and isinstance(r.get('target_height_km'),(int,float))
                and isinstance(r.get('target_face_km'),(int,float))]
    ordered = sorted((r for r in ray_records if isinstance(r.get('solar_altitude_deg'),(int,float))
                      and isinstance(r.get('time'),str)), key=lambda r:r['time'])
    target = next((r['target_height_km'] for r in verified), None)
    if target is None:
        target=next((r.get('target_cloud_base_km') for r in ray_records
                     if isinstance(r.get('target_cloud_base_km'),(int,float))),None)
    if target is None:
        target=next((p.get('estimated_base_km') for p in profiles
                     if p.get('distance_km')==0 and isinstance(p.get('estimated_base_km'),(int,float))),None)
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
    # Candidate solar beams: use the same per-cloud-face, minute-varying
    # geometry as the Desktop fusion visual when available.  They remain dotted
    # and MUST NOT be confused with the accepted OM-reviewed rays below.
    face_candidates=[r for r in (inferred.get('spherical_face_plot_traces') or [])
                     if isinstance(r,dict) and isinstance(r.get('target_face_km'),(int,float))
                     and isinstance(r.get('target_height_km'),(int,float))
                     and isinstance(r.get('solar_altitude_deg'),(int,float))]
    if face_candidates:
        step=max(1,len(face_candidates)//35)
        for ray in face_candidates[::step][:36]:
            face=float(ray['target_face_km']); h0=float(ray['target_height_km']);alt=float(ray['solar_altitude_deg'])
            if not 0<=face<plotdist:continue
            for j in range(330):
                dist=face+(plotdist-face)*j/329
                relative=dist-face
                if _earth_occluded(h0,alt,relative):continue
                h=_ray_height_km(h0,alt,relative)
                if math.isfinite(h) and 0<=h<=maxh and j%2==0:
                    xx,yy=x(dist),y(h)
                    d.ellipse((xx-1,yy-1,xx+1,yy+1),fill='#bb66e8')
    else:
        # Legacy standalone ray debug is a candidate drawing only, never a
        # substitute for the final strict_primary_path_diagnostic evidence.
        candidates=[r for r in ordered if isinstance(r.get('solar_altitude_deg'),(int,float))]
        if len(candidates)>36:
            picks={round(i*(len(candidates)-1)/35) for i in range(36)}
            candidates=[candidates[i] for i in sorted(picks)]
        for ray in candidates:
            for dist,h,blocked in sample_ray_curve(target,float(ray['solar_altitude_deg']),plotdist,650)[::2]:
                if not blocked and math.isfinite(h) and 0<=h<=maxh:
                    xx,yy=x(dist),y(h)
                    d.ellipse((xx-1,yy-1,xx+1,yy+1),fill='#bb66e8')
    # Real accepted rays after OM conflict review. Plot each at ITS OWN cloud
    # target height and face, not a shared artificial default cloud base.
    drawn=0
    for ray in verified:
        face=float(ray['target_face_km']);alt=float(ray['solar_altitude_deg'])
        cloud_h=float(ray['target_height_km'])
        far=min(plotdist,float(ray.get('source_check_km',plotdist)))
        if not (0<=face<far and 0<cloud_h<=maxh):continue
        coords=[]
        for j in range(401):
            dist=face+(far-face)*j/400.
            radial=dist-face
            if _earth_occluded(cloud_h,alt,radial):
                if len(coords)>1:d.line(coords,fill='#7434ac',width=3)
                coords=[];continue
            h=_ray_height_km(cloud_h,alt,radial)
            if math.isfinite(h) and 0<=h<=maxh:
                coords.append((x(dist),y(h)))
            else:
                if len(coords)>1:d.line(coords,fill='#7434ac',width=3)
                coords=[]
        if len(coords)>1:d.line(coords,fill='#7434ac',width=3)
        drawn+=1
        if drawn>=18:break  # Avoid hiding the cloud cells under 60 lines.
    # Bounded logged conflict samples are placed at their actual face-axis
    # distances and ray heights (not at guessed cloud-bottom positions).
    conflicts = gate.get('horizontal_conflict_examples') or []
    for row in conflicts[:24]:
        try:
            dist=float(row['distance_km']);h=float(row['ray_height_km'])
        except (KeyError,TypeError,ValueError):continue
        if not (0<=dist<=plotdist and 0<=h<=maxh):continue
        xx,yy=x(dist),y(h)
        for dd in ((-7,-7,7,7),(-7,7,7,-7)):
            d.line((xx+dd[0],yy+dd[1],xx+dd[2],yy+dd[3]),fill='#ec770f',width=4)
    if not verified:
        d.text((left+pw*.20,top+25),'No final validated OM-compatible ray (dotted = geometry candidates)',fill='#8b3274')
    elif conflicts:
        d.text((left+pw*.07,top+25),f'OM horizontal/vertical conflicts: {gate.get("horizontal_conflict_count",len(conflicts))} (orange X = samples)',fill='#c45b00')
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
