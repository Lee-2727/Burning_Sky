# Burning Sky · Pro / Lite / Desktop

此包以原版 Burning Sky V2.1.6（已包含 700 km 光路、太阳偏轴补采与 AOD 修复、Lite GFS 概览合并修复）为基础，仅清理冗余文件，并调整 Pro/Lite 五日概览柱状图。**本次没有更换天气计算引擎。**

## 三个入口

| 客户端 | 入口 | 启动 |
|---|---|---|
| Pro Web | `app.py` | `streamlit run app.py` |
| Lite Web | `lite/app.py` | `streamlit run lite/app.py` |
| Windows 桌面 | `desktop/fire_cloud_forecast_v1_4_67_local_200km_burn_scale.py` | 双击 `启动桌面版.bat` |

依赖：网页使用 `requirements.txt`，Lite 使用 `lite/requirements.txt`；Windows 本地可运行 `安装依赖.bat`。本包保留 `engine.py`、`burning_sky/`、`desktop/`、`clients/desktop/`、城市 Excel 和界面资源。

## 五日概览柱状图规则

- 高度严格对应火烧云规模，颜色对应色彩分。
- 柱体填充的不透明度严格等于 `常规概率/100`，没有独立可见描边、灰化系数或透明度保底。
- 概率为 0% **不绘制柱体**；缺失数据仍用 `—` 或虚线标记，两者不能混淆。
- 两个网页继续读取各自原来的概览数据入口，**不会因为这次 UI 修改触发重算或重建缓存**。

## GitHub / Streamlit Cloud 部署注意

1. 将 ZIP 中 `Burning_Sky-main/` **内部内容** 上传到仓库根目录，不要套第二层目录。
2. **保留现有仓库中的 `data/`、`.github/workflows/`、`.streamlit/secrets.toml` 和你自己的 `overview_cities.txt` 等配置。** ZIP 故意不附带这些历史/私密文件。不要使用“先清空整个仓库再上传”的方式部署。
3. Pro 与 Lite 应使用同一仓库和分支，分别指定 `app.py`、`lite/app.py`。
4. 如果你希望仓库中的旧 `CHANGELOG_*`、`README_DEBUG*` 等文件真正消失，单纯上传更精简的 ZIP **不会自动删除它们**，需在 GitHub 显式提交删除。
5. 详细 Secrets 与案例永久存储设置见 `PRO_LITE_DEPLOY_CN.md`。

## 清理范围

删除了旧版本逐条更新说明、历史实验说明、未被代码引用的设计展示页、只用于调试的单独案例重放脚本与重复启动 BAT。**未删除算法模块、运行时案例库功能、案例 ZIP 导出功能、测试代码、桌面客户端、字体/天空图片等资源。** 没有删除远端历史数据。

## 测试

运行核心图表与概览测试：

```bash
python -m unittest test_lite_overview_parity_fix test_pro_lite_release
```

离线测试不能替代 GitHub Actions 和 Streamlit Cloud 实际部署回归。
