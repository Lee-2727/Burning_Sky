"""Regression: preserve strict illumination while allowing user-defined above-low-cloud chance."""
import unittest
from burning_sky.algorithm import apply_low_cloud_overburn_exception

class TestLowCloudOverburn(unittest.TestCase):
    def sample(self, rule='local_low_cloud_at_least_90pct', low=90):
        return {'regular_score':0,'score':0,'upper_score':60,
                'verdict_override':'低云遮挡过多' if low>=90 else '云底过低',
                'verdict_rule':rule, 'pre_low_gate_regular_score':57,
                'special_rule_diagnostics':{'local_low_cloud_pct':low}}

    def test_strict_greater_than(self):
        self.assertFalse(apply_low_cloud_overburn_exception(self.sample(),45,True)['low_cloud_overburn'])
        r=apply_low_cloud_overburn_exception(self.sample(),45.01,True)
        self.assertTrue(r['low_cloud_overburn'])
        self.assertEqual(r['regular_score'],57)
        self.assertIsNone(r['verdict_override'])

    def test_cloud_base_and_no_valid_path(self):
        r=self.sample('profile_cloud_base_below_500m',low=10)
        self.assertTrue(apply_low_cloud_overburn_exception(r,5.01,True)['low_cloud_overburn'])
        self.assertFalse(apply_low_cloud_overburn_exception(r,100,False)['low_cloud_overburn'])
        self.assertFalse(apply_low_cloud_overburn_exception(r,100,True,True)['low_cloud_overburn'])

    def test_other_zero_rule_not_overridden(self):
        r=self.sample('local_100km_mean_total_cloud_below_5pct',low=90)
        self.assertFalse(apply_low_cloud_overburn_exception(r,100,True)['low_cloud_overburn'])

if __name__=='__main__': unittest.main()
