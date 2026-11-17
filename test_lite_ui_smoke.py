"""UI execution smoke with minimal Streamlit fake when Streamlit is unavailable.
This is NOT a real Cloud browser test; it checks first-run and completed-result
render paths without outbound I/O.
"""
from __future__ import annotations
from datetime import date
import json
import os
from pathlib import Path
import runpy
import sys
import types
import unittest
import zipfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent

class FakeStreamlit(types.ModuleType):
    def __init__(self, page, submit=False):
        super().__init__('streamlit')
        self.session_state={}
        self.secrets={}
        self.page=page
        self.submit=submit
        self.submit_calls=0
        self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def cache_data(self,*args,**kwargs):return lambda f:f
    def set_page_config(self,*args,**kwargs):return None
    def markdown(self,*args,**kwargs):self.calls.append(('markdown',args));return None
    def html(self,markup):self.calls.append(('html',markup));return None
    def image(self,*args,**kwargs):self.calls.append(('image','result'));return None
    def title(self,*args,**kwargs):return None
    def text_input(self,*args,**kwargs):return ''
    def button(self,*args,**kwargs):return False
    def segmented_control(self,label,options,default=None,key=None,**kwargs):
        return self.page if key=='lite_nav' else (self.session_state.get(key) or default or options[0])
    def spinner(self,*args,**kwargs):return self
    def expander(self,*args,**kwargs):return self
    def columns(self,spec,*args,**kwargs):return [self for i in range(len(spec) if isinstance(spec,(tuple,list)) else spec)]
    def form(self,*args,**kwargs):return self
    def selectbox(self,label,options,index=0,**kwargs):return options[index]
    def date_input(self,label,value=None,**kwargs):return value
    def form_submit_button(self,*args,**kwargs):
        self.submit_calls+=1
        return self.submit and self.submit_calls==1
    def metric(self,*args,**kwargs):self.calls.append(('metric',args));return None
    def caption(self,*args,**kwargs):return None
    def dataframe(self,*args,**kwargs):return None
    def info(self,*args,**kwargs):self.calls.append(('info',args));return None
    def error(self,*args,**kwargs):self.calls.append(('error',args));return None
    def stop(self):raise RuntimeError('st.stop')
    def divider(self,*args,**kwargs):return None
    def subheader(self,*args,**kwargs):return None
    def file_uploader(self,*args,**kwargs):return None
    def rerun(self):raise RuntimeError('st.rerun')


class UISmoke(unittest.TestCase):
    def _execute(self, page, fake_result=None, submit=False):
        fake=FakeStreamlit(page,submit=submit)
        # Theme carries Streamlit-only CSS logic; replace only appearance for
        # this smoke to avoid assuming a real browser DOM exists.
        theme=types.ModuleType('burning_sky.warm_web_theme')
        theme.apply_warm_theme=lambda:None
        theme.render_hero=lambda *a,**k:None
        if fake_result is not None:
            fake.session_state['lite_current']=fake_result
        with patch.dict(sys.modules,{'streamlit':fake,'burning_sky.warm_web_theme':theme}):
            if submit:
                import burning_sky.lite_forecast as lite_forecast
                def fake_single(*args,**kwargs):
                    return {'result':{'inferred':{'regular_score':0,'color_score':18,
                        'burn_scale_index':0,'upper_score':57,'low':0,'mid':11,'high':0,
                        'verdict_override':'云量不足'},'event_dt':'2026-11-20T17:09:00',
                        'dynamic_light_curve':{'samples':[]},
                        'summary':'模型数据上线：2026-11-16 19:31（北京时间）'},
                        'cache':{'mock':True}, 'fetched_at':'2026-11-16 19:31',
                        'meta':{'place':'合肥','lat':31.8047,'lon':117.2214,'elev':30.0,
                            'date':'2026-11-20','model_label':'ECMWF IFS 0.25°',
                            'direction':'morning'}}
                cm=patch.object(lite_forecast,'run_single',side_effect=fake_single)
            else:
                cm=patch('lite.overview_data.get_overview',return_value={
                  'has_data':True,'updated_at':'2026-11-16 09:00',
                  'days':[{'date':'2026-11-16','morning':{'ECMWF IFS 0.25°':{
                      'probability':0,'color_score':18,'burn_scale':0}}}],
                  'checkpoint_count':None})
            with cm:
                runpy.run_path(str(ROOT/'lite'/'app.py'),run_name='__main__')
        return fake

    def test_overview_read_only_renders_bars(self):
        fake=self._execute('未来五日')
        htmls=[v for k,v in fake.calls if k=='html']
        self.assertEqual(len(htmls),2)
        self.assertIn('bs-lite-bar',htmls[0])
        self.assertFalse(any(k=='error' for k,_ in fake.calls))

    def test_single_form_no_request_without_submit(self):
        fake=self._execute('单次预测')
        self.assertFalse(any(k=='error' for k,_ in fake.calls))

    def test_single_submit_forecast_render(self):
        fake=self._execute('单次预测',submit=True)
        self.assertTrue(any(k=='metric' for k,_ in fake.calls))
        self.assertTrue(any(k=='image' for k,_ in fake.calls))
        self.assertFalse(any(k=='error' for k,_ in fake.calls))

if __name__=='__main__':
    unittest.main(verbosity=2)
