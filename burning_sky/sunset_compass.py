"""Eight-direction polygon from locally sampled transverse cloud-hole evidence.
Not eight independent forecasts; unverified directions remain unknown.
"""
import math
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from .spherical_raytrace import destination

def transverse_sites(lat, lon, azimuth, boundaries):
    gaps = boundaries.get('observed_clear_gaps') or boundaries.get('clear_gaps') or []
    gaps = [g for g in gaps if isinstance(g,dict) and g.get('start_km') is not None and g.get('end_km') is not None]
    if not gaps: return [], None
    best = max(gaps,key=lambda g:float(g.get('size_km') or (float(g['end_km'])-float(g['start_km']))))
    distance = max(10., (float(best['start_km'])+float(best['end_km']))/2)
    sites=[]
    for offset in (-30.,-15.,15.,30.):
        bearing = (azimuth+(90 if offset>0 else -90))%360
        side=destination(lat,lon,bearing,abs(offset))
        a,b=destination(*side,azimuth,distance)
        sites.append({'lat':a,'lon':b,'offset_km':offset,'distance_km':distance})
    return sites, best

def cloud_evidence(sites, responses, dt):
    out=[]
    if dt.tzinfo: dt=dt.replace(tzinfo=None)
    for s,raw in zip(sites,responses):
        h=raw.get('hourly') or {};times=h.get('time') or []
        ix=None
        if times:
            try:ix=min(range(len(times)),key=lambda i:abs((datetime.fromisoformat(times[i].replace('Z','+00:00')).replace(tzinfo=None)-dt).total_seconds()))
            except (ValueError,TypeError):pass
        def get(key):
            arr=h.get(key) or []
            return float(arr[ix]) if ix is not None and ix<len(arr) and isinstance(arr[ix],(int,float)) else None
        lo,mid,hi=get('cloud_cover_low'),get('cloud_cover_mid'),get('cloud_cover_high')
        # Cloud hole evidence only. It is not a 3D raytrace validation.
        if None in (lo,mid,hi):clear=None
        else:clear=max(0.,1.-max(lo,mid,hi)/100.)
        out.append({**s,'cloud_cover_low':lo,'cloud_cover_mid':mid,'cloud_cover_high':hi,'clear_proxy':clear})
    return out

def estimate(result, sites):
    """15-degree compass: only sampled lateral cloud evidence is considered measured.

    This is a directional *proxy*, never an independent fire-cloud probability.
    """
    inferred=result.get('inferred') or {}
    az=float(result.get('azimuth') or 270)
    boundaries=result.get('boundaries') or {}
    gaps=boundaries.get('observed_clear_gaps') or boundaries.get('clear_gaps') or []
    score=float(inferred.get('regular_score') or 0)
    rgb=inferred.get('color_rgb') or [248,115,65]
    if not isinstance(rgb,(list,tuple)) or len(rgb)!=3:rgb=[248,115,65]
    rgb=[max(0,min(255,int(v))) for v in rgb]
    distance=max(10.,float(sites[0]['distance_km'])) if sites else None
    rays=[]
    # Do not claim the main line is a verified 3D ray simply because a gap exists.
    center_strength=score if gaps and score>0 else 0.
    rays.append({'azimuth':az,'offset_km':0.,'strength_proxy':round(center_strength,1),
                 'evidence':'main_line_model','cloud_cover':None})
    for site in sites:
        off=float(site['offset_km']);dist=max(10.,float(site['distance_km']))
        direction=(az+math.degrees(math.atan2(off,dist)))%360
        clear=site.get('clear_proxy')
        strength=None if clear is None else round(max(0.,min(100.,score*clear)),1)
        rays.append({'azimuth':direction,'offset_km':off,'strength_proxy':strength,
                     'evidence':'transverse_cloud_only' if clear is not None else 'missing',
                     'cloud_cover':{'low':site.get('cloud_cover_low'),'mid':site.get('cloud_cover_mid'),'high':site.get('cloud_cover_high')}})
    rays.sort(key=lambda r:r['offset_km'])
    valid=[r for r in rays if r['strength_proxy'] is not None and r['offset_km']!=0]
    status='sampled' if len(valid)==4 else ('partial' if valid else 'unavailable')
    # Directions are a viewing-angle proxy, not a measured observer-to-cloud projection.
    angles=[r['azimuth'] for r in rays if r['strength_proxy'] is not None and r['strength_proxy']>=20]
    return {'schema_version':2,'sun_azimuth':az,'main_probability':score,'peak_rgb':rgb,
            'rays':rays,'sites':sites,'gap_count':len(gaps),'transverse_status':status,
            'angular_support_deg':sorted(angles),
            'meaning':'Local transverse cloud-cover proxy; viewing azimuth and colors are not independently validated by cloud-target 3D projection.'}

