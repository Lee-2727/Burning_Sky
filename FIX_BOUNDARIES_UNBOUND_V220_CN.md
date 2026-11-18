# V2.2.0 朝霞/晚霞查询中 `boundaries` 未定义修复

问题：`engine.py` 动态补采规划阶段读取了 `boundaries.get("fire_cloud_range_km")`，但旧版直到补采完成后才计算 `boundaries`，因此出现 `UnboundLocalError`，朝霞、晚霞都会受影响。

修复：在调用 `dynamic_solar_source_horizon()` 前从当前已校准的 `samples` 和 `local_reach` 计算初始 `boundaries`；完成上游补采后仍按原逻辑重新计算一次完整云场边界。只改变量初始化顺序，不改变 700 km 非固定终点、动态太阳角度、云底/云锋面光路、远端遮挡及 GFS 云量融合规则。

## 安装
- 推荐：现有 V2.2.0 动态太阳光路版本，解压本补丁后覆盖项目的 `V2_2_0/engine.py` 即可。测试脚本、说明文档可选。
- 完整桌面版：解压到一个新目录后运行 `START_GFS_OM_DESKTOP_DEBUG.bat`，不要把旧 `data/` 缓存直接混入测试目录。

## 实测回归（离线，不访问 Open-Meteo）
- 新增 `tests/test_dynamic_engine_boundaries_order_fix.py`，通过真实 `FireCloudApp._fetch_weather_task` 朝霞/晚霞计算入口运行至动态补采计划，验证边界数据在实际调用顺序中已初始化。
- 同一测试在修复前版本稳定复现 UnboundLocalError，在修复后 2/2 通过。
- 云底、GFS 云量保护、远端遮挡、缓存测试合计 41 通过。
- 全部测试 138 通过 / 27 已存在失败；旧版 136 通过 / 27 已存在失败。旧失败来自其他功能和不能联网的测试，本次未修改那些功能。
- 没有通过线上 Open-Meteo 实际请求做全程联网预报，不保证没有其他运行期异常。
