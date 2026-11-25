"""Shared schematic rainbow view for Tk and Streamlit. Not a verified arc reconstruction."""
import math
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

def _font(size):
    for path in ('/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc','C:/Windows/Fonts/msyh.ttc'):
        try:return ImageFont.truetype(path,size)
        except OSError:pass
    return ImageFont.load_default()

def summarize(result):
    row=next((r for r in result.get('timeline',[]) if r.get('time')==result.get('time')), {})
    valid=[d for d in row.get('details',[]) if d.get('primary_index') is not None]
    # Spatial samples do not provide a 3-D projection of a particular viewer's rainbow arc.
    # Do not infer a missing left or right half from sparse azimuth bins.
    vis=[d.get('visibility_m') for d in valid if isinstance(d.get('visibility_m'),(int,float))]
    quality=float(result.get('primary_quality') or 0)
    penalties=[]
    if not vis: penalties.append('能见度缺测：色彩/对比度不确定')
    elif min(vis)<8000:penalties.append('能见度偏低：对比度可能减弱')
    pm=[d.get('pm2_5') for d in valid if isinstance(d.get('pm2_5'),(int,float))]
    aod=[d.get('aod') for d in valid if isinstance(d.get('aod'),(int,float))]
    if pm and max(pm)>35:penalties.append('PM2.5偏高：空气消光可能增强')
    if aod and max(aod)>.5:penalties.append('AOD偏高：通透度可能下降')
    if not pm and not aod:penalties.append('颗粒物/AOD缺测：气溶胶影响未验证')
    if quality<65:penalties.append('主虹质量偏低：颜色可能偏淡')
    if row.get('rejections',{}).get('no_direct_sun',0):penalties.append('部分雨区太阳光路受遮挡')
    if row.get('rejections',{}).get('no_rain',0):penalties.append('部分采样区缺少雨滴证据')
    if not penalties:penalties.append('无明显扣分项；虹弧实际完整度仍未知')
    return {'row':row,'penalties':penalties,'arc_known':False}

def render(result,width=900,height=480,peak_color_rgb=None, sunset_context=False):
    s=summarize(result)
    # Only tint the sky for a high-opportunity sunset rainbow when a real peak cloud
    # colour was supplied by the fire-cloud forecast. Never fabricate this colour.
    primary=float(result.get('primary_index') or 0)
    secondary=float(result.get('secondary_index') or 0)
    pq=float(result.get('primary_quality') or 0)
    sq=float(result.get('secondary_quality') or 0)
    best=max(primary,secondary)
    sky=(235,245,252)
    if sunset_context and best>30 and isinstance(peak_color_rgb,(list,tuple)) and len(peak_color_rgb)>=3:
        try:
            peak=tuple(max(0,min(255,int(v))) for v in peak_color_rgb[:3])
            sky=tuple(round(.70*b+.30*c) for b,c in zip(sky,peak))
        except (TypeError,ValueError):pass
    im=Image.new('RGB',(width,height),sky);d=ImageDraw.Draw(im)
    # Idealized two concentric semicircular rainbows. Dashed sections denote unknown extent,
    # NOT forecast missing portions. Cloud overlays only denote unverified cloud obstruction.
    cx=width//2; ground=int(height*.78)
    d.rectangle((0,ground,width,height),fill=(206,224,199))
    colors=[(216,48,51),(240,124,43),(251,195,67),(117,184,83),(60,147,201),(96,93,168),(160,91,166)]
    # Both opportunity and quality govern schematic opacity; zero means barely visible.
    def opacity(opportunity,quality,base):
        if opportunity<=0 or quality<=0:return .012
        return min(base,base*(opportunity/100)**.7*(quality/100)**.6)
    for outer,reverse,alpha in ((int(height*.63),True,opacity(secondary,sq,.65)),
                                (int(height*.51),False,opacity(primary,pq,.95))):
        palette=list(reversed(colors)) if reverse else colors
        for j,c in enumerate(palette):
            r=outer-j*6
            # Draw the upper half only; this is a schematic, not observed completeness.
            points=[]
            for k in range(181):
                a=math.pi+k*math.pi/180
                x=cx+int(r*math.cos(a)); y=ground+int(r*math.sin(a))
                if y<=ground:points.append((x,y))
            if len(points)>1:
                bg=sky
                col=tuple(int(alpha*v+(1-alpha)*b) for v,b in zip(c,bg))
                d.line(points,fill=col,width=5)
    # Cloud cover illustration is generic, not a location-specific occlusion prediction.
    cloud=(178,190,200)
    for x,y,scale in ((int(width*.19),int(height*.30),1.0),(int(width*.80),int(height*.38),.85)):
        for dx,dy,rr in ((0,12,23),(24,0,30),(49,12,22)):
            rr=int(rr*scale);xx=x+int(dx*scale);yy=y+int(dy*scale)
            d.ellipse((xx-rr,yy-rr,xx+rr,yy+rr),fill=cloud)
    d.text((18,14),'RAINBOW · SCHEMATIC',font=_font(20),fill=(35,55,75))
    d.text((18,height-58),'Rainbow arc extent / left-right visibility: UNKNOWN',font=_font(15),fill=(52,67,80))
    d.text((18,height-34),'Cloud icons are illustrative, NOT located occlusions',font=_font(13),fill=(78,90,100))
    return im

