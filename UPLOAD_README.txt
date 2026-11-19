Burning Sky Lite 离线缓存重放补丁

仅 3 个代码文件，覆盖仓库对应路径：
- engine.py
- burning_sky/lite_forecast.py
- lite/app.py

使用：
1. 解压并覆盖以上 3 个文件，保持目录结构。
2. 打开 Lite -> 单次预测。
3. 在页面顶部“离线案例重放”上传 Pro 导出的案例 ZIP。
4. 点击“读取缓存并离线重算”。
5. 查看 Pro 存档与 Lite 离线重算对照表。

安全说明：
- 离线重放只读取 ZIP 内 request_cache.json。
- 每次请求都必须在缓存中精确匹配；缺失时立即报错。
- 不会联网补请求，不消耗 Open-Meteo API 配额。
- 本次功能不修改算法规则、评分阈值、OM/GFS 云场策略或现有查询缓存。
- 仅对包含 case_manifest.json、analysis_state.json、request_cache.json 的完整案例 ZIP 生效。

本补丁已用用户提供的 2026-11-20 武汉 GFS Pro 案例做离线回归：
Pro / Lite 重放：
常规概率 73 / 73
色彩分 74 / 74
几何规模 71.12 / 71.12
视觉规模 84.86 / 84.86
几何可染色占比 83.3 / 83.3
视觉可染色占比 99.4 / 99.4
缓存请求命中 16 / 16
