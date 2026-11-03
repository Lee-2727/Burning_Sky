# -*- coding: utf-8 -*-
"""Offline experimental diagnostic; run directly with Python."""
import os, sys, json, zipfile, threading, queue, tkinter as tk
from tkinter import filedialog, ttk, messagebox
from datetime import datetime
from burning_sky.sky_six import adaptive_profile_diagnostic

class App:
    def __init__(self, root):
        self.root=root;root.title('Burning Sky Debug19 · 上海光路自适应测试');root.geometry('920x650')
        self.q=queue.Queue();self.path=tk.StringVar(value='');self.status=tk.StringVar(value='选择案例 ZIP，使用已有气象数据离线测试（不请求 API）')
        top=ttk.Frame(root,padding=10);top.pack(fill='x')
        ttk.Entry(top,textvariable=self.path).pack(side='left',fill='x',expand=True)
        ttk.Button(top,text='选择案例ZIP',command=self.pick).pack(side='left',padx=6)
        self.btn=ttk.Button(top,text='开始离线测试',command=self.run);self.btn.pack(side='left')
        ttk.Label(root,textvariable=self.status,padding=8).pack(fill='x')
        self.bar=ttk.Progressbar(root,mode='indeterminate');self.bar.pack(fill='x',padx=12)
        self.txt=tk.Text(root,wrap='word',font=('Consolas',10));self.txt.pack(fill='both',expand=True,padx=10,pady=10)
        self.root.after(100,self.poll)
    def pick(self):
        p=filedialog.askopenfilename(filetypes=[('ZIP案例','*.zip')]);
        if p:self.path.set(p)
    def run(self):
        if not os.path.isfile(self.path.get()):messagebox.showwarning('缺少案例','请先选择案例 ZIP');return
        self.btn.configure(state='disabled');self.bar.start(12);self.txt.delete('1.0','end')
        self.status.set('正在读取案例并扫描临界角度……（离线计算）')
        threading.Thread(target=self.worker,args=(self.path.get(),),daemon=True).start()
    def worker(self,path):
        try:
            with zipfile.ZipFile(path) as z:
                n=next(n for n in z.namelist() if n.endswith('analysis_state.json'))
                a=json.loads(z.read(n))
            d=a['direction_results']['morning'];profiles=d['profiles'];event=datetime.fromisoformat(d['event_dt'])
            result=adaptive_profile_diagnostic(profiles,event,8,float(a['latitude']),float(a['longitude']),direction='morning',target_limit_km=100)
            output=os.path.join(os.path.dirname(os.path.abspath(path)),'Debug19_上海光路自适应诊断.json')
            with open(output,'w',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
            self.q.put(('done',result,output))
        except Exception as e:self.q.put(('error',repr(e)))
    def poll(self):
        try:
            while True:
                msg=self.q.get_nowait()
                self.bar.stop();self.btn.configure(state='normal')
                if msg[0]=='error':self.status.set('测试失败');self.txt.insert('end',msg[1])
                else:
                    r,out=msg[1:];self.status.set('测试完成 · '+out)
                    self.txt.insert('end',f"已检查 {r['target_count']} 个候选云层，气压剖面到 {r['profile_end_km']} km\n")
                    self.txt.insert('end','说明：候选透光仅为经验风险筛选，非实测透光。\n\n')
                    for t in r['targets']:
                        self.txt.insert('end',f"目标 {t['target_distance_km']:g} km / {t['target_height_km']:.2f} km / 云量 {t['target_cloud_cover_pct']:.0f}%  候选点数：{t['candidate_counts']}\n")
        except queue.Empty:pass
        self.root.after(100,self.poll)
if __name__=='__main__':App(tk.Tk()).root.mainloop()
