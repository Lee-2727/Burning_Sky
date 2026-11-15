# 定时五日概览 HTTP 429 修复说明（2026-11-16）

- `scripts/update_overview.py` 在加载预测引擎前设置 `BURNING_SKY_REQUEST_PROFILE=scheduled`，不再误用 interactive 的35次/分。
- `burning_sky/api_rate_limit.py` 把 scheduled 独立设为最小12秒一次真实HTTP请求、滚动60秒不超过5次；不调整单次 interactive 或卫星进阶档位。
- 定时任务继续在每项朝霞/晚霞之间留至少20秒，按真实HTTP出口限速。
- 遇到明确的 `Minutely API request limit exceeded`，按冷却时间等待并最多重试3次当前项，而不是误当 Hourly 限流立即终止。
- 发生长期限额或重试耗尽时，保存已成功结果到 `data/overview_checkpoint_合肥.json`。下次同一天相同模型和日期的定时任务恢复成功项，不重复请求；成功发布后删除检查点。
- GitHub Actions 会把检查点和状态提交推送至仓库，以便换runner续跑。不要从GitHub删除 checkpoint，除非你想强制重跑当日全部20项。
- 概览正式数据仍只在完整20项成功后发布，避免新旧混合数据伪装成完整预测。
- 未改朝晚霞计算逻辑、卫星校准、美工和五日柱状图。
- 本地已通过Python编译和无HTTP的模拟批处理测试；GitHub Actions/Open-Meteo真实运行尚未验证，实际额度也可能受共享IP与请求成本影响。
