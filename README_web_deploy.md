# 火烧云预报工具 Web 迁移版（基于 v1.4.49）

这是一个 Streamlit 网页包装项目。`engine.py` 由上传的 v1.4.49 原脚本生成：保留原有计算函数、天气请求方法与核心预报流程，仅移除 Tkinter 导入并将主类改为不创建 GUI 的普通类；`app.py` 提供浏览器表单与结果展示。

## 本地预览

需要 Python 3.11 或较新版本：

```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

浏览器打开 Streamlit 提示的本地地址。

## 私人访问密码

部署到可能公开访问的托管平台时，建议设置 `APP_PASSWORD`：

- Streamlit Cloud：在应用的 Settings / Secrets 中设置 `APP_PASSWORD = "你的长密码"`
- 其他托管：设置同名环境变量 `APP_PASSWORD`

未设置密码时，页面不启用密码门。请勿将真实密钥或密码提交到代码仓库。

## 免费云端部署（Streamlit Community Cloud）

1. 将本项目整个目录上传到自己的 GitHub 仓库。
2. 在 Streamlit Community Cloud 连接仓库，主文件选择 `app.py`。
3. 部署完成后，在应用设置里添加 `APP_PASSWORD`。
4. 用浏览器打开应用网址；免费实例休眠后首次打开可能需要等待。

具体平台策略可能变化，请以部署页面当前提示为准。

## 当前迁移范围

优先接通单次朝霞/晚霞预测流程，并复用原版沿线采样、云底剖面、CAMS AOD、低云约束、评分和动态亮度曲线生成。案例库、Excel 实况模拟、全国地图等依赖原 Tkinter 独立窗口的功能尚未迁入网页，需要后续逐项迁移。

## 注意

- 默认时区仍为 UTC+8，与原脚本一致。
- 评分为经验指标，不是经过历史观测校准的统计概率。
- 免费服务、天气接口可能有休眠、配额或限流情况。
