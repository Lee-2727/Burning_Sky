"""Opt-in satellite screenshot vision helpers for Streamlit/cloud.

OCR locates city label *text*, not exact city marker coordinates. Registration is
validated using holdout residuals; high residuals must be manually corrected.
Image classes are radiance/palette appearance evidence, NOT retrieved cloud
cover or physically calibrated brightness temperatures.
No Tkinter imports and no Open-Meteo calls.
"""
from __future__ import annotations

import csv
import io
import math
import shutil
import subprocess
from collections import Counter
from PIL import Image, ImageEnhance, ImageFilter
from .satellite_calibration import CITIES, _fit_affine

# Selected widely distributed anchor points beyond the default city table.
EXTRA_CITIES = {
    '青岛': (36.0671, 120.3826), '福州': (26.0745,119.2965),
    '石家庄': (38.0428,114.5149), '济南': (36.6512,117.1201),
    '武汉': (30.5928,114.3055), '郑州': (34.7466,113.6254),
    '台北': (25.0330,121.5654), '南平': (26.6418,118.1777),
    '上饶': (28.4541,117.9434), '金华': (29.0781,119.6474),
    '蚌埠': (32.9163,117.3893), '扬州': (32.3932,119.4127),
    '泰州': (32.4558,119.9231), '漳州': (24.5130,117.6473),
}
CITIES.update({k:v for k,v in EXTRA_CITIES.items() if k not in CITIES})


def _city_of(s):
    name=''.join(ch for ch in str(s).strip() if '\u4e00' <= ch <= '\u9fff')
    for suffix in ('自治州','地区','市','县','区'):
        if name.endswith(suffix): name=name[:-len(suffix)]
    return name if name in CITIES else None


def _fit_best(marks):
    """Fit once, drop at most 2 worst OCR outliers when >=5 found.

    Label text center has nonzero offset from true city marker and this method
    reports that uncertainty explicitly. Do not silently assert precision.
    """
    current=list(marks)
    for _ in range(3):
        if len(current)<3: return current,None
        try:
            _,q=_fit_affine([(x['city'],x['x'],x['y']) for x in current])
        except Exception: return current,None
        errors=q['errors_px']
        if len(current)<=4 or max(errors)<21:break
        idx=max(range(len(errors)), key=lambda i: errors[i])
        current.pop(idx)
    try:
        _,q=_fit_affine([(x['city'],x['x'],x['y']) for x in current])
    except Exception:return current,None
    return current,q


def recognize_cities(image:Image.Image):
    """Run ONE tesseract OCR pass per upload, return validated suggestions.

    Requires tesseract + chi_sim data. Missing dependency becomes explicit
    warning; web UI remains usable with manual landmarks.
    """
    if not shutil.which('tesseract'):
        return {'status':'unavailable','message':'服务器未安装Tesseract OCR，仍可手动校准。','landmarks':[]}
    w,h=image.size
    scale=min(2.0, 2200/max(w,1))
    if scale<1: scale=1.0
    view=image.convert('RGB')
    if scale!=1: view=view.resize((round(w*scale),round(h*scale)),Image.Resampling.LANCZOS)
    view=ImageEnhance.Contrast(view).enhance(1.45)
    buf=io.BytesIO();view.save(buf,format='PNG')
    try:
        # stdin/stdout: no temporary screenshot written to persistent web disk.
        p=subprocess.run(['tesseract','stdin','stdout','-l','chi_sim+eng','--psm','11','tsv'],
                         input=buf.getvalue(),capture_output=True,timeout=22,check=False)
    except (OSError,subprocess.TimeoutExpired):
        return {'status':'error','message':'OCR执行超时或失败，可使用手动校准。','landmarks':[]}
    if p.returncode:
        return {'status':'unavailable','message':'OCR中文语言包不可用；请确认tesseract-ocr-chi-sim已安装。','landmarks':[]}
    lines=list(csv.DictReader(io.StringIO(p.stdout.decode('utf-8','replace')),delimiter='\t'))
    picks={}
    for row in lines:
        city=_city_of(row.get('text',''))
        if not city:continue
        try:
            confidence=float(row['conf']); x=float(row['left']);y=float(row['top']);
            tw=float(row['width']);th=float(row['height'])
        except (ValueError,KeyError):continue
        if confidence<40:continue
        px=(x+tw/2)/scale;py=(y+th/2)/scale
        # omit controls at top/bottom, but do not assume every image is a phone screenshot.
        if not (0<=px<w and .025*h<=py<.93*h):continue
        candidate={'city':city,'x':round(px,1),'y':round(py,1),'confidence':round(confidence,1)}
        if city not in picks or confidence>picks[city]['confidence']:picks[city]=candidate
    selected,quality=_fit_best(list(picks.values()))
    reliable=bool(quality and len(selected)>=4 and quality['rms_px']<=18 and quality['max_px']<=30)
    # NOTE: OCR label center is offset from actual geo marker even if RMS small.
    return {'status':'review' if selected else 'no_cities',
            'message':('已自动发现城市标签；请核对图中标记，确认后才能用于云图修正。' if selected
                       else '没有找到足够可信的中文城市地标，请手动标定。'),
            'landmarks':selected,'quality':quality,'registration_candidate':reliable,
            'candidates_total':len(picks)}


def auto_cloud_references(image:Image.Image):
    """Suggest color archetypes from sampled map pixels, not cloud fractions.

    Colored cold enhancement, bright neutral IR and warm/dark neutral areas are
    grouped using *appearance* only. Ignore top/bottom UI bars as best effort;
    users MUST review markers and can click alternatives.
    """
    im=image.convert('RGB');w,h=im.size
    # Map region heuristic; reference points are always reviewable.
    y0=int(h*.17); y1=int(h*.75); x0=int(w*.09);x1=int(w*.91)
    groups={'cold':[],'band':[],'warm':[]}
    step=max(7,int(max(w,h)/175))
    for y in range(y0,y1,step):
        for x in range(x0,x1,step):
            r,g,b=im.getpixel((x,y)); vmax=max(r,g,b);vmin=min(r,g,b)
            saturation=(vmax-vmin)/max(1,vmax)
            # Common Windy IR palette: vivid blue/turquoise cold cloud tops.
            if saturation>.30 and b>r*1.25 and b>g*.97 and b>95:
                groups['cold'].append((x,y,r,g,b))
            elif saturation<.17 and 165<=vmax<=250 and vmax-vmin<30:
                groups['band'].append((x,y,r,g,b))
            elif saturation<.13 and 48<=vmax<=135 and vmax-vmin<20:
                groups['warm'].append((x,y,r,g,b))
    result={}
    for label,items in groups.items():
        if len(items)<12:continue
        # center of thickest local pocket, avoid text/one-pixel noise.
        from collections import defaultdict
        buckets=defaultdict(list)
        for x,y,*rgb in items:
            buckets[(x//55,y//55)].append((x,y))
        ordered=sorted(buckets.values(),key=len,reverse=True)
        found=None
        for group in ordered[:12]:
            x,y=group[len(group)//2]
            if len(group)>2:
                found=(x,y);break
        if found:
            result[label]={'x':int(found[0]),'y':int(found[1]),
                           'sample_pixels':len(items),'type':'satellite_palette_appearance_only'}
    return result
