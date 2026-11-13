# -*- coding: utf-8 -*-
"""Shared, purely presentational Streamlit warm theme for Burning Sky pages.

No data requests, caching, physics or chart palettes are modified here.
"""
import streamlit as st

STYLE = r"""
<style>
:root {
  --bs-ink: #483b37;
  --bs-muted: #826b64;
  --bs-coral: #db765c;
  --bs-coral-deep: #bd5948;
  --bs-border: rgba(177,113,84,.21);
  --bs-shadow: 0 8px 30px rgba(111,63,42,.045);
}
/* Warm, light canvas. Gradients stay out of all scientific plots and images. */
[data-testid="stAppViewContainer"], .stApp {
  background: linear-gradient(145deg, #fffaf4 0%, #fff4e6 38%, #fff6ea 68%, #fff2e6 100%) !important;
  color: var(--bs-ink);
}
[data-testid="stHeader"] {background: rgba(255,249,240,.77) !important;}
[data-testid="stSidebar"] {
  background: linear-gradient(175deg, #fff3df 0%, #fff8ef 48%, #ffeddf 100%) !important;
  border-right: 1px solid var(--bs-border);
}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {padding-top: 1.15rem;}
[data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
  color: #955448 !important;
}
[data-testid="stMainBlockContainer"] {
  padding-top: 2.4rem;
  padding-bottom: 4rem;
  max-width: 1420px;
}
h1, h2, h3, h4 {color: #75463d !important; letter-spacing: -.012em;}
h1 {font-weight: 760 !important;}
h2, h3 {font-weight: 690 !important;}
[data-testid="stCaptionContainer"], .stCaption {color: var(--bs-muted) !important;}
/* Soft dividers and restrained panel chrome. */
hr {border-color: var(--bs-border) !important;}
[data-testid="stVerticalBlockBorderWrapper"] > div[style*="border"] {
  background: rgba(255,254,251,.82);
  border-color: var(--bs-border) !important;
  border-radius: 17px !important;
  box-shadow: var(--bs-shadow);
}
[data-testid="stExpander"] {
  background: rgba(255,254,250,.86);
  border: 1px solid var(--bs-border) !important;
  border-radius: 15px !important;
  overflow: hidden;
  box-shadow: var(--bs-shadow);
}
[data-testid="stExpander"] details summary {color: #7f4c42; font-weight: 620;}
/* Controls are uniform on desktop and mobile. */
.stButton > button, .stDownloadButton > button,
[data-testid="stFormSubmitButton"] > button,
[data-testid="stBaseButton-secondary"] {
  border-radius: 11px !important;
  border: 1px solid #eac5af !important;
  background: linear-gradient(135deg,#fffaf3,#ffe9d7) !important;
  color: #825345 !important;
  font-weight: 600 !important;
  box-shadow: 0 2px 7px rgba(128,70,41,.035);
  transition: background .16s, border-color .16s, box-shadow .16s;
}
.stButton > button:hover, .stDownloadButton > button:hover {
  border-color: #dd9b7c !important;
  background: linear-gradient(135deg,#fff5e4,#ffdeca) !important;
  color: #9f4d3c !important;
}
button[kind="primary"], [data-testid="stBaseButton-primary"] {
  background: linear-gradient(112deg,#cc6456 0%,#e89470 58%,#f3b775 100%) !important;
  border: 1px solid #d87862 !important;
  color: #fff !important;
  box-shadow: 0 5px 14px rgba(198,91,69,.13);
}
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {
  background: linear-gradient(112deg,#bb554b,#dc8064 60%,#eaa65f) !important;
  color: #fff !important;
}
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
[data-testid="stDateInput"] input, textarea,
[data-baseweb="select"] > div {
  border-radius: 10px !important;
  border-color: #ead4c7 !important;
  background-color: rgba(255,255,252,.96) !important;
  color: #493c38 !important;
}
[data-testid="stTextInput"] input:focus, [data-testid="stNumberInput"] input:focus,
[data-testid="stDateInput"] input:focus {
  border-color: #d8846f !important;
  box-shadow: 0 0 0 1px rgba(216,132,111,.16) !important;
}
/* Tabs, radio checks, progress; do not recolor scientific figure content. */
[data-baseweb="tab-list"] {border-bottom: 1px solid var(--bs-border);}
button[data-baseweb="tab"] {color: #85695f !important;}
button[data-baseweb="tab"][aria-selected="true"] {color: #ba624e !important;}
[data-baseweb="tab-highlight"] {background: #d77a5e !important;}
[data-testid="stProgressBar"] > div > div > div {background: linear-gradient(90deg,#d96a5f,#f3bd79) !important;}
[data-testid="stMetric"] {
  border: 1px solid rgba(210,153,121,.22);
  border-radius: 14px;
  padding: .7rem .9rem;
  background: linear-gradient(145deg,rgba(255,255,252,.94),rgba(255,242,226,.78));
}
[data-testid="stMetricValue"] {color: #ad594a !important;}
/* Tables must remain readable (especially long scientific diagnostics). */
[data-testid="stDataFrame"], [data-testid="stTable"] {
  border: 1px solid var(--bs-border);
  border-radius: 12px;
  overflow: hidden;
}
/* Explicit existing navigation buttons: warm versions, do not hide them. */
.st-key-open_multi_day_overview button {
  background: linear-gradient(115deg,#d97963,#ebad79) !important;
  border-color: #d97861 !important;
  color: white !important;
}
.st-key-open_multi_day_overview button:hover {
  background: linear-gradient(115deg,#c76553,#e39a6d) !important;
  color: white !important;
}
.st-key-open_manual_overview button {
  background: #fff9f1 !important;
  border-color: #e6b99c !important;
  color: #a75b49 !important;
}
@media (max-width: 720px) {
  [data-testid="stMainBlockContainer"] {padding-top: 1.3rem; padding-left: .75rem; padding-right: .75rem;}
  h1 {font-size: 1.75rem !important;}
  h2 {font-size: 1.3rem !important;}
  [data-testid="stMetric"] {padding: .55rem .6rem;}
  [data-testid="stExpander"] {border-radius: 12px !important;}
}
</style>
"""

def apply_warm_theme():
    """Inject CSS only; this is safe to call in every Streamlit page."""
    st.markdown(STYLE, unsafe_allow_html=True)
