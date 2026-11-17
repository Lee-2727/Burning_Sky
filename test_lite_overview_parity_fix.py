"""Lite overview parity regression tests: offline, no API calls."""
from datetime import date, timedelta
import copy
import unittest
from lite.overview_data import from_sources, get_overview
from lite.bar_cards import render

EC='ECMWF IFS 0.25°'
GFS='GFS Global'
TODAY=date(2026,11,17)
DATES=[(TODAY+timedelta(days=i)).isoformat() for i in range(5)]


def rows(p=20, color=30, scale=25):
    return {'probability':p,'color_score':color,'burn_scale':scale}


def snapshot(ec=True,gfs=True,updated='2026-11-17 10:00:00 CST'):
    return {'updated_at':updated,'days':[
        {'date': day,
         'morning':({EC:rows()} if ec else {})|({GFS:rows(0,0,0)} if gfs else {}),
         'evening':({EC:rows()} if ec else {})|({GFS:rows(62,37,34)} if gfs else {})}
        for day in DATES]}


class LiteOverviewParityTest(unittest.TestCase):
    def test_zero_is_valid_but_is_invisible_not_missing(self):
        source=snapshot()
        result=from_sources('合肥',{'city':source},today=TODAY)
        self.assertEqual(result['valid_count'],20)
        self.assertEqual(result['model_counts'][GFS],10)
        self.assertEqual(result['days'][0]['morning'][GFS]['burn_scale'],0)
        markup=render(result['days'],'morning','朝霞')
        self.assertIn('bs-lite-bar empty',markup)
        self.assertNotIn('bs-lite-bar missing',markup)

    def test_missing_is_not_zero(self):
        result=from_sources('合肥',{'city':snapshot(gfs=False)},today=TODAY)
        self.assertEqual(result['model_counts'][GFS],0)
        self.assertIn('bs-lite-bar missing',render(result['days'],'morning','朝霞'))

    def test_prefer_complete_over_later_partial(self):
        published=snapshot(updated='2026-11-17 08:00:00 CST')
        newer_partial=snapshot(gfs=False,updated='2026-11-17 09:00:00 CST')
        result=from_sources('合肥',{'city':published,'global':{'cities':{'合肥':newer_partial}},
                                      'checkpoint':{'place':'合肥','batch_day':TODAY.isoformat(),
        'dates':DATES,'saved_at':'2026-11-17 11:00:00','completed':[],
        'snapshot':newer_partial}},today=TODAY)
        self.assertEqual(result['valid_count'],20)
        self.assertEqual(result['days'][0]['evening'][GFS]['probability'],62)

    def test_merge_missing_gfs_from_other_published_source(self):
        city=snapshot(gfs=False,updated='2026-11-17 11:00:00 CST')
        global_=snapshot(ec=False,updated='2026-11-17 09:00:00 CST')
        result=from_sources('合肥',{'city':city,'global':{'cities':{'合肥':global_}}},today=TODAY)
        self.assertEqual(result['valid_count'],20)
        self.assertEqual(result['model_counts'][GFS],10)

    def test_checkpoint_only_fills_completed_and_does_not_replace_published(self):
        city=snapshot(gfs=False,updated='2026-11-17 08:00:00 CST')
        cp=snapshot(ec=False,updated='2026-11-17 09:00:00 CST')
        # 4 GFS rows recorded complete but only three valid non-null records.
        cp['days'][2]['morning'][GFS]['probability']=None
        completed=[f'合肥|{d}|{GFS}|morning' for d in DATES[:4]]
        result=from_sources('合肥',{'city':city,'checkpoint':{'place':'合肥',
              'batch_day':TODAY.isoformat(),'dates':DATES,'saved_at':'2026-11-17 09:00:01',
              'completed':completed,'snapshot':cp}},today=TODAY)
        self.assertEqual(result['checkpoint_filled'],3)
        self.assertEqual(result['model_counts'][GFS],3)
        self.assertNotIn(GFS,result['days'][2]['morning'])
        self.assertEqual(result['days'][0]['morning'][EC]['probability'],20)

    def test_0_percent_prob_nonzero_scale_is_invisible(self):
        source=snapshot()
        source['days'][0]['evening'][GFS]=rows(0,20,32)
        markup=render(source['days'],'evening','晚霞')
        self.assertNotIn('opacity:0.0000',markup)
        self.assertIn('bs-lite-bar empty',markup)

    def test_get_overview_complete_only_fetches_city(self):
        calls=[]
        def fake(filename):
            calls.append(filename)
            return snapshot()
        result=get_overview('合肥',reader=fake,today=TODAY)
        self.assertEqual(result['valid_count'],20)
        self.assertEqual(calls,['overview_合肥.json'])

if __name__=='__main__': unittest.main()
