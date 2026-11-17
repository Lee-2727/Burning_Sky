"""Web visualization of saved 2D cloud sampling, without new weather requests."""
import io, math

def render_cloud_2d(result,layer='综合'):
    raw=result.get('raw_details') or []
    records=[r for group in raw if isinstance(group,dict) for r in (group.get('records') or [])
             if isinstance(r,dict) and isinstance(r.get('lat'),(int,float)) and isinstance(r.get('lon'),(int,float))]
    if not records:
        return None,'当前案例未保存二维空间采样经纬度及云量；无法从一维剖面可靠重建二维云图。需要重新联网查询并保存完整案例。'
    from PIL import Image,ImageDraw
    from burning_sky.cloud_map_view import cloud_overlay
    lats=[float(r['lat']) for r in records];lons=[float(r['lon']) for r in records]
    latpad=max(.12,(max(lats)-min(lats))*.08)
    lonpad=max(.12,(max(lons)-min(lons))*.08)
    bounds=(min(lats)-latpad,max(lats)+latpad,min(lons)-lonpad,max(lons)+lonpad)
    w,h=900,540
    image=Image.new('RGBA',(w,h),(240,246,250,255))
    d=ImageDraw.Draw(image)
    for i in range(1,6):
        x=round(w*i/6);y=round(h*i/6)
        d.line((x,0,x,h),fill=(206,218,229,255),width=1)
        d.line((0,y,w,y),fill=(206,218,229,255),width=1)
    image.alpha_composite(cloud_overlay(records,bounds,w,h,layer=layer,support_km=30))
    d=ImageDraw.Draw(image)
    origin=result.get('origin')
    if isinstance(origin,(tuple,list)) and len(origin)>=2:
        lat,lon=origin[:2]
        if isinstance(lat,(int,float)) and isinstance(lon,(int,float)):
            x=int((lon-bounds[2])/(bounds[3]-bounds[2])*w)
            y=int((bounds[1]-lat)/(bounds[1]-bounds[0])*h)
            d.ellipse((x-7,y-7,x+7,y+7),fill=(235,70,70,255),outline='white',width=2)
    stream=io.BytesIO();image.convert('RGB').save(stream,format='PNG')
    return stream.getvalue(),'蓝色低云、紫色中云、橙色高云；不透明度反映云量。仅对已采样走廊周围30 km插值，其他区域无观测支持；不是卫星云图，也不是独立光路验证。未新增API请求。'
