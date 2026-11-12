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
    """Recognize OCR labels; never invent an unseen city from a fuzzy match."""
    import unicodedata
    import difflib
    name=unicodedata.normalize('NFKC',str(s or '')).strip()
    name=name.replace('巿','市').replace('柿','市').replace('巾','市')
    name=''.join(ch for ch in name if '\u4e00' <= ch <= '\u9fff')
    for suffix in ('壮族自治区','回族自治区','自治州','地区','自治县','市','县'):
        if name.endswith(suffix):name=name[:-len(suffix)]
    if name in CITIES:return name, 'exact'
    # OCR often replaces one glyph in a 3+ CJK-character label. This is only
    # admitted if unique and later confirmed by map geometry.
    if len(name)>=3:
        options=[city for city in CITIES if len(city)>=3 and abs(len(city)-len(name))<=1]
        near=difflib.get_close_matches(name,options,n=2,cutoff=.78)
        if len(near)==1:return near[0], 'fuzzy'
    return None, None


def _ocr_variant(image,kind):
    view=image.convert('RGB')
    w,h=view.size
    scale=min(2.4,max(1.0,2100/max(w,1)))
    if scale!=1:view=view.resize((round(w*scale),round(h*scale)),Image.Resampling.LANCZOS)
    if kind=='contrast':
        view=ImageEnhance.Contrast(view).enhance(1.6)
    else:
        # A second image variant recovers pale labels on busy cloud backgrounds.
        view=ImageEnhance.Sharpness(view).enhance(1.5)
        view=ImageEnhance.Contrast(view.convert('L')).enhance(2.0)
    return view,scale


def _read_ocr(image,kind):
    view,scale=_ocr_variant(image,kind)
    data=io.BytesIO();view.save(data,format='PNG')
    try:
        p=subprocess.run(['tesseract','stdin','stdout','-l','chi_sim+eng',
                          '--psm','11','tsv'],input=data.getvalue(),
                         capture_output=True,timeout=24,check=False)
    except (OSError,subprocess.TimeoutExpired):return [],'执行超时'
    if p.returncode:return [],'中文OCR语言包不可用'
    rows=list(csv.DictReader(io.StringIO(p.stdout.decode('utf-8','replace')),delimiter='\t'))
    # Group neighbouring OCR tokens that belong to one line, restoring
    # split labels like "桂林" + "市" and "广" + "州" + "市".
    units=[]
    for r in rows:
        try:
            if float(r['conf'])<15:continue
            x,y,ww,hh=(float(r[q])/scale for q in ('left','top','width','height'))
            units.append({'text':r['text'],'x':x,'y':y,'w':ww,'h':hh,
                          'conf':float(r['conf']), 'line':(r['block_num'],r['par_num'],r['line_num'])})
        except (KeyError,ValueError):continue
    variants=[]
    for u in units:
        variants.append(u)
    byline={}
    for u in units:byline.setdefault(u['line'],[]).append(u)
    for group in byline.values():
        group.sort(key=lambda q:q['x'])
        for i in range(len(group)):
            for n in (2,3):
                part=group[i:i+n]
                if len(part)!=n:continue
                if any((part[j+1]['x']-(part[j]['x']+part[j]['w']))>max(15,part[j]['h']*.9)
                       for j in range(n-1)):continue
                variants.append({'text':''.join(u['text'] for u in part),
                                 'x':part[0]['x'],'y':min(u['y'] for u in part),
                                 'w':part[-1]['x']+part[-1]['w']-part[0]['x'],
                                 'h':max(u['y']+u['h'] for u in part)-min(u['y'] for u in part),
                                 'conf':min(u['conf'] for u in part)})
    return variants,None


def _geometric_consensus(candidates, image_size):
    """Spatial consistency filtering with affine pixel-vs-Mercator geometry.

    Exhaustively seed three geographically separated landmarks (bounded to a
    small top-confidence set) and keep inliers with <= adaptive residual.
    Requires at least four inliers for automatic acceptance, while three may
    still be displayed as an unconfirmed draft for a one-tap correction.
    """
    from .satellite_calibration import _merc
    from itertools import combinations
    pts=list(candidates)
    if len(pts)<3:return pts,None
    cap=min(15,len(pts))
    pool=sorted(pts,key=lambda m:m['confidence'],reverse=True)[:cap]
    best=None
    threshold=max(24, min(image_size)*.032)
    for trio in combinations(pool,3):
        try:
            tf,q=_fit_affine([(p['city'],p['x'],p['y']) for p in trio])
        except Exception:continue
        # Three almost collinear points give unstable affine extrapolation.
        x1,y1=trio[0]['x'],trio[0]['y']
        x2,y2=trio[1]['x'],trio[1]['y']
        x3,y3=trio[2]['x'],trio[2]['y']
        area=abs((x2-x1)*(y3-y1)-(x3-x1)*(y2-y1))/2
        if area<image_size[0]*image_size[1]*.002:continue
        inliers=[]
        for p in pts:
            px,py=tf(*CITIES[p['city']])
            if math.hypot(px-p['x'],py-p['y'])<=threshold:inliers.append(p)
        # Fitted residual is NOT independent validation, just geometric agreement.
        if len(inliers)<3:continue
        quality=sum(p['confidence'] for p in inliers)
        value=(len(inliers),round(quality,1))
        if best is None or value>best[0]:best=(value,inliers)
    if best:
        current=best[1]
        try:
            _,q=_fit_affine([(p['city'],p['x'],p['y']) for p in current])
        except Exception:q=None
        return current,q
    return [],None


def recognize_cities(image:Image.Image):
    """OCR + spatial consistency; output always needs user visual review."""
    if not shutil.which('tesseract'):
        return {'status':'unavailable','message':'服务器未安装Tesseract OCR，仍可手动校准。','landmarks':[]}
    w,h=image.size
    picks={}
    errors=[]
    for kind in ('contrast','grayscale'):
        rows,error=_read_ocr(image,kind)
        if error:errors.append(error)
        for r in rows:
            city,match=_city_of(r.get('text'))
            if not city:continue
            x=r['x']+r['w']/2; y=r['y']+r['h']/2
            if not (.01*w<x<.99*w and .10*h<y<.90*h):continue
            conf=float(r['conf'])
            if match=='fuzzy':conf-=17
            if conf<25:continue
            cand={'city':city,'x':round(x,1),'y':round(y,1),
                  'confidence':round(conf,1),'recognition':match}
            if city not in picks or conf>picks[city]['confidence']:
                picks[city]=cand
        # Most screenshots succeed in first pass; avoid duplicate OCR cost.
        if len(picks)>=6:break
    selected,quality=_geometric_consensus(list(picks.values()),(w,h))
    if not selected and len(picks)<4:
        selected,quality=_fit_best(list(picks.values()))
    reliable=bool(quality and len(selected)>=4 and
                  quality['rms_px']<=max(18,min(w,h)*.025) and
                  quality['max_px']<=max(30,min(w,h)*.045))
    message=('已识别城市并通过空间位置一致性检查，仍请检查预览标记。'
             if reliable else '已找到一些城市文字，但自动地理配准尚未可靠通过；请核对或补充地标。')
    return {'status':'review' if selected else 'no_cities',
            'message':message if selected else ('OCR地标不足或空间关系冲突；请人工补充。 '+','.join(errors)),
            'landmarks':selected,'quality':quality,'registration_candidate':reliable,
            'candidates_total':len(picks),
            'all_candidate_cities':[v['city'] for v in picks.values()]}


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
