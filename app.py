"""
NAQEL Express — Analytics Intelligence Platform
================================================
Premium Streamlit dashboard — works on Databricks (via dbutils) OR locally.

Environment detection (automatic):
  • Databricks notebook / job  → reads token from dbutils.notebook.entry_point
  • Local machine              → reads token from databricks_api_key.txt

Auto-refresh: configurable live polling so the dashboard stays current as the
DLT pipeline processes new data dropped by live_stream.py.
"""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ── Environment detection ───────────────────────────────────────────────────────
def _detect_env() -> tuple[str, str, str]:
    """
    Returns (TOKEN, HOST, WH_ID) from whichever environment we're running in.

    Priority order:
      1. Streamlit secrets  (st.secrets["DATABRICKS_TOKEN"] etc.)
      2. Environment variables  DATABRICKS_TOKEN / DATABRICKS_HOST / DATABRICKS_WH_ID
      3. Databricks dbutils  (running inside a Databricks notebook or job)
      4. Local key file  databricks_api_key.txt
    """
    HOST_DEFAULT = "https://dbc-0e5855b4-0c93.cloud.databricks.com"
    WH_DEFAULT   = "e77494e943c4fe65"

    # 1. Streamlit secrets
    try:
        if "DATABRICKS_TOKEN" in st.secrets:
            return (
                st.secrets["DATABRICKS_TOKEN"],
                st.secrets.get("DATABRICKS_HOST", HOST_DEFAULT),
                st.secrets.get("DATABRICKS_WH_ID",  WH_DEFAULT),
            )
    except Exception:
        pass

    # 2. Environment variables
    env_token = os.environ.get("DATABRICKS_TOKEN")
    if env_token:
        return (
            env_token,
            os.environ.get("DATABRICKS_HOST", HOST_DEFAULT),
            os.environ.get("DATABRICKS_WH_ID",  WH_DEFAULT),
        )

    # 3. Databricks dbutils (running inside a notebook / job)
    try:
        import IPython
        ip = IPython.get_ipython()
        if ip is not None:
            dbutils = ip.user_ns.get("dbutils")
            if dbutils:
                token = dbutils.notebook.entry_point.getDbutils().notebook().getContext() \
                    .apiToken().get()
                host  = dbutils.notebook.entry_point.getDbutils().notebook().getContext() \
                    .apiUrl().get()
                return token, host, WH_DEFAULT
    except Exception:
        pass

    # Also try the spark context approach used inside Databricks Repos
    try:
        ctx = __import__("dbruntime.dbutils").dbutils.notebook.entry_point \
            .getDbutils().notebook().getContext()
        return ctx.apiToken().get(), ctx.apiUrl().get(), WH_DEFAULT
    except Exception:
        pass

    # 4. Local key file
    for candidate in [
        Path(__file__).parent.parent / "databricks_api_key.txt",
        Path("databricks_api_key.txt"),
    ]:
        if candidate.exists():
            raw = candidate.read_text()
            token = next(
                (l.split(":", 1)[1].strip()
                 for l in raw.splitlines()
                 if l.strip().lower().startswith("tokenid:")),
                None,
            )
            if token:
                return token, HOST_DEFAULT, WH_DEFAULT

    raise RuntimeError(
        "No Databricks credentials found.\n"
        "Set DATABRICKS_TOKEN env var, add to st.secrets, or provide databricks_api_key.txt"
    )


try:
    TOKEN, HOST, WH_ID = _detect_env()
except RuntimeError as _e:
    TOKEN, HOST, WH_ID = "", "https://dbc-0e5855b4-0c93.cloud.databricks.com", "e77494e943c4fe65"

NS = "naqel_lakehouse.naqel_express"

def _make_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

HEADERS = _make_headers(TOKEN)

# ── Pre-populate session state with detected credentials (first run only) ───────
def _init_session_state():
    if "active_token" not in st.session_state:
        st.session_state["active_token"] = TOKEN
    if "active_host"  not in st.session_state:
        st.session_state["active_host"]  = HOST
    if "active_wh"    not in st.session_state:
        st.session_state["active_wh"]    = WH_ID

_init_session_state()

# ── Design tokens ───────────────────────────────────────────────────────────────
C_NAVY   = "#001F5B"
C_BLUE   = "#003087"
C_GOLD   = "#C8A84B"
C_GOLD_L = "#E8C96B"
C_RED    = "#D62828"
C_GREEN  = "#1DB954"
C_GREY   = "#94A3B8"
C_DARK   = "#0A0F1E"
C_CARD   = "#0D1629"
C_BORDER = "#1E2D4D"

PALETTE  = [C_BLUE, C_GOLD, "#00B4D8", "#7B2FBE", C_GREEN, C_RED,
            "#FB8500", "#06D6A0", "#EF476F", "#118AB2"]

# ── Page config ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NAQEL Express Analytics",
    page_icon="🚚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global CSS ──────────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

/* ─── Base ─────────────────────────────────────────────── */
html, body, [class*="css"] {{
  font-family: 'Inter', sans-serif !important;
}}
.stApp {{
  background: {C_DARK};
}}
#MainMenu, footer, header {{ visibility: hidden; }}
.block-container {{ padding: 1.5rem 2rem 2rem !important; }}

