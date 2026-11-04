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

def render(result,width=900,height=480):
    s=summarize(result)
    im=Image.new('RGB',(width,height),(235,245,252));d=ImageDraw.Draw(im)
    # Idealized two concentric semicircular rainbows. Dashed sections denote unknown extent,
    # NOT forecast missing portions. Cloud overlays only denote unverified cloud obstruction.
    cx=width//2; ground=int(height*.78)
    d.rectangle((0,ground,width,height),fill=(206,224,199))
    colors=[(216,48,51),(240,124,43),(251,195,67),(117,184,83),(60,147,201),(96,93,168),(160,91,166)]
    for outer,reverse,alpha in ((int(height*.63),True,.52),(int(height*.51),False,.86)):
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
                bg=(235,245,252)
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

def png_bytes(result):
    b=BytesIO();render(result).save(b,format='PNG');return b.getvalue()
