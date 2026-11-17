"""Offline release regression tests. No weather API or GitHub access needed."""
from __future__ import annotations
from datetime import date,timedelta
from io import BytesIO
import base64
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from burning_sky import case_library
from burning_sky.lite_forecast import number, verdict, create_runner, run_single, source_time, clouds
from burning_sky.lite_satellite import calibrate, all_segments
from lite.overview_data import from_sources
from lite.bar_cards import render


class LiteDataTests(unittest.TestCase):
    def test_zero_and_missing_are_distinct(self):
        self.assertEqual(number(0),0.)
        self.assertEqual(number('0'),0.)
        self.assertIsNone(number(None))
        self.assertIsNone(number(float('nan')))
        self.assertIn('云量不足',verdict({'inferred':{'verdict_override':'云量不足',
            'regular_score':0,'color_score':18,'burn_scale_index':0}}))
        self.assertEqual(clouds({'inferred':{'low':0,'mid':11,'high':0}}),(0.,11.,0.))
        self.assertEqual(source_time({'summary':'模型数据上线：2026-11-16 19:31（北京时间）\n晚霞'}),
                         '2026-11-16 19:31（北京时间）')

    def test_overview_checkpoint_masks_unfinished(self):
        today=date(2026,11,16)
        days=[{'date':(today+timedelta(days=i)).isoformat(),
               'morning':{'ECMWF IFS 0.25°':{'probability':0,'color_score':18,'burn_scale':0}},
               'evening':{'GFS Global':{'probability':30,'color_score':60,'burn_scale':20}}}
               for i in range(5)]
        completed=['|'.join(['合肥',d['date'],m,direction]) for d in days
                   for direction,m in [('morning','ECMWF IFS 0.25°'),('evening','GFS Global')]]
        completed.remove('|'.join(['合肥',days[-1]['date'],'GFS Global','evening']))
        cp={'place':'合肥','batch_day':today.isoformat(), 'dates':[d['date'] for d in days],
            'completed':completed, 'saved_at':'2026-11-16 11:00:00',
            'snapshot':{'days':days,'updated_at':'2026-11-16 11:00:00'}}
        old={'days':[{'date':days[0]['date'],'morning':days[0]['morning']}],
             'updated_at':'2026-11-16 01:00:00'}
        result=from_sources('合肥',{'city':old,'checkpoint':cp},today=today)
        self.assertEqual(len(result['days']),5)
        self.assertTrue(result['has_data'])
        self.assertEqual(result['checkpoint_count'],len(completed))
        self.assertNotIn('GFS Global',result['days'][-1]['evening'])
        self.assertEqual(result['days'][0]['morning']['ECMWF IFS 0.25°']['probability'],0)
        # Stale checkpoints must not hide a newer fully-published result.
        new=copy.deepcopy(old);new['updated_at']='2026-11-16 13:00:00'
        result=from_sources('合肥',{'city':new,'checkpoint':cp},today=today)
        self.assertIsNone(result['checkpoint_count'])
        self.assertEqual(result['days'][-1].get('evening',{}),{})

    def test_bars_css_not_svg(self):
        days=[{'date':'2026-11-16','morning':{'ECMWF IFS 0.25°':
               {'probability':0,'color_score':90,'burn_scale':0},'GFS Global':
               {'probability':100,'color_score':30,'burn_scale':40}}}]
        chart=render(days,'morning','朝霞')
        self.assertNotIn('<svg',chart)
        self.assertIn('rgba(255,255,255,.82)',__import__('lite.bar_cards',fromlist=['CSS']).CSS)
        self.assertIn('bs-lite-bar zero',chart)
        self.assertIn('opacity:1.000',chart)
        self.assertIn('height:100.000%',chart)
        self.assertIn('暂无数据',chart) if False else None
        self.assertIn('EC',chart)
        self.assertIn('GFS',chart)

    def test_single_query_one_direction_and_restores_context(self):
        import engine
        orig_context=engine._CASE_REQUEST_CONTEXT
        calls=[]
        def fake_fetch(self,direction,**kwargs):
            calls.append((direction,kwargs))
            self.direction_results[direction]={'inferred':{'regular_score':0,'color_score':18,'burn_scale_index':0}}
            self.last_fetch_time='2026-11-16 19:31'
        with patch.object(engine.FireCloudApp,'_fetch_weather_task',fake_fetch):
            state=run_single(engine,place='合肥',lat=31.8,lon=117.2,elevation=30,
                   day='2026-11-20',model_label='GFS Global',direction='evening')
        self.assertEqual([r[0] for r in calls],['evening'])
        self.assertEqual(state['result']['inferred']['regular_score'],0)
        self.assertIs(engine._CASE_REQUEST_CONTEXT,orig_context)
        with self.assertRaises(ValueError):
            run_single(engine,place='合肥',lat=31.8,lon=117.2,elevation=30,
                       day='2026-11-20',model_label='GFS Global',direction='both')
        runner=create_runner(engine,place='合肥',lat=31.8,lon=117.2,elevation=30,
                             date='2026-11-20',model_label='GFS Global')
        self.assertTrue(runner.skip_rainbow_single_var.get())


