Burning Skies V2.1.6 — Pro 五日概览防降级显示修复

问题：正式 overview_合肥.json 已包含完整五天 EC/GFS 朝晚霞20项，
但 app.py 因 overview_checkpoint_合肥.json 保存时间更新，直接将10/20
检查点作为页面展示数据，导致 GFS 缺失并错误显示10/20。

修复：
1. 优先使用当前五日真实有效记录最多的已发布概览数据，之后再比较更新时间。
2. 已发布完整20项时，绝不允许任何较新的部分检查点覆盖。
3. 若已发布数据缺项，只从当前有效检查点填充缺失项，保留已有有效EC/GFS记录。
4. 状态栏根据实际展示的完整20项显示；状态文件进度只作为非权威附注。
5. 概率0%、色彩分0、规模0%均视为合法有效结果。

仅覆盖仓库根目录下：
app.py
burning_sky/overview_status_ui.py
burning_sky/overview_snapshot_guard.py （新增）

不要覆盖 data/、lite/、scripts/、.github/workflows/。
不需要重新触发定时概览，也不需要清除缓存。

验证：使用 V2.1.6 自带20项正式数据，构造较新的10/20检查点，
通过「完整数据不降级」「缺项时只补缺失」「0%有效」「状态栏优先完整结果」测试。
未在线上 Streamlit Cloud 真实部署验证。
