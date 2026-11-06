# 网页手动多日概览 · 本次修改说明（V2_0_7）

本轮只改网页交互与后台任务可靠性，预测算法、评分口径、自动概览流程均未改动。

## 1. “手动查询多日概览”独立成页

- 左侧栏在「未来多日概览」按钮下方新增「手动查询多日概览」按钮（白底蓝边，与上方入口区分），点击进入独立页面。
- 原概览页底部的内嵌手动区块整体移出，概览页只保留三模式三日的自动结果，不再出现“概览 → 手动查询”混排。
- 手动页顶部提供「← 返回单次预报」「未来多日概览 →」两个返回入口。
- **自动切页**：在手动页里直接点左侧「查询朝霞 / 查询晚霞 / 查询并更新朝晚霞」时，`show_manual_overview` 会先被清掉，本次查询结果页在同一轮渲染中直接显示，不会被 `st.stop()` 或旧页面状态截断。

## 2. 修复 `No master specified and tkinter is configured to not support default root`

- 根因：`engine.py` 的预测流程末尾调用 `messagebox.showinfo("查询完成", …)`。在无显示环境（GitHub Actions、容器）第一次调用时 `Tk()` 创建失败抛出 `TclError`，同时把 tkinter 的 `_support_default_root` 置为 False；异常被 `_fetch_weather_task` 的 `except` 捕获后又去调用 `messagebox.showerror`，于是抛出这条 `RuntimeError`，把已经算完的结果整项判为失败。
- 修法：
  - `engine.py` 新增 `gui_is_available()`，在导入 tkinter 之前检查显示环境（Linux 需要 `DISPLAY` 或 `WAYLAND_DISPLAY`；`BURNING_SKY_HEADLESS=1` 时强制无界面）。无界面时使用无副作用替身（含 `after` 等 Tk 兼容方法），不导入 tkinter。
  - `_tracked_showerror` / `_tracked_showinfo` 捕获弹窗自身的异常，弹窗失败再也不会中断计算；错误/提示仍可通过 `engine.messagebox.last_error` / `last_info` 读取。
  - `app.py` 在加载引擎前设置 `BURNING_SKY_HEADLESS=1`，Streamlit 永远不会尝试打开 Tk 窗口。

## 3. 手动结果按“日期 → 朝霞/晚霞 → 模式”展示

- 新增 `_render_manual_overview_result()`：三个日期各一块，块内左右两列分别是 🌄 朝霞 与 🌆 晚霞，每列一张三模式表格（模式 / 常规概率 / 色彩分 / 火烧云规模），列名、单位、配色与自动多日概览完全一致。
- 缺失项显示 `—`，不再把朝霞晚霞塞进同一张平铺表；失败项收在“失败详情”折叠区，并显示 `已发布 x/18 · 状态 · 数据获得时间 · 来源（GitHub 已发布文件 / 本地缓存）`。

## 4. 阻止并发启动手动概览任务

- 页面读取该工作流全部运行记录，只要有 `queued / in_progress / waiting / pending / requested` 的任务：
  - 显示醒目提示，并给出运行编号；
  - 「▶ 启动 GitHub 后台更新」按钮直接禁用。
- 点击启动时再做一次服务端确认：重新拉取运行列表，存在活动任务就报错「请等待上一个手动多日概览任务完成」并且**不提交**；另外加 30 秒内重复提交冷却，避免主页与多标签页同时点两次。
- 网络异常导致无法确认任务状态时，按“不确定即阻止”处理，不会盲目提交。

## 5. 顺带修复的两处后台问题

- `scripts/manual_overview_action.py` / `scripts/update_overview.py`：headless runner 允许显式传入坐标。原来它只查 `overview_cities.txt`，选表外地名（如上海）会 `KeyError` 导致 18 项全失败。
- `.github/workflows/manual-overview.yml`：发布结果前先 `git stash --include-untracked` 清理工作区，避免运行时产生的缓存/调试文件让 `git rebase` 失败、结果发布不出去。

## 验证

- `python3 -m py_compile app.py engine.py scripts/*.py` 通过。
- 无显示环境（`DISPLAY` 未设置）跑一个模式朝/晚霞完整预测：均返回结果，无 Tk 报错。
- Streamlit AppTest：默认页、未来多日概览页、手动查询页均无异常；手动页在“合肥 · 三个日期”缓存结果下渲染 6 张表；在手动页点「查询朝霞」后 `show_manual_overview` 变 False，标题切回单次预报并显示「🌄 朝霞」结果与“查询完成”。
