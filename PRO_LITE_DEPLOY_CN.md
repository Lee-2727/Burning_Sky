# Burning Sky Pro + Lite｜部署说明

## 1. 仓库与两套入口

- **同一个 GitHub 仓库：** `Lee-2727/Burning_Sky`。
- **Pro 主入口：** `app.py`（原网页，不变）。
- **Lite 主入口：** `lite/app.py`（另建一个 Streamlit Community Cloud App，选择相同仓库与分支，指定此入口）。
- 两个应用不会共享 `st.session_state` 或服务器临时磁盘缓存；各自保存自己的当日 EC/GFS 预测结果与卫星校准缓存。共享的是 GitHub 已发布的多日概览和同一仓库的预测代码。
- Lite 的“未来五日”只读取 `data/overview_合肥.json`、`data/overview.json`、`data/overview_checkpoint_合肥.json`，绝不触发后台任务，也不访问 Open-Meteo。
- Lite 的单次预测支持 EC、GFS、EC&GFS；EC 默认。EC&GFS 依次计算两个模式，一次选择一个方向（朝霞或晚霞）。
- Lite 与 Pro 使用同一核心引擎。常规可染色占比只接受严格云底受光，云锋面不进入主占比；Lite 对该算法路径有一致性检查，缺少云底诊断字段时拒绝显示旧版结果。
- Lite 的卫星校准自动识别至少4个地理一致的城市地标以及3类卫星图外观参考；当日 EC/GFS 都有缓存时，会自动比较云带、云洞和纹理匹配度并选择基准，不要求手动选模型。校准失败不会改变原始结果。
- Lite 主显示视觉规模，不显示几何规模；Pro 保留几何规模辅助诊断。色彩分是共同的大气色彩质量分。

**上传注意：** 将 ZIP 中 `Burning_Sky-main/` *内部的文件* 放在 GitHub 仓库根目录，不要再套一层 `Burning_Sky-main/`。不能仅上传 Lite 文件而遗漏 `burning_sky` 共用模块。

**保护数据：** 不覆盖、不删除原有 `data/`，也不覆盖原有 `.github/workflows/`。本包不包含这两个路径，因此不会清空旧五日概览或检查点，也不会自动触发后台20项任务。原 `.github/workflows` 仍按原计划执行。

## 2. Lite Secrets（只读概览）

在 Lite 对应的 Streamlit App → Settings → Secrets 中，推荐配置：

```toml
BURNING_SKY_GITHUB_REPOSITORY = "Lee-2727/Burning_Sky"
BURNING_SKY_GITHUB_BRANCH = "main"
# LITE_APP_PASSWORD = "可选：为 Lite 设置访问密码"
```

如果当前仓库为公开仓库，无需额外 GitHub token 就可以读取五日概览。Lite 不会获得 Pro 的存档写入令牌。Lite 入口所在的 `lite/requirements.txt` 定义轻量版 Python 依赖，仓库根目录的 `packages.txt` 含 OCR 与中文字体依赖。

Lite 优先读取你持续维护的根目录 `china_city_coordinates.xlsx` 或 `shared_locations/china_city_coordinates.xlsx`；如文件不存在，使用 `lite/cities_fallback.xlsx` 作为独立参考。因用户要求 Lite 不提供坐标/海拔修改，请维护正确的地点库数据。

## 3. Pro 永久案例库（需要独立设置私密存储）

建议新建一个**私有** GitHub 仓库（例如 `Lee-2727/Burning_Sky_Cases`），供 Pro 存案例。避免将人物隐私、私人机位、完整天气采样等案例 ZIP 直接放进公开的 Burning_Sky 主仓库。

在 Pro App → Settings → Secrets 中配置：

```toml
APP_PASSWORD = "建议：Pro 的访问密码"
BURNING_SKY_CASE_GITHUB_REPO = "Lee-2727/Burning_Sky_Cases"
BURNING_SKY_CASE_GITHUB_BRANCH = "main"
BURNING_SKY_CASE_GITHUB_TOKEN = "github_pat_请在GitHub生成令牌后自行填入"
# 如果你不想给 Pro 整站加访问密码，则改用以下独立管理口令：
# CASE_LIBRARY_ADMIN_PASSWORD = "一个独立强密码"
```

- **Fine-grained personal access token** 只授权给案例仓库，权限为 Contents → Read and write。请不要将真实令牌或口令提交到 GitHub/截图/日志中。
- 分支必须已存在（独立案例仓库创建时选择初始化README即可）。仓库根目录无需提前建立 `burning_sky_cases`；首次保存时会自动创建该目录。
- 保存会把案例 ZIP 和同 ID 的 JSON 索引写入案例仓库；两步写入成功且索引被远端读取核验，网页才显示“已永久保存至 GitHub”。读取时校验 ZIP 的 SHA256，避免损坏却显示为成功。
- GitHub Contents API 的常规 JSON 模式不能读取超过1MiB的ZIP，修正版使用认证 raw 媒体类型读取大文件；建议案例保持合理大小（单个ZIP上限75MiB），超过上限请手动下载归档。
- 不设置以上两个 GitHub 案例库密钥时，Pro 仍允许临时本地存档，但会警告服务器重部署可能丢失；**仅调整 `BURNING_SKY_CASE_LIBRARY_DIR` 并不能自动获得永久存储**。
- **权限保护：** 有远端写入令牌时，需已通过 Pro 登录（APP_PASSWORD）或提供 CASE_LIBRARY_ADMIN_PASSWORD，才会开放远端案例列举、下载、保存和删除。
- 当日缓存位于每个应用自己的本地 `data/interactive_forecast_cache/`，卫星校准缓存位于 `data/advanced_analysis_cache/`；以中国标准时间跨日清理前一日缓存。两应用之间不共享缓存，且 Streamlit Cloud 容器重启/重新部署可能清空临时磁盘。想长期保存卫星修正，应先将修正结果导出为完整案例 ZIP 再归档。

## 4. 交付检查结果与限制

- 代码复用同一个 `engine.py`；Lite 单次没有额外模型算法实现。
- 离线测试覆盖：合法0%结果、五日概览原始/检查点读取、自动全选区域计算、自动识别失败拒绝修正、本地案例ZIP保存读取及哈希验证、GitHub大文件raw模拟、Pro远端存档权限判断。
- 本次代码检查包含 Python 全量语法编译、当日缓存写入/读取/跨日清理、Lite EC&GFS 顺序执行、云底算法路径一致性和视觉规模分级单元测试。当前工作环境未连接 Open-Meteo 实时接口，也未在你的 Streamlit Cloud 实例上进行真实在线请求；上线后仍需分别验证 Pro 与 Lite。
