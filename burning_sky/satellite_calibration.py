"""Desktop satellite infrared screenshot comparison for an EXISTING single result.

This is NOT a satellite retrieval of cloud-cover percentages. It uses screenshot
landmarks and two IR exemplars to propose discrepancies, then sends human-approved
spatial edits to the existing single-result forecasting pipeline.
"""
import math
from dataclasses import dataclass
from PIL import Image
from .spherical_raytrace import destination

# City coordinates are only navigation control points, not a dated forecast.
CITIES = {
    '上海': (31.2304, 121.4737), '南京': (32.0603, 118.7969),
    '杭州': (30.2741, 120.1551), '合肥': (31.8206, 117.2272),
    '南通': (31.9802, 120.8943), '盐城': (33.3474, 120.1636),
    '淮安': (33.5515, 119.1132), '徐州': (34.2618, 117.1859),
    '铜陵': (30.9454, 117.8121), '九江': (29.7051, 116.0019),
    '宁波': (29.8683, 121.5440), '苏州': (31.2989, 120.5853),
    '无锡': (31.4912, 120.3119), '连云港': (34.5966, 119.2216),
    '温州': (27.9938, 120.6994), '济宁': (35.4146, 116.5872),
    '芜湖': (31.3525, 118.4331), '南昌': (28.6820, 115.8582),
    '黄山': (29.7147, 118.3376), '阜阳': (32.8900, 115.8142),
    # Yangtze River Delta: full Shanghai + Jiangsu(13), Zhejiang(11), Anhui(16) coverage.
    '宿迁': (33.9631, 118.2752),
    '湖州': (30.8943, 120.0868), '衢州': (28.9701, 118.8595),
    '舟山': (29.9853, 122.2072), '丽水': (28.4517, 119.9177),
    '淮南': (32.6255, 116.9999), '马鞍山': (31.6705, 118.5060),
    '淮北': (33.9558, 116.7983), '滁州': (32.3018, 118.3163),
    '亳州': (33.8463, 115.7793), '池州': (30.6647, 117.4916),
    '宣城': (30.9407, 118.7588),
    '常州': (31.8107, 119.9741), '镇江': (32.1878, 119.4250),
    '嘉兴': (30.7461, 120.7555), '绍兴': (30.0303, 120.5802),
    '台州': (28.6561, 121.4208),
    '安庆': (30.5435, 117.0636), '宿州': (33.6464, 116.9639),
    '六安': (31.7353, 116.5219),
}

def _merc(lat, lon):
    lat=max(-85., min(85.,float(lat)))
    return (math.radians(float(lon)), math.log(math.tan(math.pi/4+math.radians(lat)/2)))


def _solve3(matrix, target):
    a=[list(map(float,row))+[float(v)] for row,v in zip(matrix,target)]
    for k in range(3):
        i=max(range(k,3),key=lambda j: abs(a[j][k]))
        a[k],a[i]=a[i],a[k]
        if abs(a[k][k])<1e-11: raise ValueError('校准点过于接近或共线，请选择跨度更大的城市')
        div=a[k][k]
        a[k]=[v/div for v in a[k]]
        for j in range(3):
            if j==k:continue
            f=a[j][k];a[j]=[u-f*v for u,v in zip(a[j],a[k])]
    return [a[i][3] for i in range(3)]


