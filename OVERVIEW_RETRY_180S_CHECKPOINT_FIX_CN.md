# Burning Sky 定时五日概览：失败后180秒续跑

本次基于 `Burning_Sky_V2_1_1_Engine_Failure_Diagnostic_NoRerun.zip` 修改，保持既有预测算法、网页及玻璃柱状图不变。

## 规则

- 每个任务由地点、日期、模式、朝霞/晚霞组成。失败时**等待至少180秒**，只重新创建该任务的预测对象并执行该任务，不会重跑此前已成功的任务。
- 每项在一轮后台执行中最多尝试**3次**（初次+两次180秒冷却重试）。限流器如果要求更长时间，则取更长冷却时间。429、网络异常、预测引擎没有返回有效指标均适用。
- 仍失败的任务写入 `data/overview_status.json` 的 `failures` 和 `retry_diagnostics`；成功的其他任务继续计算。
- 成功项目立即保存在 `data/overview_checkpoint_合肥.json`，中途失败时发布检查点。下次**同一天、同一组五日日期和模式**自动续跑缺失项目；跨天不错误沿用上一次任务完成标记。
- 所有20项成功后发布 `overview_合肥.json` 和 `overview.json`，并删除检查点；部分成功时保留最后一次完整发布，网页可使用检查点展示已验证的部分数据。

## 注意

- checkpoint 的粒度是单个朝霞/晚霞任务，并非某个任务内部的第几个HTTP请求。失败项重试时会重新执行该项；底层缓存能否复用取决于原有引擎缓存。
- 修改 GitHub 仓库根目录的 `scripts/update_overview.py`。不要将 `V2_1_1` 整个嵌套上传，也不要删除或覆盖已存在的 `data/` 文件。
- **无需手动重新触发20项。** 下次已有的 GitHub Actions 计划运行会按当前任务的有效检查点自动恢复；若日期切换则以新日期范围建立新任务。
- 日志应出现 `build=scheduled-v2.1.1-20261116-retry180-resume-v1` 和 `profile=scheduled policy=(12.0, 5)`。
- 重试不是算法修复：确定性的代码异常仍可能重复失败，完整诊断日志会保留。
- GitHub Actions 中请配置 workflow concurrency 互斥、适当提高 timeout-minutes，以允许实际等待；本ZIP未包含你仓库当前 `.github/workflows` 文件，无法代替修改原工作流。

## 本地模拟测试

- 第一次返回无有效结果、第二次成功：20/20顺利发布。
- 单项连续3次失败：继续后续19项，checkpoint保留19/20，不虚报已完成。
- 已有19/20同日检查点：仅执行最后一个任务并完成发布。
- 合法的 `probability=0`、`color_score=18`、`burn_scale=0` 判定为有效。

以上是模拟测试，不等于真实Open-Meteo / GitHub Actions端到端验证。
