R5 紧急缓存抢救补丁（不调用天气 API）
只包含 2 个文件：
- burning_sky/interactive_result_cache.py
- lite/app.py

作用：
1. 保留 R5 新缓存读取逻辑。
2. 增加读取 V2.3 R4 原始缓存（schema_version=1、旧 cache key）的兼容回退。
3. 如果找到旧缓存，Lite 明确标注“R4 原始缓存”，避免误认为已经按 R5 重算。
4. 不改评分、云场、球面追踪算法；不主动发起天气 API 请求。

上传：
解压后覆盖 GitHub 仓库对应的两个文件。不要删除 data/interactive_forecast_cache。
部署后打开 Lite，选择原地点、日期、GFS、朝霞/晚霞，但不要点击“查询/运行”按钮。
如果服务器当天旧缓存仍存在，页面会显示恢复的 R4 缓存标记；如果未显示，说明服务器上的旧缓存不可访问/已不存在，本补丁不会伪造数据。
