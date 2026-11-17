# Burning Sky Pro + Lite｜部署说明

## 1. 仓库与两套入口

- **同一个 GitHub 仓库：** `Lee-2727/Burning_Sky`。
- **Pro 主入口：** `app.py`（原网页，不变）。
- **Lite 主入口：** `lite/app.py`（另建一个 Streamlit Community Cloud App，选择相同仓库与分支，指定此入口）。
- 两个应用不会共享 `st.session_state`、服务器临时磁盘或卫星修正缓存；共享的是 GitHub 已发布的多日概览和同一仓库的预测代码。
- Lite 的“未来五日”只读取 `data/overview_合肥.json`、`data/overview.json`、`data/overview_checkpoint_合肥.json`，绝不触发后台任务，也不访问 Open-Meteo。
- Lite 的单次预测使用地点库、查询日期、EC/GFS模式；一次只查朝霞或晚霞。没有额外彩虹计算。
- Lite 的卫星校准自动识别至少4个地理一致的城市地标以及3类卫星图外观参考；校准失败不会改变原始结果。无法确认图像时需要换一张清晰截图，Lite 不开放人工修正。
- Lite 只显示精简指标：一句话、常规概率、色彩分、规模、模型数据时间、事件时间、低/中/高云量、上限概率、云色时序图、卫星前后对照。

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
- 案例存档只包含已保存的计算结果及气象请求缓存；不会自动备份服务器本地 24 小时卫星校准缓存。想永久保存卫星修正，应先将修正结果导出为完整案例ZIP再归档。

## 4. 交付检查结果与限制

- 代码复用同一个 `engine.py`；Lite 单次没有额外模型算法实现。
- 离线测试覆盖：合法0%结果、五日概览原始/检查点读取、自动全选区域计算、自动识别失败拒绝修正、本地案例ZIP保存读取及哈希验证、GitHub大文件raw模拟、Pro远端存档权限判断。
- 当前工作环境不包含 Streamlit 包，无法在本地真实启动浏览器进行UI点击回归，也没有你的 GitHub 私有令牌，不能替你完成实际远端存储试写。上线时需要按上述Secrets配置，并分别验证两个应用的操作。
