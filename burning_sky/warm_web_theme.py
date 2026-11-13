# -*- coding: utf-8 -*-
"""Burning Sky visual layer adapted from the user's V2_1_0 mockups.
All forecasting, cache, 429 and satellite behavior is left unchanged.
"""
from __future__ import annotations
import base64
from datetime import date
from pathlib import Path
import streamlit as st

PHOTOS = Path(__file__).resolve().parents[1] / "assets" / "sky"

STYLE = r"""
<style>
:root { --bs-text:#f6f5f8; --bs-muted:#d1cad6; --bs-accent:#ffd18b; --bs-border:rgba(255,255,255,.19); --bs-glass:rgba(25,25,38,.55); }
html { color-scheme:dark; }
/* The immersive dark sky from the reference rather than the previous light orange theme. */
[data-testid="stAppViewContainer"],.stApp {
 background:radial-gradient(ellipse 70% 34% at 52% 96%,rgba(245,147,103,.32),transparent 75%),
 linear-gradient(180deg,#141b30 0%,#362a45 32%,#6d465e 60%,#a46157 87%,#58394b 100%) fixed !important;
 color:var(--bs-text)!important;
}
[data-testid="stHeader"] {background:rgba(15,18,31,.18)!important; backdrop-filter:blur(14px);}
[data-testid="stSidebar"] {
 background:linear-gradient(165deg,rgba(20,24,39,.94),rgba(31,29,47,.9))!important;
 border-right:1px solid rgba(255,255,255,.17);backdrop-filter:blur(20px);
}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {padding-top:1.4rem;}
[data-testid="stMainBlockContainer"] {max-width:1370px;padding-top:1.2rem;padding-bottom:4rem;}
[data-testid="stAppViewContainer"] p,[data-testid="stAppViewContainer"] label,
[data-testid="stAppViewContainer"] li,[data-testid="stAppViewContainer"] span { color:inherit; }
h1,h2,h3,h4 {color:#fff5ed!important;letter-spacing:-.025em;font-weight:620!important;}
h1 {font-size:2.25rem!important;}h2 {font-size:1.45rem!important;}
[data-testid="stCaptionContainer"] {color:#e1d4d9!important;}
hr {border-color:rgba(255,255,255,.16)!important;}
[data-testid="stExpander"] {
 background:linear-gradient(140deg,rgba(37,41,59,.72),rgba(27,28,43,.66))!important;
 border:1px solid rgba(255,255,255,.19)!important;border-radius:20px!important;
 box-shadow:0 12px 34px rgba(9,10,24,.14);backdrop-filter:blur(22px);
}
[data-testid="stExpander"] summary {color:#fff4e8!important;}
[data-testid="stMetric"] {
 background:linear-gradient(140deg,rgba(43,44,62,.72),rgba(30,29,46,.72))!important;
 border:1px solid rgba(255,255,255,.17);border-radius:18px;padding:1rem 1.1rem;
 box-shadow:0 8px 32px rgba(9,10,25,.10);backdrop-filter:blur(18px);
}
[data-testid="stMetricLabel"], [data-testid="stMetricDelta"] {color:#e1d7dc!important;}
[data-testid="stMetricValue"] {color:#fff1db!important;}
.stButton > button,.stDownloadButton > button,[data-testid="stFormSubmitButton"] button {
 background:linear-gradient(145deg,rgba(255,255,255,.16),rgba(255,255,255,.07))!important;
 border:1px solid rgba(255,255,255,.28)!important;color:#fff4e8!important;
 border-radius:13px!important;box-shadow:0 5px 19px rgba(10,10,24,.08);
 font-weight:580!important;min-height:42px;transition:transform .14s,border-color .14s,background .14s;
}
.stButton > button:hover,.stDownloadButton > button:hover {border-color:rgba(255,211,154,.72)!important;
 background:rgba(255,204,153,.17)!important;transform:translateY(-1px);}
button[kind="primary"],[data-testid="stBaseButton-primary"] {
 background:linear-gradient(110deg,#ffc883,#f4a37e,#dd847e)!important;
 color:#3a2733!important;border-color:rgba(255,215,160,.52)!important;
 font-weight:720!important;
}
button[kind="primary"]:hover,[data-testid="stBaseButton-primary"]:hover {color:#2b2331!important;filter:brightness(1.07);}
[data-testid="stTextInput"] input,[data-testid="stNumberInput"] input,[data-testid="stDateInput"] input,textarea,
[data-baseweb="select"] > div, [data-testid="stTimeInput"] input {
 background:rgba(26,31,48,.80)!important;border:1px solid rgba(255,255,255,.25)!important;
 color:#fffaf4!important;border-radius:12px!important;
}
[data-baseweb="select"] *, [data-testid="stWidgetLabel"] p {color:#fff4ec!important;}
[data-baseweb="tab-list"] {background:rgba(22,25,40,.28);border-radius:15px;padding:.3rem;}
button[data-baseweb="tab"] {color:#ddd4dc!important;}
button[data-baseweb="tab"][aria-selected="true"] {color:#ffdcab!important;}
[data-baseweb="tab-highlight"] {background:#ffca8d!important;}
[data-testid="stProgressBar"] > div > div > div {background:linear-gradient(90deg,#f7ad8f,#ffe0a2)!important;}
[data-testid="stDataFrame"],[data-testid="stTable"] {border-radius:14px;overflow:hidden;}
/* Scientific plots remain original; don't overlay them with opacity or CSS filters. */
[data-testid="stImage"] img {border-radius:15px;max-width:100%;}
[data-testid="stAlert"] {border-radius:14px;}
.bs-v2-hero {height:228px;position:relative;overflow:hidden;border-radius:26px;margin:0 0 20px;
 border:1px solid rgba(255,255,255,.32);box-shadow:0 20px 45px rgba(14,15,26,.22);isolation:isolate;}
.bs-v2-hero .photo {position:absolute;inset:0;background-size:cover;background-position:center 52%;z-index:-2;}
.bs-v2-hero:before {content:"";position:absolute;inset:0;z-index:-1;background:linear-gradient(95deg,rgba(14,17,29,.85),rgba(20,21,39,.50) 55%,rgba(12,16,32,.08));}
.bs-v2-hero .inner {padding:34px 42px;}
.bs-v2-eyebrow {font-size:11px;letter-spacing:.24em;text-transform:uppercase;color:#ffddaa;margin-bottom:12px;}
.bs-v2-hero h1 {margin:0!important;color:#fff6ea!important;font-weight:640!important;font-size:42px!important;line-height:1.2;}
.bs-v2-hero .sub {font-size:14px;color:#f8e8df;margin-top:13px;max-width:600px;line-height:1.75;}
.bs-v2-hero .hint {position:absolute;bottom:17px;right:22px;font-size:11px;color:rgba(255,255,255,.78);letter-spacing:.02em;}
@media(max-width:720px){[data-testid="stMainBlockContainer"]{padding:1rem .75rem 3rem;}
 .bs-v2-hero{height:176px;border-radius:18px;margin-bottom:13px;}
 .bs-v2-hero .inner{padding:22px 20px;}.bs-v2-hero h1{font-size:27px!important;}
 .bs-v2-hero .sub{font-size:12px;line-height:1.6;max-width:90%;}.bs-v2-hero .hint{display:none;}
 h1{font-size:1.8rem!important;} h2{font-size:1.3rem!important;}
 [data-testid="stMetric"]{padding:.65rem .65rem;border-radius:14px;}
}
</style>
"""

def apply_warm_theme():
    """Backwards-compatible theme entry point used throughout the project."""
    st.markdown(STYLE, unsafe_allow_html=True)

def render_sky_hero(title="Burning Sky",subtitle="光线、云层与天空的精细预测", compact=False):
    """Use one of the user's 13 supplied sky photographs, changing by date."""
    files=sorted(PHOTOS.glob('sky*.jpg'))
    if not files:
        st.title(title)
        return
    selected=files[date.today().toordinal()%len(files)]
    uri=base64.b64encode(selected.read_bytes()).decode('ascii')
    import html
    title=html.escape(title)
    subtitle=html.escape(subtitle)
    st.markdown(f"""<section class="bs-v2-hero" role="banner">
      <div class="photo" style="background-image:url(data:image/jpeg;base64,{uri})"></div>
      <div class="inner"><div class="bs-v2-eyebrow">PHOTOGRAPHY WEATHER INTELLIGENCE</div>
      <h1>{title}</h1><div class="sub">{subtitle}</div></div>
      <div class="hint">SKY STUDY · PHOTOGRAPHY</div></section>""",unsafe_allow_html=True)