def _fit_affine(landmarks):
    """Least-squares Mercator registration; report reprojection errors.

    The 3-point solution can fit bad clicks exactly, so >=4 widely separated
    landmarks is strongly recommended. This model assumes an unrotated or
    affine-mapped Web Mercator screenshot, not an arbitrary perspective globe.
    """
    if len(landmarks)<3: raise ValueError('至少需要3个城市地标，推荐4～6个')
    xy=[_merc(*CITIES[name]) for name,_,_ in landmarks]
    center_x=sum(t[0] for t in xy)/len(xy)
    center_y=sum(t[1] for t in xy)/len(xy)
    # Relative Mercator coordinates in degrees improve conditioning.
    rows=[[math.degrees(x-center_x),math.degrees(y-center_y),1.] for x,y in xy]
    def least(values, weights):
        mat=[[sum(w*r[i]*r[j] for r,w in zip(rows,weights)) for j in range(3)] for i in range(3)]
        vec=[sum(w*r[i]*v for r,w,v in zip(rows,weights,values)) for i in range(3)]
        return _solve3(mat,vec)
    vx=[float(x) for _,x,_ in landmarks];vy=[float(y) for _,_,y in landmarks]
    weights=[1.]*len(rows)
    for _ in range(4 if len(rows)>=5 else 1):
        ax=least(vx,weights);ay=least(vy,weights)
        errors=[math.hypot(sum(ax[j]*r[j] for j in range(3))-x,
                           sum(ay[j]*r[j] for j in range(3))-y)
                for r,x,y in zip(rows,vx,vy)]
        if len(rows)>=5:
            vals=sorted(errors)
            scale=max(3., vals[len(vals)//2]*1.5)
            weights=[min(1.,scale/max(e,1e-6)) for e in errors]
    rms=math.sqrt(sum(e*e for e in errors)/len(errors))
    def transform(lat,lon):
        x,y=_merc(lat,lon)
        r=[math.degrees(x-center_x),math.degrees(y-center_y),1.]
        return sum(ax[j]*r[j] for j in range(3)),sum(ay[j]*r[j] for j in range(3))
    return transform, {'rms_px':round(rms,2),'max_px':round(max(errors),2),
                       'points':len(landmarks),'errors_px':[round(e,1) for e in errors]}


def affine_from_landmarks(landmarks):
    transform,quality=_fit_affine(landmarks)
    if len(landmarks)>=4 and quality['rms_px']>18:
        raise ValueError('地理配准残差 %.1f 像素，过大；请检查选点，尽量选择不同方位的城市。'%quality['rms_px'])
    return transform


def _rgb_to_lab(rgb):
    """Approximate sRGB CIELAB (D65), for palette distances, not IR physics."""
    vals=[]
    for c in rgb[:3]:
        c=float(c)/255.
        vals.append(c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4)
    r,g,b=vals
    X=(.4124564*r+.3575761*g+.1804375*b)/.95047
    Y=(.2126729*r+.7151522*g+.0721750*b)
    Z=(.0193339*r+.1191920*g+.9503041*b)/1.08883
    def f(t):return t**(1/3) if t>.008856 else 7.787*t+16/116
    fx,fy,fz=f(X),f(Y),f(Z)
    return (116*fy-16,500*(fx-fy),200*(fy-fz))


def _avg_patch(im,x,y,r=7):
    """Robust patch median; do not classify a single pixel."""
    w,h=im.size;x=int(round(x));y=int(round(y))
    if x-r<0 or y-r<0 or x+r>=w or y+r>=h:return None
    channels=[[],[],[]]
    for yy in range(y-r,y+r+1,2):
        for xx in range(x-r,x+r+1,2):
            rgb=im.getpixel((xx,yy))[:3]
            for j in range(3):channels[j].append(rgb[j])
    for c in channels:c.sort()
    return tuple(c[len(c)//2] for c in channels)


def _diff(a,b):
    aa=_rgb_to_lab(a); bb=_rgb_to_lab(b)
    return math.sqrt(sum((u-v)**2 for u,v in zip(aa,bb)))


def _patch_evidence(im,x,y,exemplars,radius=10):
    """Classify a local area, vote across pixels in perceptual LAB space.

    Purity and image heterogeneity are returned to prevent false precision.
    Reference classes are *visual classes*, not observed cloud percentages.
    """
    w,h=im.size;x=int(round(x));y=int(round(y))
    if x-radius<0 or y-radius<0 or x+radius>=w or y+radius>=h:return None
    votes={name:0 for name,_,_ in exemplars}
    closeness=[];pixels=[]
    refs=[(name,_rgb_to_lab(rgb),level) for name,rgb,level in exemplars]
    for yy in range(y-radius,y+radius+1,3):
        for xx in range(x-radius,x+radius+1,3):
            rgb=im.getpixel((xx,yy))[:3]
            lab=_rgb_to_lab(rgb)
            ds=sorted((math.dist(lab,ref),name) for name,ref,_ in refs)
            votes[ds[0][1]]+=1
            closeness.append((ds[1][0]-ds[0][0])/max(1.,ds[1][0]))
            pixels.append(rgb)
    ordered=sorted(votes.items(),key=lambda it:it[1],reverse=True)
    name,count=ordered[0]
    purity=count/max(1,len(pixels))
    avg_margin=sum(closeness)/max(1,len(closeness))
    confidence=max(0.,min(1.,purity*.65+max(0,avg_margin)*.35))
    # Probabilistic votes are NOT cloud fraction; these are just color-class coverage.
    return {'classification':name,'color_purity':round(purity,3),
            'confidence':round(confidence,3),'votes':votes,
            'patch_pixels':len(pixels),'margin':round(avg_margin,3)}


def compare_samples(im, landmarks, cloud_rgb, warm_rgb, result, band_rgb=None):
    """Compare CURRENT forecast samples with screenshot appearance. No IR->cloud % inversion."""
    transform,registration=_fit_affine(landmarks)
    if len(landmarks)>=4 and registration['rms_px']>18:
        raise ValueError('地图配准误差 %.1f px 过大，请重新点击地标。'%registration['rms_px'])
    origin=result.get('origin')
    if not isinstance(origin,(list,tuple)) or len(origin)<2:
        raise ValueError('单次结果缺少观测坐标')
    az=float(result.get('azimuth') or 0)
    original_samples=sorted([v for v in (result.get('samples') or []) if isinstance(v.get('distance_km'),(int,float))],key=lambda v:v['distance_km'])
    if not original_samples:return []
    # Dense PHOTO inspection only; API calls occur after the user approves edits.
    samples=[]
    for i,sm in enumerate(original_samples):
        samples.append(sm)
        if i+1>=len(original_samples):continue
        other=original_samples[i+1]
        gap=float(other['distance_km'])-float(sm['distance_km'])
        segments=max(1,math.ceil(gap/10.))
        for n in range(1,segments):
            f=n/segments
            derived={'distance_km':float(sm['distance_km'])+f*gap,'satellite_interpolated_prior':True}
            for field in ('cloud_cover_high','cloud_cover_mid'):
                a=sm.get(field);b=other.get(field)
                derived[field]=(float(a)+(float(b)-float(a))*f) if isinstance(a,(int,float)) and isinstance(b,(int,float)) else None
            samples.append(derived)
    samples.sort(key=lambda v:v['distance_km'])
    output=[]
    exemplars=[('厚云候选',cloud_rgb,90.),('少云候选',warm_rgb,10.)]
    if band_rgb is not None:
        exemplars.insert(1,('云带候选',band_rgb,55.))
    if min(_diff(a[1],b[1]) for i,a in enumerate(exemplars) for b in exemplars[i+1:])<10:
        raise ValueError('三类参考区域颜色太相近，无法可靠区分；请重新选择有代表性的厚云、云带和少云区域。')
    for sm in samples:
        dist=sm.get('distance_km')
        if not isinstance(dist,(float,int)):continue
        lat,lon=destination(float(origin[0]),float(origin[1]),az,float(dist))
        x,y=transform(lat,lon)
        proof=_patch_evidence(im,x,y,exemplars,radius=10)
        if proof is None:continue
        classification=proof['classification'];confidence=proof['confidence']
        mid=sm.get('cloud_cover_mid');high=sm.get('cloud_cover_high')
        source=max(float(v) for v in (mid,high) if isinstance(v,(int,float))) if any(isinstance(v,(int,float)) for v in (mid,high)) else None
        # A screenshot does not quantify cloud amount. Preserve forecast value
        # and offer only a bounded, editable shift proportional to evidence.
        prior=float(high) if isinstance(high,(float,int)) else None
        level=next(v for label,_,v in exemplars if label==classification)
        suggested=round(prior+(level-prior)*min(.55,max(0.,confidence-.48)*1.7),1) if prior is not None else None
        if len(landmarks)==3:
            confidence*=.85  # registration cannot be validated from exactly 3 points
        if confidence<.62:
            judgement='不确定（混合像素或参考类别重叠）'
        elif source is None:
            judgement='缺少原始中高云量'
        elif classification=='厚云候选' and source<=40:
            judgement='疑似新增云层（原预报少云）'
        elif classification=='少云候选' and source>=60:
            judgement='疑似新增云洞（不能排除暖低云）'
        elif classification=='云带候选' and abs(source-level)>=35:
            judgement='疑似云带偏差（需核对云层高度）'
        else:
            judgement='暂无明显矛盾'
        output.append({'distance_km':float(dist),'lat':lat,'lon':lon,'x':x,'y':y,
                       'predicted_mid_high':source,'original_high':high,
                       'classification':classification,'confidence':round(confidence,3),
                       'class_purity':proof['color_purity'],'color_votes':proof['votes'],
                       'suggested_high':suggested,'reference_level':level,
                       'registration_rms_px':registration['rms_px'],
                       'satellite_interpolated_prior':bool(sm.get('satellite_interpolated_prior')),
                       'segment_half_width_km':4.8,
                       'judgement':judgement})
    return output


def launch(parent, result, direction, on_apply):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from PIL import ImageTk
    if not result:messagebox.showinfo('卫星校准','请先完成当前方向单次查询。');return
    if not result.get('samples'):messagebox.showinfo('卫星校准','当前结果缺少距离采样。');return
    win=tk.Toplevel(parent)
    direction_label='朝霞' if direction=='morning' else '晚霞'
    model=result.get('query_model_label') or result.get('query_model_id') or '未知模式'
    event=result.get('event_dt')
    event_date=str(event)[:10]
    event_hour=str(event)[11:16] if event else '18:00'
    win.title(f'卫星云图实况校准 · {direction_label} · {model} · {event}')
    win.geometry('1100x860')
    win.columnconfigure(0,weight=1);win.rowconfigure(1,weight=1)
    description=('当前绑定：'+direction_label+' | '+str(model)+' | '+str(event)+' | 太阳方位 %.1f°\n'%float(result.get('azimuth') or 0)
                 +'红外卫星截图不能直接反演三层云量；建议4～6个地标（至少3个）+ 厚云、云带、少云各一个参考区。'
                 +'自动发现的是差异候选，勾选确认后才写入高云量修正。')
    ttk.Label(win,text=description,wraplength=1030,justify='left').grid(row=0,column=0,sticky='ew',padx=8,pady=8)
    content=ttk.PanedWindow(win,orient='horizontal');content.grid(row=1,column=0,sticky='nsew',padx=8,pady=5)
    left=ttk.Frame(content);right=ttk.Frame(content);content.add(left,weight=3);content.add(right,weight=2)
    image_canvas=tk.Canvas(left,bg='#1f2937',highlightthickness=0)
    image_canvas.pack(fill='both',expand=True)
    status=tk.StringVar(value='请先打开卫星截图。')
    ttk.Label(win,textvariable=status,wraplength=1040).grid(row=2,column=0,sticky='ew',padx=10,pady=5)
    state={'image':None,'photo':None,'scale':1.,'landmarks':[],'cold':None,'warm':None,'band':None,'rows':[],'checks':[],'mode':'landmark'}
    ttk.Label(right,text='卫星观测时刻（北京时间；日期继承当前预测，可修改）').pack(anchor='w')
    observed_at=tk.StringVar(value=f'{event_date} {event_hour}')
    ttk.Entry(right,textvariable=observed_at).pack(fill='x',pady=(2,6))
    ttk.Button(right,text='1. 打开卫星截图',command=lambda:open_image()).pack(fill='x',pady=4)
    ttk.Label(right,text='2. 选择4～6个地标，在图中点击城市中心').pack(anchor='w')
    landmark_name=tk.StringVar(value='上海')
    ttk.Combobox(right,textvariable=landmark_name,values=list(CITIES),state='readonly').pack(fill='x',pady=4)
    ttk.Button(right,text='点击图片：添加当前地标',command=lambda:set_mode('landmark')).pack(fill='x')
    ttk.Button(right,text='撤销最后一个地标',command=lambda:undo()).pack(fill='x',pady=3)
    ttk.Button(right,text='3. 点击图片：厚云参考点（冷云顶）',command=lambda:set_mode('cold')).pack(fill='x',pady=3)
    ttk.Button(right,text='4. 点击图片：灰白云带参考点',command=lambda:set_mode('band')).pack(fill='x',pady=3)
    ttk.Button(right,text='5. 点击图片：少云参考点（较暖背景）',command=lambda:set_mode('warm')).pack(fill='x',pady=3)
    ttk.Button(right,text='6. 分析与原预报的云洞差异',command=lambda:analyze()).pack(fill='x',pady=5)
    result_list=tk.Text(right,height=16,width=46,wrap='word')
    result_list.pack(fill='both',expand=True,pady=5)
    ttk.Button(right,text='7. 审核并确认：自动重算当前方向',command=lambda:confirm()).pack(fill='x',pady=5)
    ttk.Label(right,text='确认后自动重算；缺失请求及新增采样点会联网补采。使用CIELAB颜色距离和局部像素统计，结合原高云量保守建议；不是实测值；少云区仍可能有低云。确认后才会自动重算。',wraplength=345).pack(anchor='w')

    def set_mode(m):
        state['mode']=m
        status.set({'landmark':'请在地图中点击“'+landmark_name.get()+'”中心（3个城市各点一次）。',
                    'cold':'请选择明显冷云顶/厚云区，避开文字和UI。',
                    'band':'请选择亮灰白色、纹理明显的云带内部。',
                    'warm':'请选择较均匀偏暖、疑似少云的背景区（不能保证晴空）。'}[m])
    def open_image():
        path=filedialog.askopenfilename(parent=win,filetypes=[('卫星截图','*.png *.jpg *.jpeg *.webp'),('全部文件','*.*')])
        if not path:return
        try:
            state['image']=Image.open(path).convert('RGB')
            state['landmarks']=[];state['cold']=None;state['warm']=None;state['band']=None
            state['rows']=[];state['mode']='landmark';state['path']=path
            redraw();status.set('已读取截图；请选择4～6个分布较广的城市地标（至少3个）。')
        except Exception as exc:messagebox.showerror('图片读取失败',str(exc),parent=win)
    def redraw():
        im=state.get('image');image_canvas.delete('all')
        if im is None:return
        w=max(360,image_canvas.winfo_width());h=max(440,image_canvas.winfo_height())
        scale=min((w-12)/im.width,(h-12)/im.height)
        state['scale']=scale;sw=max(1,int(im.width*scale));sh=max(1,int(im.height*scale))
        state['photo']=ImageTk.PhotoImage(im.resize((sw,sh),Image.Resampling.LANCZOS))
        image_canvas.create_image(0,0,image=state['photo'],anchor='nw')
        for name,x,y in state['landmarks']:
            px=x*scale;py=y*scale
            image_canvas.create_oval(px-5,py-5,px+5,py+5,outline='#ffff00',width=2)
            image_canvas.create_text(px+5,py-6,text=name,fill='#ffff00',anchor='sw')
        for label,key,color in [('厚云','cold','#ff5555'),('云带','band','#e4c76f'),('少云','warm','#5af5ff')]:
            pt=state.get(key+'pos')
            if pt:
                px,py=pt[0]*scale,pt[1]*scale
                image_canvas.create_oval(px-6,py-6,px+6,py+6,outline=color,width=2)
                image_canvas.create_text(px+10,py-8,text=label,fill=color,anchor='sw')
        for row in state['rows']:
            px=row['x']*scale;py=row['y']*scale
            color='#ff5b49' if '新增云层' in row['judgement'] else '#55e0ff' if '新增云洞' in row['judgement'] else '#e4c76f' if '云带偏差' in row['judgement'] else '#e9e8d3'
            image_canvas.create_oval(px-3,py-3,px+3,py+3,fill=color,outline='black')
    def undo():
        if state['landmarks']:state['landmarks'].pop();redraw()
    def on_canvas_click(event):
        im=state.get('image')
        if im is None:return
        scale=state.get('scale') or 1
        x=event.x/scale;y=event.y/scale
        if not (0<=x<im.width and 0<=y<im.height):return
        mode=state['mode']
        if mode=='landmark':
            name=landmark_name.get()
            state['landmarks']=[v for v in state['landmarks'] if v[0]!=name]
            state['landmarks'].append((name,x,y))
            if len(state['landmarks'])>8:state['landmarks'].pop(0)
            status.set('已选地标：'+', '.join(v[0] for v in state['landmarks'])+'（推荐4～6个；至少3个）')
        elif mode in ('cold','band','warm'):
            col=_avg_patch(im,x,y,r=5)
            if col is None:return
            state[mode]=col;state[mode+'pos']=(x,y)
            status.set({'cold':'厚云','band':'云带','warm':'少云'}[mode]+'参考点已采集。')
        redraw()
    image_canvas.bind('<Button-1>',on_canvas_click)
    image_canvas.bind('<Configure>',lambda event:redraw())
    def analyze():
        if state['image'] is None or len(state['landmarks'])<3 or state['cold'] is None or state['warm'] is None or state['band'] is None:
            messagebox.showwarning('材料不完整','请上传截图、选择至少3个地标（推荐4～6个）及厚云/云带/少云三个参考点。',parent=win);return
        try:
            from datetime import datetime
            parsed=datetime.strptime(observed_at.get().strip(),'%Y-%m-%d %H:%M')
            if event:
                try:
                    event_dt=datetime.fromisoformat(str(event)).replace(tzinfo=None)
                    gap=abs((parsed-event_dt).total_seconds())/60
                    if gap>90 and not messagebox.askyesno('时间差较大',f'卫星图与预测事件时刻相差约{gap:.0f}分钟。\n云团可能明显移动，仍用该卫星帧做修正吗？',parent=win):
                        return
                except ValueError:pass
            state['observation_time']=parsed.isoformat(timespec='minutes')
            rows=compare_samples(state['image'],state['landmarks'],state['cold'],state['warm'],result,band_rgb=state['band'])
            state['rows']=rows
            suggestions=[row for row in rows if '疑似' in row['judgement'] and row['confidence']>=.62]
            result_list.delete('1.0','end')
            _trans,_quality=_fit_affine(state['landmarks'])
            result_list.insert('end',f'地图配准：{_quality["points"]}个地标，重投影RMS {_quality["rms_px"]:.1f}px（3点无法独立检验）\n')
            result_list.insert('end',f'覆盖的路径检查点：{len(rows)}（约10km加密），可审核差异：{len(suggestions)}\n')
            result_list.insert('end','【注意】不再把90/55/10%直接作为云量结果；使用局部像素投票和原高云量生成保守修正建议，仍需人工判断。\n\n')
            for row in rows:
                result_list.insert('end',f"{row['distance_km']:.0f} km | 原中高云最大 {row['predicted_mid_high']}% | {row['classification']} | 建议高云 {str(row['suggested_high'])+'%' if row['suggested_high'] is not None else '未知'} | 置信度 {row['confidence']:.0%} | 区域一致度 {row['class_purity']:.0%} | {row['judgement']}\n")
            redraw();status.set(f'完成：{len(rows)}个覆盖的采样点，{len(suggestions)}个待人工审核的云洞变化候选。无网络请求。')
        except Exception as exc:messagebox.showerror('校准失败',str(exc),parent=win)
    def confirm():
        candidates=[r for r in state['rows'] if '疑似' in r['judgement'] and r['confidence']>=.62 and r.get('suggested_high') is not None]
        # Show the strongest differences first while keeping the process inspectable.
        candidates.sort(key=lambda r:(-r['confidence']*abs(float(r['suggested_high'])-float(r['original_high'] or 0)),r['distance_km']))
        if not candidates:
            messagebox.showinfo('无可审核候选','没有达到颜色区分度门槛的候选，暂不修正。',parent=win);return
        modal=tk.Toplevel(win);modal.title('卫星云覆盖候选（仅修改高云，需确认）');modal.geometry('780x630')
        ttk.Label(modal,text='高云建议结合原预测与局部像素证据保守计算，不等于卫星实测云量。\n选择并人工确认后，才把指定距离段的高云量写入修正框。中低云维持原值。',wraplength=590).pack(padx=10,pady=8)
        frame_container=ttk.Frame(modal);frame_container.pack(fill='both',expand=True,padx=12)
        scroll=tk.Canvas(frame_container,highlightthickness=0)
        scrollbar=ttk.Scrollbar(frame_container,orient='vertical',command=scroll.yview)
        scroll.configure(yscrollcommand=scrollbar.set)
        scroll.pack(side='left',fill='both',expand=True)
        scrollbar.pack(side='right',fill='y')
        frame=ttk.Frame(scroll); frame_id=scroll.create_window((0,0),window=frame,anchor='nw')
        frame.bind('<Configure>',lambda e:scroll.configure(scrollregion=scroll.bbox('all')))
        scroll.bind('<Configure>',lambda e:scroll.itemconfigure(frame_id,width=e.width))
        choices=[]
        for r in candidates[:24]:
            is_cold='新增云层' in r['judgement']
            label=(f"{r['distance_km']:.0f} km | {r['judgement']} | "
                   f"置信度 {r['confidence']:.0%} | 原高云 {r['original_high']}")
            var=tk.BooleanVar(value=False);level=tk.StringVar(value=str(round(r['suggested_high'])))
            row_frame=ttk.Frame(frame);row_frame.pack(fill='x')
            ttk.Checkbutton(row_frame,text=label,variable=var).pack(side='left')
            ttk.Label(row_frame,text='高云%').pack(side='left')
            ttk.Entry(row_frame,textvariable=level,width=5).pack(side='left',padx=4)
            choices.append((r,var,level))
        selection_bar=ttk.Frame(modal)
        selection_bar.pack(fill='x',padx=12,pady=(6,0))
        ttk.Button(selection_bar,text='一键全选候选',command=lambda:[v.set(True) for _,v,_ in choices]).pack(side='left',padx=(0,8))
        ttk.Button(selection_bar,text='全部取消',command=lambda:[v.set(False) for _,v,_ in choices]).pack(side='left')
        ttk.Label(selection_bar,text=f'当前可选 {len(choices)} 项（按置信度排序，最多24项）').pack(side='right')
        def commit():
            edits=[]
            for row,var,level in choices:
                if not var.get():continue
                try:v=float(level.get())
                except ValueError:messagebox.showerror('输入错误','高云量需为数字',parent=modal);return
                if not 0<=v<=100:messagebox.showerror('输入错误','高云量必须在0–100%',parent=modal);return
                d=row['distance_km'];edits.append((d,v,row))
            if not edits:return
            if not messagebox.askyesno('确认修正',f'确认用{len(edits)}个已审核高云修正重新计算{direction_label}？\n若新增点缺少天气资料会联网补采；这不是卫星直接测得的云量。',parent=modal):return
            try:on_apply(direction,edits,{'landmarks':state['landmarks'],'registration_quality':_fit_affine(state['landmarks'])[1],'screenshot_path':state.get('path'),'event_dt':str(event),'observation_time':state.get('observation_time'),'model':model,'edits':[{'distance_km':d,'high_cloud_pct':v} for d,v,_ in edits]})
            except Exception as exc:messagebox.showerror('写入失败',str(exc),parent=modal);return
            modal.destroy();status.set(f'已确认{len(edits)}项修正；当前方向正在自动重新计算，必要时将联网。')
        ttk.Button(modal,text='确认并更新单次预测',command=commit).pack(fill='x',padx=12,pady=8)
    set_mode('landmark')
    return win