def png_bytes(result,peak_color_rgb=None,sunset_context=False):
    b=BytesIO();render(result,peak_color_rgb=peak_color_rgb,sunset_context=sunset_context).save(b,format='PNG');return b.getvalue()


def timeline_chart_bytes(result, timezone_name="Asia/Shanghai", threshold=30):
    """Plot only qualifying sampled intervals; invalid periods consume no x-axis space.

    The x-axis is a categorical sequence of genuine 15-minute time slots rather
    than a continuous axis that includes physically invalid midday/night slots.
    """
    from datetime import datetime, timedelta, timezone
    try:
        from zoneinfo import ZoneInfo
        tz=ZoneInfo(timezone_name)
    except Exception:
        tz=timezone(timedelta(hours=8))
    rows=[]
    for row in result.get('timeline',[]) or []:
        try:
            score=float(row.get('primary_index') or 0)
            quality=float(row.get('primary_quality') or 0)
            altitude=row.get('solar_altitude_deg')
            if altitude is None or not math.isfinite(float(altitude)) or not (-2 <= float(altitude) <= 53):continue
            if not math.isfinite(score) or score<=threshold:continue
            dt=datetime.fromisoformat(str(row['time']).replace('Z','+00:00'))
            if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
            dt=dt.astimezone(tz)
            rows.append((dt,min(100,max(0,score)),min(100,max(0,quality))))
        except (TypeError,ValueError,KeyError):continue
    rows.sort(key=lambda r:r[0])
    width=1040;height=420
    im=Image.new('RGB',(width,height),'white');d=ImageDraw.Draw(im)
    left=66;right=width-24;top=48;bottom=height-95
    # ASCII text remains readable even on Streamlit Cloud images without CJK fonts.
    d.text((left,9),'PRIMARY RAINBOW  |  Opportunity / Quality',font=_font(21),fill=(32,50,68))
    for v in (0,25,50,75,100):
        y=bottom-round((bottom-top)*v/100)
        d.line((left,y,right,y),fill=(225,232,239),width=1)
        d.text((12,y-9),str(v),font=_font(15),fill=(70,80,90))
    d.line((left,top,left,bottom),fill=(85,95,105),width=2)
    d.line((left,bottom,right,bottom),fill=(85,95,105),width=2)
    if not rows:
        d.text((left+120,top+115),'NO QUALIFYING PERIODS (>30/100)',font=_font(22),fill=(95,105,115))
    else:
        # Only qualifying intervals get categories. The time labels still reveal
        # discontinuities, without plotting false periods or long blank axes.
        n=len(rows)
        slot=(right-left)/n
        for i,(dt,score,quality) in enumerate(rows):
            x1=left+round(i*slot)+1
            x2=left+round((i+1)*slot)-2
            y=bottom-round((bottom-top)*score/100)
            t=quality/100
            col=(round(193-143*t),round(221-124*t),round(245-54*t))
            d.rectangle((x1,y,max(x1+1,x2),bottom-1),fill=col)
        # Label segment boundaries and some interior samples, avoiding overlaps.
        indices={0,n-1}
        indices.update(round(i*(n-1)/max(1,min(6,n-1))) for i in range(min(6,n-1)+1))
        prev=None
        for i in sorted(indices):
            x=left+round((i+.5)*slot)
            if prev is not None and x-prev<90:continue
            d.text((max(left,x-24),bottom+10),rows[i][0].strftime('%H:%M'),font=_font(15),fill=(70,80,90))
            prev=x
        best=max(rows,key=lambda r:r[1])
        d.text((left,bottom+55),f'Peak {best[1]:.0f}/100  |  {best[0]:%H:%M}  |  Quality {best[2]:.0f}/100',font=_font(17),fill=(35,55,75))
    b=BytesIO();im.save(b,format='PNG');return b.getvalue()
