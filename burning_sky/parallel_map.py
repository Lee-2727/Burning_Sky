"""Three sun-parallel cloud sampling lines and offline schematic Hefei backdrop.
The city map is deliberately illustrative, not official administrative GIS geometry.
"""
from __future__ import annotations
import math
from pathlib import Path
from datetime import datetime
from PIL import Image,ImageDraw,ImageFont
from .spherical_raytrace import destination

ASSET=Path(__file__).resolve().parent.parent/'assets'/'hefei_schematic.png'
LANDMARKS=[('大蜀山',31.850,117.175),('天鹅湖',31.817,117.226),('庐阳区',31.884,117.264),('蜀山区',31.835,117.215),('包河区',31.792,117.302),('瑶海区',31.865,117.332),('肥西县',31.704,117.154),('肥东县',31.890,117.465),('长丰县',32.086,117.164)]

def make_sites(lat,lon,bearing,distances=(0,25,50,75,100,150,200,300),offsets=(-30,0,30)):
    out=[]
    for off in offsets:
        lateral_bearing=(bearing+90 if off>=0 else bearing-90)%360
        a,b=destination(lat,lon,lateral_bearing,abs(off))
        for dist in distances:
            x,y=destination(a,b,bearing,dist)
            out.append({'offset_km':off,'distance_km':dist,'lat':x,'lon':y})
    return out

def _nearest_hour(hourly,dt):
    times=hourly.get('time') or []
    if not times:return None
    if getattr(dt,'tzinfo',None):dt=dt.replace(tzinfo=None)
    try:
        index=min(range(len(times)),key=lambda i:abs((datetime.fromisoformat(times[i].replace('Z','+00:00')).replace(tzinfo=None)-dt).total_seconds()))
    except (ValueError,TypeError):return None
    return index

def evaluate_lines(sites,event_dt):
    out=[]
    for site in sites:
        raw=site.get('raw_weather') or {}
        h=raw.get('hourly') or {}
        i=_nearest_hour(h,event_dt)
        sm=site.get('sample') or {}
        vals={}
        for k in ('cloud_cover_low','cloud_cover_mid','cloud_cover_high','cloud_cover'):
            data=h.get(k)
            v=data[i] if i is not None and isinstance(data,list) and i<len(data) else sm.get(k)
            vals[k]=float(v) if isinstance(v,(float,int)) else None
        vals['total_cloud_proxy']=vals['cloud_cover']
        if vals['total_cloud_proxy'] is None:
            valid=[v for k,v in vals.items() if k in ('cloud_cover_low','cloud_cover_mid','cloud_cover_high') and v is not None]
            vals['total_cloud_proxy']=max(valid) if valid else None
        out.append({**{k:site[k] for k in ('lat','lon','offset_km','distance_km')},**vals})
    return out

def _font(size):
    for name in ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','C:/Windows/Fonts/msyh.ttc'):
        try:return ImageFont.truetype(name,size)
        except OSError:pass
    return ImageFont.load_default()

def create_asset():
    ASSET.parent.mkdir(parents=True,exist_ok=True)
    im=Image.new('RGB',(1200,900),'#f1f5f1');d=ImageDraw.Draw(im)
    # Simplified landmarks/urban area; intentionally not authoritative boundaries.
    d.ellipse((405,220,830,670),fill='#e4ebdd',outline='#b8c7b0',width=3)
    d.polygon([(440,370),(510,300),(610,315),(675,410),(620,520),(475,500)],fill='#e5e6d5')
    d.line([(90,540),(380,490),(580,430),(840,380),(1140,300)],fill='#b6d9ec',width=18)
    d.line([(100,530),(380,480),(580,420),(840,370),(1140,290)],fill='#e2f3f9',width=7)
    for path in [[(240,0),(330,230),(490,420),(740,590),(1020,900)],[(0,270),(330,310),(650,340),(1200,450)],[(120,850),(430,620),(710,410),(1090,0)]]:
        d.line(path,fill='#ffffff',width=12);d.line(path,fill='#d0d9d2',width=2)
    # Approximate district separators, not cadastral lines.
    for path in [[(450,310),(560,420),(680,460)],[(510,420),(470,590)],[(610,420),(760,330)]]:
        d.line(path,fill='#a8b5aa',width=2)
    def pt(lat,lon):return 600+(lon-117.23)*1100,440-(lat-31.82)*1200
    for name,lat,lon in LANDMARKS:
        x,y=pt(lat,lon)
        if not (30<x<1160 and 25<y<865):continue
        d.ellipse((x-5,y-5,x+5,y+5),fill='#64748b')
        d.text((x+7,y-17),name,font=_font(17),fill='#334155')
    d.text((22,20),'HEFEI · schematic base / 合肥简化示意底图',font=_font(21),fill='#334155')
    d.text((22,865),'Landmarks approximate · district lines schematic · not for navigation',font=_font(14),fill='#475569')
    im.save(ASSET)
    return im

def render_map(origin,bearing,points,title='三条平行采样线'):
    if not ASSET.exists():create_asset()
    im=Image.open(ASSET).convert('RGBA');overlay=Image.new('RGBA',im.size);d=ImageDraw.Draw(overlay)
    # Draw a local east/north plane centered at observer; map extent adapts to the 300-km ray length.
    # Overlay is schematic relative to observer, not a georeferenced cloud field on the Hefei city map.
    cx,cy=600,440
    def xy(lat,lon):
        east=(lon-origin[1])*111.0*math.cos(math.radians(origin[0]));north=(lat-origin[0])*111.0
        return cx+east*1.15,cy-north*1.15
    for off in (-30,0,30):
        coords=[]
        for dist in (0,25,50,75,100,150,200,300):
            base=destination(origin[0],origin[1],(bearing+(90 if off>=0 else -90))%360,abs(off))
            lat,lon=destination(*base,bearing,dist)
            coords.append(xy(lat,lon))
        d.line(coords,fill=(230,110,40,240) if off==0 else (32,101,191,210),width=5)
    for p in points:
        x,y=xy(p['lat'],p['lon']);v=p.get('total_cloud_proxy')
        if v is None:color=(160,160,160,140)
        else:
            t=max(0,min(1,float(v)/100))
            color=(int(250-150*t),int(240-170*t),int(245-170*t),190)
        d.ellipse((x-10,y-10,x+10,y+10),fill=color,outline=(20,30,45,200),width=2)
    x,y=xy(*origin);d.ellipse((x-8,y-8,x+8,y+8),fill='#e11d48',outline='white',width=2)
    im=Image.alpha_composite(im,overlay).convert('RGB');d=ImageDraw.Draw(im)
    d.rectangle((12,55,710,125),fill='#ffffff',outline='#cad5e2',width=2)
    d.text((24,65),title,font=_font(23),fill='#172033')
    d.text((24,98),'Side offsets ±30 km; 15-km sampling average; proxy cloud cover',font=_font(15),fill='#475569')
    d.text((20,820),'NOTICE: city landmarks and cloud overlays are schematic, not GIS-aligned at 300 km.',font=_font(16),fill='#9a3412')
    return im
