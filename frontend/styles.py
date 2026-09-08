"""Design tokens and CSS injection for the Streamlit UI.

Concept: "desk and document" -- a dark, warm-charcoal chrome (the desk) frames
light bond-paper content panels (the documents actually being reviewed). Color
carries meaning: the four relation types each get one fixed color used only for
that purpose, everywhere. Quoted primary-source text renders in a serif, as if
photocopied from the report; everything the app itself produced renders in a
grotesk sans; raw numerals (page numbers, confidence, values) render in a
tabular monospace for scanability -- not decoration, alignment.
"""

DESK = "#1E2228"
DESK_LINE = "#383E47"
PAPER = "#EAE8E1"
PAPER_LINE = "#D8D5C9"
INK = "#1B1E22"
INK_SOFT = "#5B6169"
SEAL = "#A8763A"
# Distinct from SEAL on purpose -- SEAL is reserved for the "reconciled via
# context" relation badge; reusing it for the button would make an interactive
# control look like a semantic status color.
ACCENT = "#7A4A9E"

RELATION_COLORS = {
    "corroborates": "#3F7856",
    "contradicts": "#A23B3B",
    "reconcilable_context": "#A8763A",
    "unrelated": "#8A8F97",
}
RELATION_LABELS = {
    "corroborates": "Corroborates",
    "contradicts": "Contradicts",
    "reconcilable_context": "Reconciled via context",
    "unrelated": "Unrelated",
}


def confidence_badge(confidence: float, skip_reason: str | None) -> str:
    """A permanent, visible marker -- never hidden, never styled to look like a
    fully-grounded fact. Half-filled dot + a warning tag when skip_reason is set."""
    if skip_reason:
        return (
            f'<span style="display:inline-flex;align-items:center;gap:5px;'
            f'font-family:\'IBM Plex Mono\',monospace;font-size:12px;'
            f'color:#8A5A1E;background:#F3E6D2;border:1px solid #D9B77C;'
            f'border-radius:3px;padding:1px 7px;">◐ {confidence:.2f} unverified</span>'
        )
    return (
        f'<span style="display:inline-flex;align-items:center;gap:5px;'
        f'font-family:\'IBM Plex Mono\',monospace;font-size:12px;'
        f'color:{INK_SOFT};background:transparent;border:1px solid {PAPER_LINE};'
        f'border-radius:3px;padding:1px 7px;">● {confidence:.2f}</span>'
    )


def relation_badge(relation: str) -> str:
    color = RELATION_COLORS.get(relation, INK_SOFT)
    label = RELATION_LABELS.get(relation, relation)
    return (
        f'<span style="display:inline-block;font-family:\'IBM Plex Sans\',sans-serif;'
        f'font-size:12px;font-weight:600;color:#fff;background:{color};'
        f'border-radius:3px;padding:3px 10px;">{label}</span>'
    )


def inject_base_styles() -> str:
    return f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400;8..60,500&display=swap');

    html, body, [class*="css"] {{
        font-family: 'IBM Plex Sans', sans-serif;
    }}

    .stApp {{
        background: {PAPER};
        color: {INK};
    }}
    [data-testid="stMain"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stMain"] [data-testid="stMarkdownContainer"] li,
    [data-testid="stMain"] [data-testid="stHeading"],
    [data-testid="stMain"] [data-testid="stWidgetLabel"] p,
    [data-testid="stMain"] [data-testid="stCaptionContainer"],
    [data-testid="stMain"] [data-testid="stTextInput"] input,
    [data-testid="stMain"] [data-testid="stSelectbox"] {{
        color: {INK} !important;
    }}

    [data-testid="stHeader"] {{
        background: {DESK};
    }}

    [data-testid="stSidebar"] {{
        background: {DESK};
        border-right: 1px solid {DESK_LINE};
    }}
    [data-testid="stSidebar"] * {{
        color: #D9DAE0 !important;
    }}
    [data-testid="stSidebar"] .stRadio label {{
        font-family: 'IBM Plex Sans', sans-serif;
    }}

    .masthead {{
        font-family: 'IBM Plex Sans', sans-serif;
        font-weight: 600;
        font-size: 22px;
        letter-spacing: 0.01em;
        color: #EDEBE3;
        margin: 0;
    }}
    .masthead-sub {{
        font-family: 'IBM Plex Mono', monospace;
        font-size: 12px;
        color: #9CA1AA;
        margin-top: 2px;
    }}

    .quote-block {{
        font-family: 'Source Serif 4', serif;
        font-size: 16px;
        line-height: 1.55;
        color: {INK};
        background: #F5F3EC;
        border-left: 3px solid {SEAL};
        padding: 10px 16px;
        margin: 8px 0;
    }}

    .evidence-meta {{
        font-family: 'IBM Plex Mono', monospace;
        font-size: 12px;
        color: {INK_SOFT};
    }}

    .fact-row {{
        border-bottom: 1px solid {PAPER_LINE};
        padding: 10px 2px;
    }}

    .skip-reason-box {{
        font-family: 'IBM Plex Sans', sans-serif;
        font-size: 13px;
        color: #8A5A1E;
        background: #F3E6D2;
        border: 1px solid #D9B77C;
        border-radius: 3px;
        padding: 8px 12px;
        margin-top: 6px;
    }}

    div.stButton > button {{
        background: {ACCENT};
        color: #fff;
        border: none;
        border-radius: 3px;
        font-family: 'IBM Plex Sans', sans-serif;
        font-weight: 500;
        white-space: nowrap;
    }}
    div.stButton > button:hover {{
        background: #5E3A7D;
        color: #fff;
    }}

    [data-testid="stExpander"] {{
        background: #F5F3EC;
        border: 1px solid {PAPER_LINE};
        border-radius: 3px;
    }}
    </style>
    """