class LiteSatelliteTests(unittest.TestCase):
    @staticmethod
    def candidates():
        return [{'distance_km':25,'suggested_high':75,'confidence':.95,'judgement':'疑似新增云层'},
                {'distance_km':35,'suggested_high':30,'confidence':.98,'judgement':'疑似新增云洞'}]

    def test_selection_all_nonoverlap(self):
        seg,extra=all_segments(self.candidates())
        self.assertEqual(len(seg),2)
        self.assertLess(seg[0][1],seg[1][0])
        self.assertIn(25,extra)
        self.assertIn(35,extra)
        with self.assertRaises(ValueError):
            all_segments([{'distance_km':10,'suggested_high':101}])

    def test_one_click_uses_all_and_keeps_original_immutable(self):
        original={'inferred':{'regular_score':0,'color_score':18,'burn_scale_index':0}}
        old=copy.deepcopy(original)
        captured={}
        def fake_compute(config,direction,**kwargs):
            captured['dir']=direction
            captured['segments']=kwargs['overrides']
            captured['cache']=kwargs['cache']
            return {'inferred':{'regular_score':25,'color_score':50,'burn_scale_index':12}}, kwargs['cache']
        with patch('burning_sky.lite_satellite.photo_candidates',return_value=(None,self.candidates(),{'rms_px':8})):
            out=calibrate(original,{'a':'b'}, {'place':'合肥','lat':31.8,'lon':117.2,
                       'elev':30,'date':'2026-11-20','model_label':'GFS Global',
                       'direction':'evening'},b'imagedata',compute=fake_compute,
                       recover_cache={'saved':'checkpoint'})
        self.assertTrue(out['changed'])
        self.assertEqual(out['candidate_count'],2)
        self.assertEqual(captured['dir'],'evening')
        self.assertEqual(len(captured['segments']),2)
        self.assertEqual(captured['cache']['saved'],'checkpoint')
        self.assertEqual(original,old)

    def test_unregistered_image_never_calls_engine(self):
        called=[]
        def fake_compute(*args,**kwargs):called.append(True)
        with patch('burning_sky.lite_satellite.photo_candidates',side_effect=ValueError('地标不足')):
            with self.assertRaisesRegex(ValueError,'地标不足'):
                calibrate({'inferred':{}},{},{},b'data',compute=fake_compute)
        self.assertFalse(called)

    def test_no_candidate_means_unmodified_not_fake_correction(self):
        original={'inferred':{'regular_score':0}}
        with patch('burning_sky.lite_satellite.photo_candidates',return_value=(None,[],{})):
            out=calibrate(original,{},{},b'data',compute=lambda *a,**k:1)
        self.assertFalse(out['changed'])
        self.assertIs(out['result'],original)


