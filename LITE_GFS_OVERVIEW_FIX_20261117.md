# Burning Sky V2.1.6 · Lite 五日 EC/GFS 发布与图表修复（2026-11-17）

## 修复对象
- `lite/overview_data.py`：与 Pro 采用相同的“完整发布优先，已完成检查点只补缺失”原则；逐日期、逐模型、逐朝晚霞合并城市与汇总文件，不再整份覆盖。
- `lite/bar_cards.py`：有效 0 值显示 `0` 标记；数据缺失显示 `—`；概率 0% 的非零规模保留轮廓，仅填充颜色按概率透明化。
- `lite/app.py`：显示有效项目统计（总计20，EC10、GFS10），增加「刷新已发布数据」按钮、检查点补缺提示。
- `test_lite_overview_parity_fix.py`：新增 7 项回归测试。

## 细节与部署
- Lite 五日概览仍是只读、不会触发 Open-Meteo 或 GitHub Actions。
- 优先使用 GitHub 已发布城市数据；raw GitHub URL 每分钟变化查询参数，降低 CDN 缓存陈旧风险；下载失败仍会读取本地副本。
- 只需要把补丁包的 `lite/` 文件覆盖同名代码；不用覆盖 `data/`、`.github/workflows/` 或仓库中的任何预测结果。
- 页面显示的 `有效 x/20` 表示这一日期窗口内已保存完整三项指标的记录数，**不是**后台任务运行进度。
- 软件不会自动对 `data/` 重算 GFS；如果发布文件确实缺少 GFS，需要修复上游发布工作流，而不是让 Lite 虚构结果。

## 本地验证
- `python -m unittest test_lite_overview_parity_fix test_pro_lite_release.LiteDataTests test_lite_ui_smoke -v`：14 项通过。
- 本地重放 2026-11-17 历史 `overview_合肥.json`：20/20 有效，EC10/10、GFS10/10；11/18 GFS 晚霞常规概率46%、规模23.39%。
- 尚未访问用户两套 Streamlit Cloud 实际部署页面；页面实际版本和远端网络缓存需要上线后核对。