def _font(size):
    for name in ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc','C:/Windows/Fonts/msyh.ttc','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        try:return ImageFont.truetype(name,size)
        except OSError:pass
    return ImageFont.load_default()



def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


def _mix_hex(a, b, t):
    t=max(0.0,min(1.0,float(t)))
    ar,ag,ab=a; br,bg,bb=b
    return (int(round(ar+(br-ar)*t)), int(round(ag+(bg-ag)*t)), int(round(ab+(bb-ab)*t)))


def _ray_color(base_rgb, strength, az, sun_az):
    strength=_clamp(strength)/100.0
    diff=abs((az-sun_az+180)%360-180)
    if diff<=22.5: target=(255,183,77)
    elif diff<=55: target=(255,124,69)
    elif diff<=95: target=(255,98,146)
    else: target=(190,120,255)
    return _mix_hex(tuple(base_rgb), target, 0.35+0.55*strength)

def render(diag):
    from PIL import ImageColor
    im=Image.new('RGB',(1000,920),'#f8fafc');d=ImageDraw.Draw(im,'RGBA')
    cx,cy,R=500,395,300
    def point(a,r):
        t=math.radians(a);return (cx+math.sin(t)*r,cy-math.cos(t)*r)
    for frac in (.25,.5,.75,1.):
        x=R*frac;d.ellipse((cx-x,cy-x,cx+x,cy+x),outline='#cbd5e1',width=2)
    for i,name in enumerate(['北','东北','东','东南','南','西南','西','西北']):
        a=i*45;d.line((cx,cy,*point(a,R)),fill='#cbd5e1',width=2)
        x,y=point(a,R+30);d.text((x,y),name,font=_font(24),fill='#334155',anchor='mm')
    az=float(diag['sun_azimuth'])
    # Dashed solar azimuth, not a fallback forecast shape.
    ex,ey=point(az,R+12)
    for t in range(0,300,24):
        a=t/300;b=min(t+13,300)/300
        d.line((cx+(ex-cx)*a,cy+(ey-cy)*a,cx+(ex-cx)*b,cy+(ey-cy)*b),fill='#eab308',width=5)
    d.ellipse((ex-7,ey-7,ex+7,ey+7),fill='#eab308')
    rays=diag.get('rays') or []
    sides=[r for r in rays if r.get('offset_km')!=0 and r.get('strength_proxy') is not None]
    status=diag.get('transverse_status','unavailable')
    if status=='sampled' and len(sides)>=4:
        # Preserve actual measured angle vertices, no rounded interpolation or invented 360-degree support.
        points=[point(r['azimuth'],R*max(0.,min(100.,r['strength_proxy']))/100.) for r in rays if r.get('strength_proxy') is not None]
        base=tuple(diag['peak_rgb'])
        # Color is main-line peak hue; side hue is unverified.
        if len(points)>=3:
            d.polygon([(cx,cy)]+points,fill=(*base,95))
            d.line(points,fill=(*base,255),width=5,joint='curve')
        for r in rays:
            if r.get('strength_proxy') is None:continue
            x,y=point(r['azimuth'],R*max(0.,min(100.,r['strength_proxy']))/100.)
            d.ellipse((x-6,y-6,x+6,y+6),fill=(*base,255))
    else:
        d.rounded_rectangle((190,348,810,445),radius=15,fill=(254,226,226,245),outline='#dc2626',width=3)
        d.text((500,375),'横向采样失败 / 数据不完整',font=_font(28),fill='#991b1b',anchor='mm')
        d.text((500,415),'南北晚霞可见角度：未知（不绘制预测多边形）',font=_font(19),fill='#991b1b',anchor='mm')
    d.ellipse((cx-8,cy-8,cx+8,cy+8),fill='#0f172a')
    d.text((35,24),'Burning Sky | 八方位补全图',font=_font(29),fill='#0f172a')
    d.text((35,67),f"太阳方位 {az:.1f}°  主线常规概率 {diag['main_probability']:.0f}%  主线云洞 {diag['gap_count']} 个",font=_font(21),fill='#334155')
    d.text((35,740),'黄虚线：太阳方位；彩色区域：基于当前结果的八方位补全显示（非中间角度独立严格验证）',font=_font(19),fill='#9a3412')
    d.text((35,776),'颜色已按方向作差异化增强；仍属于辅助方位图，不替代主算法概率与剖面验证。',font=_font(19),fill='#475569')
    for i,r in enumerate(rays):
        v=r.get('strength_proxy');s='未知' if v is None else f'{v:.0f}/100'
        d.text((30+i*190,818),f"{r['offset_km']:+.0f} km: {s}",font=_font(18),fill='#334155')
    if diag.get('transverse_error'):
        msg=str(diag['transverse_error'])[:85]
        d.text((35,862),'横向请求错误：'+msg,font=_font(16),fill='#b91c1c')
    return im


