# 网页手动三模式三日后台任务（GitHub Actions）

1. 将本 ZIP 内容上传到 GitHub 仓库，包含 `.github/workflows/manual-overview.yml` 与 `scripts/manual_overview_action.py`。
2. 在 Streamlit Cloud → App settings → Secrets 中设置：

```toml
BURNING_SKY_GITHUB_REPOSITORY = "你的用户名/你的仓库"
BURNING_SKY_GITHUB_BRANCH = "main"
BURNING_SKY_GITHUB_TOKEN = "你的GitHub细粒度访问令牌"
```

GitHub 细粒度令牌限制为该仓库，授权 **Actions: Read and write**；如果仓库私有且需要读取仓库元数据，确认 Metadata 可读。**令牌绝不可提交到仓库**。建议同时设置 `APP_PASSWORD` 限制网页访问。

3. GitHub 仓库 Settings → Actions → General → Workflow permissions，确认 Actions 有 Contents 写权限，以发布结果 JSON。
4. 网页“未来多日概览”中，填写地点、坐标和日期后点击“启动 GitHub 后台更新”。网页无需保持打开。
5. “停止任务”取消当前该手动概览工作流正在运行或排队的任务；停止不是即时的。按钮不影响定时的 `update-overview.yml`。
6. 网页“刷新状态”查看 GitHub 运行状态。任务结束后，结果写入 `data/manual_overview_runs/<请求ID>.json`，网页从 GitHub 获取。当前实现**不提供18项实时细分进度**，只显示 Actions 运行状态及最终结果；日志会逐项打印 `MANUAL_OVERVIEW_PROGRESS`。

注意：工作流取消时，GitHub 可能跳过结果发布步骤；已发布的上一次结果不会删除。GitHub Actions 的实际执行仍受 API 限流器和 Actions 配额限制。不要把后台工作流的成功与全部18项预测成功混淆：网页会列出失败项。
