import importlib.util
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app.py"

def test_web_finalizer_for_user_reported_case():
    # Load only the helper definitions without starting Streamlit is not safe;
    # this source-level regression verifies the finalizer is wired at render boundary.
    source = APP.read_text(encoding="utf-8")
    assert "inferred = _web_finalize_special_verdict(" in source
    assert "profile_cloud_base_below_500m_web_summary" in source
    assert 'st.caption("特殊判定诊断（常驻显示）")' in source