def render_compact(diag, size=760):
    """Web compact compass: complete radar-like polygon bound to the current single-result context."""
    im=Image.new('RGB',(size,size), '#f8fafc')
    d=ImageDraw.Draw(im,'RGBA')
    cx=cy=size//2; R=size*0.355
    def point(az,r):
        a=math.radians(az)
        return (cx+math.sin(a)*r, cy-math.cos(a)*r)
    # octagonal guides
    headings=[0,45,90,135,180,225,270,315]
    for frac in (.25,.5,.75,1.0):
        pts=[]
        rr=R*frac
        for az in headings: pts.extend(point(az,rr))
        d.polygon(pts, outline='#d4dde8')
    for az,label in zip(headings,('北','东北','东','东南','南','西南','西','西北')):
        d.line((*point(az,0), *point(az,R)), fill='#d4dde8', width=2)
        d.text(point(az,R+size*.05), label, font=_font(20), fill='#334155', anchor='mm')
    sun_az=float(diag.get('sun_azimuth') or 0)
    for i in range(20):
        if i%2==0:
            d.line((*point(sun_az,R*i/20), *point(sun_az,R*(i+1)/20)), fill='#eab308', width=4)
    sx,sy=point(sun_az,R+18); d.ellipse((sx-7,sy-7,sx+7,sy+7),fill='#facc15')
    d.text((sx,sy),'日',font=_font(12),fill='#5b4200',anchor='mm')
    rays=diag.get('rays') or []
    active=[r for r in rays if r.get('strength_proxy') is not None]
    base_rgb=tuple(int(max(0,min(255,v))) for v in (diag.get('peak_rgb') or [245,120,95])[:3])
    if active:
        # build full 8-direction support by distributing nearest measured support; unsampled sectors stay faint
        support={az:0.0 for az in headings}; colors={az:(98,111,128) for az in headings}
        for az in headings:
            nearest=min(active, key=lambda r: abs(((float(r['azimuth'])-az+180)%360)-180))
            diff=abs(((float(nearest['azimuth'])-az+180)%360)-180)
            raw=float(nearest.get('strength_proxy') or 0.0)
            factor=max(0.0, 1.0-diff/90.0)
            val=raw*factor
            support[az]=val
            if raw>0 and factor>0.15:
                colors[az]=_ray_color(base_rgb, raw, az, sun_az)
            elif nearest.get('evidence')=='main_line_model':
                colors[az]=_mix_hex(base_rgb,(98,111,128),0.45)
        # sector fills
        for i,az in enumerate(headings):
            az2=headings[(i+1)%8]
            p1=point(az, R*support[az]/100.0)
            p2=point(az2, R*support[az2]/100.0)
            fill=_mix_hex(colors[az], colors[az2], 0.5)
            d.polygon([(cx,cy),p1,p2], fill=(*fill,120), outline=None)
        # outline polygon
        poly=[]
        for az in headings: poly.append(point(az, R*support[az]/100.0))
        d.line(poly+[poly[0]], fill='#f6c453', width=4, joint='curve')
        for az,p in zip(headings, poly):
            d.ellipse((p[0]-4,p[1]-4,p[0]+4,p[1]+4), fill='#fff7dd')
            # show representative high-cloud number if nearest site has it
            nearest=min(active, key=lambda r: abs(((float(r['azimuth'])-az+180)%360)-180))
            cloud=(nearest.get('cloud_cover') or {}).get('high') if nearest.get('cloud_cover') else None
            lx,ly=point(az, max(( (p[0]-cx)**2 + (p[1]-cy)**2 )**0.5 + 18, 48))
            d.text((lx,ly), '?' if cloud is None else str(int(round(float(cloud)))), font=_font(18), fill='#ffffff', anchor='mm')
    d.ellipse((cx-6,cy-6,cx+6,cy+6), fill='#0f172a')
    return im
