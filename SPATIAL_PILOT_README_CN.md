# Burning Skies Pro 空间云场接入试验 V0.1

**这是可回退的 Pro 单次查询试验，不是已完成全云场三维光路重构。**

## 本版本实际完成
- Pro 网页“开始预测”处增加 Open-Meteo 空间云场实验开关，默认关闭。
- 仅本次联网单次预测、仅 EC 0.25° / GFS Global 尝试从 `.om` 的**原生气压层**读取云量、相对湿度和位势高度，并结合地形高程 API 映射到旧版 `profiles` 结构。
- 使用官方 `latest.json` 的完整模型起报/有效时间元数据；超过 90 分钟、缺少足够气压层、解码失败/未安装依赖时，不使用空间结果，整批回退旧坐标 API。
- 根据 `.om` 中的 `crs_wkt` 转换经纬度，拒绝超出覆盖和过大切片的查询；不能把无数据视为晴空。
- 查询后展开“空间网格数据源诊断”查看哪些批次真正采用空间数据、哪些回退。
- Lite、桌面版、后台多日概览及其原有数据/定时任务保持不变；Lite 仍然读取 Pro 发布的概览结果。
- 上一个补丁的 Lite 顶部裸露 CSS 修复已合并。

## 如何启用
1. 先备份当前 GitHub 仓库，特别是 `data/`、`.github/workflows/` 和 `overview_cities.txt`；更新只覆盖 ZIP 中代码，不要删除现有数据。
2. 把此包上传到现有 GitHub 项目。Lite 原有安装依赖不变。
3. **只对 Pro 端**，在 Streamlit Cloud 的 `Advanced settings` -> 指定 Python 依赖文件为 `requirements-spatial.txt`（如果平台支持自定义依赖文件）；否则将 `requirements-spatial.txt` 的 `omfiles[fsspec,grids]`、`fsspec`、`aiohttp` 三行临时加入仓库根目录 `requirements.txt`。务必保证 Streamlit Pro 能安装这些可选依赖。
4. 在 Pro 单次预测页面展开“Open-Meteo 空间云场（实验入口）”，勾选开关，先查询 EC 0.25° 单模式。
5. 结果下方查看“空间网格数据源诊断”。如果出现 `point_api_fallback`，会标出缺失依赖、时次或云层字段原因；结果是来自原坐标 API 的结果。`spatial_used` 才代表该批剖面来自空间文件。
6. 开关关闭时完全使用原来的坐标 API，便于同一天同一模型 A/B 对比。

## 未完成（避免错误承诺）
- 未在真实 S3 `.om` 服务器上完成网络端到端测试。当前环境禁止外网读取，所以还需用户部署后验证字段与 WKT 投影解析。
- 本版本**没有**将整张二维云场直接用于所有太阳射线的三维遮挡计算，也**没有**把 Pro 后台自动概览任务改为空间数据源。
- 目前仍然需要单点 API 获取地面天气、AOD、未能覆盖的其他变量以及一次批量地形高程；不能声称节省了固定百分比的请求量。
- GFS Global 的坐标 API 使用的模型网格分辨率可能与 `.om` 的 `ncep_gfs025` 不一致，因此 GFS 必须单独做模型运行/云量核对，不得将其当作同一原始网格进行精确对比。
- 即使 spatial_used，部分高度/云层依然有模式误差，不能把模型格点云量当作已确认的真实云洞。

官方资料：
https://github.com/open-meteo/open-data
https://open-meteo.github.io/python-omfiles/
