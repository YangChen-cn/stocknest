"""Native Streamlit styling and a consistent Plotly theme; no data requests."""
import plotly.io as pio
import streamlit as st

from stockwatch.presentation import BACKGROUND, BORDER, FONT, GREEN, HEADING_FONT, INK, NEUTRAL, PALETTE, SOFT


def apply_style():
    pio.templates['stockwatch'] = pio.templates['plotly_white']
    pio.templates['stockwatch'].layout.update(
        font=dict(family=FONT, color=INK, size=13), colorway=PALETTE,
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        title=dict(font=dict(family=HEADING_FONT, size=20)),
        xaxis=dict(gridcolor=BORDER, zerolinecolor=BORDER),
        yaxis=dict(gridcolor=BORDER, zerolinecolor=BORDER),
        margin=dict(l=20,r=15,t=55,b=35), hoverlabel=dict(bgcolor='white',font_color=INK),
    )
    pio.templates.default = 'stockwatch'
    st.html(f'''<style>
:root {{ --sw-ink:{INK}; --sw-muted:{NEUTRAL}; --sw-border:{BORDER}; }}
[data-testid="stAppViewContainer"], [data-testid="stHeader"] {{background:{BACKGROUND};color:{INK};}}
[data-testid="stSidebar"] {{background:#f0f2eb;border-right:1px solid {BORDER};}}
[data-testid="stMainBlockContainer"] {{padding:4rem 2rem 3rem;max-width:1600px;}}
[data-testid="stAppViewContainer"] p, [data-testid="stAppViewContainer"] label {{font-family:{FONT};}}
h1,h2,h3 {{font-family:{HEADING_FONT}!important;color:{INK};letter-spacing:-.025em;}}
h1 {{font-size:2.4rem!important;}} h2 {{font-size:1.45rem!important;}} h3 {{font-size:1.2rem!important;}}
[data-testid="stCaptionContainer"] {{color:{NEUTRAL};}}
[data-testid="stMetric"] {{background:white;border:1px solid {BORDER};border-radius:8px;padding:16px;min-height:124px;}}
[data-testid="stMetricValue"] {{font-family:{HEADING_FONT};font-size:1.9rem;font-variant-numeric:tabular-nums;}}
[data-testid="stMetricLabel"] {{color:{NEUTRAL};font-size:13px;}}
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {{border-radius:8px;}}
[data-testid="stExpander"] {{background:#fff;border-color:{BORDER};border-radius:8px;}}
[data-testid="stButton"] button, [data-testid="stDownloadButton"] button {{border-color:{BORDER};border-radius:7px;color:{GREEN};}}
[data-testid="stButton"] button:hover, [data-testid="stDownloadButton"] button:hover {{border-color:{GREEN};background:{SOFT};color:{GREEN};}}
[data-testid="stDownloadButton"] button {{background:{GREEN};color:white;}}
[data-testid="stSidebar"] [data-testid="stRadioOption"] {{display:flex!important;width:100%!important;padding:10px 12px;border-radius:7px;}}
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] {{background:#e0ebe2;color:{GREEN};}}
[data-testid="stSidebar"] [data-testid="stRadioOption"] p {{font-size:16px;}}
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:first-child {{display:none;}}
[data-testid="stSidebar"] [data-testid="stRadioOption"]:focus-within {{outline:2px solid {GREEN};outline-offset:2px;}}
.st-key-overview_charts [data-testid="stVerticalBlockBorderWrapper"], .st-key-overview_charts [data-testid="stVerticalBlock"]:has(> [data-testid="stVerticalBlockBorderWrapper"]) {{background:white;border-color:{BORDER};}}
.sw-milestone {{background:{SOFT};color:{GREEN};border-radius:7px;padding:12px 16px;}}
.sw-record {{font-size:13px;color:{NEUTRAL};margin:4px 0 12px;}}
.sw-footer {{color:{NEUTRAL};font-size:13px;border-top:1px solid {BORDER};padding-top:16px;margin-top:20px;}}
.sw-garden {{max-width:920px;margin:0 auto!important;}}
.sw-garden img {{margin-left:auto!important;margin-right:auto!important;}}
.sw-classic {{border-left:2px solid #c6b58d;max-width:920px;}}
.sw-classic a {{text-decoration:none;border-bottom:1px solid #c6b58d;}}
[data-testid="stBaseButton-primary"] {{background:{GREEN}!important;color:white!important;}}
[data-testid="stSidebar"] h1 {{font-size:2rem!important;}}
@media(max-width:768px) {{[data-testid="stMainBlockContainer"] {{padding:4rem 1rem 1rem;}}h1{{font-size:2rem!important;}}
[data-testid="stMetricValue"]{{font-size:1.65rem;}} [data-testid="stMetric"]{{min-height:100px;}}
.st-key-overview_charts [data-testid="stHorizontalBlock"] {{flex-wrap:wrap;}}
.st-key-overview_charts [data-testid="stColumn"] {{min-width:100%;}}
.st-key-summary_metrics [data-testid="stHorizontalBlock"], .st-key-page_header [data-testid="stHorizontalBlock"] {{flex-wrap:wrap;gap:10px;}}
.st-key-summary_metrics [data-testid="stColumn"], .st-key-page_header [data-testid="stColumn"] {{min-width:calc(50% - 5px)!important;width:calc(50% - 5px)!important;flex:1 1 calc(50% - 5px)!important;}}
.st-key-summary_metrics [data-testid="stColumn"]:first-child, .st-key-page_header [data-testid="stColumn"]:first-child {{min-width:100%!important;flex-basis:100%!important;}}
}}
</style>''')


def plot(figure, **kwargs):
    # Explicit template takes precedence over Streamlit's injected default palette.
    figure.update_layout(template='stockwatch', font=dict(family='Arial, PingFang SC, sans-serif', color=INK), paper_bgcolor='white', plot_bgcolor='white')
    st.plotly_chart(figure, theme=None, width='stretch', **kwargs)
