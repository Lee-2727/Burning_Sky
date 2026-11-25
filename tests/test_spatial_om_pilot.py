import unittest
from datetime import datetime, timezone
from unittest.mock import patch
import numpy as np
from burning_sky.spatial_om import (SpatialUnavailable, resolve_valid_file,
                                    _pixel_indices, fetch_pressure_profiles)
from burning_sky.cloud_vertical import analyze_vertical_layers
from burning_sky.algorithm import estimate_cloud_base_from_profile

NOW = datetime(2026,11,18,0,0,tzinfo=timezone.utc)
LEVELS = [925, 850, 700, 500]
NAMES = ([f'cloud_cover_{level}hPa' for level in LEVELS]
        +[f'geopotential_height_{level}hPa' for level in LEVELS]
        +['cloud_cover_low','cloud_cover_mid','cloud_cover_high'])
META = {'completed':True,'reference_time':'2026-11-17T18:00:00Z',
        'valid_times':['2026-11-18T00:00Z','2026-11-18T03:00Z'],
        'variables':NAMES}

class FakeVar:
    is_array=True
    def __init__(self, data): self._data=np.asarray(data,dtype=float);self.shape=self._data.shape
    def read_array(self,sl): return self._data[sl]

class FakeScalar:
    def read_scalar(self): return 'EPSG:4326'

class FakeRoot:
    is_group=True
    def __init__(self):
        self._data={'crs_wkt':FakeScalar()}
        for lev in LEVELS:
            self._data[f'cloud_cover_{lev}hPa']=FakeVar(np.full((5,5),70))
            self._data[f'geopotential_height_{lev}hPa']=FakeVar(np.full((5,5),{925:800,850:1500,700:3100,500:5500}[lev]))
    def __enter__(self):return self
    def __exit__(self,*x):pass
    def get_child_by_name(self,name):return self._data[name]

def grid_factory(wkt,shape):
    lon,lat=np.meshgrid(np.arange(115,120),np.arange(29,34))
    return lon,lat

class SpatialPilotTests(unittest.TestCase):
    def test_resolve_real_run_and_valid_time(self):
        info=resolve_valid_file('ecmwf_ifs025',NOW,META)
        self.assertEqual(info['model_domain'],'ecmwf_ifs025')
        self.assertTrue(info['url'].endswith('/2026/11/17/1800Z/2026-11-18T0000.om'))
    def test_no_wrong_time(self):
        with self.assertRaises(SpatialUnavailable):
            resolve_valid_file('ecmwf_ifs025',datetime(2026,11,21,tzinfo=timezone.utc),META)
    def test_no_outside_grid_extrapolation(self):
        a,b=grid_factory('',(5,5))
        with self.assertRaises(SpatialUnavailable):
            _pixel_indices(b,a,[(50,115)])
    def test_correct_lon_wrap(self):
        lats=np.array([[30.,30.]])
        lons=np.array([[359.5,0.]])
        self.assertEqual(_pixel_indices(lats,lons,[(30.,-0.5)]),[(0,0)])
    def test_pressure_profiles_from_spatial_array(self):
        result=fetch_pressure_profiles([(31,117),(32,118)],[0,50],NOW,'ecmwf_ifs025',
              estimate_cloud_base=estimate_cloud_base_from_profile,
              analyze_vertical_layers=analyze_vertical_layers,
              metadata=META,reader_factory=FakeRoot,mesh_factory=grid_factory,
              local_ground_elevation_m=100,ground_elevations=[90,130])
        self.assertEqual(len(result['samples']),2)
        self.assertEqual(result['samples'][0]['levels'][0]['pressure_hpa'],925)
        self.assertEqual(result['spatial_source']['points'],2)
        self.assertEqual(result['samples'][0]['ground_elevation_m'],100)
        self.assertTrue(result['samples'][0]['levels'][0]['height_m_asl'] is not None)
    def test_cloud_layer_metadata_missing_no_fake_evidence(self):
        with self.assertRaises(SpatialUnavailable):
            fetch_pressure_profiles([(31,117)],[0],NOW,'ecmwf_ifs025',
                metadata={**META,'variables':['cloud_cover_low','cloud_cover_mid']},
                estimate_cloud_base=estimate_cloud_base_from_profile,
                analyze_vertical_layers=analyze_vertical_layers,
                reader_factory=FakeRoot,mesh_factory=grid_factory)

if __name__=='__main__': unittest.main()
