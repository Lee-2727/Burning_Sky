"""Two-layer strict light window and >=5% main-colored-area timeline.

The low-opacity light window is a categorical time band, NOT a percent.
The foreground is the engine's strictly gated colored-cloud area proxy.
No interpolation across unsupported minutes. Historical caches without the
per-minute area field must be recomputed rather than recolored artificially.
"""

def draw_dynamic_color_curve(samples, peak=None, direction_key="evening"):
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1400, 530
    img = Image.new('RGB', (W, H), '#ffffff')
    d = ImageDraw.Draw(img)
    # Prefer CJK glyphs for labels on supported systems; preserve fallback.
    from pathlib import Path
    font = small = None
    for face in ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                 '/usr/share/fonts/truetype/arphic/uming.ttc',
                 'C:/Windows/Fonts/msyh.ttc',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(face).exists():
            try:
                font = ImageFont.truetype(face, 17)
                small = ImageFont.truetype(face, 14)
                break
            except Exception:
                pass
    if font is None:
        font = small = ImageFont.load_default()
    left, right, top, bottom = 78, W-30, 42, 385
    def x(i): return left+(right-left)*i/max(1,len(samples)-1)
    def y(v): return bottom-(bottom-top)*max(0.,min(100.,float(v)))/100.
    def rgb(point):
        c=point.get('color') or (190,125,105)
        if not isinstance(c,(tuple,list)) or len(c)<3: c=(190,125,105)
        return tuple(max(0,min(255,int(k))) for k in c[:3])
    def lighten(c,opacity=0.20):
        return tuple(round(255*(1-opacity)+v*opacity) for v in c)
    def available(point):
        return 'colored_cloud_area_pct' in point and 'strict_light_window' in point
    def active(point): return bool(point.get('strict_light_window')) if available(point) else False
    def major(point): return bool(point.get('major_coloration_valid')) if available(point) else False
    d.text((left,12),'有效染色云面积占比 %',font=font,fill=(42,52,69))
    for v in (0,5,20,40,60,80,100):
        yy=y(v); d.line((left,yy,right,yy),fill=(222,228,238),width=1)
        d.text((left-12,yy-8),str(v),font=small,fill=(78,91,111),anchor='ra')
    d.line((left,top,left,bottom),fill=(110,120,139),width=2)
    d.line((left,bottom,right,bottom),fill=(110,120,139),width=2)
    d.line((left,y(5),right,y(5)),fill=(183,113,106),width=2)
    d.text((right-6,y(5)-20),'主要染色门槛 5%',font=small,fill=(145,86,92),anchor='ra')
    if samples:
        step=(right-left)/max(1,len(samples)-1)
        # Draw every strict witness as a background band. A clear path is
        # a Boolean state, never presented as 100% illuminated area.
        for i,p in enumerate(samples):
            if active(p):
                col=lighten(rgb(p),0.13)
                x0=max(left,x(i)-step*0.5); x1=min(right,x(i)+step*0.5)
                d.rectangle((x0,top,x1,bottom),fill=col)
        # Foreground: strict light AND area >= 5%, with each minute's RGB.
        for i,p in enumerate(samples):
            if major(p):
                x0=max(left,x(i)-step*0.5); x1=min(right,x(i)+step*0.5)
                d.rectangle((x0,y(p['colored_cloud_area_pct']),x1,bottom),fill=rgb(p))
        # Segments connect only adjacent foreground minutes, never across gaps.
        for i in range(len(samples)-1):
            a,b=samples[i],samples[i+1]
            if major(a) and major(b):
                d.line((x(i),y(a['colored_cloud_area_pct']),x(i+1),y(b['colored_cloud_area_pct'])),fill=rgb(a),width=4)
        event_i=min(range(len(samples)),key=lambda i:abs(float(samples[i].get('minutes_from_event',999))))
        ex=x(event_i); d.line((ex,top,ex,bottom),fill=(100,116,139),width=1)
        for t in range(7):
            i=round((len(samples)-1)*t/6)
            xx=x(i);d.line((xx,bottom,xx,bottom+7),fill=(110,120,139),width=1)
            dt=samples[i].get('datetime')
            txt=dt.strftime('%H:%M') if hasattr(dt,'strftime') else str(samples[i].get('minutes_from_event',''))
            if i==event_i: txt+=' 日出' if direction_key=='morning' else ' 日落'
            d.text((xx,bottom+10),txt,font=small,fill=(51,65,85),anchor='ma')
        valid=[i for i,p in enumerate(samples) if major(p)]
        if valid:
            i=max(valid,key=lambda k:samples[k]['colored_cloud_area_pct'])
            xx,yy=x(i),y(samples[i]['colored_cloud_area_pct'])
            d.ellipse((xx-6,yy-6,xx+6,yy+6),fill=(255,255,255),outline=rgb(samples[i]),width=3)
        if not any(available(p) for p in samples):
            d.text(((left+right)/2,top+70),'旧缓存缺少逐分钟面积数据，请重新计算以显示双层时序',font=font,fill=(108,114,130),anchor='ma')
    else:
        d.text(((left+right)/2,(top+bottom)/2),'当前没有云色时序数据',font=font,fill=(108,114,130),anchor='ma')
    # Legend uses separate semantics: band = strict illumination available,
    # actual colored fill = measured proxy above display threshold.
    d.rectangle((left,451,left+28,466),fill=(242,221,225))
    d.text((left+38,451),'浅色背景：严格有效光路窗口（不代表面积100%）',font=font,fill=(70,80,96))
    d.rectangle((left+720,451,left+748,466),fill=(218,84,110))
    d.text((left+758,451),'彩色前景：染色云面积 ≥ 5%',font=font,fill=(70,80,96))
    d.text((left,490),'色彩为逐分钟模型估计色相；阴影断开处不进行插值。纵轴是沿采样剖面的云面积代理，并非全天空实测面积。',font=small,fill=(93,104,122))
    return img
