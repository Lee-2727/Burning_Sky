Burning Skies V2.1.4：定时概览发布与手动刷新修复

这次从用户上传的 V2_1_4.zip 出发。未修改 lite/app.py、lite/bar_cards.py 等美工代码。

关键改动
1) scripts/update_overview.py 使用干净的临时 Git worktree 发布指定数据文件，不再在运行中的工作目录 rebase。
2) GitHub 同时有新提交时，重新 fetch、基于最新 main 重新构建数据提交，最多5次，不强制推送。
3) GitHub 发布失败不会被假装成发布成功。最后20项算完但推送失败时，日志明确提示发布失败。
4) 增加 BURNING_SKY_OVERVIEW_RUN_MODE=resume/fresh；默认 resume 继续当天同条件检查点；fresh 无视当天检查点完成标记，重新计算本次未来5天。
5) 提供 .github/workflows/update-overview.yml，去掉旧工作流里与 Python 发布相冲突的末尾 git push，并设置互斥。
6) 定时触发 cron 为 UTC 19:00、01:00、06:00，对应北京时间03:00、09:00、14:00；GitHub Actions 定时可能延迟。

部署方式
- 将 ZIP 内 V2_1_4/ 的内容按原有目录放在仓库根目录。
- 关键文件 scripts/update_overview.py 和 .github/workflows/update-overview.yml 都要替换。
- 原仓库的 requirements.txt 保持原状，ZIP 没有覆盖它。
- 保留整个 data/，无需删除 checkpoint 和已经发布的 overview JSON。
- 在 GitHub Actions > Update Burning Skies five-day overview > Run workflow，选择 resume（默认）或 fresh。
- resume：继续同日未完成的条目；fresh：从头计算当前未来五日。两者都不需要删除旧的 data/。
- 其他也写入 data/overview*.json 的工作流如存在，请使用同一个并发组或禁止重复发布，以减少冲突。脚本有推送冲突重试，但无法保证外部工作流永远不发生修改。

提醒
这是避免“20/20已计算却因额外 git push 失败”的修复，不保证气象 API 不会返回429。任何计算失败仍保留检查点，按已有180秒恢复策略续跑。
