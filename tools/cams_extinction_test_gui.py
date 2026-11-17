"""Independent CAMS 532 nm profile test GUI; no Burning Sky engine dependency."""
import sys, os, datetime, threading, traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from cams_extinction_core import retrieve_ads, load_profile, vertical_aod, demo_profile

class App:
    def __init__(self,root):
        self.root=root;root.title('CAMS 532 nm 垂直消光测试（独立模块）');root.geometry('850x680')
        self.vars={}
        form=ttk.Frame(root,padding=12);form.pack(fill='x')
        for i,(name,default) in enumerate((('纬度','31.82'),('经度','117.23'),('UTC日期',datetime.date.today().isoformat()),('起报UTC','00:00'),('预报时效小时','0'))):
            ttk.Label(form,text=name).grid(row=i//3*2,column=i%3,sticky='w')
            var=tk.StringVar(value=default);self.vars[name]=var
            ttk.Entry(form,textvariable=var,width=22).grid(row=i//3*2+1,column=i%3,sticky='ew',padx=3,pady=3)
        actions=ttk.Frame(root,padding=8);actions.pack(fill='x')
        ttk.Button(actions,text='演示数据（非真实预报）',command=lambda:self.show(demo_profile(),demo=True)).pack(side='left',padx=3)
        ttk.Button(actions,text='打开已下载GRIB/NetCDF',command=self.open_file).pack(side='left',padx=3)
        ttk.Button(actions,text='从CAMS ADS下载',command=self.download).pack(side='left',padx=3)
        ttk.Label(root,text='需ADS账号/API凭据；真实下载可能排队。测试结果不写入火烧云概率或色彩分。',foreground='#665500').pack(anchor='w',padx=12)
        self.log=tk.Text(root,height=27,wrap='none');self.log.pack(fill='both',expand=True,padx=12,pady=8)
        self.log.insert('end','CAMS 532 nm 垂直消光测试模块\n仅计算垂直方向的积分AOD（不将水平距离乘入）。\n气压层高度目前使用标准大气近似，未获得真实位势高度时不能视为精确积分。\n')
    def show(self,rows,demo=False):
        self.log.delete('1.0','end');self.log.insert('end','数据来源：'+('合成演示，非CAMS实况' if demo else '本地CAMS文件')+'\n')
        self.log.insert('end','气压hPa    近似海拔km    消光系数m^-1\n')
        for r in rows:self.log.insert('end',f"{r['pressure_hpa']:8.1f}    {r['height_km_standard_atmosphere']:9.3f}    {r['extinction_m_inv']:.8f}\n")
        self.log.insert('end',f'\n有限气压层区间垂直积分光学厚度 ≈ {vertical_aod(rows):.4f}\n')
        self.log.insert('end','注意：此值不等于完整0-大气顶的AOD，气压层之间使用梯形近似。\n')
    def open_file(self):
        path=filedialog.askopenfilename(filetypes=[('CAMS data','*.grib *.grib2 *.grb *.nc *.nc4'),('All','*.*')]);
        if not path:return
        try:self.show(load_profile(path,float(self.vars['纬度'].get()),float(self.vars['经度'].get())))
        except Exception as e:messagebox.showerror('读取失败',str(e))
    def download(self):
        path=filedialog.asksaveasfilename(defaultextension='.grib',filetypes=[('GRIB','*.grib')]);
        if not path:return
        args=(path,self.vars['UTC日期'].get(),self.vars['起报UTC'].get(),self.vars['预报时效小时'].get(),float(self.vars['纬度'].get()),float(self.vars['经度'].get()))
        self.log.insert('end','开始ADS下载；需要有效 ~/.adsapirc 凭据与数据集授权……\n')
        def work():
            try:
                retrieve_ads(*args)
                rows=load_profile(path,args[4],args[5])
                self.root.after(0,lambda:self.show(rows))
            except Exception as e:
                msg=str(e)
                self.root.after(0,lambda:self.log.insert('end','下载/解析失败：'+msg+'\n'))
        threading.Thread(target=work,daemon=True).start()
if __name__=='__main__':App(tk.Tk()).root.mainloop()
