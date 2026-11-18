# Burning Sky · GFS OM 云量一致性保护 V2（2026-11-18）

## 安装
本 ZIP 内目录相对于项目根目录 `Burning_Sky-main/`。将代码文件按路径覆盖到现有 Burning Sky 项目并提交。**不要删除/覆盖 data/、缓存或其他项目文件**。该补丁不包含 app.py，避免回退你最近的三日概览/网页 UI 修复。

## 修改点
1. `burning_sky/spatial_pipeline.py`：当云场来源是 **GFS ncep_gfs025 的气压层 proxy** 时，只将原生主 API 的 `cloud_cover_low/mid/high` 用于常规概率、云洞快筛和后续光路水平一致性门控。只要对应 API 云量有效，proxy 即使差异很小也不覆盖它；避免 14% proxy/17% API 跨越 15% 清空阈值。API 缺失的云层仍可采用 proxy，但保留来源说明。
2. 超过 **25 个百分点**的 GFS 原生/OM proxy 分层差异记录到 `inferred.spatial_calibration.conflicts`，原始 OM 值保存在 `profiles[].spatial_horizontal_om`，实际使用值在 `profiles[].spatial_horizontal_effective`。不会默默把 100% 高云改成 13.9%。
3. `burning_sky/spatial_ray_fusion.py`：水平光路交叉验证统一看 **effective** 数据，不能用被回退的 13.9% proxy 假装晴空。人工/卫星修正优先，手动修正时停用原始 OM 的额外交叉否决。
4. `burning_sky/spatial_om.py`：GFS 气压层抽样优先包含 **350 hPa**（如数据源实际提供），避免 350 hPa 有云但被 5 层均匀抽样跳过；维持原有每个高度带最多 5 层的 I/O 上限，不额外增加 API 请求。
5. `engine.py`：最远 700 km 的延伸采样完成后重新执行**幂等**的分层冲突保护，确保新增的上游数据不漏处理。
6. EC 二维原生 OM 云量沿用已有校准流程；不改变球面光路算法、概率系数、朝晚霞窗口或概览规则。

## 用户案例离线复核
使用 `2026年11月20日_合肥_GFS.zip` 中保存的 17 个路径采样点：主 API 0 km 高云 100%、OM proxy 13.913% -> **最终判定输入 100%**。25km、50km 的主 API 高云也分别保持 100%，而 OM 原值仍保留在诊断。共 9 处分层差距超过 25 个百分点；这不等于 9 个独立空间云块。

离线关联回归：`62 passed`（空间 OM、严格光路交叉验证、700km 扩展等）。未进行 Open-Meteo 实时联网预报或 Streamlit Cloud 端到端部署测试。

## 边界说明
- 修的是**正式计算时使用的 GFS OM 分层云量**与主 API 的严重口径冲突。**原始 OM 25km 网格图、全国云图仍是气压层 proxy**，不会凭空变成主 API 13km 的原生云图；两者仍可能在图面上不一致。
- 主 API 是半径 15km 附近多个采样点均值，OM proxy 是 25km 网格值；同一时次不保证模型起报批次一致。本补丁不能物理证明任意一方准确，只避免让不可比 proxy 无条件覆盖更直接的原生分层字段。
- 旧案例 JSON 会原样显示旧结果；请在允许请求时重新查询一次以获得新的正式计算结果；不要在 429 限流冷却期间反复尝试。
