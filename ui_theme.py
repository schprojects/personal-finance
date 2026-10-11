"""Visual design system for the dashboard: one CSS block, a few
small layout helpers and a shared Plotly style. Presentation only — no data
access and no behaviour. Colours that matter come from `.streamlit/config.toml`
(light + dark); the CSS here uses neutral translucent tones and a fixed deep
gradient for the hero so it reads well in both. No external fonts, CDNs or JS.
"""
import html

import streamlit as st

# Section -> Material icon (nav, Home cards). Option strings stay plain words;
# icons are applied through format_func so widget values never change.
ICONS = {
    "Home": "🏠", "Overview": "📊", "Categories": "🏷️", "Transactions": "🧾",
    "Holdings": "💼", "Import": "📥", "Export": "📤",
}
BLURBS = {
    "Overview": "Net spend, savings rate and trends at a glance.",
    "Categories": "Where the money goes, month by month, payee by payee.",
    "Transactions": "Filter, classify and bulk-edit your entries.",
    "Holdings": "Mutual funds, deposits and net worth with XIRR.",
    "Import": "Load a bank statement, preview it, save to the database.",
    "Export": "Write entries to a CSV for chosen members and years.",
}

# Same hues as .streamlit/config.toml chartCategoricalColors (light / dark).
PALETTE = {
    "light": ["#0F766E", "#4F46E5", "#D97706", "#DB2777",
              "#0284C7", "#65A30D", "#7C3AED", "#64748B"],
    "dark": ["#2DD4BF", "#818CF8", "#FBBF24", "#F472B6",
             "#38BDF8", "#A3E635", "#A78BFA", "#94A3B8"],
}


def palette():
    """Series colours for the viewer's current theme (light when unknown)."""
    try:
        kind = st.context.theme.type or "light"
    except Exception:
        kind = "light"
    return PALETTE.get(kind, PALETTE["light"])


CSS = """
<style>
/* layout */
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1200px; }
header[data-testid="stHeader"] { background: transparent; }
footer { visibility: hidden; }
h1, h2, h3 { letter-spacing: -0.01em; }

/* hero */
.pf-hero {
  border-radius: 1.1rem; padding: 2rem 2.2rem; margin-bottom: 1.4rem; color: #fff;
  background: linear-gradient(135deg, #0F766E 0%, #115E59 38%, #4F46E5 100%);
  box-shadow: 0 10px 30px rgba(15, 118, 110, .25);
}
.pf-hero .pf-kicker { font-size: .78rem; text-transform: uppercase;
  letter-spacing: .14em; opacity: .8; margin: 0 0 .35rem 0; }
.pf-hero h1 { color: #fff; margin: 0; font-size: 2.1rem; font-weight: 700; padding: 0; }
.pf-hero p { margin: .45rem 0 0 0; opacity: .88; font-size: 1.02rem; }
.pf-hero.pf-compact { padding: 1.3rem 1.6rem; }
.pf-hero.pf-compact h1 { font-size: 1.6rem; }

/* page header */
.pf-head { margin: 0 0 1.1rem 0; }
.pf-head h1 { margin: 0; padding: 0; font-size: 1.75rem; font-weight: 700; }
.pf-head p { margin: .2rem 0 0 0; opacity: .65; }

/* section titles */
.pf-section { margin: 1.4rem 0 .5rem 0; }
.pf-section h3 { margin: 0; padding: 0; font-size: 1.08rem; font-weight: 650; }
.pf-section p { margin: .1rem 0 0 0; opacity: .6; font-size: .88rem; }

/* cards (st.container(border=True)) and keyed Home cards */
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 1rem; }
[class*="st-key-card_"] { transition: transform .12s ease, box-shadow .12s ease; }
[class*="st-key-card_"]:hover { transform: translateY(-2px);
  box-shadow: 0 8px 22px rgba(15, 23, 42, .12); }
.pf-card-icon { font-size: 1.7rem; line-height: 1; margin-bottom: .35rem; }
.pf-card-title { font-size: 1.1rem; font-weight: 650; margin: 0; }
.pf-card-text { opacity: .65; font-size: .9rem; margin: .15rem 0 .6rem 0; min-height: 3.1em; }

/* metrics */
[data-testid="stMetric"] { padding: .35rem .2rem; min-height: 5.4rem; }
[data-testid="stMetricLabel"] { opacity: .7; }
[data-testid="stMetricValue"] { font-weight: 700; font-size: 1.7rem; }
[data-testid="stMetricValue"] > div { white-space: normal; overflow: visible;
  text-overflow: clip; line-height: 1.2; }

/* sidebar */
[data-testid="stSidebar"] { border-right: 1px solid rgba(128,128,128,.18); }
.pf-brand { display: flex; align-items: center; gap: .6rem; font-weight: 700;
  font-size: 1.15rem; margin: .2rem 0 .8rem 0; }
.pf-brand .pf-logo { width: 2rem; height: 2rem; border-radius: .6rem; display: grid;
  place-items: center; color: #fff; font-size: 1rem;
  background: linear-gradient(135deg, #0F766E, #4F46E5); }
.pf-user { font-size: .85rem; opacity: .75; margin: 0 0 .4rem 0; }
.pf-label { font-size: .72rem; text-transform: uppercase; letter-spacing: .12em;
  opacity: .55; margin: 1rem 0 .3rem 0; }
[data-testid="stSidebar"] [role="radiogroup"] { gap: .15rem; }
[data-testid="stSidebar"] [role="radiogroup"] label { padding: .35rem .6rem;
  border-radius: .6rem; width: 100%; }
[data-testid="stSidebar"] [role="radiogroup"] label:hover { background: rgba(128,128,128,.12); }
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] {
  background: rgba(15, 118, 110, .18); font-weight: 600; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div > div:first-child:not([data-testid]) {
  display: none; }

/* badges */
.pf-chip { display: inline-block; padding: .18rem .65rem; border-radius: 999px;
  font-size: .8rem; font-weight: 600; background: rgba(128,128,128,.16); }
.pf-chip.pf-warn { background: rgba(217, 119, 6, .18); color: #B45309; }
.pf-chip.pf-ok { background: rgba(15, 118, 110, .16); color: #0F766E; }

/* stepper */
.pf-steps { display: flex; gap: .6rem; margin: .2rem 0 1rem 0; flex-wrap: wrap; }
.pf-step { display: flex; align-items: center; gap: .5rem; padding: .35rem .8rem;
  border-radius: 999px; background: rgba(128,128,128,.12); font-size: .88rem; opacity: .6; }
.pf-step b { display: inline-grid; place-items: center; width: 1.4rem; height: 1.4rem;
  border-radius: 50%; background: rgba(128,128,128,.3); font-size: .78rem; }
.pf-step.pf-on { opacity: 1; background: rgba(15, 118, 110, .16); font-weight: 600; }
.pf-step.pf-on b { background: #0F766E; color: #fff; }

@media (max-width: 640px) {
  .pf-hero { padding: 1.3rem 1.2rem; }
  .pf-hero h1 { font-size: 1.5rem; }
  .block-container { padding-left: 1rem; padding-right: 1rem; }
}
</style>
"""


