# Pro / Lite 部署

在 Streamlit Cloud 中使用同一个仓库，分别创建入口为 `app.py` 和 `lite/app.py` 的应用。Lite 优先读取仓库维护的地点表，缺失时使用 `lite/cities_fallback.xlsx`。

## Lite Secrets

```toml
BURNING_SKY_GITHUB_REPOSITORY = "Lee-2727/Burning_Sky"
BURNING_SKY_GITHUB_BRANCH = "main"
# LITE_APP_PASSWORD = "可选访问密码"
```

公开仓库的三日概览可直接读取。两套应用的当日查询和校准缓存各自保存，按北京时间跨日清理。

## Pro 案例库 Secrets

如需永久保存案例，使用独立私有仓库。令牌只授权该仓库的 Contents 读写权限。访问控制可使用 `APP_PASSWORD` 或独立的 `CASE_LIBRARY_ADMIN_PASSWORD`。

```toml
APP_PASSWORD = "访问密码"
BURNING_SKY_CASE_GITHUB_REPO = "你的账号/案例仓库"
BURNING_SKY_CASE_GITHUB_BRANCH = "main"
BURNING_SKY_CASE_GITHUB_TOKEN = "案例仓库令牌"
```

未配置远端案例库时，可下载案例 ZIP 或使用临时本地存档。
