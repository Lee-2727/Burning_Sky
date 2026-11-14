# -*- coding: utf-8 -*-
"""Shared, purely presentational Streamlit theme for Burning Sky pages.

No data requests, caching, physics or chart palettes are modified here.
This module only injects CSS, a theme switcher (session_state backed) and an
optional daily-rotating hero backdrop built from bundled sky photographs.
"""
import os
import base64
import html as _html
import datetime

import streamlit as st

# --------------------------------------------------------------------------- #
#  Nine immersive sky themes. Only presentational sky / glow / accent variables.
# --------------------------------------------------------------------------- #
THEMES = {
    "golden": {
        "label": "日落",
        "sky1": "#2A2447", "sky2": "#5B3E63", "sky3": "#C2645A", "sky4": "#7E4A44",
        "glow_a": "rgba(255,176,110,.55)", "glow_b": "rgba(255,120,76,.30)",
        "glow_c": "rgba(255,88,64,.13)", "accent": "#FFCF8A", "eve": "#FF9C7A",
    },
    "crimson": {
        "label": "赤红",
        "sky1": "#2A0E1C", "sky2": "#6B1E2E", "sky3": "#C8323C", "sky4": "#7E2430",
        "glow_a": "rgba(255,110,80,.50)", "glow_b": "rgba(230,60,60,.26)",
        "glow_c": "rgba(200,40,50,.12)", "accent": "#FFB3A0", "eve": "#FF7E6E",
    },
    "gold": {
        "label": "鎏金",
        "sky1": "#3A2A18", "sky2": "#8A5A26", "sky3": "#E8A94A", "sky4": "#9C6A2E",
        "glow_a": "rgba(255,214,130,.55)", "glow_b": "rgba(255,170,80,.28)",
        "glow_c": "rgba(255,140,60,.12)", "accent": "#FFE0A0", "eve": "#FFC47A",
    },
    "peach": {
        "label": "橘粉",
        "sky1": "#3A2438", "sky2": "#8A4A5E", "sky3": "#F09A8E", "sky4": "#C9747A",
        "glow_a": "rgba(255,186,150,.50)", "glow_b": "rgba(255,140,120,.26)",
        "glow_c": "rgba(255,120,110,.12)", "accent": "#FFD2B8", "eve": "#FFA898",
    },
    "blue": {
        "label": "蓝调",
        "sky1": "#142238", "sky2": "#26405E", "sky3": "#4A5E7E", "sky4": "#2C3A52",
        "glow_a": "rgba(120,170,220,.30)", "glow_b": "rgba(90,140,200,.16)",
        "glow_c": "rgba(70,120,180,0)", "accent": "#9FD2F5", "eve": "#8FB8E8",
    },
    "teal": {
        "label": "青暮",
        "sky1": "#0E2630", "sky2": "#1E4A52", "sky3": "#3E7E78", "sky4": "#244E52",
        "glow_a": "rgba(120,210,190,.26)", "glow_b": "rgba(80,170,160,.14)",
        "glow_c": "rgba(60,140,140,0)", "accent": "#9FE8D8", "eve": "#8FC8C0",
    },
    "lilac": {
        "label": "淡紫",
        "sky1": "#241E3E", "sky2": "#4E3E6E", "sky3": "#8E6E9E", "sky4": "#5E4A72",
        "glow_a": "rgba(190,160,230,.30)", "glow_b": "rgba(150,120,210,.16)",
        "glow_c": "rgba(120,100,190,0)", "accent": "#D8C4FF", "eve": "#B8A8E8",
    },
    "dawn": {
        "label": "晨霞",
        "sky1": "#3A2E4E", "sky2": "#7A5570", "sky3": "#E89A86", "sky4": "#C97A6A",
        "glow_a": "rgba(255,206,160,.50)", "glow_b": "rgba(255,150,120,.26)",
        "glow_c": "rgba(255,120,90,.12)", "accent": "#FFD9A0", "eve": "#FFB49A",
    },
    "night": {
        "label": "夜色",
        "sky1": "#0B1020", "sky2": "#161E33", "sky3": "#2A2C44", "sky4": "#181B2E",
        "glow_a": "rgba(120,130,200,.22)", "glow_b": "rgba(90,100,170,.12)",
        "glow_c": "rgba(70,80,150,0)", "accent": "#C9B8FF", "eve": "#9FB0E8",
    },
}

