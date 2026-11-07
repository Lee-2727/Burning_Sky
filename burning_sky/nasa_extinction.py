"""Experimental GEOS-FP 3D aerosol extinction retrieval (not a CAMS product).

Requires xarray + pydap. Fail closed: no invented NASA values and no silent
substitution with CAMS. NASA OPeNDAP accessibility depends on deployment.
"""
from __future__ import annotations
import math
from datetime import datetime, timezone

URL = 'https://opendap.nccs.nasa.gov/dods/GEOS-5/fp/0.25_deg/fcast/inst3_3d_ext_Np.latest'


def _extinction_to_km(value, units):
    u = str(units or '').lower().replace(' ', '').replace('**','^')
    if u in ('m-1','m^-1','1/m','m⁻¹'):
        return float(value)*1000.0
    if u in ('km-1','km^-1','1/km','km⁻¹'):
        return float(value)
    raise ValueError('NASA 消光变量单位无法识别：'+str(units))


def vertical_aod(layers):
    """Trapezoidal integral of extinction in km^-1 over height in km.

    Layers must cover a meaningful atmospheric column; never fabricate
    a ground layer or an upper tail when levels are missing.
    """
    rows = sorted((float(h),float(a)) for h,a in layers if math.isfinite(h) and math.isfinite(a) and a>=0)
    if len(rows)<3: raise ValueError('NASA 有效消光层不足')
    return sum((rows[i][1]+rows[i-1][1])*0.5*(rows[i][0]-rows[i-1][0])
               for i in range(1,len(rows)) if rows[i][0]>rows[i-1][0])


def fetch_nasa_aod(lat,lon,event_utc):
    try:
        import xarray as xr
        import numpy as np
        import pydap  # noqa: F401
    except ImportError as exc:
        raise RuntimeError('NASA 模式需要 xarray、numpy、pydap：pip install xarray numpy pydap') from exc
    try:
        ds=xr.open_dataset(URL,engine='pydap',decode_times=False)
        try:
            dims={name.lower():name for name in ds.coords}
            t=next((dims[n] for n in ('time',) if n in dims),None)
            la=next((dims[n] for n in ('lat','latitude') if n in dims),None)
            lo=next((dims[n] for n in ('lon','longitude') if n in dims),None)
            lev=next((dims[n] for n in ('lev','level','plev') if n in dims),None)
            if not all((t,la,lo,lev)):
                raise ValueError('NASA 数据坐标结构改变：'+str(list(ds.coords)))
            lonvals=ds[lo].values
            target_lon=lon%360 if float(np.nanmax(lonvals))>180 else ((lon+180)%360-180)
            # GEOS OPeNDAP may encode dates as 'days since 1-1-1'.
            # Do not ask xarray/numpy datetime64 to decode year 0001.
            time_coord=ds[t]
            time_units=str(time_coord.attrs.get('units',''))
            calendar=str(time_coord.attrs.get('calendar','standard'))
            if not time_units or 'since' not in time_units.lower():
                raise ValueError('NASA 时间坐标缺少可识别的 CF units：'+repr(time_units))
            try:
                import cftime
            except ImportError as exc:
                raise RuntimeError('NASA 时间坐标需要 cftime：请运行 pip install cftime') from exc
            target=event_utc
            if isinstance(target,str): target=datetime.fromisoformat(target.replace('Z','+00:00'))
            if target.tzinfo is not None: target=target.astimezone(timezone.utc).replace(tzinfo=None)
            target_num=float(cftime.date2num(target,units=time_units,calendar=calendar))
            time_values=np.asarray(time_coord.values,dtype=float)
            if time_values.ndim!=1 or not np.isfinite(time_values).any():
                raise ValueError('NASA 时间坐标为空或格式异常')
            time_index=int(np.nanargmin(np.abs(time_values-target_num)))
            gap=abs(float(time_values[time_index])-target_num)*({'day':24,'days':24,'hour':1,'hours':1,'minute':1/60,'minutes':1/60,'second':1/3600,'seconds':1/3600}.get(time_units.lower().split('since')[0].strip(),float('nan')))
            if not np.isfinite(gap):
                # Robust conversion even for nonstandard unit spellings.
                actual_cf=cftime.num2date(float(time_values[time_index]),units=time_units,calendar=calendar)
                gap=abs((datetime(actual_cf.year,actual_cf.month,actual_cf.day,actual_cf.hour,actual_cf.minute,actual_cf.second)-target).total_seconds())/3600
            if gap>4: raise ValueError(f'NASA 最近时次偏离目标 {gap:.1f} 小时')
            actual=str(cftime.num2date(float(time_values[time_index]),units=time_units,calendar=calendar))
            subset=ds.isel({t:time_index}).sel({la:lat,lo:target_lon},method='nearest')
            levels=np.asarray(subset[lev].values,dtype=float)
            units=str(ds[lev].attrs.get('units','')).lower()
            if units in ('pa','pascal','pascals'): levels=levels/100
            elif units not in ('hpa','mb','millibar','millibars'):
                raise ValueError('无法确认气压层单位：'+str(ds[lev].attrs.get('units')))
            if any((p<=0 or p>1100) for p in levels):raise ValueError('气压层范围不合理')
            # Pressure-to-altitude is an approximate standard-atmosphere mapping.
            # No fictitious measured geopotential height is claimed.
            heights=44.330*(1.0-(levels/1013.25)**0.1903)
            candidates=[]
            for name,var in subset.data_vars.items():
                if lev not in var.dims:continue
                low=name.lower()
                if ('ext' not in low and 'extinction' not in str(var.attrs.get('long_name','')).lower()):continue
                if 'total' in low:continue
                try:
                    unit=var.attrs.get('units','')
                    vals=np.asarray(var.values,dtype=float).squeeze()
                    if vals.shape!=levels.shape:continue
                    converted=np.array([_extinction_to_km(v,unit) for v in vals])
                    candidates.append((name,converted))
                except (ValueError,TypeError):continue
            if not candidates:raise ValueError('NASA 数据未找到单位明确的分组消光变量；不以质量浓度代替')
            ext=sum((a for _,a in candidates),np.zeros_like(levels))
            rows=[(float(h),float(a)) for h,a in zip(heights,ext) if np.isfinite(h) and np.isfinite(a) and h>=0 and a>=0]
            if len(rows)<3:raise ValueError('NASA 可用廓线层数不足')
            aod=vertical_aod(rows)
            if not 0<=aod<=5:raise ValueError(f'NASA 积分 AOD={aod:.3f} 超出测试安全范围')
            return {'aod':aod,'layers':rows,'time':str(actual),'variables':[n for n,_ in candidates],
                    'note':'NASA 532nm（须核实各变量波长）；气压高度为标准大气近似；气柱端点可能截断，结果仅供实验'}
        finally:ds.close()
    except Exception as exc:
        raise RuntimeError('NASA GEOS-FP 三维消光获取/解析失败：'+str(exc)) from exc
