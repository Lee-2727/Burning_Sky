# Burning Sky V1.4.67 — Engine Architecture Migration

This release is a compatibility-first architecture refactor. The validated v1.4.67
forecast calculations are retained; no new weather models are enabled in this build.

## Architecture

- `burning_sky/algorithm.py`: independent deterministic calculation core extracted
  verbatim from the v1.4.67 baseline. It has no Tkinter, Streamlit, HTTP, or file-I/O
  dependencies.
- `burning_sky/services/`: provider-neutral application service that calls the core.
- `burning_sky/models.py`: common request, model metadata, and normalized forecast
  data contracts. Native resolution, initialization time, and cloud provenance are
  retained so native and humidity-derived cloud fields are never silently conflated.
- `burning_sky/providers/`: provider interface and Open-Meteo migration adapter.
  The existing Open-Meteo fetch/parser remains in the compatibility runtime in this
  release; the adapter boundary is prepared for the next migration step.
- `engine.py`: shared v1.4.67 compatibility runtime. It hosts the existing data
  acquisition and UI-facing workflow and imports the independent algorithm module.
- `app.py`: Streamlit web client; uses the same shared runtime and algorithm.
- `desktop/` and `clients/desktop/`: launchers for the same shared desktop runtime.

## Run

Desktop (Windows):

```bash
python desktop/fire_cloud_forecast_v1_4_67_local_200km_burn_scale.py
```

Web:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Algorithm preservation

The pure calculation block (`clamp` through `score_two_day_event_shared_core`,
including AOD, cloud-base geometry, path scoring, low-cloud screen constraint,
color scoring, and two-day scoring) was moved without changing function bodies.
The desktop runtime source is the v1.4.67 desktop implementation, not the older
headless `engine.py` copy from the prior package. This also keeps the local 200 km
burn-scale label behavior in both clients.

The class-level live-simulation/report rendering and legacy Open-Meteo request flow
remain in the compatibility runtime for this migration release. They are deliberately
not rewritten in the same change, to minimize regression risk. The next step can
move provider I/O and the remaining UI-independent live-simulation calculations into
separate modules behind the same interfaces.

## Tests

Run:

```bash
python -m unittest discover -s tests -v
```

The regression tests compare representative algorithm outputs against values
captured from the original v1.4.67 source, and compile/import checks cover both
client entry points.

## 多天气模式接入（本次更新）

主预测界面的天气模式选择为以下五项，顺序固定：

1. ECMWF IFS 0.25°（默认）
2. GFS Global
3. ICON Global（Open-Meteo model id: `icon_global`）
4. CMA GRAPES Global（`cma_grapes_global`，约 10 天；Open-Meteo 公共数据服务可能拥堵）
5. ECMWF (AI)（`ecmwf_aifs025_single`，约 15 天；原生 6 小时数据由 API 插值为逐小时序列，部分云层分层由湿度场推算）

JMA MSM 已从可选模式目录注释停用，待后续确认数据服务可用性后再考虑恢复。

各模式均通过 Open-Meteo Generic Forecast API 的 `models` 参数明确指定，不使用 Best Match，不会在失败时静默切换到其他模式。各模式按自身预报时效请求数据。

桌面 GUI 和 Streamlit 网页版共用 `engine.WEATHER_MODEL_CATALOG`，保证选项和模型 ID 一致。核心评分算法未修改。

官方参考：
- Open-Meteo 通用 API 模型列表：https://open-meteo.com/en/docs
- ICON：https://open-meteo.com/en/docs/dwd-api
- JMA：https://open-meteo.com/en/docs/jma-api
- ECMWF IFS / AIFS：https://open-meteo.com/en/docs/ecmwf-api
- CMA GRAPES：https://open-meteo.com/en/docs/cma-api

## 未来多日概览（合肥固定地点）

- 网页左侧查询区底部新增「未来多日概览」入口，展示未来三个自然日的朝霞/晚霞。
- 每个方向按 ECMWF、GFS、ICON、CMA、ECMWF (AI) 排列，展示常规概率、色彩分和霞云规模指数。
- 霞云规模指数沿用 V1.4.67 的本地 200 km 采样算法：`max(0, max(中云, 高云) - 低云) × 可染色云量占比 / 100`。它是规模指数，不是发生概率。
- 后台更新脚本：`python scripts/update_overview.py`。脚本复用完整预测引擎，固定坐标 31.8047, 117.2214，保存到 `data/overview.json`。
- `.github/workflows/update-overview.yml` 配置北京时间每日 00、04、08、12、16、20 点运行（GitHub Actions cron 使用 UTC）；也支持手动触发。任务完成后提交快照，Streamlit Cloud 随仓库更新重新部署。
- GitHub 仓库需要允许 Actions 使用 `GITHUB_TOKEN` 写入 Contents。GitHub Actions 的定时任务属于 best-effort，可能因平台排队略有延迟；网页展示快照更新时间。
- JMA MSM 已从可选目录停用；旧 AIFS 显示名称仍保留内部兼容映射，新显示名为 `ECMWF (AI)`。
- 后台预计算会执行 5 个模式 × 3 天的完整朝晚霞计算，耗时和接口请求量明显高于单次查询。接口失败的模式/日期会留空，不会用其他模式结果冒充。

### Streamlit Cloud 引擎导入修复

网页版启动时会先将 `app.py` 所在的项目根目录加入 `sys.path`，再按绝对路径加载同目录 `engine.py`。这是因为 `engine.py` 还需要导入同目录下的 `burning_sky` Python 包；仅使用 `spec_from_file_location()` 加载引擎文件并不能保证该包可被 Python 找到。
