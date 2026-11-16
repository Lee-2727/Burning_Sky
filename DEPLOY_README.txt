Burning Skies V2.1.4 - GitHub Actions 实际运行文件修复补丁

原因：2026-11-16 GitHub main 仍使用旧的 update-overview.yml（尾部有额外 git push），且 scripts/update_overview.py 仍是 scheduled-v2.1.1 旧版本。上一轮 ZIP 没有真正覆盖到仓库的隐藏 .github 目录。

请将补丁的这两个文件，按相同目录结构上传覆盖到 GitHub 仓库 Lee-2727/Burning_Sky 的 main 分支：
  .github/workflows/update-overview.yml
  scripts/update_overview.py
不要直接将 ZIP 上传为一个压缩包，不要嵌套 V2_1_4 文件夹。

网页操作：建议打开 GitHub 仓库，在 .github/workflows/update-overview.yml 点击铅笔编辑，粘贴补丁文件内容并 Commit；再按同路径覆盖 scripts/update_overview.py。

验证：
1. 在 GitHub 网站直接打开 .github/workflows/update-overview.yml，确认已经没有 `Commit overview data`、`git add`、`git push`。
2. 打开 scripts/update_overview.py，确认 UPDATER_BUILD_ID 为 v2.1.4-clean-worktree-publish-fresh-mode-20261116。
3. 新 Actions 日志出现该 build 标识，成功发布应出现 `from clean worktree`；不会再有 `Update multi-day overview` 的旧尾部 push。

注意：这只是发布代码修复，不会自行触发天气请求；保留 data/ 全部数据。此前那次 Actions 的20项结果如果未成功推送，也没有保存成 Artifact，不能声称能无请求恢复。
