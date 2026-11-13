# Burning Sky 暖色简约主题更新（2026-11-13）

- 新增 `burning_sky/warm_web_theme.py`，统一网页风格：暖白 / 杏黄背景、低饱和橙红主按钮、暖灰文本、奶油白卡片。
- `app.py` 与 `pages/1_Advanced_Analysis.py` 均调用 `apply_warm_theme()`。
- 原先主页面的蓝色多日概览按钮改成与整体一致的珊瑚橙与奶油白色。
- 桌面 Tk 版和 Weather Photography 面板不变。
- 未修改火烧云计算引擎、图像本身的颜色、云色时序数据、剖面曲线、模型查询和缓存、任务逻辑。
- 本版属于纯样式整合，未实际在 Streamlit Cloud 的不同浏览器版本中验证 CSS 选择器。个别 Streamlit 组件在版本更新后可能仍显示默认色。