def _e(x):
    return html.escape(str(x), quote=True)


def inject_css():
    """Call once per run, before any page content."""
    st.html(CSS)


def hero(title, subtitle="", kicker="", compact=False):
    """Gradient banner (landing page and login)."""
    st.html(
        f'<div class="pf-hero{" pf-compact" if compact else ""}">'
        + (f'<p class="pf-kicker">{_e(kicker)}</p>' if kicker else "")
        + f"<h1>{_e(title)}</h1>"
        + (f"<p>{_e(subtitle)}</p>" if subtitle else "") + "</div>")


def page_header(title, subtitle="", icon=""):
    st.html('<div class="pf-head"><h1>' + (f"{_e(icon)} " if icon else "")
            + f"{_e(title)}</h1>"
            + (f"<p>{_e(subtitle)}</p>" if subtitle else "") + "</div>")


def section(title, caption=""):
    st.html(f'<div class="pf-section"><h3>{_e(title)}</h3>'
            + (f"<p>{_e(caption)}</p>" if caption else "") + "</div>")


def chip(text, kind=""):
    """Small pill: kind in '', 'warn', 'ok'. Returns HTML (use with st.html)."""
    return f'<span class="pf-chip{" pf-" + kind if kind else ""}">{_e(text)}</span>'


def stepper(steps, active):
    """Numbered step pills; `active` is the 1-based current step."""
    st.html('<div class="pf-steps">' + "".join(
        f'<div class="pf-step{" pf-on" if i <= active else ""}"><b>{i}</b>{_e(s)}</div>'
        for i, s in enumerate(steps, 1)) + "</div>")


def kpi(label, value, delta=None, help=None, icon=None, neutral=False):
    """Bordered metric card. Same element type/order as st.metric, so tests
    that read `at.metric` are unaffected. `neutral` renders the delta as plain
    grey text (a note, not a change)."""
    extra = dict(delta_color="off", delta_arrow="off") if neutral else {}
    st.metric(label, value, delta, help=help, border=True, icon=icon, **extra)


def style_fig(fig, height=360, legend=True, title=None):
    """Shared Plotly look: transparent background, light grid, compact ticks,
    bottom legend, unified hover. Series colours come from the Streamlit theme."""
    fig.update_layout(
        height=height, margin=dict(t=44 if title else 12, b=12, l=8, r=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="sans-serif", size=13), hovermode="x unified",
        colorway=palette(), showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    title_text=""),
        hoverlabel=dict(font_size=13))
    if title:
        fig.update_layout(title=dict(text=title))
    fig.update_xaxes(showgrid=False, zeroline=False, title_text=None)
    fig.update_yaxes(gridcolor="rgba(128,128,128,.18)", zeroline=False,
                     tickformat="~s", title_text=None)
    return fig


def chart(fig, height=340, legend=True, title=None):
    """Style a figure and render it full-width without the Plotly toolbar."""
    st.plotly_chart(style_fig(fig, height, legend, title), width="stretch",
                    config={"displayModeBar": False})