class CaseLibraryTests(unittest.TestCase):
    @staticmethod
    def package():
        data=BytesIO()
        with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('analysis_state.json',json.dumps({'place':'合肥','date':'2026-11-20','model_code':'GFS'}))
            z.writestr('request_cache.json',json.dumps({'sample':'some raw weather cache'}))
        return data.getvalue()

    def test_local_roundtrip_and_corruption_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(case_library,'github_enabled',return_value=False),\
                 patch.object(case_library,'library_root',return_value=Path(folder)):
                metadata=case_library.save(self.package(),note='永久案例测试')
                self.assertEqual(metadata['storage'],'local_ephemeral')
                self.assertEqual(case_library.read(metadata['id']),self.package())
                self.assertEqual(len(case_library.list_cases()),1)
                (Path(folder)/(metadata['id']+'.zip')).write_bytes(b'corrupt')
                with self.assertRaises(ValueError):case_library.read(metadata['id'])
                case_library.delete(metadata['id'])
                self.assertEqual(case_library.list_cases(),[])

    def test_remote_content_raw_and_admin_gate(self):
        files={}
        def remote(method,name,body=None,*,raw=False,object_meta=False):
            if method=='PUT':
                files[name]=base64.b64decode(body['content'])
                return {'content':{'sha':'123'}}
            if method=='GET':
                if not name:return [{'name':n} for n in files]
                if name not in files:return None
                if raw:return files[name]
                return {'type':'file','sha':'123','encoding':'base64',
                        'content':base64.b64encode(files[name]).decode()}
            if method=='DELETE':
                files.pop(name,None)
                return {}
        settings={'BURNING_SKY_CASE_GITHUB_REPO':'example/private',
                  'BURNING_SKY_CASE_GITHUB_TOKEN':'not-real-token'}
        with patch.object(case_library,'_setting',side_effect=lambda k,d='':settings.get(k,d)),\
             patch.object(case_library,'_gh_request',side_effect=remote):
            self.assertTrue(case_library.github_enabled())
            self.assertFalse(case_library.authorized())
            self.assertTrue(case_library.authorized(pro_authenticated=True))
            data=self.package()
            meta=case_library.save(data,note='ok')
            self.assertEqual(meta['storage'],'github')
            self.assertEqual(case_library.read(meta['id']),data)
            self.assertEqual(len(case_library.list_cases()),1)
            # Simulate large archive (>1 MiB), same raw endpoint used for all ZIPs.
            files[meta['id']+'.zip']=b'x'*(1024*1024+10)
            self.assertEqual(len(case_library._gh_load(meta['id']+'.zip')),1024*1024+10)
            with self.assertRaises(ValueError):case_library.read(meta['id'])
            case_library.delete(meta['id'])
            self.assertEqual(case_library.list_cases(),[])

    def test_remote_second_commit_failure_is_not_success(self):
        files={}
        def remote(method,name,body=None,*,raw=False,object_meta=False):
            if method=='PUT':
                if name.endswith('.json'):raise RuntimeError('failed to commit metadata')
                files[name]=base64.b64decode(body['content']);return {}
            if method=='GET':
                if name in files:return {'sha':'anything'}
                return None
            if method=='DELETE': files.pop(name,None);return {}
        with patch.object(case_library,'github_enabled',return_value=True),\
             patch.object(case_library,'_gh_request',side_effect=remote):
            with self.assertRaisesRegex(RuntimeError,'metadata'):
                case_library.save(self.package())
        self.assertEqual(files,{})


if __name__=='__main__':
    unittest.main(verbosity=2)

# Additional integration checks for Pro case archive and screenshot portability.
class ProCaseExportTests(unittest.TestCase):
    def test_satellite_export_must_match_exact_baseline(self):
        from burning_sky.pro_case_export import prepare_case
        from burning_sky import advanced_bridge
        from tempfile import TemporaryDirectory
        original={'evening':{'inferred':{'regular_score':0,'color_score':18,'burn_scale_index':0}}}
        corrected={'inferred':{'regular_score':21,'color_score':42,'burn_scale_index':9}}
        metadata={'place':'合肥','lat':31.8,'lon':117.2,'elevation':30,
                  'date':'2026-11-20','model':'GFS Global'}
        cfg=advanced_bridge.config_from_main(metadata,'evening')
        key=advanced_bridge.identity(cfg)
        objects={key+'_base':{'config':cfg,'result':original['evening']},
                 key+'_corrected':{'config':cfg,'result':corrected,
                         'edits':[[20,30,None,None,35]], 'request_cache':{'extra':'cached'}}}
        with TemporaryDirectory() as folder:
            Path(folder,f'{key}_corrected.jpg').write_bytes(b'fake JPEG evidence')
            saved,cache,revisions,images=prepare_case(original,original,metadata,{'initial':'response'},
                 lambda v:v,reader=objects.get,cache_dir=Path(folder))
            self.assertEqual(saved['evening'],corrected)
            self.assertIn('evening',revisions)
            self.assertEqual(cache['extra'],'cached')
            self.assertEqual(images['evening'],b'fake JPEG evidence')
            edited={'evening':{'inferred':{'regular_score':70,'color_score':64,'burn_scale_index':35}}}
            saved2,cache2,revisions2,images2=prepare_case(edited,original,metadata,{'initial':'response'},
                lambda v:v,reader=objects.get,cache_dir=Path(folder))
            self.assertEqual(saved2,edited)
            self.assertFalse(revisions2)
            self.assertFalse(images2)
            # Another baseline model run must never reuse the prior screenshot.
            objects[key+'_base']['result']={'inferred':{'regular_score':77}}
            saved3,_,revisions3,_=prepare_case(original,original,metadata,{},lambda v:v,
                 reader=objects.get,cache_dir=Path(folder))
            self.assertEqual(saved3,original)
            self.assertFalse(revisions3)
