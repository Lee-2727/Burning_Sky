"""Standalone CAMS 532nm extinction profile test. Does not change forecast scores.

Requires user-owned ADS credentials for online download. GRIB via cfgrib/eccodes,
NetCDF via xarray. Profiles are model forecasts, not lidar observations.
"""
import math
from pathlib import Path

DATASET='cams-global-atmospheric-composition-forecasts'
VARIABLE='aerosol_extinction_coefficient_532nm'
PRESSURE_LEVELS=['1000','950','925','900','850','800','700','600','500','400','300','250','200']


def ads_request(date, hour='00:00', leadtime='0', lat=31.82, lon=117.23):
    """A small area request; ADS API account and dataset terms must be accepted."""
    return {'variable':[VARIABLE], 'date':str(date), 'time':[hour],
            'leadtime_hour':[str(leadtime)], 'type':'forecast',
            'model_level':[], # unused; request pressure levels only
            'pressure_level':PRESSURE_LEVELS,
            'area':[round(lat+0.4,3),round(lon-0.4,3),round(lat-0.4,3),round(lon+0.4,3)],
            'data_format':'grib'}


def retrieve_ads(path, date, hour, leadtime, lat, lon):
    try:import cdsapi
    except ImportError as e:raise RuntimeError('请先安装 cdsapi：pip install cdsapi') from e
    request=ads_request(date,hour,leadtime,lat,lon)
    request.pop('model_level',None)
    # ADS request fields can evolve; surface service errors without hiding them.
    cdsapi.Client().retrieve(DATASET,request,str(path))
    return str(path)


def load_profile(path, lat=None, lon=None):
    """Read GRIB/NetCDF. Select nearest grid cell, retain pressure coordinates."""
    import xarray as xr
    path=Path(path)
    if path.suffix.lower() in ('.grib','.grb','.grib2'):
        try:ds=xr.open_dataset(path,engine='cfgrib')
        except ImportError as e:raise RuntimeError('GRIB读取需要 pip install cfgrib eccodes') from e
    else:ds=xr.open_dataset(path)
    try:
        if lat is not None and lon is not None:
            for dim,target in [('latitude',lat),('longitude',lon)]:
                if dim in ds.coords:
                    ds=ds.sel({dim:target},method='nearest')
        keys=[VARIABLE,'aerext532','aerosol_extinction_coefficient_532nm']
        field=next((ds[k] for k in keys if k in ds),None)
        if field is None:
            raise ValueError('文件中找不到532nm消光系数；可用变量：'+','.join(ds.data_vars))
        pcoord=next((k for k in ('isobaricInhPa','pressure_level','level','pressure') if k in field.coords),None)
        if pcoord is None:raise ValueError('未找到气压层坐标')
        for dim in list(field.dims):
            if dim!=pcoord:field=field.isel({dim:0})
        levels=field[pcoord].values
        values=field.values
        rows=[]
        for p,v in zip(levels,values):
            p=float(p);v=float(v)
            if p>2000:p/=100. # Pa to hPa
            if math.isfinite(v) and v>=0 and 0<p<=1100:
                rows.append({'pressure_hpa':p,'extinction_m_inv':v,
                             'height_km_standard_atmosphere':44330*(1-(p/1013.25)**0.1903)/1000})
        return sorted(rows,key=lambda r:r['height_km_standard_atmosphere'])
    finally:ds.close()


def vertical_aod(rows):
    """Approximate vertical optical depth using *standard-atmosphere* heights.

    For proper quantitative work supply geopotential/temperature-derived heights.
    No horizontal path length is used or multiplied into AOD here.
    """
    ordered=sorted(rows,key=lambda r:r['height_km_standard_atmosphere'])
    total=0.
    for a,b in zip(ordered,ordered[1:]):
        dz=(b['height_km_standard_atmosphere']-a['height_km_standard_atmosphere'])*1000
        if dz>0:total+=.5*(a['extinction_m_inv']+b['extinction_m_inv'])*dz
    return total


def demo_profile():
    return [{'pressure_hpa':p,'height_km_standard_atmosphere':h,
             'extinction_m_inv':0.00015*math.exp(-h/1.3)}
            for p,h in ((1000,.1),(950,.55),(900,1),(850,1.5),(800,2),(700,3),(600,4.2),(500,5.5),(400,7))]