/* ─── Sidebar ──────────────────────────────────────────── */
[data-testid="stSidebar"] {{
  background: linear-gradient(180deg, {C_NAVY} 0%, {C_DARK} 100%) !important;
  border-right: 1px solid {C_BORDER};
}}
[data-testid="stSidebar"] * {{ color: #CBD5E1 !important; }}
[data-testid="stSidebar"] .stRadio label {{
  display: flex; align-items: center;
  padding: 8px 12px; border-radius: 8px;
  transition: all .15s ease; cursor: pointer;
  font-size: .85rem; font-weight: 500;
}}
[data-testid="stSidebar"] .stRadio label:hover {{
  background: rgba(200,168,75,.12);
  color: {C_GOLD_L} !important;
}}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{
  font-size: .72rem; letter-spacing: .06em;
  text-transform: uppercase; color: {C_GREY} !important;
  margin: 1rem 0 .3rem;
}}
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stSelectbox div {{
  color: #CBD5E1 !important; font-size: .82rem;
}}
[data-testid="stSidebar"] .stButton button {{
  background: rgba(200,168,75,.15) !important;
  border: 1px solid {C_GOLD} !important;
  color: {C_GOLD_L} !important;
  border-radius: 8px !important; font-size: .8rem;
  width: 100%;
}}

/* ─── Page header ──────────────────────────────────────── */
.page-hero {{
  background: linear-gradient(135deg, {C_NAVY} 0%, #0A1628 50%, {C_DARK} 100%);
  border: 1px solid {C_BORDER};
  border-radius: 16px;
  padding: 28px 32px 22px;
  margin-bottom: 24px;
  position: relative; overflow: hidden;
}}
.page-hero::before {{
  content: '';
  position: absolute; top: -60px; right: -60px;
  width: 220px; height: 220px;
  background: radial-gradient(circle, rgba(200,168,75,.18) 0%, transparent 70%);
  border-radius: 50%;
}}
.page-hero h1 {{
  font-size: 1.75rem; font-weight: 800;
  background: linear-gradient(90deg, #FFFFFF 30%, {C_GOLD_L} 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  margin: 0 0 4px;
}}
.page-hero .subtitle {{
  font-size: .82rem; color: {C_GREY}; letter-spacing: .02em;
}}

/* ─── KPI cards ─────────────────────────────────────────── */
.kpi-grid {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 24px; }}
.kpi-card {{
  flex: 1; min-width: 160px;
  background: {C_CARD};
  border: 1px solid {C_BORDER};
  border-radius: 14px;
  padding: 18px 20px 16px;
  position: relative; overflow: hidden;
  transition: transform .2s ease, box-shadow .2s ease;
}}
.kpi-card:hover {{
  transform: translateY(-2px);
  box-shadow: 0 8px 32px rgba(0,0,0,.4);
}}
.kpi-card::after {{
  content: '';
  position: absolute; bottom: 0; left: 0; right: 0;
  height: 3px;
  background: var(--accent);
  border-radius: 0 0 14px 14px;
}}
.kpi-card.c-blue  {{ --accent: {C_BLUE};  }}
.kpi-card.c-gold  {{ --accent: {C_GOLD};  }}
.kpi-card.c-green {{ --accent: {C_GREEN}; }}
.kpi-card.c-red   {{ --accent: {C_RED};   }}
.kpi-icon  {{ font-size: 1.4rem; margin-bottom: 10px; display: block; }}
.kpi-value {{
  font-size: 1.7rem; font-weight: 800;
  color: #F1F5F9; line-height: 1;
  margin-bottom: 4px;
}}
.kpi-label {{ font-size: .72rem; color: {C_GREY}; font-weight: 500; letter-spacing: .04em; text-transform: uppercase; }}
.kpi-delta-pos {{ font-size: .72rem; color: {C_GREEN}; font-weight: 600; margin-top: 4px; }}
.kpi-delta-neg {{ font-size: .72rem; color: {C_RED};   font-weight: 600; margin-top: 4px; }}

/* ─── Section title ─────────────────────────────────────── */
.section-title {{
  font-size: .68rem; font-weight: 700;
  letter-spacing: .12em; text-transform: uppercase;
  color: {C_GOLD}; margin: 20px 0 10px;
  display: flex; align-items: center; gap: 8px;
}}
.section-title::after {{
  content: ''; flex: 1; height: 1px;
  background: linear-gradient(90deg, {C_BORDER}, transparent);
}}

/* ─── Chart cards ───────────────────────────────────────── */
.chart-card {{
  background: {C_CARD};
  border: 1px solid {C_BORDER};
  border-radius: 14px;
  padding: 18px;
  margin-bottom: 16px;
}}

/* ─── Streamlit elements overrides ──────────────────────── */
h1, h2, h3 {{ color: #F1F5F9 !important; }}
p, li, span {{ color: #CBD5E1; }}

.stTabs [data-baseweb="tab-list"] {{
  gap: 4px;
  background: {C_CARD};
  border-radius: 10px;
  padding: 4px;
  border: 1px solid {C_BORDER};
}}
.stTabs [data-baseweb="tab"] {{
  background: transparent;
  border-radius: 8px !important;
  color: {C_GREY} !important;
  font-size: .8rem; font-weight: 500;
  padding: 6px 18px;
  border: none !important;
}}
.stTabs [aria-selected="true"] {{
  background: linear-gradient(135deg, {C_BLUE}, {C_NAVY}) !important;
  color: white !important;
  box-shadow: 0 2px 8px rgba(0,48,135,.4);
}}

div[data-testid="stDataFrame"] {{
  border: 1px solid {C_BORDER} !important;
  border-radius: 10px !important;
  overflow: hidden;
}}
.stDataFrame th {{
  background: {C_NAVY} !important;
  color: {C_GOLD} !important;
  font-size: .72rem !important;
  letter-spacing: .06em;
  text-transform: uppercase;
}}
.stDataFrame td {{
  background: {C_CARD} !important;
  color: #CBD5E1 !important;
  font-size: .8rem !important;
  border-color: {C_BORDER} !important;
}}

.stAlert {{
  background: rgba(200,168,75,.08) !important;
  border: 1px solid rgba(200,168,75,.25) !important;
  border-radius: 10px !important;
}}

div[data-testid="stMetric"] {{
  background: {C_CARD};
  border: 1px solid {C_BORDER};
  border-radius: 12px;
  padding: 14px 18px !important;
}}
div[data-testid="stMetric"] label {{
  color: {C_GREY} !important; font-size: .72rem !important;
  text-transform: uppercase; letter-spacing: .05em;
}}
div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
  color: #F1F5F9 !important; font-size: 1.4rem !important;
  font-weight: 700 !important;
}}

.stDownloadButton button {{
  background: rgba(200,168,75,.1) !important;
  border: 1px solid rgba(200,168,75,.35) !important;
  color: {C_GOLD_L} !important;
  border-radius: 8px !important;
  font-size: .75rem !important;
}}

div[data-testid="stSelectbox"] > div {{
  background: {C_CARD} !important;
  border: 1px solid {C_BORDER} !important;
  border-radius: 8px !important;
  color: #CBD5E1 !important;
}}

.stSpinner > div {{ border-top-color: {C_GOLD} !important; }}

/* ─── Footer ────────────────────────────────────────────── */
.nq-footer {{
  text-align: center;
  padding: 20px 0 8px;
  font-size: .7rem;
  color: {C_BORDER};
  letter-spacing: .04em;
}}
</style>
""", unsafe_allow_html=True)


# ── Chart theme factory ─────────────────────────────────────────────────────────
# Base layout — no axis/legend keys (those are passed per-chart to avoid duplicate kwarg errors)
CHART_BASE = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter", color="#94A3B8", size=11),
    margin=dict(t=16, b=8, l=0, r=0),
    hoverlabel=dict(bgcolor=C_CARD, bordercolor=C_BORDER,
                    font=dict(color="white", size=11)),
)
# Reusable axis style dicts
XAXIS  = dict(gridcolor=C_BORDER, linecolor=C_BORDER, tickfont=dict(color=C_GREY))
YAXIS  = dict(gridcolor=C_BORDER, linecolor=C_BORDER, tickfont=dict(color=C_GREY))
LEGEND = dict(bgcolor="rgba(13,22,41,0.8)", bordercolor=C_BORDER, borderwidth=1,
              font=dict(color="#CBD5E1", size=10))
LEGEND_H = dict(orientation="h", y=-0.22, bgcolor="rgba(0,0,0,0)",
                font=dict(color=C_GREY, size=10))

# Keep CHART_LAYOUT as an alias for code that uses **CHART_LAYOUT directly
CHART_LAYOUT = CHART_BASE

def _apply(fig: go.Figure, height: int = 360) -> go.Figure:
    fig.update_layout(height=height, xaxis=XAXIS, yaxis=YAXIS,
                      legend=LEGEND, **CHART_BASE)
    return fig

def bar_fig(df, x, y, color=None, title="", color_seq=None, height=360, **kw) -> go.Figure:
    fig = px.bar(df, x=x, y=y, color=color, title=title,
                 color_discrete_sequence=color_seq or PALETTE, **kw)
    fig.update_traces(marker_line_width=0)
    return _apply(fig, height)

def pie_fig(df, names, values, height=340) -> go.Figure:
    fig = px.pie(df, names=names, values=values, color_discrete_sequence=PALETTE)
    fig.update_traces(textposition="inside", textinfo="percent+label",
                      textfont=dict(size=10, color="white"),
                      marker=dict(line=dict(color=C_DARK, width=2)))
    fig.update_layout(showlegend=False, height=height,
                      legend=LEGEND, **CHART_BASE)
    return fig

def line_fig(df, x, y, color=None, height=360, **kw) -> go.Figure:
    fig = px.line(df, x=x, y=y, color=color,
                  color_discrete_sequence=PALETTE, **kw)
    fig.update_traces(line_width=2)
    return _apply(fig, height)

def scatter_fig(df, x, y, color=None, size=None, height=380, **kw) -> go.Figure:
    plot_df = df.copy()
    if size and size in plot_df.columns:
        plot_df = plot_df.dropna(subset=[size])
        plot_df[size] = plot_df[size].clip(lower=0)
    fig = px.scatter(plot_df, x=x, y=y, color=color, size=size,
                     color_discrete_sequence=PALETTE, **kw)
    return _apply(fig, height)


# ── SQL helper ──────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def sql(query: str, _silent: bool = False) -> pd.DataFrame:
    """Execute SQL via Databricks Statement API and return a DataFrame.

    If _silent=True, suppresses st.error on failure (used for existence probes).
    Uses the token from session state (set in sidebar) so a new token can be
    applied without restarting the app.
    """
    active_token = st.session_state.get("active_token") or TOKEN
    active_host  = st.session_state.get("active_host")  or HOST
    active_wh    = st.session_state.get("active_wh")    or WH_ID

    if not active_token:
        if not _silent:
            st.error("🔑 No Databricks token configured. Enter your token in the sidebar.")
        return pd.DataFrame()

    headers = _make_headers(active_token)
    payload = {
        "statement":       query.strip(),
        "warehouse_id":    active_wh,
        "wait_timeout":    "50s",
        "on_wait_timeout": "CONTINUE",
    }
    try:
        r = requests.post(f"{active_host}/api/2.0/sql/statements",
                          headers=headers, json=payload, timeout=90)
    except requests.exceptions.ConnectionError:
        if not _silent:
            st.error("🌐 Cannot reach Databricks workspace. Check your network or HOST URL.")
        return pd.DataFrame()

    if r.status_code == 401 or r.status_code == 403:
        if not _silent:
            # Show auth error only once per session (not on every query)
            if not st.session_state.get("_auth_error_shown"):
                st.session_state["_auth_error_shown"] = True
                st.error(
                    f"🔑 **Token expired (HTTP {r.status_code})** — open **🔑 Databricks Connection** "
                    "in the sidebar, paste a new token, and click **✅ Apply**.\n\n"
                    "[Generate new token →](https://dbc-0e5855b4-0c93.cloud.databricks.com"
                    "/settings/user/developer/access-tokens)"
                )
        return pd.DataFrame()

    r.raise_for_status()
    resp    = r.json()
    stmt_id = resp["statement_id"]
    status  = resp.get("status", {}).get("state", "UNKNOWN")
    deadline = time.time() + 300
    while status in ("PENDING", "RUNNING") and time.time() < deadline:
        time.sleep(3)
        r2 = requests.get(f"{active_host}/api/2.0/sql/statements/{stmt_id}",
                          headers=headers, timeout=30)
        r2.raise_for_status()
        resp   = r2.json()
        status = resp.get("status", {}).get("state", "UNKNOWN")

    if status != "SUCCEEDED":
        err = resp.get("status", {}).get("error", {}).get("message", "unknown error")
        if not _silent:
            st.error(f"⚠️  Query failed: {err}")
        return pd.DataFrame()

    cols = [c["name"] for c in resp["manifest"]["schema"]["columns"]]
    rows = resp.get("result", {}).get("data_array", []) or []
    return pd.DataFrame(rows, columns=cols)


# ── Utilities ───────────────────────────────────────────────────────────────────
def n(val, default=0.0):
    try:    return float(val) if val is not None else default
    except: return default

def tonums(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def dl(df: pd.DataFrame, key: str, label="⬇  Export CSV") -> None:
    buf = io.StringIO(); df.to_csv(buf, index=False)
    st.download_button(label, buf.getvalue(), file_name=f"{key}.csv",
                       mime="text/csv", key=key)

def hero(icon: str, title: str, sub: str = "") -> None:
    from datetime import datetime as _dt
    _ts = _dt.now().strftime("%H:%M:%S")
    _on = "DATABRICKS_RUNTIME_VERSION" in os.environ
    _badge_col = C_GREEN if _on else C_GOLD
    _badge_txt = "Databricks" if _on else "Local"
    st.markdown(
        f'<div class="page-hero">'
        f'<h1>{icon}&nbsp; {title}</h1>'
        f'<div class="subtitle">'
        f'{sub}'
        f'&nbsp;&nbsp;'
        f'<span style="background:rgba(255,255,255,.06); border:1px solid {C_BORDER}; '
        f'border-radius:20px; padding:2px 10px; font-size:.7rem; margin-left:6px;">'
        f'<span style="color:{_badge_col};">●</span>&nbsp;{_badge_txt}'
        f'&nbsp;&nbsp;'
        f'<span style="color:{C_GREY};">updated {_ts}</span>'
        f'</span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

def section(label: str) -> None:
    st.markdown(f'<div class="section-title">{label}</div>', unsafe_allow_html=True)

def kpi_row(cards: list[tuple]) -> None:
    """cards = list of (icon, value, label, colour, delta)"""
    html = '<div class="kpi-grid">'
    for icon, value, label, colour, delta in cards:
        if delta:
            neg = delta.startswith("-")
            dc  = "kpi-delta-neg" if neg else "kpi-delta-pos"
            arrow = "▼ " if neg else "▲ "
            delta_html = f'<div class="{dc}">{arrow}{delta}</div>'
        else:
            delta_html = ""
        html += (
            f'<div class="kpi-card c-{colour}">'
            f'<span class="kpi-icon">{icon}</span>'
            f'<div class="kpi-value">{value}</div>'
            f'<div class="kpi-label">{label}</div>'
            f'{delta_html}'
            f'</div>'
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

def chart_card(content_fn, *args, **kwargs):
    st.markdown('<div class="chart-card">', unsafe_allow_html=True)
    content_fn(*args, **kwargs)
    st.markdown('</div>', unsafe_allow_html=True)


# ── Sidebar ─────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        f'<div style="padding:16px 0 8px; text-align:center;">'
        f'<div style="font-size:2rem;">🚚</div>'
        f'<div style="font-size:1rem; font-weight:800; color:white; letter-spacing:.04em;">NAQEL Express</div>'
        f'<div style="font-size:.68rem; color:{C_GREY}; letter-spacing:.08em; text-transform:uppercase; margin-top:2px;">Analytics Intelligence</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<hr style="border-color:#1E2D4D; margin:12px 0;">', unsafe_allow_html=True)

    # ── Credential panel ────────────────────────────────────────────────────
    _active_tok  = st.session_state.get("active_token", TOKEN)
    _tok_present = bool(_active_tok and len(_active_tok) > 10)

    with st.expander("🔑 Databricks Connection", expanded=not _tok_present):

        st.markdown(
            '<div style="font-size:.76rem;color:#94a3b8;margin-bottom:8px;">'
            'Paste your Databricks Personal Access Token below.<br>'
            '<a href="https://dbc-0e5855b4-0c93.cloud.databricks.com/settings/user/developer/access-tokens" '
            'target="_blank" style="color:#ff8c5a;">🔗 Generate new token here</a>'
            '</div>',
            unsafe_allow_html=True,
        )

        _tok_in = st.text_input(
            "Paste new token here",
            value="",
            type="password",
            placeholder="Paste your Personal Access Token…",
        )
        _host_in = st.text_input(
            "Workspace URL",
            value=st.session_state.get("active_host", HOST),
        )
        _wh_in = st.text_input(
            "SQL Warehouse ID",
            value=st.session_state.get("active_wh", WH_ID),
        )

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("✅ Apply", use_container_width=True):
                _new_tok = _tok_in.strip()
                if not _new_tok:
                    st.error("Paste a token first")
                else:
                    st.session_state["active_token"] = _new_tok
                    st.session_state["active_host"]  = _host_in.strip().rstrip("/")
                    st.session_state["active_wh"]    = _wh_in.strip()
                    st.session_state["_auth_error_shown"] = False  # reset error banner
                    # Persist to key file
                    _key_file = Path(__file__).parent.parent / "databricks_api_key.txt"
                    try:
                        _key_file.write_text(
                            f"TokenID: {_new_tok}\nTokenName: updated\n",
                            encoding="utf-8"
                        )
                    except Exception:
                        pass
                    st.cache_data.clear()
                    st.success("✅ Saved — reloading…")
                    time.sleep(0.6)
                    st.rerun()

        with col_b:
            if st.button("🔍 Test", use_container_width=True):
                _t = _tok_in.strip() or st.session_state.get("active_token", TOKEN)
                _h = st.session_state.get("active_host", HOST)
                if not _t:
                    st.warning("Paste a token first")
                else:
                    try:
                        _r = requests.get(
                            f"{_h}/api/2.0/sql/warehouses",
                            headers=_make_headers(_t), timeout=12
                        )
                        if _r.status_code == 200:
                            _whs = _r.json().get("warehouses", [])
                            st.success(f"✅ Connected — {len(_whs)} WH(s)")
                        elif _r.status_code in (401, 403):
                            st.error(f"❌ {_r.status_code} — token invalid or expired")
                        else:
                            st.warning(f"⚠️ HTTP {_r.status_code}")
                    except requests.exceptions.ConnectionError:
                        st.error("❌ Cannot reach workspace")
                    except Exception as _ex:
                        st.error(f"❌ {_ex}")

    # Token status banner — compact, below expander
    _active_tok = st.session_state.get("active_token", TOKEN)
    if not _active_tok or len(_active_tok) < 10:
        st.markdown(
            '<div style="background:rgba(214,40,40,.08);border:1px solid rgba(214,40,40,.25);'
            'border-radius:8px;padding:7px 12px;font-size:.72rem;color:#f87171;margin-bottom:6px;">'
            '⚠️ No token — paste one above and click <b>Apply</b>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        _tok_preview = _active_tok[:8] + "…" + _active_tok[-4:]
        st.markdown(
            f'<div style="background:rgba(29,185,84,.07);border:1px solid rgba(29,185,84,.2);'
            f'border-radius:8px;padding:6px 12px;font-size:.7rem;color:#6ee7b7;margin-bottom:6px;">'
            f'✅ Token: <code style="color:#86efac;">{_tok_preview}</code></div>',
            unsafe_allow_html=True,
        )

    st.markdown('<hr style="border-color:#1E2D4D; margin:8px 0;">', unsafe_allow_html=True)

    st.markdown("Navigation")
    page = st.radio("Page", [
        "📊  Executive",
        "📦  Shipments",
        "🛣️  Routes & SLA",
        "💰  COD & Revenue",
        "🚛  Fleet Safety",
        "🏭  Warehouse",
        "✈️  Freight",
        "👥  Customers",
        "🔍  Hub Performance",
        "🤖  ML Risk",
        "🔮  Churn & Demand",
        "🕸️  Network Graph",
        "⚡  Live Streams",
        "⚠️  Anomalies",
        "🩺  Data Quality",
    ], label_visibility="collapsed")

    st.markdown('<hr style="border-color:#1E2D4D; margin:12px 0;">', unsafe_allow_html=True)
    st.markdown("Period")
    date_range = st.selectbox("Period", ["Last 7 days","Last 30 days","Last 90 days","All time"],
                               index=1, label_visibility="collapsed")

    DAYS = {"Last 7 days":7, "Last 30 days":30, "Last 90 days":90, "All time":None}[date_range]
    DATE_FILTER    = f"AND created_date >= CURRENT_DATE - INTERVAL {DAYS} DAYS" if DAYS else ""
    DATE_FILTER_TS = f"AND created_at    >= CURRENT_DATE - INTERVAL {DAYS} DAYS" if DAYS else ""
    DATE_FILTER_RD = f"AND reading_date  >= CURRENT_DATE - INTERVAL {DAYS} DAYS" if DAYS else ""
    DATE_FILTER_ED = f"AND event_date    >= CURRENT_DATE - INTERVAL {DAYS} DAYS" if DAYS else ""

    st.markdown('<hr style="border-color:#1E2D4D; margin:12px 0;">', unsafe_allow_html=True)

    # ── Auto-refresh controls ───────────────────────────────────────────────
    st.markdown("Live Refresh")
    auto_refresh = st.toggle("Auto-refresh", value=False)
    refresh_interval = st.select_slider(
        "Refresh interval",
        options=[10, 30, 60, 120, 300],
        value=60,
        format_func=lambda x: f"{x}s" if x < 60 else f"{x//60}m",
        label_visibility="collapsed",
    )

    # Countdown + pulse indicator
    if auto_refresh:
        # Store last-refresh time in session state
        if "last_refresh" not in st.session_state:
            st.session_state["last_refresh"] = time.time()

        elapsed   = time.time() - st.session_state["last_refresh"]
        remaining = max(0, refresh_interval - int(elapsed))
        pct       = min(1.0, elapsed / refresh_interval)

        # Progress bar as countdown
        st.markdown(
            f'<div style="margin:6px 0 2px; font-size:.68rem; color:{C_GREY};">'
            f'Next refresh in <span style="color:{C_GOLD_L}; font-weight:700;">{remaining}s</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.progress(pct)

        if elapsed >= refresh_interval:
            st.cache_data.clear()
            st.session_state["last_refresh"] = time.time()
            st.rerun()
    else:
        st.markdown(
            f'<div style="font-size:.68rem; color:{C_BORDER}; margin:4px 0;">enable to poll Databricks live</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<hr style="border-color:#1E2D4D; margin:12px 0;">', unsafe_allow_html=True)

    col_ref, col_clr = st.columns(2)
    with col_ref:
        if st.button("↺ Refresh now"):
            st.cache_data.clear()
            st.session_state["last_refresh"] = time.time()
            st.rerun()
    with col_clr:
        if st.button("✕ Clear cache"):
            st.cache_data.clear()
            st.rerun()

    # ── Connection info ─────────────────────────────────────────────────────
    # Determine if we're running on Databricks or locally
    _on_databricks = "DATABRICKS_RUNTIME_VERSION" in os.environ
    _env_label     = "Databricks" if _on_databricks else "Local"
    _env_colour    = C_GREEN if _on_databricks else C_GOLD

    st.markdown(
        f'<div style="margin-top:auto; padding-top:12px;">'
        f'<div style="font-size:.65rem; color:{C_BORDER}; line-height:1.8;">'
        f'<span style="color:{_env_colour}; font-weight:700;">● {_env_label}</span><br>'
        f'Catalog &nbsp;·&nbsp; <code style="color:{C_GREY}">naqel_express</code><br>'
        f'WH &nbsp;·&nbsp; <code style="color:{C_GREY}">{WH_ID[:8]}…</code><br>'
        f'Cache TTL &nbsp;·&nbsp; <code style="color:{C_GREY}">5 min</code>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Schedule a rerun so the countdown ticks even when the user isn't interacting
    if auto_refresh:
        time.sleep(1)
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — Executive
# ══════════════════════════════════════════════════════════════════════════════
if page == "📊  Executive":
    hero("📊", "Executive Dashboard",
         f"Operational intelligence for NAQEL Express &nbsp;·&nbsp; {date_range}")

    with st.spinner(""):
        kpi = sql(f"""
            SELECT SUM(total_shipments)                           AS total_shipments,
                   ROUND(SUM(total_revenue_sar)/1e6, 2)          AS revenue_m,
                   ROUND(AVG(sla_compliance_rate_pct), 1)        AS sla_pct,
                   ROUND(AVG(avg_delivery_hrs), 1)               AS avg_hrs,
                   ROUND(AVG(first_attempt_failure_rate_pct), 2) AS fail_pct,
                   ROUND(SUM(total_cod_sar)/1e6, 2)              AS cod_m,
                   SUM(failed_delivery_count)                    AS failed_del,
                   COUNT(DISTINCT hub_code)                      AS hubs
            FROM {NS}.gold_daily_shipment_kpis WHERE 1=1 {DATE_FILTER}""")
        flt = sql(f"""
            SELECT COUNT(DISTINCT vehicle_id)  AS vehicles,
                   ROUND(AVG(safety_score), 1) AS avg_safety,
                   SUM(overspeed_events)        AS overspeeds
            FROM {NS}.gold_fleet_daily_summary WHERE 1=1 {DATE_FILTER_RD}""")
        prev = sql(f"""
            SELECT ROUND(SUM(total_revenue_sar)/1e6,2)    AS p_rev,
                   ROUND(AVG(sla_compliance_rate_pct), 1) AS p_sla
            FROM {NS}.gold_daily_shipment_kpis
            WHERE created_date >= CURRENT_DATE - INTERVAL {(DAYS or 30)*2} DAYS
              AND created_date <  CURRENT_DATE - INTERVAL {DAYS or 30}     DAYS""")

    if not kpi.empty:
        r = kpi.iloc[0]; f = (flt.iloc[0] if not flt.empty else {}); p = (prev.iloc[0] if not prev.empty else {})
        rev_d = f"{n(r.get('revenue_m')) - n(p.get('p_rev')):+.1f}M" if n(p.get("p_rev")) else ""
        sla_d = f"{n(r.get('sla_pct')) - n(p.get('p_sla')):+.1f}pp"  if n(p.get("p_sla")) else ""
        kpi_row([
            ("📦", f"{int(n(r.get('total_shipments'))):,}", "Total Shipments",    "blue",  ""),
            ("💰", f"SAR {n(r.get('revenue_m'))}M",         "Total Revenue",       "gold",  rev_d),
            ("✅", f"{n(r.get('sla_pct'))}%",               "SLA Compliance",      "green", sla_d),
            ("⏱", f"{n(r.get('avg_hrs'))}h",               "Avg Delivery",        "blue",  ""),
            ("❌", f"{n(r.get('fail_pct'))}%",              "1st-Attempt Fail",    "red",   ""),
            ("🏦", f"SAR {n(r.get('cod_m'))}M",             "COD Collected",       "gold",  ""),
            ("🚗", f"{int(n(f.get('vehicles', 0))):,}",      "Vehicles Tracked",    "blue",  ""),
            ("🛡", f"{n(f.get('avg_safety', 0))}",           "Fleet Safety Score",  "green", ""),
        ])

    col_l, col_r = st.columns([3, 2], gap="medium")
    with col_l:
        section("Revenue & SLA Trend")
        trend = sql(f"""
            SELECT DATE_TRUNC('month', created_date) AS month,
                   SUM(total_revenue_sar) AS rev, SUM(total_shipments) AS shp,
                   ROUND(AVG(sla_compliance_rate_pct), 1) AS sla
            FROM {NS}.gold_daily_shipment_kpis GROUP BY 1 ORDER BY 1""")
        if not trend.empty:
            trend = tonums(trend, ["rev","shp","sla"])
            fig = go.Figure()
            fig.add_bar(x=trend["month"], y=trend["rev"], name="Revenue SAR",
                        marker=dict(color=C_BLUE, opacity=.85), yaxis="y")
            fig.add_scatter(x=trend["month"], y=trend["sla"], name="SLA %",
                            mode="lines+markers", yaxis="y2",
                            line=dict(color=C_GOLD, width=2.5),
                            marker=dict(size=5, color=C_GOLD))
            fig.update_layout(
                height=340, **CHART_BASE,
                yaxis=dict(title="", tickformat=",", gridcolor=C_BORDER, linecolor=C_BORDER),
                yaxis2=dict(title="", overlaying="y", side="right",
                            range=[50, 100], gridcolor="rgba(0,0,0,0)", linecolor=C_BORDER),
                legend=dict(orientation="h", y=-0.18, bgcolor="rgba(0,0,0,0)",
                            font=dict(color=C_GREY, size=10)),
                bargap=0.35,
            )
            st.plotly_chart(fig, use_container_width=True)

    with col_r:
        section("Hub Volume Share — 30 days")
        hs = sql(f"""
            SELECT hub_code, SUM(total_shipments) AS shp
            FROM {NS}.gold_daily_shipment_kpis
            WHERE created_date >= CURRENT_DATE - INTERVAL 30 DAYS
            GROUP BY hub_code ORDER BY shp DESC""")
        if not hs.empty:
            hs = tonums(hs, ["shp"])
            fig = pie_fig(hs, "hub_code", "shp", height=340)
            st.plotly_chart(fig, use_container_width=True)

    section("Daily Revenue Heatmap — last 60 days")
    hm = sql(f"""
        SELECT created_date, hub_code, SUM(total_revenue_sar) AS rev
        FROM {NS}.gold_daily_shipment_kpis
        WHERE created_date >= CURRENT_DATE - INTERVAL 60 DAYS
        GROUP BY 1, 2 ORDER BY 1""")
    if not hm.empty:
        hm = tonums(hm, ["rev"])
        pivot = hm.pivot_table(index="hub_code", columns="created_date",
                               values="rev", aggfunc="sum")
        fig = px.imshow(pivot, aspect="auto",
                        color_continuous_scale=[[0,"#0A1628"],[.5,C_BLUE],[1,C_GOLD_L]],
                        labels=dict(color="SAR"))
        fig.update_layout(height=240, **CHART_BASE,
                          xaxis=dict(showgrid=False, tickfont=dict(size=9, color=C_GREY)),
                          yaxis=dict(showgrid=False, tickfont=dict(size=10, color=C_GREY)))
        st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — Shipments
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📦  Shipments":
    hero("📦", "Shipment Analytics", "Status · Revenue · Performance · Attempts")

    tab1, tab2, tab3 = st.tabs(["  Overview  ", "  Delivery Performance  ", "  Multi-Attempt Analysis  "])

    with tab1:
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            section("Status Breakdown")
            status = sql(f"""SELECT status, COUNT(*) AS cnt FROM {NS}.silver_shipments
                            WHERE 1=1 {DATE_FILTER_TS} GROUP BY status ORDER BY cnt DESC""")
            if not status.empty:
                st.plotly_chart(pie_fig(tonums(status,["cnt"]), "status","cnt"), use_container_width=True)

        with c2:
            section("Revenue by Service Code")
            svc = sql(f"""SELECT service_code,
                                 ROUND(SUM(total_charge_sar),0) AS rev, COUNT(*) AS shp
                          FROM {NS}.silver_shipments WHERE 1=1 {DATE_FILTER_TS}
                          GROUP BY service_code ORDER BY rev DESC""")
            if not svc.empty:
                svc = tonums(svc, ["rev","shp"])
                fig = bar_fig(svc, "service_code", "rev", title="", text_auto=".3s")
                fig.update_layout(showlegend=False, xaxis_tickangle=-30)
                st.plotly_chart(fig, use_container_width=True)

        section("Domestic vs International")
        split = sql(f"""SELECT route_type, COUNT(*) AS shp,
                               ROUND(SUM(total_charge_sar),0) AS rev,
                               ROUND(AVG(delivery_duration_hrs),1) AS avg_hrs
                        FROM {NS}.silver_shipments WHERE 1=1 {DATE_FILTER_TS} GROUP BY route_type""")
        if not split.empty:
            split = tonums(split, ["shp","rev","avg_hrs"])
            sc = st.columns(len(split))
            for i, row in split.iterrows():
                icon = "🌐" if "INTER" in str(row.get("route_type","")).upper() else "🏠"
                with sc[i]:
                    st.metric(f"{icon}  {row['route_type']}",
                              f"{int(row['shp']):,} shipments",
                              f"SAR {int(row['rev']):,}")

        section("Hub Performance Summary")
        hub = sql(f"""SELECT hub_code, SUM(total_shipments) AS shp,
                             ROUND(SUM(total_revenue_sar),0) AS rev,
                             ROUND(AVG(sla_compliance_rate_pct),1) AS sla,
                             ROUND(AVG(avg_delivery_hrs),1) AS hrs,
                             ROUND(AVG(first_attempt_failure_rate_pct),2) AS fail
                      FROM {NS}.gold_daily_shipment_kpis WHERE 1=1 {DATE_FILTER}
                      GROUP BY hub_code ORDER BY shp DESC""")
        if not hub.empty:
            hub = tonums(hub, ["shp","rev","sla","hrs","fail"])
            st.dataframe(hub.style.format({"shp":"{:,.0f}","rev":"{:,.0f}",
                         "sla":"{:.1f}%","hrs":"{:.1f}h","fail":"{:.2f}%"})
                         .background_gradient(subset=["sla"], cmap="RdYlGn"),
                         use_container_width=True, height=260)
            dl(hub, "hub_summary")

    with tab2:
        section("Delivery Time Percentiles (p50 / p90 / p99)")
        perc = sql(f"""
            SELECT service_code, route_type, COUNT(*) AS cnt,
                   ROUND(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY delivery_duration_hrs),1) AS p50,
                   ROUND(PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY delivery_duration_hrs),1) AS p90,
                   ROUND(PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY delivery_duration_hrs),1) AS p99,
                   ROUND(AVG(delivery_duration_hrs),1) AS mean,
                   ROUND(STDDEV(delivery_duration_hrs),1) AS std
            FROM {NS}.silver_shipments
            WHERE delivery_duration_hrs IS NOT NULL AND status='DELIVERED' {DATE_FILTER_TS}
            GROUP BY service_code, route_type ORDER BY service_code""")
        if not perc.empty:
            perc = tonums(perc, ["cnt","p50","p90","p99","mean","std"])
            fig = go.Figure()
            for _, row in perc.iterrows():
                lbl = f"{row['service_code']} · {row['route_type']}"
                fig.add_trace(go.Box(name=lbl, q1=[row["p50"]*0.7], median=[row["p50"]],
                                     q3=[row["p90"]], mean=[row["mean"]],
                                     lowerfence=[max(0,row["mean"]-row["std"])],
                                     upperfence=[row["p99"]], boxmean=True,
                                     marker_color=C_GOLD, line_color=C_BLUE))
            fig.update_layout(height=360, showlegend=False, xaxis_tickangle=-25, **CHART_BASE,
                               yaxis_title="Hours")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(perc.style.format({"cnt":"{:,.0f}","p50":"{:.1f}h","p90":"{:.1f}h",
                         "p99":"{:.1f}h","mean":"{:.1f}h","std":"{:.1f}"}),
                         use_container_width=True, height=220)
            dl(perc, "percentiles")

        section("SLA Breach Detail")
        br = sql(f"""SELECT service_code, hub_code, route_type,
                            COUNT(*) AS breaches,
                            ROUND(AVG(delivery_duration_hrs),1) AS avg_hrs,
                            ROUND(AVG(attempt_count),2) AS avg_att
                     FROM {NS}.silver_shipments
                     WHERE sla_met=false AND status='DELIVERED' {DATE_FILTER_TS}
                     GROUP BY 1,2,3 ORDER BY breaches DESC LIMIT 30""")
        if not br.empty:
            br = tonums(br, ["breaches","avg_hrs","avg_att"])
            st.dataframe(br.style.background_gradient(subset=["breaches"], cmap="YlOrRd"),
                         use_container_width=True, height=280)
            dl(br, "sla_breaches")

    with tab3:
        section("Shipments Requiring 3+ Delivery Attempts")
        att = sql(f"""SELECT attempt_count, hub_code, service_code, COUNT(*) AS shp,
                             ROUND(AVG(total_charge_sar),2) AS avg_rev,
                             ROUND(AVG(delivery_duration_hrs),1) AS avg_hrs,
                             SUM(CASE WHEN status='DELIVERED' THEN 1 ELSE 0 END) AS delivered,
                             SUM(CASE WHEN status='RETURNED'  THEN 1 ELSE 0 END) AS returned
                      FROM {NS}.silver_shipments
                      WHERE attempt_count>=3 {DATE_FILTER_TS}
                      GROUP BY attempt_count, hub_code, service_code
                      ORDER BY attempt_count DESC, shp DESC""")
        if not att.empty:
            att = tonums(att, ["attempt_count","shp","avg_rev","avg_hrs","delivered","returned"])
            c1, c2 = st.columns(2, gap="medium")
            with c1:
                agg = att.groupby("attempt_count")["shp"].sum().reset_index()
                fig = bar_fig(agg, "attempt_count", "shp", title="Volume by Attempt Count",
                              color_seq=[C_RED])
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                ha = att.groupby("hub_code")["shp"].sum().reset_index().sort_values("shp", ascending=False)
                fig = bar_fig(ha, "hub_code", "shp", title="Multi-Attempt by Hub",
                              color_seq=[C_GOLD])
                fig.update_layout(xaxis_tickangle=-30)
                st.plotly_chart(fig, use_container_width=True)
            st.dataframe(att, use_container_width=True, height=260)
            dl(att, "multi_attempt")
        else:
            st.info("No multi-attempt data for the selected period.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — Routes & SLA
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🛣️  Routes & SLA":
    hero("🛣️", "Route Performance & SLA", "Network efficiency · On-time delivery · Breach analysis")

    routes = sql(f"""SELECT route_code, origin_city, dest_city, service_code,
                            shipment_count,
                            ROUND(avg_transit_hrs,1) AS avg_hrs,
                            ROUND(on_time_pct,1)     AS on_time_pct,
                            ROUND(total_revenue_sar,0) AS rev
                     FROM {NS}.gold_route_performance
                     WHERE shipment_count>=5 ORDER BY shipment_count DESC LIMIT 60""")
    if not routes.empty:
        routes = tonums(routes, ["shipment_count","avg_hrs","on_time_pct","rev"])

        c1, c2 = st.columns(2, gap="medium")
        with c1:
            section("Top 20 Routes — Volume coloured by On-Time %")
            fig = px.bar(routes.head(20), x="route_code", y="shipment_count",
                         color="on_time_pct",
                         color_continuous_scale=[[0,C_RED],[.5,"#FB8500"],[1,C_GREEN]],
                         labels={"on_time_pct":"On-Time %"})
            fig.update_traces(marker_line_width=0)
            fig.update_layout(height=380, xaxis_tickangle=-45, **CHART_BASE,
                               coloraxis_colorbar=dict(tickfont=dict(color=C_GREY),
                                                       title=dict(text="OT%", font=dict(color=C_GREY))))
            st.plotly_chart(fig, use_container_width=True)

        with c2:
            section("Transit Time vs On-Time % (bubble = revenue)")
            fig = scatter_fig(routes, "avg_hrs", "on_time_pct",
                              color="service_code", size="shipment_count",
                              hover_data=["route_code","origin_city","dest_city"])
            fig.add_hline(y=90, line_dash="dash", line_color=C_GREEN,
                          annotation_text="90 % target",
                          annotation_font=dict(color=C_GREEN, size=10))
            st.plotly_chart(fig, use_container_width=True)

        section("All Routes")
        st.dataframe(
            routes.style.format({"shipment_count":"{:,.0f}","avg_hrs":"{:.1f}h",
                                  "on_time_pct":"{:.1f}%","rev":"{:,.0f}"})
            .background_gradient(subset=["on_time_pct"], cmap="RdYlGn"),
            use_container_width=True, height=300)
        dl(routes, "routes")

        section("SLA Breach Detail")
        br = sql(f"""SELECT service_code, hub_code, route_type,
                            COUNT(*) AS breaches,
                            ROUND(AVG(delivery_duration_hrs),1) AS avg_hrs
                     FROM {NS}.silver_shipments
                     WHERE sla_met=false AND status='DELIVERED' {DATE_FILTER_TS}
                     GROUP BY 1,2,3 ORDER BY breaches DESC LIMIT 20""")
        if not br.empty:
            br = tonums(br, ["breaches","avg_hrs"])
            c1, c2 = st.columns([2,3], gap="medium")
            with c1:
                fig = bar_fig(br.head(10), "hub_code", "breaches",
                              color="service_code", title="Breaches by Hub")
                fig.update_layout(xaxis_tickangle=-30)
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                st.dataframe(br.style.background_gradient(subset=["breaches"], cmap="OrRd"),
                             use_container_width=True, height=340)
            dl(br, "sla_breaches")
    else:
        st.warning("No route performance data available.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — COD & Revenue
# ══════════════════════════════════════════════════════════════════════════════
elif page == "💰  COD & Revenue":
    hero("💰", "COD & Revenue Analysis", "Cash-on-delivery reconciliation · Month-over-month growth")

    tab1, tab2 = st.tabs(["  COD Reconciliation  ", "  Revenue Deep-Dive  "])

    with tab1:
        cod = sql(f"""SELECT origin_city, courier_id,
                             COUNT(CASE WHEN is_cod THEN 1 END) AS cod_shp,
                             ROUND(SUM(CASE WHEN is_cod AND status='DELIVERED' THEN cod_amount_sar ELSE 0 END),0) AS collected,
                             ROUND(SUM(CASE WHEN is_cod THEN cod_amount_sar ELSE 0 END),0) AS expected,
                             ROUND(SUM(CASE WHEN is_cod AND status='DELIVERED' THEN cod_amount_sar ELSE 0 END)*100.0
                                   /NULLIF(SUM(CASE WHEN is_cod THEN cod_amount_sar ELSE 0 END),0),1) AS rate_pct
                      FROM {NS}.silver_shipments WHERE is_cod=true {DATE_FILTER_TS}
                      GROUP BY origin_city, courier_id
                      HAVING COUNT(CASE WHEN is_cod THEN 1 END)>5
                      ORDER BY rate_pct ASC LIMIT 40""")
        if not cod.empty:
            cod = tonums(cod, ["cod_shp","collected","expected","rate_pct"])
            total_exp = cod["expected"].sum(); total_col = cod["collected"].sum()
            overall   = total_col/total_exp*100 if total_exp else 0
            kpi_row([
                ("💵", f"SAR {total_col:,.0f}",   "COD Collected",       "gold",  ""),
                ("📋", f"SAR {total_exp:,.0f}",   "COD Expected",        "blue",  ""),
                ("📈", f"{overall:.1f}%",           "Overall Collection",  "green", ""),
            ])
            c1, c2 = st.columns(2, gap="medium")
            with c1:
                section("Collection Rate by City")
                ca = cod.groupby("origin_city").agg(rate=("rate_pct","mean"),shp=("cod_shp","sum")).reset_index().sort_values("rate")
                fig = px.bar(ca, x="origin_city", y="rate",
                             color="rate", color_continuous_scale=[[0,C_RED],[.5,"#FB8500"],[1,C_GREEN]],
                             text_auto=".1f")
                fig.update_traces(marker_line_width=0)
                fig.add_hline(y=90, line_dash="dash", line_color=C_GREY,
                              annotation_text="90 % target",
                              annotation_font=dict(color=C_GREY, size=10))
                fig.update_layout(height=360, xaxis_tickangle=-30, showlegend=False,
                                   **CHART_BASE,
                                   coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                section("Courier: Volume vs Collection Rate")
                fig = scatter_fig(cod, "cod_shp", "rate_pct",
                                  color="rate_pct", size="expected",
                                  hover_data=["origin_city","courier_id"])
                fig.update_coloraxes(colorscale=[[0,C_RED],[.5,"#FB8500"],[1,C_GREEN]])
                st.plotly_chart(fig, use_container_width=True)

            section("Lowest Collection Rate Couriers")
            st.dataframe(
                cod.sort_values("rate_pct").head(20)
                .style.format({"cod_shp":"{:,.0f}","collected":"{:,.0f}","expected":"{:,.0f}","rate_pct":"{:.1f}%"})
                .background_gradient(subset=["rate_pct"], cmap="RdYlGn"),
                use_container_width=True, height=300)
            dl(cod, "cod_reconciliation")

    with tab2:
        section("Month-over-Month Revenue Growth by Hub")
        mom = sql(f"""
            WITH m AS (SELECT DATE_TRUNC('month',created_date) AS mo,
                              hub_code, SUM(total_revenue_sar) AS rev
                       FROM {NS}.gold_daily_shipment_kpis GROUP BY 1,2),
            w AS (SELECT mo, hub_code, rev,
                         LAG(rev) OVER (PARTITION BY hub_code ORDER BY mo) AS prev
                  FROM m)
            SELECT mo AS month, hub_code,
                   ROUND(rev,0) AS rev_sar,
                   ROUND((rev-prev)*100.0/NULLIF(prev,0),2) AS mom_pct
            FROM w WHERE prev IS NOT NULL
            ORDER BY mo DESC, rev_sar DESC""")
        if not mom.empty:
            mom = tonums(mom, ["rev_sar","mom_pct"])
            fig = px.bar(mom.sort_values("month"), x="month", y="mom_pct",
                         color="hub_code", barmode="group",
                         color_discrete_sequence=PALETTE,
                         labels={"mom_pct":"MoM Growth %","month":""})
            fig.update_traces(marker_line_width=0)
            fig.add_hline(y=0, line_color="#475569", line_width=1)
            fig.update_layout(height=360, **CHART_BASE,
                               legend=dict(orientation="h", y=-0.2, bgcolor="rgba(0,0,0,0)"))
            st.plotly_chart(fig, use_container_width=True)
            dl(mom, "mom_revenue")

        section("Revenue by Service Code — Monthly Area")
        svc_t = sql(f"""SELECT DATE_TRUNC('month',created_at) AS mo, service_code,
                               ROUND(SUM(total_charge_sar),0) AS rev, COUNT(*) AS shp
                        FROM {NS}.silver_shipments GROUP BY 1,2 ORDER BY 1""")
        if not svc_t.empty:
            svc_t = tonums(svc_t, ["rev","shp"])
            fig = px.area(svc_t, x="mo", y="rev", color="service_code",
                          color_discrete_sequence=PALETTE,
                          labels={"rev":"Revenue SAR","mo":""})
            fig.update_traces(line_width=1.5)
            fig.update_layout(height=340, **CHART_BASE,
                               legend=dict(orientation="h", y=-0.2, bgcolor="rgba(0,0,0,0)"))
            st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 5 — Fleet Safety
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🚛  Fleet Safety":
    hero("🚛", "Fleet Safety & Utilisation", "Safety scores · Driving events · Fuel efficiency")

    fleet = sql(f"""SELECT vehicle_id, vehicle_type, hub_code,
                           ROUND(AVG(safety_score),1)  AS safety,
                           SUM(overspeed_events)        AS overspeeds,
                           SUM(harsh_brake_events)      AS brakes,
                           SUM(harsh_accel_events)      AS accels,
                           SUM(geofence_violations)     AS geofences,
                           SUM(engine_overheat_events)  AS overheats,
                           ROUND(SUM(total_fuel_l),1)   AS fuel_l,
                           COUNT(DISTINCT reading_date) AS active_days
                    FROM {NS}.gold_fleet_daily_summary WHERE 1=1 {DATE_FILTER_RD}
                    GROUP BY vehicle_id, vehicle_type, hub_code
                    ORDER BY safety ASC""")

    if not fleet.empty:
        fleet = tonums(fleet, ["safety","overspeeds","brakes","accels",
                                "geofences","overheats","fuel_l","active_days"])
        kpi_row([
            ("🚗", f"{len(fleet):,}",              "Fleet Size",        "blue",  ""),
            ("🛡", f"{fleet['safety'].mean():.1f}", "Avg Safety Score",  "green", ""),
            ("⚡", f"{int(fleet['overspeeds'].sum()):,}", "Total Overspeeds","red",""),
            ("⛽", f"{fleet['fuel_l'].mean():,.0f} L", "Avg Fuel/Vehicle", "gold", ""),
        ])

        c1, c2 = st.columns(2, gap="medium")
        with c1:
            section("Safety Score Distribution")
            fig = px.histogram(fleet, x="safety", nbins=20,
                               color_discrete_sequence=[C_BLUE])
            fig.update_traces(marker_line_color=C_DARK, marker_line_width=1)
            fig.add_vline(x=70, line_dash="dash", line_color=C_RED,
                          annotation_text="Critical 70",
                          annotation_font=dict(color=C_RED, size=10))
            fig.add_vline(x=85, line_dash="dash", line_color=C_GOLD,
                          annotation_text="Warning 85",
                          annotation_font=dict(color=C_GOLD, size=10))
            fig.update_layout(height=320, showlegend=False, **CHART_BASE)
            st.plotly_chart(fig, use_container_width=True)

        with c2:
            section("Overspeed vs Harsh Brakes")
            fig = px.scatter(fleet, x="overspeeds", y="brakes",
                             color="safety",
                             color_continuous_scale=[[0,C_RED],[.5,"#FB8500"],[1,C_GREEN]],
                             size="fuel_l",
                             hover_data=["vehicle_id","vehicle_type","hub_code"])
            fig.update_layout(height=320, **CHART_BASE,
                               coloraxis_colorbar=dict(tickfont=dict(color=C_GREY),
                                                       title=dict(text="Safety", font=dict(color=C_GREY))))
            st.plotly_chart(fig, use_container_width=True)

        section("Safety Score by Hub")
        hs = fleet.groupby("hub_code").agg(safety=("safety","mean"),
                                            cnt=("vehicle_id","count")).reset_index().sort_values("safety")
        fig = px.bar(hs, x="hub_code", y="safety",
                     color="safety",
                     color_continuous_scale=[[0,C_RED],[.5,"#FB8500"],[1,C_GREEN]],
                     text_auto=".1f")
        fig.update_traces(marker_line_width=0)
        fig.add_hline(y=85, line_dash="dash", line_color=C_GREY,
                      annotation_text="85 target",
                      annotation_font=dict(color=C_GREY, size=10))
        fig.update_layout(height=280, showlegend=False, **CHART_BASE,
                           coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

        section("Daily Fleet Trend — last 30 days")
        tf = sql(f"""SELECT reading_date,
                            ROUND(AVG(safety_score),1) AS safety,
                            SUM(overspeed_events) AS ov,
                            SUM(harsh_brake_events) AS hb,
                            ROUND(SUM(total_fuel_l),0) AS fuel
                     FROM {NS}.gold_fleet_daily_summary
                     WHERE reading_date >= CURRENT_DATE - INTERVAL 30 DAYS
                     GROUP BY reading_date ORDER BY reading_date""")
        if not tf.empty:
            tf = tonums(tf, ["safety","ov","hb","fuel"])
            fig = go.Figure()
            fig.add_scatter(x=tf["reading_date"], y=tf["safety"],
                            name="Avg Safety", mode="lines+markers",
                            line=dict(color=C_GREEN, width=2.5),
                            marker=dict(size=4))
            fig.add_bar(x=tf["reading_date"], y=tf["ov"], name="Overspeeds",
                        marker_color=C_RED, opacity=0.55, yaxis="y2")
            fig.update_layout(height=300, **CHART_BASE,
                               yaxis=dict(title="Safety Score",
                                          gridcolor=C_BORDER, linecolor=C_BORDER),
                               yaxis2=dict(title="Events", overlaying="y", side="right",
                                           gridcolor="rgba(0,0,0,0)"),
                               legend=dict(orientation="h", y=-0.2, bgcolor="rgba(0,0,0,0)"),
                               bargap=0.3)
            st.plotly_chart(fig, use_container_width=True)

        section("Bottom 20 Vehicles by Safety Score")
        st.dataframe(
            fleet.head(20).style
            .format({"safety":"{:.1f}","overspeeds":"{:,.0f}","brakes":"{:,.0f}","fuel_l":"{:,.1f}","active_days":"{:.0f}"})
            .background_gradient(subset=["safety"], cmap="RdYlGn"),
            use_container_width=True, height=300)
        dl(fleet, "fleet_safety")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 6 — Warehouse
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🏭  Warehouse":
    hero("🏭", "Warehouse Operations", "Throughput · Damage rates · Shift productivity")

    wh = sql(f"""SELECT warehouse_id, zone, event_type, shift,
                        SUM(total_units) AS units,
                        ROUND(SUM(total_value_sar),0) AS val,
                        SUM(damaged_items) AS damaged,
                        ROUND(AVG(damage_rate_pct),3) AS dmg_rate
                 FROM {NS}.gold_warehouse_throughput WHERE 1=1 {DATE_FILTER_ED}
                 GROUP BY warehouse_id, zone, event_type, shift ORDER BY units DESC""")
    if not wh.empty:
        wh = tonums(wh, ["units","val","damaged","dmg_rate"])
        kpi_row([
            ("📦", f"{int(wh['units'].sum()):,}",  "Units Processed",  "blue", ""),
            ("💰", f"SAR {int(wh['val'].sum()):,}", "Total Value",      "gold", ""),
            ("⚠️", f"{int(wh['damaged'].sum()):,}", "Damaged Items",    "red",  ""),
        ])
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            section("Throughput by DC & Shift")
            agg = wh.groupby(["warehouse_id","shift"])["units"].sum().reset_index()
            fig = bar_fig(agg, "warehouse_id","units", color="shift",
                          color_seq=PALETTE[:4])
            fig.update_layout(barmode="group", legend=dict(orientation="h",y=-0.2,bgcolor="rgba(0,0,0,0)"))
            st.plotly_chart(fig, use_container_width=True)

        with c2:
            section("Damage Rate by Zone")
            dz = wh.groupby("zone")["dmg_rate"].mean().reset_index().sort_values("dmg_rate", ascending=False)
            fig = px.bar(dz, x="zone", y="dmg_rate",
                         color="dmg_rate",
                         color_continuous_scale=[[0,C_GREEN],[.5,"#FB8500"],[1,C_RED]],
                         text_auto=".4f")
            fig.update_traces(marker_line_width=0)
            fig.update_layout(height=360, showlegend=False, **CHART_BASE,
                               coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)

        c3, c4 = st.columns(2, gap="medium")
        with c3:
            section("Event Type Breakdown")
            ev = wh.groupby("event_type")["units"].sum().reset_index()
            st.plotly_chart(pie_fig(ev, "event_type","units"), use_container_width=True)

        with c4:
            section("Units per Hour by Shift (est.)")
            sp = wh.groupby(["warehouse_id","shift"]).agg(units=("units","sum"),days=("units","count")).reset_index()
            sp["uph"] = (sp["units"]/sp["days"]/8).round(1)
            fig = bar_fig(sp.sort_values("uph", ascending=False),
                          "warehouse_id","uph", color="shift",
                          color_seq=PALETTE[:4])
            fig.update_layout(barmode="group", legend=dict(orientation="h",y=-0.2,bgcolor="rgba(0,0,0,0)"),
                               yaxis_title="Units / Hour")
            st.plotly_chart(fig, use_container_width=True)

        section("Full Warehouse Data")
        st.dataframe(
            wh.style.format({"units":"{:,.0f}","val":"{:,.0f}","damaged":"{:,.0f}","dmg_rate":"{:.4f}"})
            .background_gradient(subset=["dmg_rate"], cmap="YlOrRd"),
            use_container_width=True, height=280)
        dl(wh, "warehouse")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 7 — Freight
# ══════════════════════════════════════════════════════════════════════════════
elif page == "✈️  Freight":
    hero("✈️", "Freight Forwarding", "Air · Sea · Ground — cost, transit, anomalies")

    tab1, tab2, tab3 = st.tabs(["  By Mode  ", "  Top Corridors  ", "  Cost Anomalies  "])

    with tab1:
        md = sql(f"""SELECT mode, SUM(job_count) AS jobs,
                            ROUND(AVG(avg_freight_cost_sar),0) AS avg_cost,
                            ROUND(AVG(avg_transit_days),1)     AS avg_days,
                            ROUND(AVG(delay_rate_pct),1)       AS delay_pct,
                            ROUND(AVG(avg_cost_per_kg_usd),4)  AS rate_per_kg
                     FROM {NS}.gold_freight_cost_analysis GROUP BY mode ORDER BY jobs DESC""")
        if not md.empty:
            md = tonums(md, ["jobs","avg_cost","avg_days","delay_pct","rate_per_kg"])
            icons = {"AIR":"✈️","SEA":"🚢","GROUND":"🚛","RAIL":"🚂"}
            kpi_row([(icons.get(str(r["mode"]).upper(),"📦"),
                      f"SAR {int(r['avg_cost']):,}",
                      f"{r['mode']} · {r['avg_days']}d · {r['delay_pct']}% delayed",
                      "blue","") for _, r in md.iterrows()])

            c1, c2 = st.columns(2, gap="medium")
            with c1:
                section("Jobs by Mode")
                fig = pie_fig(md, "mode","jobs")
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                section("Cost vs Transit Days")
                fig = scatter_fig(md, "avg_days","avg_cost",
                                  color="mode", size="jobs")
                st.plotly_chart(fig, use_container_width=True)

    with tab2:
        corr = sql(f"""SELECT mode, origin_country, dest_country, job_count,
                              ROUND(avg_freight_cost_sar,0) AS avg_cost,
                              ROUND(avg_transit_days,1)     AS avg_days,
                              ROUND(delay_rate_pct,1)       AS delay_pct
                       FROM {NS}.gold_freight_cost_analysis
                       WHERE job_count>=5 ORDER BY avg_cost DESC LIMIT 40""")
        if not corr.empty:
            corr = tonums(corr, ["job_count","avg_cost","avg_days","delay_pct"])
            corr["corridor"] = corr["origin_country"] + " → " + corr["dest_country"]
            section("Transit vs Cost (bubble = volume, colour = mode)")
            fig = px.scatter(corr, x="avg_days", y="avg_cost",
                             color="mode", size="job_count",
                             hover_data=["corridor","delay_pct"],
                             color_discrete_sequence=PALETTE,
                             labels={"avg_days":"Avg Transit (days)","avg_cost":"Avg Cost SAR"})
            fig.update_layout(height=420, **CHART_BASE,
                               legend=dict(orientation="h", y=-0.15, bgcolor="rgba(0,0,0,0)"))
            st.plotly_chart(fig, use_container_width=True)

            section("Corridor Detail")
            st.dataframe(
                corr.style.format({"job_count":"{:,.0f}","avg_cost":"{:,.0f}",
                                    "avg_days":"{:.1f}","delay_pct":"{:.1f}%"})
                .background_gradient(subset=["delay_pct"], cmap="YlOrRd"),
                use_container_width=True, height=300)
            dl(corr, "corridors")

    with tab3:
        section("Freight Cost Anomalies (z-score > 2)")
        an = sql(f"""
            WITH cs AS (SELECT mode, origin_country, dest_country,
                               AVG(freight_cost_usd) AS mean, STDDEV(freight_cost_usd) AS std,
                               COUNT(*) AS cnt
                        FROM {NS}.silver_freight_jobs GROUP BY 1,2,3 HAVING COUNT(*)>=5),
            jz AS (SELECT j.freight_job_id, j.mode, j.origin_country, j.dest_country,
                          j.freight_cost_usd, j.gross_weight_kg,
                          ROUND((j.freight_cost_usd-c.mean)/NULLIF(c.std,0),3) AS zscore
                   FROM {NS}.silver_freight_jobs j JOIN cs c USING (mode,origin_country,dest_country))
            SELECT *, CASE WHEN ABS(zscore)>3 THEN 'EXTREME'
                           WHEN ABS(zscore)>2 THEN 'HIGH' ELSE 'MODERATE' END AS severity
            FROM jz WHERE ABS(zscore)>2 ORDER BY ABS(zscore) DESC LIMIT 50""")
        if not an.empty:
            an = tonums(an, ["freight_cost_usd","gross_weight_kg","zscore"])
            c1, c2 = st.columns(2, gap="medium")
            with c1:
                sev = an["severity"].value_counts().reset_index()
                sev.columns = ["severity","cnt"]
                fig = pie_fig(sev, "severity","cnt")
                fig.update_traces(marker=dict(colors=[C_RED, C_GOLD, C_GREY]))
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                fig = px.scatter(an, x="freight_cost_usd", y="gross_weight_kg",
                                 color="zscore",
                                 color_continuous_scale=[[0,C_GREEN],[.5,C_GOLD],[1,C_RED]],
                                 symbol="mode",
                                 hover_data=["freight_job_id","origin_country","dest_country"])
                fig.update_layout(height=360, **CHART_BASE)
                st.plotly_chart(fig, use_container_width=True)
            st.dataframe(an, use_container_width=True, height=280)
            dl(an, "freight_anomalies")
        else:
            st.success("No freight cost anomalies detected.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 8 — Customers
# ══════════════════════════════════════════════════════════════════════════════
elif page == "👥  Customers":
    hero("👥", "Customer Analytics", "RFM segmentation · Top accounts · Churn risk")

    tab1, tab2, tab3 = st.tabs(["  RFM Segments  ", "  Top Accounts  ", "  Churn Risk  "])

    with tab1:
        rfm = sql(f"""
            WITH base AS (
                SELECT customer_account,
                       DATEDIFF(CURRENT_DATE, MAX(DATE(created_at))) AS rec,
                       COUNT(*) AS freq,
                       SUM(total_charge_sar) AS mon,
                       NTILE(5) OVER (ORDER BY DATEDIFF(CURRENT_DATE,MAX(DATE(created_at)))) AS r,
                       NTILE(5) OVER (ORDER BY COUNT(*) DESC) AS f,
                       NTILE(5) OVER (ORDER BY SUM(total_charge_sar) DESC) AS m
                FROM {NS}.silver_shipments GROUP BY customer_account)
            SELECT CASE WHEN r+f+m>=13 THEN 'CHAMPION'
                        WHEN r>=4 AND f>=4 THEN 'LOYAL'
                        WHEN r>=4 AND f<3  THEN 'NEW'
                        WHEN r<=2 AND f>=4 THEN 'AT RISK'
                        WHEN r<=2 AND f<=2 THEN 'LOST'
                        ELSE 'POTENTIAL' END AS segment,
                   COUNT(*) AS customers,
                   ROUND(AVG(mon),0) AS avg_ltv,
                   ROUND(AVG(rec),1) AS avg_days,
                   ROUND(AVG(freq),1) AS avg_shp
            FROM base GROUP BY segment ORDER BY customers DESC""")
        if not rfm.empty:
            rfm = tonums(rfm, ["customers","avg_ltv","avg_days","avg_shp"])
            SEG = {"CHAMPION":C_GOLD,"LOYAL":C_GREEN,"NEW":C_BLUE,
                   "AT RISK":C_RED,"LOST":"#475569","POTENTIAL":C_GREY}
            c1, c2 = st.columns(2, gap="medium")
            with c1:
                section("Customer Segments")
                fig = px.bar(rfm, x="segment", y="customers",
                             color="segment", color_discrete_map=SEG,
                             text_auto=True)
                fig.update_traces(marker_line_width=0)
                fig.update_layout(height=340, showlegend=False, **CHART_BASE)
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                section("Recency vs LTV Bubble")
                fig = px.scatter(rfm, x="avg_days", y="avg_ltv",
                                 size="customers", color="segment",
                                 color_discrete_map=SEG,
                                 labels={"avg_days":"Avg Days Since Last Ship","avg_ltv":"Avg LTV SAR"})
                fig.update_layout(height=340, showlegend=False, **CHART_BASE)
                st.plotly_chart(fig, use_container_width=True)
            st.dataframe(
                rfm.style.format({"customers":"{:,.0f}","avg_ltv":"{:,.0f}",
                                   "avg_days":"{:.1f}","avg_shp":"{:.1f}"}),
                use_container_width=True, height=220)

    with tab2:
        top = sql(f"""SELECT customer_account, customer_name_en, account_type, city,
                             total_shipments,
                             ROUND(total_revenue_sar,0) AS rev,
                             ROUND(delivery_rate_pct,1) AS del_pct,
                             ROUND(avg_shipment_value_sar,2) AS avg_order
                      FROM {NS}.gold_customer_revenue_summary
                      WHERE customer_name_en IS NOT NULL
                      ORDER BY total_revenue_sar DESC LIMIT 20""")
        if not top.empty:
            top = tonums(top, ["total_shipments","rev","del_pct","avg_order"])
            section("Top 20 Accounts by Lifetime Revenue")
            fig = px.bar(top, x="customer_name_en", y="rev",
                         color="account_type", color_discrete_sequence=PALETTE,
                         labels={"rev":"Revenue SAR","customer_name_en":""})
            fig.update_traces(marker_line_width=0)
            fig.update_layout(height=360, xaxis_tickangle=-30,
                               legend=dict(orientation="h",y=-0.25,bgcolor="rgba(0,0,0,0)"),
                               **CHART_BASE)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(
                top.style.format({"total_shipments":"{:,.0f}","rev":"{:,.0f}",
                                   "del_pct":"{:.1f}%","avg_order":"{:,.2f}"})
                .background_gradient(subset=["rev"], cmap="Blues"),
                use_container_width=True, height=300)
            dl(top, "top_accounts")

        section("Account Status Distribution")
        acct = sql(f"""SELECT account_status, account_type, COUNT(*) AS cnt
                       FROM {NS}.silver_customers_merged
                       WHERE __END_AT IS NULL GROUP BY account_status, account_type""")
        if not acct.empty:
            acct = tonums(acct, ["cnt"])
            fig = px.sunburst(acct, path=["account_status","account_type"], values="cnt",
                              color="cnt",
                              color_continuous_scale=[[0,C_NAVY],[.5,C_BLUE],[1,C_GOLD]])
            fig.update_layout(height=360, **CHART_BASE,
                               coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)

    with tab3:
        section("Active Accounts — No Shipment in 60+ Days")
        ch = sql(f"""SELECT c.account_id, c.customer_name_en, c.account_type, c.city,
                            c.monthly_volume_avg,
                            MAX(s.created_at) AS last_ship,
                            DATEDIFF(CURRENT_DATE, MAX(DATE(s.created_at))) AS days,
                            COUNT(s.waybill_number) AS total_shp
                     FROM {NS}.silver_customers_merged c
                     LEFT JOIN {NS}.silver_shipments s ON c.account_id=s.customer_account
                     WHERE c.__END_AT IS NULL AND c.account_status='ACTIVE'
                     GROUP BY 1,2,3,4,5
                     HAVING days>60 OR last_ship IS NULL
                     ORDER BY days DESC NULLS LAST LIMIT 50""")
        if not ch.empty:
            ch = tonums(ch, ["days","total_shp","monthly_volume_avg"])
            kpi_row([
                ("😴", f"{len(ch):,}",               "At-Risk Accounts",   "red",  ""),
                ("📅", f"{ch['days'].mean():.0f}d",   "Avg Days Dormant",   "gold", ""),
            ])
            c1, c2 = st.columns(2, gap="medium")
            with c1:
                fig = px.histogram(ch, x="days", nbins=20,
                                   color_discrete_sequence=[C_RED],
                                   labels={"days":"Days Since Last Shipment"})
                fig.update_traces(marker_line_color=C_DARK, marker_line_width=1)
                fig.update_layout(height=300, showlegend=False, **CHART_BASE)
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                fig = px.scatter(ch, x="days", y="monthly_volume_avg",
                                 color="account_type",
                                 color_discrete_sequence=PALETTE,
                                 hover_data=["customer_name_en","city"],
                                 labels={"days":"Days Dormant","monthly_volume_avg":"Avg Monthly Volume"})
                fig.update_layout(height=300, **CHART_BASE,
                                   legend=dict(orientation="h",y=-0.25,bgcolor="rgba(0,0,0,0)"))
                st.plotly_chart(fig, use_container_width=True)
            st.dataframe(
                ch.style.format({"days":"{:.0f}","total_shp":"{:,.0f}","monthly_volume_avg":"{:.1f}"})
                .background_gradient(subset=["days"], cmap="Reds"),
                use_container_width=True, height=320)
            dl(ch, "churn_risk")
        else:
            st.success("No accounts meet churn criteria for this period.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 9 — Hub Performance
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🔍  Hub Performance":
    hero("🔍", "Hub Performance Tiers", "30-day composite ranking · STAR · HIGH VOLUME · HIGH QUALITY")

    hubs = sql(f"""
        WITH daily AS (SELECT created_date, hub_code,
                              SUM(total_shipments) AS shp, SUM(total_revenue_sar) AS rev,
                              AVG(sla_compliance_rate_pct) AS sla, AVG(avg_delivery_hrs) AS hrs
                       FROM {NS}.gold_daily_shipment_kpis
                       WHERE created_date>=CURRENT_DATE-INTERVAL 30 DAYS GROUP BY 1,2),
        summary AS (SELECT hub_code,
                           SUM(shp) AS shp_30d, ROUND(SUM(rev),0) AS rev_30d,
                           ROUND(AVG(sla),1) AS avg_sla, ROUND(AVG(hrs),1) AS avg_hrs,
                           COUNT(DISTINCT created_date) AS active_days
                    FROM daily GROUP BY hub_code),
        ranked AS (SELECT *, RANK() OVER (ORDER BY rev_30d DESC) AS rr,
                             RANK() OVER (ORDER BY avg_sla DESC) AS sr FROM summary)
        SELECT hub_code, shp_30d, rev_30d, avg_sla, avg_hrs, active_days,
               rr AS revenue_rank, sr AS sla_rank, rr+sr AS composite_rank,
               CASE WHEN rr<=2 AND sr<=2 THEN 'STAR'
                    WHEN rr<=2           THEN 'HIGH VOLUME'
                    WHEN sr<=2           THEN 'HIGH QUALITY'
                    ELSE 'STANDARD' END AS tier
        FROM ranked ORDER BY composite_rank""")
    if not hubs.empty:
        hubs = tonums(hubs, ["shp_30d","rev_30d","avg_sla","avg_hrs","active_days",
                               "revenue_rank","sla_rank","composite_rank"])
        TIER_C = {"STAR":C_GOLD,"HIGH VOLUME":C_BLUE,"HIGH QUALITY":C_GREEN,"STANDARD":C_GREY}
        TIER_I = {"STAR":"⭐","HIGH VOLUME":"📦","HIGH QUALITY":"🏅","STANDARD":"🔵"}
        tc = hubs["tier"].value_counts()
        kpi_row([(TIER_I.get(t,"📍"), str(int(c)), f"{t} Hubs", "gold" if t=="STAR" else "blue","")
                 for t, c in tc.items()])

        c1, c2 = st.columns(2, gap="medium")
        with c1:
            section("Volume vs SLA % — Bubble = Revenue")
            fig = px.scatter(hubs, x="shp_30d", y="avg_sla",
                             size="rev_30d", color="tier",
                             color_discrete_map=TIER_C, text="hub_code",
                             hover_data=["avg_hrs","active_days"],
                             labels={"shp_30d":"30d Shipments","avg_sla":"SLA %"})
            fig.add_hline(y=90, line_dash="dash", line_color=C_GREY,
                          annotation_text="90 % target",
                          annotation_font=dict(color=C_GREY, size=10))
            fig.update_traces(textfont=dict(color="white", size=9))
            fig.update_layout(height=400, **CHART_BASE,
                               legend=dict(orientation="h",y=-0.2,bgcolor="rgba(0,0,0,0)"))
            st.plotly_chart(fig, use_container_width=True)

        with c2:
            section("30-Day Revenue by Hub")
            fig = px.bar(hubs.sort_values("composite_rank"),
                         x="hub_code", y="rev_30d",
                         color="tier", color_discrete_map=TIER_C,
                         text_auto=".3s",
                         labels={"rev_30d":"Revenue SAR"})
            fig.update_traces(marker_line_width=0)
            fig.update_layout(height=400, xaxis_tickangle=-30,
                               legend=dict(orientation="h",y=-0.2,bgcolor="rgba(0,0,0,0)"),
                               **CHART_BASE)
            st.plotly_chart(fig, use_container_width=True)

        section("Hub Performance Table")
        st.dataframe(
            hubs.style.format({"shp_30d":"{:,.0f}","rev_30d":"{:,.0f}",
                                "avg_sla":"{:.1f}%","avg_hrs":"{:.1f}h"})
            .background_gradient(subset=["avg_sla"], cmap="RdYlGn"),
            use_container_width=True, height=300)
        dl(hubs, "hub_tiers")

        section("Daily Revenue — last 30 days")
        hd = sql(f"""SELECT created_date, hub_code,
                            SUM(total_revenue_sar) AS rev, SUM(total_shipments) AS shp,
                            ROUND(AVG(sla_compliance_rate_pct),1) AS sla
                     FROM {NS}.gold_daily_shipment_kpis
                     WHERE created_date>=CURRENT_DATE-INTERVAL 30 DAYS
                     GROUP BY created_date, hub_code ORDER BY created_date""")
        if not hd.empty:
            hd = tonums(hd, ["rev","shp","sla"])
            fig = px.line(hd, x="created_date", y="rev", color="hub_code",
                          color_discrete_sequence=PALETTE,
                          labels={"rev":"Revenue SAR","created_date":""})
            fig.update_traces(line_width=2)
            fig.update_layout(height=320, **CHART_BASE,
                               legend=dict(orientation="h",y=-0.2,bgcolor="rgba(0,0,0,0)"))
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("No hub performance data available.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 10 — ML Risk
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🤖  ML Risk":
    hero("🤖", "ML Delay Risk Scores", "Random Forest SLA predictor · Active shipments · Risk bands")

    scores = sql(f"SELECT * FROM {NS}.ml_sla_risk_scores ORDER BY delay_risk_pct DESC LIMIT 1000")
    if scores.empty:
        st.warning("No scores found. Run `09b-Naqel-ML-Anomaly.py` first.")
    else:
        scores = tonums(scores, ["delay_risk_pct"])
        bc = scores["risk_band"].value_counts()
        kpi_row([
            ("🔴", str(int(bc.get("HIGH",0))),   "HIGH Risk",    "red",   ""),
            ("🟡", str(int(bc.get("MEDIUM",0))), "MEDIUM Risk",  "gold",  ""),
            ("🟢", str(int(bc.get("LOW",0))),    "LOW Risk",     "green", ""),
            ("📊", str(len(scores)),              "Total Scored", "blue",  ""),
        ])

        c1, c2 = st.columns(2, gap="medium")
        with c1:
            section("Risk Band Distribution")
            fig = pie_fig(scores, "risk_band", "risk_band")
            fig.update_traces(marker=dict(colors=[C_RED, C_GOLD, C_GREEN, C_GREY]))
            st.plotly_chart(fig, use_container_width=True)

        with c2:
            section("Risk Score Histogram")
            fig = px.histogram(scores, x="delay_risk_pct", nbins=25,
                               color_discrete_sequence=[C_BLUE])
            fig.update_traces(marker_line_color=C_DARK, marker_line_width=1)
            fig.add_vline(x=70, line_dash="dash", line_color=C_RED,
                          annotation_text="HIGH > 70",
                          annotation_font=dict(color=C_RED, size=10))
            fig.add_vline(x=40, line_dash="dash", line_color=C_GOLD,
                          annotation_text="MEDIUM > 40",
                          annotation_font=dict(color=C_GOLD, size=10))
            fig.update_layout(height=320, showlegend=False, **CHART_BASE)
            st.plotly_chart(fig, use_container_width=True)

        if "hub_code" in scores.columns:
            section("Risk Distribution by Hub")
            hr = scores.groupby(["hub_code","risk_band"]).size().reset_index(name="cnt")
            fig = px.bar(hr, x="hub_code", y="cnt", color="risk_band",
                         color_discrete_map={"HIGH":C_RED,"MEDIUM":C_GOLD,"LOW":C_GREEN},
                         barmode="stack", labels={"cnt":"Shipments"})
            fig.update_traces(marker_line_width=0)
            fig.update_layout(height=300, xaxis_tickangle=-30,
                               legend=dict(orientation="h",y=-0.2,bgcolor="rgba(0,0,0,0)"),
                               **CHART_BASE)
            st.plotly_chart(fig, use_container_width=True)

        section("High-Risk Shipments")
        high = scores[scores["risk_band"]=="HIGH"].sort_values("delay_risk_pct", ascending=False)
        st.dataframe(
            high.style.background_gradient(subset=["delay_risk_pct"], cmap="Reds"),
            use_container_width=True, height=340)
        dl(high, "high_risk_scores")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 11 — Anomalies
# ══════════════════════════════════════════════════════════════════════════════
elif page == "⚠️  Anomalies":
    hero("⚠️", "Anomaly Detection", "Isolation Forest — Fleet telemetry & Freight jobs")

    tab1, tab2 = st.tabs(["  Fleet Anomalies  ", "  Freight Anomalies  "])

    with tab1:
        fa = sql(f"""SELECT vehicle_id, reading_date,
                            avg_speed_kmh, total_fuel_l, overspeed_events,
                            harsh_brake_events, safety_score,
                            ROUND(anomaly_score,4) AS anomaly_score
                     FROM {NS}.anomaly_fleet_telemetry
                     ORDER BY anomaly_score ASC LIMIT 200""")
        if fa.empty:
            st.warning("No fleet anomalies. Run `09b-Naqel-ML-Anomaly.py` first.")
        else:
            fa = tonums(fa, ["avg_speed_kmh","total_fuel_l","overspeed_events",
                              "harsh_brake_events","safety_score","anomaly_score"])
            kpi_row([
                ("🚨", f"{len(fa):,}",                        "Anomalies Detected",    "red",  ""),
                ("📉", f"{fa['anomaly_score'].mean():.4f}",   "Avg Anomaly Score",     "gold", ""),
                ("🛡", f"{fa['safety_score'].mean():.1f}",    "Avg Safety (anomalous)","blue", ""),
            ])
            c1, c2 = st.columns(2, gap="medium")
            with c1:
                section("Speed vs Fuel — Anomaly Score")
                fig = px.scatter(fa, x="avg_speed_kmh", y="total_fuel_l",
                                 color="anomaly_score",
                                 color_continuous_scale=[[0,C_RED],[.5,C_GOLD],[1,C_GREY]],
                                 hover_data=["vehicle_id","safety_score"])
                fig.update_layout(height=340, **CHART_BASE,
                                   coloraxis_colorbar=dict(tickfont=dict(color=C_GREY)))
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                section("Top 20 Most Anomalous Vehicles")
                fig = px.bar(fa.sort_values("anomaly_score").head(20),
                             x="vehicle_id", y="anomaly_score",
                             color="anomaly_score",
                             color_continuous_scale=[[0,C_RED],[1,C_GOLD]])
                fig.update_traces(marker_line_width=0)
                fig.update_layout(height=340, xaxis_tickangle=-45,
                                   showlegend=False, **CHART_BASE,
                                   coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)
            section("Anomalous Fleet Records")
            st.dataframe(
                fa.sort_values("anomaly_score").style
                .background_gradient(subset=["anomaly_score"], cmap="Reds_r"),
                use_container_width=True, height=300)
            dl(fa, "fleet_anomalies")

    with tab2:
        fr = sql(f"""SELECT freight_job_id, mode, origin_country, dest_country,
                            freight_cost_usd, gross_weight_kg, transit_days,
                            ROUND(anomaly_score,4) AS anomaly_score
                     FROM {NS}.anomaly_freight_jobs
                     ORDER BY anomaly_score ASC LIMIT 200""")
        if fr.empty:
            st.warning("No freight anomalies. Run `09b-Naqel-ML-Anomaly.py` first.")
        else:
            fr = tonums(fr, ["freight_cost_usd","gross_weight_kg","transit_days","anomaly_score"])
            kpi_row([
                ("🚨", f"{len(fr):,}",                      "Anomalies Detected", "red",  ""),
                ("📉", f"{fr['anomaly_score'].mean():.4f}", "Avg Anomaly Score",  "gold", ""),
            ])
            c1, c2 = st.columns(2, gap="medium")
            with c1:
                section("Cost vs Weight — Anomaly Score")
                fig = px.scatter(fr, x="freight_cost_usd", y="gross_weight_kg",
                                 color="anomaly_score",
                                 color_continuous_scale=[[0,C_RED],[.5,C_GOLD],[1,C_GREY]],
                                 symbol="mode",
                                 hover_data=["freight_job_id","origin_country","dest_country"])
                fig.update_layout(height=340, **CHART_BASE)
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                section("Anomaly Count by Mode")
                ma = fr.groupby("mode").agg(cnt=("freight_job_id","count"),
                                             avg_score=("anomaly_score","mean")).reset_index()
                fig = px.bar(ma, x="mode", y="cnt",
                             color="avg_score",
                             color_continuous_scale=[[0,C_GOLD],[1,C_RED]],
                             text_auto=True)
                fig.update_traces(marker_line_width=0)
                fig.update_layout(height=340, showlegend=False, **CHART_BASE,
                                   coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)
            section("Anomalous Freight Records")
            st.dataframe(
                fr.sort_values("anomaly_score").style
                .background_gradient(subset=["anomaly_score"], cmap="Reds_r"),
                use_container_width=True, height=300)
            dl(fr, "freight_anomalies")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 12 — Data Quality
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🩺  Data Quality":
    hero("🩺", "Data Quality & Pipeline Health", "Row counts · Null rates · Quarantine")

    tab1, tab2, tab3 = st.tabs(["  Table Counts  ", "  Null Rates  ", "  Quarantine  "])

    with tab1:
        section("Row Counts Across All 17 DLT Tables")
        with st.spinner("Counting rows…"):
            counts = sql(f"""
                SELECT 'bronze_shipments'                AS tbl, COUNT(*) AS rows FROM {NS}.bronze_shipments
                UNION ALL SELECT 'bronze_warehouse_events',      COUNT(*) FROM {NS}.bronze_warehouse_events
                UNION ALL SELECT 'bronze_fleet_telemetry',       COUNT(*) FROM {NS}.bronze_fleet_telemetry
                UNION ALL SELECT 'bronze_freight_jobs',          COUNT(*) FROM {NS}.bronze_freight_jobs
                UNION ALL SELECT 'bronze_customers',             COUNT(*) FROM {NS}.bronze_customers
                UNION ALL SELECT 'silver_shipments_merged',      COUNT(*) FROM {NS}.silver_shipments_merged
                UNION ALL SELECT 'silver_shipments',             COUNT(*) FROM {NS}.silver_shipments
                UNION ALL SELECT 'silver_warehouse_events',      COUNT(*) FROM {NS}.silver_warehouse_events
                UNION ALL SELECT 'silver_fleet_telemetry',       COUNT(*) FROM {NS}.silver_fleet_telemetry
                UNION ALL SELECT 'silver_freight_jobs',          COUNT(*) FROM {NS}.silver_freight_jobs
                UNION ALL SELECT 'silver_customers_merged',      COUNT(*) FROM {NS}.silver_customers_merged
                UNION ALL SELECT 'gold_daily_shipment_kpis',     COUNT(*) FROM {NS}.gold_daily_shipment_kpis
                UNION ALL SELECT 'gold_route_performance',       COUNT(*) FROM {NS}.gold_route_performance
                UNION ALL SELECT 'gold_fleet_daily_summary',     COUNT(*) FROM {NS}.gold_fleet_daily_summary
                UNION ALL SELECT 'gold_warehouse_throughput',    COUNT(*) FROM {NS}.gold_warehouse_throughput
                UNION ALL SELECT 'gold_freight_cost_analysis',   COUNT(*) FROM {NS}.gold_freight_cost_analysis
                UNION ALL SELECT 'gold_customer_revenue_summary',COUNT(*) FROM {NS}.gold_customer_revenue_summary
                ORDER BY rows DESC""")
        if not counts.empty:
            counts = tonums(counts, ["rows"])
            counts["layer"] = counts["tbl"].apply(
                lambda t: "Bronze" if t.startswith("bronze")
                else ("Silver" if t.startswith("silver") else "Gold"))
            LAYER_C = {"Bronze":"#CD7F32","Silver":"#94A3B8","Gold":C_GOLD}
            fig = px.bar(counts, x="tbl", y="rows", color="layer",
                         color_discrete_map=LAYER_C, text_auto=".3s",
                         labels={"tbl":"","rows":"Row Count"})
            fig.update_traces(marker_line_width=0)
            fig.update_layout(height=380, xaxis_tickangle=-40,
                               legend=dict(orientation="h",y=-0.25,bgcolor="rgba(0,0,0,0)"),
                               **CHART_BASE)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(
                counts[["layer","tbl","rows"]].style.format({"rows":"{:,.0f}"}),
                use_container_width=True, height=360)
            dl(counts, "table_counts")

    with tab2:
        section("Null Rate Analysis — silver_shipments Key Columns")
        with st.spinner("Analysing nulls…"):
            nulls = sql(f"""SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN waybill_number IS NULL THEN 1 ELSE 0 END)       AS null_waybill,
                SUM(CASE WHEN customer_account IS NULL THEN 1 ELSE 0 END)     AS null_customer,
                SUM(CASE WHEN status IS NULL THEN 1 ELSE 0 END)               AS null_status,
                SUM(CASE WHEN hub_code IS NULL THEN 1 ELSE 0 END)             AS null_hub,
                SUM(CASE WHEN total_charge_sar IS NULL THEN 1 ELSE 0 END)     AS null_charge,
                SUM(CASE WHEN weight_kg IS NULL THEN 1 ELSE 0 END)            AS null_weight,
                SUM(CASE WHEN delivery_duration_hrs IS NULL THEN 1 ELSE 0 END) AS null_duration,
                SUM(CASE WHEN sla_met IS NULL THEN 1 ELSE 0 END)              AS null_sla
                FROM {NS}.silver_shipments""")
        if not nulls.empty:
            row = nulls.iloc[0]; total = int(n(row["total"])) or 1
            ncols = [c for c in nulls.columns if c != "total"]
            nd = pd.DataFrame({"column":ncols,
                                "null_count":[int(n(row[c])) for c in ncols]})
            nd["null_pct"] = (nd["null_count"]/total*100).round(3)
            fig = px.bar(nd.sort_values("null_pct", ascending=False),
                         x="column", y="null_pct",
                         color="null_pct",
                         color_continuous_scale=[[0,C_GREEN],[.4,C_GOLD],[1,C_RED]],
                         text_auto=".3f",
                         labels={"null_pct":"Null %"})
            fig.update_traces(marker_line_width=0)
            fig.add_hline(y=1, line_dash="dash", line_color=C_RED,
                          annotation_text="1 % threshold",
                          annotation_font=dict(color=C_RED, size=10))
            fig.update_layout(height=320, showlegend=False, **CHART_BASE,
                               coloraxis_showscale=False,
                               title=dict(text=f"Total rows: {total:,}", font=dict(color=C_GREY, size=11)))
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(
                nd.style.format({"null_count":"{:,.0f}","null_pct":"{:.3f}%"})
                .background_gradient(subset=["null_pct"], cmap="YlOrRd"),
                use_container_width=True, height=220)

    with tab3:
        section("Quarantine Tables — Records Failing DQ Rules")
        st.caption(
            "Tables written by `03-Naqel-Data-Quality.py`. "
            "They may live in `naqel_lakehouse.naqel_express` or in the per-domain schemas "
            "(`naqel_lakehouse.shipments`, `naqel_lakehouse.warehouse`). "
            "The lookup below checks all locations automatically."
        )

        # Candidate locations: (schema, table_name) pairs to probe
        QUARANTINE_CANDIDATES = [
            # DLT pipeline target schema (most likely after pipeline run)
            ("naqel_lakehouse.naqel_express",  "dq_quarantine_shipments"),
            ("naqel_lakehouse.naqel_express",  "dq_quarantine_warehouse"),
            # Per-domain schemas (from 03-Naqel-Data-Quality.py direct writes)
            ("naqel_lakehouse.shipments",       "dq_quarantine_shipments"),
            ("naqel_lakehouse.warehouse",       "dq_quarantine_warehouse"),
        ]

        def _table_exists(full_name: str) -> bool:
            """Return True if the table/view exists and is queryable."""
            chk = sql(f"SELECT 1 FROM {full_name} LIMIT 0")
            # sql() returns empty DataFrame on success or on error;
            # distinguish by checking if an error was raised (columns will be empty on error).
            # Simpler: try COUNT(*) — if it errors, the table doesn't exist.
            return not chk.columns.empty if not chk.empty or len(chk.columns) > 0 else False

        def _probe_table(full_name: str) -> bool:
            """Use INFORMATION_SCHEMA to check existence without triggering st.error."""
            parts   = full_name.rsplit(".", 1)
            schema  = parts[0]          # e.g. naqel_lakehouse.naqel_express
            tname   = parts[1]          # e.g. dq_quarantine_shipments
            cat, sch = schema.split(".", 1)
            probe = sql(
                f"SELECT 1 FROM {cat}.information_schema.tables "
                f"WHERE table_schema='{sch}' AND table_name='{tname}' LIMIT 1",
                _silent=True,
            )
            return not probe.empty

        found_any = False
        seen: set[str] = set()   # avoid duplicate expanders for the same logical table

        for schema_ns, tbl in QUARANTINE_CANDIDATES:
            full = f"{schema_ns}.{tbl}"
            if tbl in seen:
                continue

            exists = _probe_table(full)
            if not exists:
                continue

            seen.add(tbl)
            found_any = True

            with st.expander(f"📋  {full}", expanded=True):
                q = sql(f"SELECT * FROM {full} LIMIT 200")
                if q.empty:
                    st.success(f"✅  No quarantined records in `{full}`")
                else:
                    st.warning(f"{len(q):,} records shown (capped at 200)")
                    st.dataframe(q, use_container_width=True, height=280)
                    dl(q, f"quarantine_{tbl}")

        if not found_any:
            st.info(
                "**Quarantine tables not found.**\n\n"
                "They are created by running `03-Naqel-Data-Quality.py` as a DLT pipeline. "
                "Once the pipeline has run at least once, quarantine tables will appear here automatically.\n\n"
                "Expected locations:\n"
                "- `naqel_lakehouse.naqel_express.dq_quarantine_shipments`\n"
                "- `naqel_lakehouse.naqel_express.dq_quarantine_warehouse`"
            )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 13 — Churn & Demand Forecast (new MLlib models)
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🔮  Churn & Demand":
    hero("🔮", "Churn & Demand Intelligence", "Logistic Regression churn scores · GBT demand forecast · Freight cost ML")

    tab1, tab2, tab3 = st.tabs(["  Customer Churn  ", "  Demand Forecast  ", "  Freight Cost ML  "])

    with tab1:
        churn = sql(f"""
            SELECT customer_account, churn_probability_pct, churn_risk_band,
                   recency_days, frequency, monetary_sar,
                   orders_last_30d, orders_last_90d, sla_satisfaction_pct,
                   scored_at
            FROM {NS}.ml_customer_churn_scores
            ORDER BY churn_probability_pct DESC
            LIMIT 1000
        """)
        if churn.empty:
            st.warning("No churn scores found. Run `11-Naqel-MLlib-Advanced.py` first.")
        else:
            churn = tonums(churn, ["churn_probability_pct", "recency_days", "frequency",
                                    "monetary_sar", "orders_last_30d", "orders_last_90d",
                                    "sla_satisfaction_pct"])
            bc = churn["churn_risk_band"].value_counts()
            kpi_row([
                ("🔴", str(int(bc.get("HIGH",   0))), "HIGH Churn Risk",    "red",   ""),
                ("🟡", str(int(bc.get("MEDIUM", 0))), "MEDIUM Churn Risk",  "gold",  ""),
                ("🟢", str(int(bc.get("LOW",    0))), "LOW Churn Risk",     "green", ""),
                ("📊", str(len(churn)),               "Customers Scored",   "blue",  ""),
            ])
            c1, c2 = st.columns(2, gap="medium")
            with c1:
                section("Churn Risk Distribution")
                fig = pie_fig(churn, "churn_risk_band", "churn_risk_band")
                fig.update_traces(marker=dict(colors=[C_RED, C_GOLD, C_GREEN]))
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                section("Recency vs Churn Probability")
                fig = scatter_fig(churn, "recency_days", "churn_probability_pct",
                                  color="churn_risk_band", size="frequency",
                                  hover_data=["customer_account", "sla_satisfaction_pct"])
                st.plotly_chart(fig, use_container_width=True)

            section("High-Risk Accounts — Immediate Action Required")
            high_risk = churn[churn["churn_risk_band"] == "HIGH"].sort_values("churn_probability_pct", ascending=False)
            st.dataframe(
                high_risk.style.background_gradient(subset=["churn_probability_pct"], cmap="Reds"),
                use_container_width=True, height=340
            )
            dl(high_risk, "high_churn_risk")

    with tab2:
        demand = sql(f"""
            SELECT created_date, hub_code, service_code,
                   demand AS actual_demand,
                   predicted_demand, demand_delta_pct, forecast_generated_at
            FROM {NS}.ml_demand_forecast
            ORDER BY created_date DESC, hub_code
            LIMIT 500
        """)
        if demand.empty:
            st.warning("No demand forecast found. Run `11-Naqel-MLlib-Advanced.py` first.")
        else:
            demand = tonums(demand, ["actual_demand", "predicted_demand", "demand_delta_pct"])
            section("Actual vs Predicted Demand by Hub")
            top_hubs = demand.groupby("hub_code")["actual_demand"].sum().nlargest(6).index.tolist()
            filt = demand[demand["hub_code"].isin(top_hubs)]
            fig = go.Figure()
            for hub in top_hubs:
                h = filt[filt["hub_code"] == hub].sort_values("created_date")
                fig.add_scatter(x=h["created_date"], y=h["actual_demand"],
                                name=f"{hub} actual", mode="lines",
                                line=dict(width=2))
                fig.add_scatter(x=h["created_date"], y=h["predicted_demand"],
                                name=f"{hub} forecast", mode="lines",
                                line=dict(width=2, dash="dash"))
            fig.update_layout(height=400, **CHART_BASE,
                               legend=dict(orientation="h", y=-0.25, bgcolor="rgba(0,0,0,0)"))
            st.plotly_chart(fig, use_container_width=True)

            section("Forecast Accuracy — Delta Distribution")
            fig = px.histogram(demand, x="demand_delta_pct", nbins=30,
                               color_discrete_sequence=[C_BLUE])
            fig.update_layout(height=280, showlegend=False, **CHART_BASE)
            st.plotly_chart(fig, use_container_width=True)
            dl(demand, "demand_forecast")

    with tab3:
        frt_ml = sql(f"""
            SELECT f.freight_job_id, actual_cost_usd, predicted_cost_usd,
                   cost_error_pct, cost_flag, scored_at
            FROM {NS}.ml_freight_cost_estimates f
            ORDER BY cost_error_pct DESC
            LIMIT 500
        """)
        if frt_ml.empty:
            st.warning("No freight cost estimates found. Run `11-Naqel-MLlib-Advanced.py` first.")
        else:
            frt_ml = tonums(frt_ml, ["actual_cost_usd", "predicted_cost_usd", "cost_error_pct"])
            kpi_row([
                ("🎯", f"{frt_ml[frt_ml['cost_flag']=='NORMAL']['cost_flag'].count():,}",  "Normal Estimates",  "green", ""),
                ("⚠️", f"{frt_ml[frt_ml['cost_flag']=='ANOMALY']['cost_flag'].count():,}", "Cost Anomalies",    "red",   ""),
                ("📊", f"{frt_ml['cost_error_pct'].mean():.1f}%",                          "Avg Error %",       "gold",  ""),
            ])
            c1, c2 = st.columns(2, gap="medium")
            with c1:
                section("Actual vs Predicted Cost")
                fig = px.scatter(frt_ml, x="actual_cost_usd", y="predicted_cost_usd",
                                 color="cost_flag",
                                 color_discrete_map={"NORMAL": C_GREEN, "ANOMALY": C_RED})
                lo, hi = frt_ml["actual_cost_usd"].min(), frt_ml["actual_cost_usd"].max()
                fig.add_scatter(x=[lo, hi], y=[lo, hi], name="Perfect",
                                mode="lines", line=dict(color=C_GREY, dash="dash", width=1))
                fig.update_layout(height=360, **CHART_BASE,
                                   legend=dict(orientation="h", y=-0.2, bgcolor="rgba(0,0,0,0)"))
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                section("Error % Distribution")
                fig = px.histogram(frt_ml, x="cost_error_pct", nbins=25,
                                   color="cost_flag",
                                   color_discrete_map={"NORMAL": C_BLUE, "ANOMALY": C_RED})
                fig.update_layout(height=360, showlegend=True, **CHART_BASE)
                st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 14 — Network Graph (GraphX results)
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🕸️  Network Graph":
    hero("🕸️", "Route Network Intelligence", "GraphX PageRank · Hubs · Communities · Bottlenecks")

    tab1, tab2, tab3, tab4 = st.tabs([
        "  Hub PageRank  ", "  Communities  ", "  Route Metrics  ", "  Network KPIs  "
    ])

    with tab1:
        pr = sql(f"SELECT * FROM {NS}.gold_hub_pagerank ORDER BY hub_rank")
        if pr.empty:
            st.warning("No PageRank data. Run `12-Naqel-GraphX-Network.py` first.")
        else:
            pr = tonums(pr, ["pagerank_score", "outbound_volume", "inbound_volume",
                              "total_volume", "dest_count", "origin_count", "hub_rank"])
            TIER_C = {
                "TIER_1_GATEWAY":  C_GOLD,
                "TIER_2_REGIONAL": C_BLUE,
                "TIER_3_LOCAL":    C_GREEN,
                "TIER_4_SPOKE":    C_GREY,
            }
            kpi_row([
                ("🥇", str(len(pr[pr["hub_tier"] == "TIER_1_GATEWAY"])),  "Gateway Hubs",  "gold",  ""),
                ("🥈", str(len(pr[pr["hub_tier"] == "TIER_2_REGIONAL"])), "Regional Hubs", "blue",  ""),
                ("🥉", str(len(pr[pr["hub_tier"] == "TIER_3_LOCAL"])),    "Local Hubs",    "green", ""),
                ("📍", str(len(pr[pr["hub_tier"] == "TIER_4_SPOKE"])),    "Spoke Cities",  "blue",  ""),
            ])
            c1, c2 = st.columns(2, gap="medium")
            with c1:
                section("PageRank Score by City")
                fig = px.bar(pr.head(25).sort_values("pagerank_score", ascending=True),
                             x="pagerank_score", y="id",
                             color="hub_tier", color_discrete_map=TIER_C,
                             orientation="h", text_auto=".5f")
                fig.update_traces(marker_line_width=0)
                fig.update_layout(height=480, **CHART_BASE,
                                   legend=dict(orientation="h", y=-0.15, bgcolor="rgba(0,0,0,0)"))
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                section("Volume vs PageRank (bubble = total flow)")
                fig = px.scatter(pr, x="total_volume", y="pagerank_score",
                                 color="hub_tier", text="id", size="total_volume",
                                 color_discrete_map=TIER_C)
                fig.update_traces(textfont=dict(size=8, color="white"))
                fig.update_layout(height=480, **CHART_BASE,
                                   legend=dict(orientation="h", y=-0.15, bgcolor="rgba(0,0,0,0)"))
                st.plotly_chart(fig, use_container_width=True)

            section("Hub Rankings Table")
            st.dataframe(
                pr.style.format({"pagerank_score": "{:.6f}", "outbound_volume": "{:,.0f}",
                                  "inbound_volume": "{:,.0f}", "total_volume": "{:,.0f}"})
                .background_gradient(subset=["pagerank_score"], cmap="YlOrBr"),
                use_container_width=True, height=300
            )
            dl(pr, "hub_pagerank")

    with tab2:
        comm = sql(f"""
            SELECT id, cluster_id, cluster_name, hub_rank, hub_tier,
                   pagerank_score, triangle_count, resilience_score
            FROM {NS}.gold_network_communities
            ORDER BY cluster_id, hub_rank
        """)
        if comm.empty:
            st.warning("No community data. Run `12-Naqel-GraphX-Network.py` first.")
        else:
            comm = tonums(comm, ["cluster_id", "hub_rank", "pagerank_score",
                                  "triangle_count", "resilience_score"])
            section("Logistics Network Communities (Label Propagation)")
            agg = comm.groupby("cluster_name").agg(
                cities=("id", "count"),
                avg_pagerank=("pagerank_score", "mean"),
                total_triangles=("triangle_count", "sum"),
            ).reset_index().sort_values("cities", ascending=False)
            fig = px.treemap(agg, path=["cluster_name"], values="cities",
                             color="avg_pagerank",
                             color_continuous_scale=[[0, C_NAVY], [0.5, C_BLUE], [1, C_GOLD]],
                             hover_data=["total_triangles"])
            fig.update_layout(height=420, **CHART_BASE)
            st.plotly_chart(fig, use_container_width=True)

            section("City Community Assignments")
            st.dataframe(
                comm.style.format({"pagerank_score": "{:.5f}", "resilience_score": "{:.2f}"})
                .background_gradient(subset=["resilience_score"], cmap="Greens"),
                use_container_width=True, height=320
            )
            dl(comm, "network_communities")

    with tab3:
        rm = sql(f"""
            SELECT origin_city, dest_city, total_volume, total_revenue_sar,
                   avg_transit_hrs, on_time_rate_pct, avg_attempts,
                   has_return_route, flow_imbalance_ratio, is_bottleneck,
                   volume_rank, efficiency_rank,
                   origin_cluster, dest_cluster, is_cross_cluster
            FROM {NS}.gold_route_graph_metrics
            ORDER BY total_volume DESC
            LIMIT 200
        """)
        if rm.empty:
            st.warning("No route graph data. Run `12-Naqel-GraphX-Network.py` first.")
        else:
            rm = tonums(rm, ["total_volume", "total_revenue_sar", "avg_transit_hrs",
                              "on_time_rate_pct", "flow_imbalance_ratio", "volume_rank"])
            bottle = rm[rm["is_bottleneck"] == True]
            no_ret = rm[rm["has_return_route"] == False]
            kpi_row([
                ("🚧", str(len(bottle)),                "Bottleneck Routes",    "red",  ""),
                ("↩️", str(len(no_ret)),                "Missing Return Routes","gold", ""),
                ("✅", str(len(rm) - len(bottle)),      "Healthy Routes",       "green",""),
                ("🌐", str(rm["is_cross_cluster"].sum()), "Cross-Cluster Routes","blue", ""),
            ])
            c1, c2 = st.columns(2, gap="medium")
            with c1:
                section("Transit vs On-Time % (bottlenecks highlighted)")
                fig = px.scatter(rm, x="avg_transit_hrs", y="on_time_rate_pct",
                                 color="is_bottleneck",
                                 color_discrete_map={True: C_RED, False: C_BLUE},
                                 size="total_volume",
                                 hover_data=["origin_city", "dest_city", "total_volume"])
                fig.add_hline(y=90, line_dash="dash", line_color=C_GREEN,
                              annotation_text="90% target",
                              annotation_font=dict(color=C_GREEN, size=10))
                fig.update_layout(height=380, **CHART_BASE,
                                   legend=dict(orientation="h", y=-0.2, bgcolor="rgba(0,0,0,0)"))
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                section("Bottleneck Routes — Action Required")
                if not bottle.empty:
                    fig = px.bar(bottle.sort_values("total_volume", ascending=False).head(15),
                                 x="origin_city", y="total_volume",
                                 color="on_time_rate_pct",
                                 color_continuous_scale=[[0, C_RED], [0.5, C_GOLD], [1, C_GREEN]],
                                 hover_data=["dest_city", "avg_transit_hrs"])
                    fig.update_layout(height=380, xaxis_tickangle=-30, **CHART_BASE,
                                       coloraxis_colorbar=dict(title=dict(text="OT%")))
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.success("No bottleneck routes identified.")

            section("Top Routes Detail")
            st.dataframe(
                rm.style.format({
                    "total_volume": "{:,.0f}", "total_revenue_sar": "{:,.0f}",
                    "avg_transit_hrs": "{:.1f}", "on_time_rate_pct": "{:.1f}%",
                }).background_gradient(subset=["on_time_rate_pct"], cmap="RdYlGn"),
                use_container_width=True, height=300
            )
            dl(rm, "route_graph_metrics")

    with tab4:
        kpis = sql(f"SELECT metric_name, value, description FROM {NS}.gold_network_kpis ORDER BY metric_name")
        if kpis.empty:
            st.warning("No network KPIs. Run `12-Naqel-GraphX-Network.py` first.")
        else:
            kpis = tonums(kpis, ["value"])
            section("Network Health KPIs")
            cols = st.columns(4, gap="medium")
            icons = {
                "total_cities": ("🌆", "blue"),
                "total_routes": ("🛣️", "blue"),
                "total_triangles": ("🔺", "green"),
                "avg_route_on_time_pct": ("✅", "green"),
                "bottleneck_routes": ("🚧", "red"),
                "missing_return_routes": ("↩️", "gold"),
                "largest_scc_cities": ("🔗", "blue"),
                "community_count": ("🏘️", "gold"),
            }
            for i, row in kpis.iterrows():
                icon, colour = icons.get(str(row["metric_name"]), ("📊", "blue"))
                with cols[i % 4]:
                    st.markdown(
                        f'<div class="kpi-card c-{colour}">'
                        f'<span class="kpi-icon">{icon}</span>'
                        f'<div class="kpi-value">{int(row["value"]):,}</div>'
                        f'<div class="kpi-label">{row["description"]}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 15 — Live Streams (Structured Streaming output)
# ══════════════════════════════════════════════════════════════════════════════
elif page == "⚡  Live Streams":
    hero("⚡", "Live Stream Intelligence", "Structured Streaming · Real-time SLA · Fleet incidents · Sliding KPIs")

    tab1, tab2, tab3, tab4 = st.tabs([
        "  SLA Alerts  ", "  Fleet Incidents  ", "  Live KPIs  ", "  Cross-Stream  "
    ])

    with tab1:
        sla_s = sql(f"""
            SELECT waybill_number, hub_code, courier_id, service_code,
                   sla_breach_severity, delivery_duration_hrs, sla_threshold_hrs,
                   total_charge_sar, event_time, _ingest_ts
            FROM {NS}.stream_shipment_status_alerts
            ORDER BY _ingest_ts DESC
            LIMIT 200
        """)
        if sla_s.empty:
            st.info("No streaming SLA alerts yet. Start the Structured Streaming notebook (13) to populate this table.")
        else:
            sla_s = tonums(sla_s, ["delivery_duration_hrs", "sla_threshold_hrs", "total_charge_sar"])
            sev_counts = sla_s["sla_breach_severity"].value_counts()
            kpi_row([
                ("🔴", str(int(sev_counts.get("CRITICAL", 0))), "CRITICAL Breaches", "red",   ""),
                ("🟠", str(int(sev_counts.get("HIGH",     0))), "HIGH Breaches",     "red",   ""),
                ("🟡", str(int(sev_counts.get("MEDIUM",   0))), "MEDIUM Breaches",   "gold",  ""),
                ("📊", str(len(sla_s)),                         "Total Alerts",      "blue",  ""),
            ])
            c1, c2 = st.columns(2, gap="medium")
            with c1:
                section("Breach Severity Distribution")
                fig = pie_fig(sla_s, "sla_breach_severity", "sla_breach_severity")
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                section("Alerts by Hub")
                ha = sla_s.groupby("hub_code").size().reset_index(name="cnt").sort_values("cnt", ascending=False)
                fig = bar_fig(ha, "hub_code", "cnt", color_seq=[C_RED])
                fig.update_layout(xaxis_tickangle=-30)
                st.plotly_chart(fig, use_container_width=True)

            section("Latest SLA Breach Events")
            st.dataframe(
                sla_s.style.background_gradient(subset=["delivery_duration_hrs"], cmap="Reds"),
                use_container_width=True, height=300
            )
            dl(sla_s, "stream_sla_alerts")

    with tab2:
        fleet_s = sql(f"""
            SELECT vehicle_id, driver_id, hub_code, window_start, window_end,
                   safety_score, alert_level, overspeed_events, harsh_brakes,
                   geofence_violations, overheat_events, avg_speed_kmh, fuel_consumed_l
            FROM {NS}.stream_fleet_safety_alerts
            ORDER BY window_start DESC
            LIMIT 200
        """)
        if fleet_s.empty:
            st.info("No streaming fleet alerts yet. Start the Structured Streaming notebook (13) to populate.")
        else:
            fleet_s = tonums(fleet_s, ["safety_score", "overspeed_events", "harsh_brakes",
                                        "geofence_violations", "avg_speed_kmh", "fuel_consumed_l"])
            kpi_row([
                ("🚨", str(len(fleet_s[fleet_s["alert_level"] == "CRITICAL"])), "CRITICAL", "red",  ""),
                ("⚠️", str(len(fleet_s[fleet_s["alert_level"] == "WARNING"])),  "WARNING",  "gold", ""),
                ("🚗", f"{fleet_s['avg_speed_kmh'].mean():.1f} km/h",            "Avg Speed","blue", ""),
                ("🛡", f"{fleet_s['safety_score'].mean():.1f}",                  "Avg Safety","green",""),
            ])
            section("Fleet Incidents Timeline")
            fig = px.scatter(fleet_s, x="window_start", y="safety_score",
                             color="alert_level",
                             color_discrete_map={"CRITICAL": C_RED, "WARNING": C_GOLD},
                             size="overspeed_events",
                             hover_data=["vehicle_id", "driver_id", "hub_code"])
            fig.update_layout(height=340, **CHART_BASE,
                               legend=dict(orientation="h", y=-0.2, bgcolor="rgba(0,0,0,0)"))
            st.plotly_chart(fig, use_container_width=True)
            section("Fleet Alert Records")
            st.dataframe(
                fleet_s.style.background_gradient(subset=["safety_score"], cmap="RdYlGn"),
                use_container_width=True, height=300
            )
            dl(fleet_s, "stream_fleet_alerts")

    with tab3:
        kpi_s = sql(f"""
            SELECT window_start, hub_code, service_code,
                   shipments_in_window, sla_breaches, sla_breach_rate_pct,
                   revenue_in_window, avg_transit_hrs, delivery_rate_pct,
                   cod_count, alert_flag
            FROM {NS}.stream_realtime_kpis
            ORDER BY window_start DESC, hub_code
            LIMIT 300
        """)
        if kpi_s.empty:
            st.info("No streaming KPI data yet. Start the Structured Streaming notebook (13).")
        else:
            kpi_s = tonums(kpi_s, ["shipments_in_window", "sla_breaches", "sla_breach_rate_pct",
                                    "revenue_in_window", "avg_transit_hrs", "delivery_rate_pct"])
            section("SLA Breach Rate — Sliding 15-min Windows")
            fig = px.line(kpi_s.sort_values("window_start"),
                          x="window_start", y="sla_breach_rate_pct",
                          color="hub_code", color_discrete_sequence=PALETTE)
            fig.add_hline(y=15, line_dash="dash", line_color=C_RED,
                          annotation_text="15% alert threshold",
                          annotation_font=dict(color=C_RED, size=10))
            fig.update_layout(height=340, **CHART_BASE,
                               legend=dict(orientation="h", y=-0.2, bgcolor="rgba(0,0,0,0)"))
            st.plotly_chart(fig, use_container_width=True)

            alerts = kpi_s[kpi_s["alert_flag"] == True]
            if not alerts.empty:
                st.warning(f"⚠️  {len(alerts)} windows breached the 15% SLA alert threshold")
                section("Alerting Windows")
                st.dataframe(
                    alerts.style.background_gradient(subset=["sla_breach_rate_pct"], cmap="Reds"),
                    use_container_width=True, height=250
                )
            else:
                st.success("✅  No windows currently above the 15% SLA alert threshold")
            dl(kpi_s, "stream_realtime_kpis")

    with tab4:
        cross_s = sql(f"""
            SELECT hub_code, vehicle_id, driver_id, fleet_event_ts,
                   overspeeding, harsh_brake, incident_engine_temp,
                   waybill_number, breach_ts, sla_breach_severity,
                   time_delta_mins, total_charge_sar
            FROM {NS}.stream_cross_incident_alerts
            ORDER BY breach_ts DESC
            LIMIT 200
        """)
        if cross_s.empty:
            st.info("No correlated incidents found yet. Cross-stream joins require both streams to have concurrent data.")
        else:
            cross_s = tonums(cross_s, ["time_delta_mins", "total_charge_sar", "incident_engine_temp"])
            st.markdown("""
            **Cross-Stream Correlation** detects fleet incidents (overspeeding, harsh braking, engine overheating)
            occurring within **±30 minutes** of an SLA breach at the same hub — indicating a causal relationship.
            """)
            kpi_row([
                ("🔗", str(len(cross_s)),                           "Correlated Incidents", "red",  ""),
                ("⏱",  f"{cross_s['time_delta_mins'].abs().mean():.1f} min", "Avg Time Delta","gold",""),
                ("💰",  f"SAR {cross_s['total_charge_sar'].sum():,.0f}",      "At-Risk Revenue","blue",""),
            ])
            section("Fleet Event → SLA Breach Correlation")
            fig = px.scatter(cross_s, x="time_delta_mins", y="total_charge_sar",
                             color="sla_breach_severity",
                             color_discrete_map={"CRITICAL": C_RED, "HIGH": C_GOLD, "MEDIUM": C_GREY},
                             size="total_charge_sar",
                             hover_data=["hub_code", "vehicle_id", "waybill_number"])
            fig.update_layout(height=380, **CHART_BASE,
                               legend=dict(orientation="h", y=-0.2, bgcolor="rgba(0,0,0,0)"))
            st.plotly_chart(fig, use_container_width=True)
            section("Cross-Incident Records")
            st.dataframe(cross_s, use_container_width=True, height=300)
            dl(cross_s, "stream_cross_incidents")


# ── Footer ───────────────────────────────────────────────────────────────────────
st.markdown(
    f'<div class="nq-footer">'
    f'NAQEL Express Analytics Intelligence &nbsp;·&nbsp; '
    f'Databricks Lakehouse &nbsp;·&nbsp; '
    f'MLlib · GraphX · Structured Streaming &nbsp;·&nbsp; '
    f'<span style="color:{C_GOLD}">naqel_lakehouse.naqel_express</span>'
    f'</div>',
    unsafe_allow_html=True,
)