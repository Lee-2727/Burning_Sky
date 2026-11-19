Burning Sky V2.3 R5 精简补丁
只包含本次修改的文件，不含完整项目、案例、缓存、debug_logs、桌面工具或 data。

上传方式：
1. 解压本 ZIP。
2. 将解压出的文件覆盖到 GitHub 仓库根目录，保持原有目录结构。
   - app.py、engine.py、REGULAR_PROBABILITY_RULES.md 放仓库根目录
   - burning_sky/ 下的 3 个文件放入仓库现有 burning_sky/ 目录
   - lite/app.py 放入仓库现有 lite/ 目录
3. 不要删除或覆盖仓库现有的 data/、.github/workflows/、其他文件。
4. Streamlit Cloud 部署完成后，重新查询武汉 GFS 朝霞，避免读取旧缓存。

本补丁只移除连续云经验判定，不修改严格球面光路追踪本身。