DEFAULT_THEME = "golden"


def _theme_css_block(key, t):
    return (
        f'[data-bs-theme="{key}"]{{'
        f'--bs-sky1:{t["sky1"]};--bs-sky2:{t["sky2"]};--bs-sky3:{t["sky3"]};--bs-sky4:{t["sky4"]};'
        f'--bs-glow-a:{t["glow_a"]};--bs-glow-b:{t["glow_b"]};--bs-glow-c:{t["glow_c"]};'
        f'--bs-accent:{t["accent"]};--bs-eve:{t["eve"]};'
        f'}}'
    )


STYLE = r"""
<style>
:root{
  --bs-ink:#FFFFFF; --bs-dim:rgba(255,255,255,.70); --bs-faint:rgba(255,255,255,.44);
  --bs-line:rgba(255,255,255,.16); --bs-hairline:rgba(255,255,255,.11);
  --bs-card:rgba(255,255,255,.09); --bs-card-line:rgba(255,255,255,.18);
  --bs-sky1:#2A2447; --bs-sky2:#5B3E63; --bs-sky3:#C2645A; --bs-sky4:#7E4A44;
  --bs-glow-a:rgba(255,176,110,.55); --bs-glow-b:rgba(255,120,76,.30); --bs-glow-c:rgba(255,88,64,.13);
  --bs-accent:#FFCF8A; --bs-eve:#FF9C7A;
}
/* Full-bleed dusk sky backdrop */
[data-testid="stAppViewContainer"], .stApp{
  background:
    radial-gradient(120% 60% at 50% 108%, var(--bs-glow-a) 0%, var(--bs-glow-b) 38%, var(--bs-glow-c) 62%, transparent 78%),
    linear-gradient(179deg, var(--bs-sky1) 0%, var(--bs-sky2) 34%, var(--bs-sky3) 66%, var(--bs-sky4) 100%) !important;
  background-attachment: fixed;
  color: var(--bs-ink);
}
[data-testid="stHeader"]{ background: transparent !important; }
[data-testid="stSidebar"]{
  background: linear-gradient(175deg, rgba(10,12,22,.42), rgba(10,12,22,.22)) !important;
  border-right: 1px solid var(--bs-line);
  backdrop-filter: blur(20px);
}
[data-testid="stSidebar"] [data-testid="stSidebarContent"]{ padding-top: 1.15rem; }
[data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3{ color: var(--bs-ink) !important; }
[data-testid="stMainBlockContainer"]{ padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1420px; }
h1,h2,h3,h4{ color: var(--bs-ink) !important; letter-spacing: -.012em; }
h1{ font-weight: 760 !important; } h2,h3{ font-weight: 690 !important; }
[data-testid="stCaptionContainer"], .stCaption, .stMarkdown p, .stMarkdown li{ color: var(--bs-dim) !important; }
hr{ border-color: var(--bs-line) !important; }

/* Frosted-glass floating panels */
[data-testid="stVerticalBlockBorderWrapper"] > div[style*="border"],
[data-testid="stExpander"]{
  background: var(--bs-card) !important;
  border: 1px solid var(--bs-card-line) !important;
  border-radius: 18px !important;
  backdrop-filter: blur(24px) saturate(150%);
  -webkit-backdrop-filter: blur(24px) saturate(150%);
  box-shadow: 0 12px 34px rgba(0,0,0,.22);
}
[data-testid="stExpander"]{ overflow: hidden; }
[data-testid="stExpander"] details summary{ color: var(--bs-ink); font-weight: 620; }
[data-testid="stExpander"] details summary:hover{ color: var(--bs-accent); }

/* Controls */
.stButton > button, .stDownloadButton > button,
[data-testid="stFormSubmitButton"] > button,
[data-testid="stBaseButton-secondary"]{
  border-radius: 12px !important;
  border: 1px solid var(--bs-card-line) !important;
  background: rgba(255,255,255,.10) !important;
  color: var(--bs-ink) !important;
  font-weight: 600 !important;
  backdrop-filter: blur(12px);
  transition: background .16s, border-color .16s, box-shadow .16s;
}
.stButton > button:hover, .stDownloadButton > button:hover{
  border-color: var(--bs-accent) !important;
  background: rgba(255,255,255,.18) !important;
  color: #fff !important;
}
button[kind="primary"], [data-testid="stBaseButton-primary"]{
  background: linear-gradient(112deg, var(--bs-eve) 0%, var(--bs-accent) 100%) !important;
  border: 1px solid var(--bs-accent) !important;
  color: #1a120c !important;
  box-shadow: 0 6px 18px rgba(0,0,0,.28);
}
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover{
  filter: brightness(1.08); color: #1a120c !important;
}

/* Theme switcher pills: sit on the sky, only a faint white wash to separate.
   No white card background. Selected pill keeps the accent tint. */
.st-key-bs_theme_picker button,
.stButtonGroup button[role="tab"]{
  background: rgba(255,255,255,.07) !important;
  border: 1px solid rgba(255,255,255,.14) !important;
  color: var(--bs-dim) !important;
  border-radius: 10px !important;
}
.st-key-bs_theme_picker button:hover,
.stButtonGroup button[role="tab"]:hover{
  background: rgba(255,255,255,.14) !important;
  color: var(--bs-ink) !important;
}
.st-key-bs_theme_picker button[aria-checked="true"],
.stButtonGroup button[role="radio"][aria-checked="true"]{
  background: rgba(255,255,255,.16) !important;
  border-color: var(--bs-accent) !important;
  color: var(--bs-ink) !important;
}

/* Sidebar: soften the native pale/dark hairlines into transparent white washes.
   All same-level navigation buttons share one color, weight and surface. */
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]{
  border-color: rgba(255,255,255,.10) !important;
  background: rgba(255,255,255,.07) !important;
  color: var(--bs-ink) !important;
  font-weight: 600 !important;
}
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover{
  border-color: rgba(255,255,255,.18) !important;
  background: rgba(255,255,255,.13) !important;
  color: #fff !important;
}
/* Manual page_link navigation in the sidebar: match the unified light ink. */
[data-testid="stSidebar"] [data-testid="stPageLink"],
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"],
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] span,
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] p{
  color: var(--bs-ink) !important;
  font-weight: 600 !important;
}
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"]:hover{
  color: #fff !important;
}
/* Auto-generated page nav links at the top of the sidebar: brighten from the
   native dark grey to the same light ink as the rest of the sidebar. */
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"],
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] span,
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] p{
  color: var(--bs-dim) !important;
  font-weight: 500 !important;
}
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover,
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover span{
  color: var(--bs-ink) !important;
}
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"]{
  color: var(--bs-ink) !important;
  font-weight: 600 !important;
}
[data-testid="stSidebar"] [data-testid="stBaseButton-headerNoPadding"]{
  border-color: rgba(255,255,255,.12) !important;
  color: var(--bs-ink) !important;
  background: transparent !important;
}
[data-testid="stSidebar"] [data-testid="stBaseButton-headerNoPadding"] svg{
  color: var(--bs-ink) !important;
}
[data-testid="stSidebar"]{ border-right-color: rgba(255,255,255,.10) !important; }
[data-testid="stSidebar"] hr{ border-color: rgba(255,255,255,.10) !important; }
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
[data-testid="stDateInput"] input, textarea,
[data-baseweb="select"] > div{
  border-radius: 11px !important;
  border-color: var(--bs-card-line) !important;
  background-color: rgba(255,255,255,.10) !important;
  color: var(--bs-ink) !important;
}
[data-testid="stTextInput"] input::placeholder, [data-testid="stNumberInput"] input::placeholder{ color: var(--bs-faint); }
[data-testid="stTextInput"] input:focus, [data-testid="stNumberInput"] input:focus,
[data-testid="stDateInput"] input:focus{
  border-color: var(--bs-accent) !important;
  box-shadow: 0 0 0 1px rgba(255,255,255,.12) !important;
}
[data-baseweb="select"] > div [role="button"]{ color: var(--bs-ink) !important; }
[data-baseweb="select"] svg{ color: var(--bs-dim) !important; }

/* Tabs / radio / progress */
[data-baseweb="tab-list"]{ border-bottom: 1px solid var(--bs-line); }
button[data-baseweb="tab"]{ color: var(--bs-dim) !important; }
button[data-baseweb="tab"][aria-selected="true"]{ color: var(--bs-accent) !important; }
[data-baseweb="tab-highlight"]{ background: var(--bs-accent) !important; }
[data-testid="stProgressBar"] > div > div > div{ background: linear-gradient(90deg, var(--bs-eve), var(--bs-accent)) !important; }

/* Metrics */
[data-testid="stMetric"]{
  border: 1px solid var(--bs-card-line);
  border-radius: 15px;
  padding: .75rem .95rem;
  background: rgba(255,255,255,.08);
  backdrop-filter: blur(16px);
}
[data-testid="stMetricLabel"]{ color: var(--bs-dim) !important; }
[data-testid="stMetricValue"]{ color: var(--bs-ink) !important; }

/* Tables / dataframes stay legible on the dark sky */
[data-testid="stDataFrame"], [data-testid="stTable"]{
  border: 1px solid var(--bs-card-line);
  border-radius: 14px;
  overflow: hidden;
}
.stDataFrame td, .stDataFrame th, .stTable td, .stTable th{ color: var(--bs-ink) !important; }

/* Checkbox / radio labels */
.stCheckbox label, .stRadio label, .stToggle label{ color: var(--bs-dim) !important; }

/* Native widget surfaces -> dark glass (labels, borders, backgrounds) */
:where([data-testid="stTextInputRootElement"], [data-testid="stNumberInputContainer"],
[data-testid="stDateInputFieldsScroller"], [data-testid="stDateInputField"],
[data-testid="stSelectbox"],
[data-testid="stDateInputRootElement"], [data-testid="stSelectboxRootElement"],
.react-aria-ComboBox > div, .react-aria-DatePicker > div,
[data-baseweb="input"] > div, [data-baseweb="number-input"] > div,
[data-baseweb="select"] > div, [data-baseweb="datepicker"] > div, textarea){
  background-color: rgba(255,255,255,.10) !important;
  border-color: var(--bs-card-line) !important;
}
:where([data-baseweb="input"] input, [data-baseweb="number-input"] input,
[data-baseweb="select"] [data-baseweb="button"],
[data-baseweb="datepicker"] input, textarea,
[data-testid="stNumberInputField"], [data-testid="stTextInputField"]){
  color: var(--bs-ink) !important;
}
/* Dropdown menu panel */
[data-baseweb="popover"] [role="listbox"], [data-baseweb="menu"]{
  background-color: #232030 !important;
  border: 1px solid var(--bs-card-line) !important;
}
[data-baseweb="popover"] [role="option"], [data-baseweb="menu"] li{ color: var(--bs-ink) !important; }
/* Alert / info / warning / success / error boxes */
[data-testid="stAlert"], .stAlertContainer{
  background: rgba(255,255,255,.08) !important;
  border: 1px solid var(--bs-card-line) !important;
  border-radius: 14px !important;
  color: var(--bs-ink) !important;
}
[data-testid="stAlert"] *{ color: var(--bs-dim) !important; }
[data-testid="stAlert"] a{ color: var(--bs-accent) !important; }
/* Radio / segmented control selected pill */
[data-baseweb="radio"] > div, [data-baseweb="checkbox"] > div{ background: transparent !important; }

/* Navigation buttons share the unified glass sidebar style above. */
.st-key-open_manual_overview button{
  background: rgba(255,255,255,.10) !important;
  border-color: var(--bs-card-line) !important; color: var(--bs-ink) !important;
}

/* Hero backdrop (daily rotating sky photo) */
.bs-hero{
  position: relative; border-radius: 22px; overflow: hidden;
  border: 1px solid var(--bs-card-line);
  box-shadow: 0 16px 44px rgba(0,0,0,.30);
  margin: 0 0 1.4rem 0;
}
.bs-hero img{ display:block; width:100%; height:300px; object-fit:cover; }
.bs-hero::after{
  content:""; position:absolute; inset:0;
  background: linear-gradient(180deg, rgba(10,12,22,.10) 0%, rgba(10,12,22,.55) 100%);
}
.bs-hero-cap{ position:absolute; left:22px; bottom:18px; z-index:2; color:#fff; }
.bs-hero-cap .t{ font-size:22px; font-weight:800; letter-spacing:-.01em; }
.bs-hero-cap .s{ font-size:13px; color:rgba(255,255,255,.78); margin-top:3px; }

@media (max-width: 720px){
  [data-testid="stMainBlockContainer"]{ padding-top: 1.3rem; padding-left: .75rem; padding-right: .75rem; }
  h1{ font-size: 1.75rem !important; } h2{ font-size: 1.3rem !important; }
  [data-testid="stMetric"]{ padding: .55rem .6rem; }
  [data-testid="stExpander"]{ border-radius: 14px !important; }
  .bs-hero img{ height: 210px; }
}
</style>
"""


