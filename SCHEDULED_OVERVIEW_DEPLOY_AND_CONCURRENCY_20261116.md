# Burning Sky 定时五日概览：部署与启动验证（2026-11-16）

## 必须验证的日志
GitHub Actions -> 更新五日概览的运行日志，开头应出现：

`[BURNING SKY UPDATER] build=scheduled-v2.1.1-20261116-profile-check-push-rebase profile=scheduled policy=(12.0, 5)`

并输出：

`Updater version: scheduled-v2.1.1-20261116-profile-check-push-rebase`

如果看到 `interactive` 或旧版 `Hourly API limit detected`，说明 Actions 仍使用旧脚本/旧运行入口。先检查仓库根目录 `scripts/update_overview.py` 是否真的被覆盖。不要仅上传 `V2_1_1` 子文件夹而使根目录脚本继续运行旧版。

## 更新文件
- `scripts/update_overview.py`：在导入引擎之前启用 scheduled，启动时核查真实限速；分钟429冷却续跑；Git push 冲突时 fetch+rebase 有界重试（不强制推送）。
- `burning_sky/api_rate_limit.py`：对应定时档位为每次真实HTTP请求最小12秒、每分钟最多5次。
- 单次预测界面、卫星校准和图表未做额外改动。

## 工作流必须串行运行
这个 ZIP 不携带你的 GitHub 原有 `.github/workflows` 文件，因此不会自动修正旧的 cron 和并发配置。请在**实际运行的** `.github/workflows/update-overview.yml`（以你仓库实际文件名为准）加上工作流顶层配置：

```yaml
concurrency:
  group: burning-sky-overview-publisher
  cancel-in-progress: false
```

注意：同一个仓库里所有能向 `main` 写入 `data/overview*.json` 的工作流，也应使用这个相同 group；否则可能照样并发推送冲突。不要随意覆盖原有 workflow 触发时间和其他步骤。

## 数据保留
仅按文件相对路径覆盖代码，不要上传 ZIP 的整个 `V2_1_1/` 文件夹到仓库根目录造成嵌套，也不要覆盖 `data/` 已有结果。检查点只有在相同日期、相同城市、模型、日期范围才复用，跨日不会自动沿用旧检查点。

## 注意
减少请求频率可以降低分钟级429发生概率，但 Open-Meteo 还可能依IP、API请求权重、并发用户或小时/日限额限制。这个 ZIP 没有在真实 GitHub Actions 和 Open-Meteo 网络端到端执行。
