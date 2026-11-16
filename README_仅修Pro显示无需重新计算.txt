修复文件：根目录 app.py 和 burning_sky/overview_visual.py。仅覆盖这两个文件。不要覆盖 lite/app.py、data/ 或 .github/workflows/。
修复：Pro 五日柱状图采用纯HTML/CSS，移除会被Streamlit过滤的SVG；长时间停留在0/20的旧 running 状态不再宣称正在计算。
不会请求 Open-Meteo，不会触发 Github Actions。