def _theme_picker_css():
    return "".join(_theme_css_block(k, v) for k, v in THEMES.items())


def current_theme():
    """Resolved theme key, stored in session_state so the choice persists per session."""
    saved = st.session_state.get("bs_theme", DEFAULT_THEME)
    return saved if saved in THEMES else DEFAULT_THEME


def sync_theme_from_picker():
    """If the segmented control already holds a choice, mirror it into bs_theme.

    Runs before the theme CSS is injected so the active variables match the
    selected pill on the same rerun. Purely presentational.
    """
    choice = st.session_state.get("bs_theme_picker")
    if not choice:
        return
    for k, v in THEMES.items():
        if v["label"] == choice:
            st.session_state["bs_theme"] = k
            return


def render_theme_switcher():
    """Render the nine-theme pill switcher. Purely presentational; no data side effects."""
    active = current_theme()
    labels = [v["label"] for v in THEMES.values()]
    st.segmented_control(
        "配色主题",
        labels,
        default=THEMES[active]["label"],
        key="bs_theme_picker",
        help="切换天空配色，仅改变外观，不影响任何预测结果。",
    )


def _sky_asset_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    # burning_sky/warm_web_theme.py -> package dir -> app root/assets/sky
    root = os.path.dirname(os.path.dirname(here))
    candidates = [
        os.path.join(root, "assets", "sky"),
        os.path.join(os.getcwd(), "assets", "sky"),
        os.path.join(os.environ.get("BURNING_SKY_ROOT", ""), "assets", "sky"),
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return candidates[0]


def daily_sky_image():
    """Pick one bundled sky photo by calendar day: same photo all day, rotates daily.

    Returns an absolute path to the bundled jpg, or None when the folder is absent.
    """
    folder = _sky_asset_dir()
    try:
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    except OSError:
        return None
    if not files:
        return None
    today = datetime.date.today()
    seed = today.year * 10000 + today.month * 100 + today.day
    return os.path.join(folder, files[seed % len(files)])


def _encode_image(path):
    try:
        with open(path, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return ""


def _esc(text):
    return _html.escape(str(text))


def render_hero(caption_title=None, caption_sub=None):
    """Full-width hero card showing the daily sky photograph.

    The hero is a display layer only; it never reads or changes forecast data.
    """
    img_path = daily_sky_image()
    if not img_path:
        return
    title = caption_title or "合肥 · 今日晚霞"
    sub = caption_sub or "核心展示区 · 每日随机实拍"
    st.markdown(
        f'<div class="bs-hero">'
        f'<img src="data:image/jpeg;base64,{_encode_image(img_path)}" alt="天空实拍" />'
        f'<div class="bs-hero-cap"><div class="t">{_esc(title)}</div>'
        f'<div class="s">{_esc(sub)}</div></div></div>',
        unsafe_allow_html=True,
    )


def apply_warm_theme():
    """Inject the dark immersive theme and active theme variables.

    Safe to call in every Streamlit page. Kept the original public name so existing
    page imports do not change. The active theme is written straight into :root so
    no client-side script is required (Streamlit strips inline <script> tags).
    """
    sync_theme_from_picker()
    theme = current_theme()
    t = THEMES[theme]
    active_vars = (
        f'--bs-sky1:{t["sky1"]};--bs-sky2:{t["sky2"]};--bs-sky3:{t["sky3"]};--bs-sky4:{t["sky4"]};'
        f'--bs-glow-a:{t["glow_a"]};--bs-glow-b:{t["glow_b"]};--bs-glow-c:{t["glow_c"]};'
        f'--bs-accent:{t["accent"]};--bs-eve:{t["eve"]};'
    )
    root_override = f"<style>:root:root{{{active_vars}}}</style>"
    st.markdown(STYLE + root_override, unsafe_allow_html=True)
