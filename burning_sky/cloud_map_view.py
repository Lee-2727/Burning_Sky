"""Offline, bounded-support cloud corridor preview. No extra weather requests."""
import math

def cloud_overlay(records, bounds, width, height, layer='综合', support_km=30):
    """RGBA overlay; transparent outside observed corridor. Never extrapolate globally."""
    from PIL import Image
    width=max(1,int(width));height=max(1,int(height))
    keys={'低云':['cloud_cover_low'],'中云':['cloud_cover_mid'],'高云':['cloud_cover_high'],
          '综合':['cloud_cover_low','cloud_cover_mid','cloud_cover_high']}
    active=keys.get(layer,keys['综合'])
    valid=[]
    for rec in records:
        lat,lon=rec.get('lat'),rec.get('lon')
        if not isinstance(lat,(float,int)) or not isinstance(lon,(float,int)):continue
        vals=[rec.get(k) for k in active]
        if not any(isinstance(v,(int,float)) and math.isfinite(v) for v in vals):continue
        valid.append((float(lat),float(lon),[max(0,min(100,float(v))) if isinstance(v,(int,float)) and math.isfinite(v) else 0 for v in vals]))
    image=Image.new('RGBA',(width,height),(0,0,0,0))
    if not valid:return image
    lo_lat,hi_lat,lo_lon,hi_lon=bounds
    coslat=max(.15,math.cos(math.radians((lo_lat+hi_lat)/2)))
    # Render at capped resolution for responsive Tk window resizing.
    factor=max(1,math.ceil(max(width/480,height/360)))
    sw=max(1,math.ceil(width/factor));sh=max(1,math.ceil(height/factor))
    pixels=[]
    palette={'cloud_cover_low':(47,138,220),'cloud_cover_mid':(151,92,198),'cloud_cover_high':(241,157,66)}
    for py in range(sh):
        lat=hi_lat-(py+.5)/sh*(hi_lat-lo_lat)
        for px in range(sw):
            lon=lo_lon+(px+.5)/sw*(hi_lon-lo_lon)
            weighted=[0.0]*len(active);total=0.0;nearest=1e9
            for slat,slon,values in valid:
                dist=111.2*math.hypot(lat-slat,(lon-slon)*coslat)
                nearest=min(nearest,dist)
                if dist>support_km:continue
                weight=math.exp(-.5*(dist/12.0)**2)
                total+=weight
                for j,v in enumerate(values):weighted[j]+=v*weight
            if total<.0001 or nearest>support_km:
                pixels.append((0,0,0,0));continue
            means=[v/total for v in weighted]
            # Combine layer opacity with cloud amount; use weighted color for mixed clouds.
            amount=100*(1-math.prod(1-v/100 for v in means))
            if amount<2:pixels.append((0,0,0,0));continue
            mix=sum(means)
            rgb=[round(sum(palette[k][c]*v for k,v in zip(active,means))/mix) for c in range(3)]
            fade=max(0.0,min(1.0,(support_km-nearest)/9.0))
            alpha=round(min(220,amount*2.15)*fade)
            pixels.append((*rgb,alpha))
    small=Image.new('RGBA',(sw,sh));small.putdata(pixels)
    return small.resize((width,height),Image.Resampling.BILINEAR)
