# Burning Sky V2.2.6 · Pro / Lite / Desktop

本包包含 Burning Sky 的网页 Pro、网页 Lite 与 Windows 桌面端，共用同一套核心预测引擎。

## 运行入口

| 客户端 | 入口 | 启动方式 |
|---|---|---|
| Pro 网页版 | `app.py` | `streamlit run app.py` |
| Lite 网页版 | `lite/app.py` | `streamlit run lite/app.py` |
| Windows 桌面版 | `desktop/fire_cloud_forecast_v1_4_67_local_200km_burn_scale.py` | 双击「启动桌面版.bat」 |

网页依赖使用根目录 `requirements.txt`；Lite 可使用 `lite/requirements.txt`。桌面端依赖使用根目录桌面启动配置；独立 GFS/OM 诊断入口才需要 `requirements-desktop-debug.txt`。

## 数据与配置

- `china_city_coordinates.xlsx`、`shared_locations/`、`lite/cities_fallback.xlsx`：地点及山峰匹配数据，请按需维护。
- `assets/`：界面图片资源。
- `burning_sky/world_coast_simplified.json`：全国地图所需静态地理边界数据。
- `.github/workflows/`：多日概览自动更新工作流。
- 运行时缓存由程序按中国标准时间管理；本发布包不附带历史缓存、调试日志或测试样例。


## 单次查询与当日缓存

- Pro 与 Lite 的查询模式均为 EC、GFS、EC&GFS；EC 为默认模式。EC&GFS 按 EC → GFS 顺序执行，不并发请求。
- Pro/Lite 分别在各自部署中保存当日最后一次成功的 EC/GFS 朝霞、晚霞完整结果及请求响应缓存（含 EC 空间云场辅助数据）。切换模型或时段时优先读取当日缓存，不重新请求天气 API。
- 当日缓存按 `Asia/Shanghai` 日期分区；新的一天首次启动/读取时自动删除前一日结果。Pro 与 Lite 是两个独立 Streamlit 应用，缓存不跨应用共享；Streamlit Cloud 若发生容器重启或重新部署，临时磁盘缓存可能丢失。
- 卫星云图校准会读取当日可用的 EC/GFS 缓存。只有一个模型时直接使用；两个模型都有时，按截图配准后的云带/云洞外观匹配评分自动选择基准，不要求用户手动选 EC/GFS。评分是相对匹配指标，不是卫星反演云量。

## 算法与结果显示

- Pro、Lite 使用同一 `engine.py` 核心计算；常规朝霞/晚霞可染色占比只接受严格验证的云底受光，云锋面/侧面只保留诊断，不进入主占比。
- 网页主结果使用视觉规模；Pro 以小字保留几何规模辅助诊断。色彩分是共同的大气色彩质量分，当前算法没有独立的“几何色彩分”。
- 视觉规模分级：低于3%不烧；3–15%微烧；15–40%小烧；40–65%中烧；65–90%大烧；超过90%世纪大烧。

## Open-Meteo 请求控制

桌面版与网页版的单次单模式查询使用 `interactive` 请求档位。HTTP 429 不再触发长时间的全局交互冷却锁定；旧版本留下的 `interactive` 冷却状态也会被忽略。正常的请求间隔与每分钟请求数量控制仍然保留，以降低重复触发限流的风险。

卫星校准、手动概览和定时概览继续使用各自独立的请求档位与保护策略，不随单次单模式查询规则改变。

## GitHub / Streamlit Cloud 部署

1. 将本包根目录中的内容上传到仓库根目录，不要额外套一层目录。
2. 更新时保留仓库已有的 `data/`、`.github/workflows/`、`.streamlit/secrets.toml` 及你自行维护的配置文件；本包不包含这些运行数据或私密配置。
3. Pro 与 Lite 可使用同一仓库和分支，分别指定 `app.py`、`lite/app.py` 作为入口。
4. 不要为了更新代码而清空整个仓库，否则会误删已发布的概览数据和个人配置。

算法口径可参阅 `README_GEOMETRIC_AND_VISUAL_BURN_SCALE_CN.md` 与 `REGULAR_PROBABILITY_RULES.md`；部署细节见 `PRO_LITE_DEPLOY_CN.md`。
