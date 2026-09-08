"""Design tokens and CSS injection for the Streamlit UI.

Concept: "instrument, not brochure". The app is a reading surface for other
people's documents, so the interface itself stays achromatic -- paper, ink and
hairlines -- and every hue in the product is reserved for meaning. There are
exactly three: agreement, conflict, and context. Buttons, links and focus rings
are ink, never a brand color, so anything colored on screen is always a claim
about the evidence and never decoration.

Two typefaces, one job each. Instrument Sans is the application's own voice.
Newsreader is used only for text quoted out of a source PDF, so a quotation is
visibly a different kind of object from anything the app wrote. Numerals use the
sans's tabular figures rather than a third monospace family -- the goal was
column alignment, and tabular-nums delivers that without the extra face.

Token values here are mirrored in frontend/.streamlit/config.toml, which drives
the widget internals (slider thumb, radio dot, focus rings) that CSS cannot
reach. Change both together.
"""

PAPER = "#F1F2F0"
SURFACE = "#FBFBFA"
INK = "#1D2321"
INK_SOFT = "#6E7672"
RULE = "#E1E3DF"

# The whole product palette. Nothing else in the UI is allowed a hue.
RELATION_COLORS = {
    "corroborates": "#2E6A4F",
    "contradicts": "#96303A",
    "reconcilable_context": "#8C6516",
    "unrelated": INK_SOFT,
}
RELATION_LABELS = {
    "corroborates": "Corroborates",
    "contradicts": "Contradicts",
    "reconcilable_context": "Reconciled via context",
    "unrelated": "Unrelated",
}

# Ungrounded facts borrow the "context" hue rather than taking a fourth color:
# both mean "true as far as it goes, but read the caveat before you use it".
FLAG = RELATION_COLORS["reconcilable_context"]
FLAG_TINT = "#F6EFDD"


def confidence_badge(confidence: float, skip_reason: str | None) -> str:
    """A permanent, visible marker -- never hidden, never styled to look like a
    fully-grounded fact. A confirmed fact shows a bare tabular number, because a
    column of identical pills is noise; only the unverified state earns a badge."""
    if skip_reason:
        return f'<span class="fkl-flag">{confidence:.2f} unverified</span>'
    return f'<span class="fkl-num fkl-dim">{confidence:.2f}</span>'


def relation_badge(relation: str) -> str:
    """A colored rule plus colored text, not a filled pill. At the density these
    appear, filled pills read as buttons and compete with the actual controls."""
    color = RELATION_COLORS.get(relation, INK_SOFT)
    label = RELATION_LABELS.get(relation, relation)
    return (
        f'<span class="fkl-relation" style="color:{color}">'
        f'<span class="fkl-relation-mark" style="background:{color}"></span>{label}</span>'
    )


def loading_bar(label: str) -> str:
    """The app's only loading indicator: one indeterminate hairline, used for
    every wait, so the reader learns a single signal instead of three."""
    return (
        '<div class="fkl-loading" role="status" aria-live="polite">'
        '<div class="fkl-loading-track"><div class="fkl-loading-head"></div></div>'
        f'<div class="fkl-loading-label">{label}</div></div>'
    )


def evidence_meta(document_label: str, page_number: int, side: str | None = None) -> str:
    """Provenance line. Weight and spacing separate the parts, so no separator
    glyph is needed between them. `side` carries the "Fact A" / "Fact B" tag on
    a relationship card, because the model's explanation refers to the two facts
    by those names and the reader has to be able to tell which is which."""
    tag = f'<span class="fkl-side">{side}</span>' if side else ""
    return (
        f'<div class="fkl-meta">{tag}<span class="fkl-meta-doc">{document_label}</span>'
        f'<span class="fkl-meta-page">page <span class="fkl-num">{page_number}</span></span></div>'
    )


def quote(text: str) -> str:
    return f'<blockquote class="fkl-quote">{text}</blockquote>'


def inject_base_styles() -> str:
    return f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=Newsreader:opsz,wght@6..72,300;6..72,400&display=swap');

    :root {{
        --paper: {PAPER};
        --surface: {SURFACE};
        --ink: {INK};
        --ink-soft: {INK_SOFT};
        --rule: {RULE};
        --flag: {FLAG};
        --flag-tint: {FLAG_TINT};
    }}

    /* Streamlit sets its own font on the markdown containers with a specificity
       that beats a bare body rule, so the containers are named explicitly. */
    html, body, [class*="css"], .stApp, button, input, select, textarea,
    [data-testid="stMarkdownContainer"],
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stHeading"],
    .fkl-cell, .fkl-th, .fkl-meta, .fkl-lede, .fkl-count,
    .fkl-verdict, .fkl-aside, .fkl-note, .fkl-flag, .fkl-relation {{
        font-family: 'Instrument Sans', ui-sans-serif, system-ui, sans-serif;
    }}

    .stApp {{
        background: var(--paper);
        color: var(--ink);
    }}

    /* Streamlit paints several text elements from its own theme; pin them to
       ink so nothing drifts to a default grey-blue. Button labels are excluded
       -- they live inside a stMarkdownContainer too, but need to follow the
       button's own background (surface text on an ink-filled primary button),
       not this blanket rule. */
    [data-testid="stMain"] [data-testid="stMarkdownContainer"]:not(button *),
    [data-testid="stMain"] [data-testid="stMarkdownContainer"] p:not(button *),
    [data-testid="stMain"] [data-testid="stMarkdownContainer"] li,
    [data-testid="stMain"] [data-testid="stHeading"],
    [data-testid="stMain"] [data-testid="stWidgetLabel"] p {{
        color: var(--ink);
    }}
    [data-testid^="stBaseButton"] [data-testid="stMarkdownContainer"] p {{
        color: inherit;
    }}

    [data-testid="stHeader"] {{ background: transparent; }}

    /* ---- page frame ---------------------------------------------------- */
    [data-testid="stMain"] .block-container {{
        max-width: 1320px;
        padding: 3.5rem 3.5rem 6rem;
    }}
    /* Streamlit's 1rem inter-element gap pushes table rows out of rhythm. Rows
       carry their own padding instead, so the frame gap goes small and the
       spacing you see is the spacing this file sets. */
    [data-testid="stMain"] [data-testid="stVerticalBlock"] {{ gap: 0.4rem; }}

    h1, h2, h3, [data-testid="stHeading"] {{
        font-family: 'Instrument Sans', sans-serif;
        letter-spacing: -0.022em;
    }}
    [data-testid="stMain"] h1 {{
        font-size: 2.1rem;
        font-weight: 600;
        padding: 0;
        margin: 0 0 0.2rem;
    }}
    .fkl-lede {{
        color: var(--ink-soft);
        font-size: 0.95rem;
        line-height: 1.5;
        max-width: 64ch;
        margin: 0 0 2.5rem;
    }}
    .fkl-section-gap {{ height: 2rem; }}

    /* ---- sidebar -------------------------------------------------------- */
    [data-testid="stSidebar"] {{
        background: var(--surface);
        border-right: 1px solid var(--rule);
    }}
    [data-testid="stSidebar"] > div {{ padding-top: 2.25rem; }}
    [data-testid="stSidebar"] * {{ color: var(--ink); }}

    .fkl-mark {{
        font-size: 1.05rem;
        font-weight: 600;
        letter-spacing: -0.02em;
        line-height: 1.25;
    }}
    .fkl-mark-sub {{
        font-size: 0.8rem;
        line-height: 1.45;
        color: var(--ink-soft);
        margin-top: 0.35rem;
        padding-bottom: 1.5rem;
        border-bottom: 1px solid var(--rule);
        margin-bottom: 1.25rem;
    }}

    /* Nav: a quiet list. The selected item is marked by weight and a rule, not
       a filled block. */
    [data-testid="stSidebar"] [role="radiogroup"] {{ gap: 0.15rem; }}
    [data-testid="stSidebar"] [data-testid="stRadioOption"] {{
        padding: 0.4rem 0 0.4rem 0.75rem;
        border-left: 2px solid transparent;
        transition: border-color .12s ease;
    }}
    [data-testid="stSidebar"] [data-testid="stRadioOption"]:hover {{
        border-left-color: var(--rule);
    }}
    [data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] {{
        border-left-color: var(--ink);
    }}
    [data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] p {{
        font-weight: 600;
    }}
    [data-testid="stSidebar"] [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] p {{
        font-size: 0.92rem;
    }}
    /* The radio dot is redundant once the rule marks the selection. It is the
       unlabelled div that precedes the option's own markdown container. */
    [data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div > div:first-child {{
        display: none;
    }}

    /* ---- controls ------------------------------------------------------- */
    [data-testid="stWidgetLabel"] p {{
        font-size: 0.82rem;
        font-weight: 500;
        color: var(--ink-soft);
    }}
    [data-testid="stTextInput"] input,
    [data-baseweb="select"] > div {{
        background: var(--surface);
        border: 1px solid var(--rule);
        border-radius: 4px;
        color: var(--ink);
        font-size: 0.92rem;
    }}
    [data-testid="stTextInput"] input:focus,
    [data-baseweb="select"] > div:focus-within {{
        border-color: var(--ink);
        box-shadow: none;
    }}

    /* Buttons are addressed by testid, not by `div.stButton > button`: when a
       button carries a tooltip Streamlit wraps it in a hover target, so it is
       not a direct child of .stButton. */
    [data-testid^="stBaseButton"] {{
        border-radius: 4px;
        font-size: 0.85rem;
        font-weight: 500;
        white-space: nowrap;
        transition: background-color .12s ease, border-color .12s ease, color .12s ease;
    }}
    /* Row-level action: a ghost control. In a 150-row table a filled button per
       row would out-shout the data it belongs to. */
    [data-testid="stBaseButton-secondary"] {{
        background-color: transparent;
        border: 1px solid var(--rule);
        color: var(--ink-soft);
        padding: 0.3rem 0.9rem;
        min-height: 0;
    }}
    [data-testid="stBaseButton-secondary"]:hover,
    [data-testid="stBaseButton-secondary"]:active,
    [data-testid="stBaseButton-secondary"]:focus {{
        background-color: transparent;
        border-color: var(--ink);
        color: var(--ink);
    }}
    [data-testid="stBaseButton-primary"] {{
        background-color: var(--ink);
        border: 1px solid var(--ink);
        color: var(--surface);
    }}
    [data-testid="stBaseButton-primary"]:hover,
    [data-testid="stBaseButton-primary"]:active,
    [data-testid="stBaseButton-primary"]:focus {{
        background-color: #2C3532;
        border-color: #2C3532;
        color: var(--surface);
    }}
    [data-testid^="stBaseButton"]:focus-visible {{
        outline: 2px solid var(--ink);
        outline-offset: 2px;
        box-shadow: none;
    }}

    /* The slider and the horizontal relationship filter both default to
       Streamlit's accent color; pull them back to ink so no control invents a
       hue that means nothing. */
    [data-testid="stSlider"] [role="slider"] {{ background-color: var(--ink); }}
    [data-testid="stSlider"] [data-testid="stSliderTickBarMin"],
    [data-testid="stSlider"] [data-testid="stSliderTickBarMax"] {{ display: none; }}
    [data-testid="stSliderThumbValue"] {{
        color: var(--ink-soft);
        font-variant-numeric: tabular-nums;
    }}
    /* The inline filter keeps its radio marks, but only the chosen one is
       filled -- Streamlit fills the inner dot on every option by default. */
    [data-testid="stMain"] [data-testid="stRadioOption"] > div > div > div:first-child > div {{
        background-color: transparent;
    }}
    [data-testid="stMain"] [data-testid="stRadioOption"][data-selected="true"]
        > div > div > div:first-child > div {{
        background-color: var(--ink);
    }}
    [data-testid="stMain"] [data-testid="stRadioOption"][data-selected="true"] p {{
        font-weight: 600;
    }}
    [data-testid="stMain"] [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] p {{
        font-size: 0.88rem;
    }}

    /* A drop target the width of the page reads as a banner, not a target. */
    [data-testid="stFileUploader"] {{ max-width: 620px; }}
    [data-testid="stFileUploaderDropzone"] {{
        background: var(--surface);
        border: 1px dashed var(--rule);
        border-radius: 6px;
    }}
    [data-testid="stFileUploaderDropzoneInstructions"] {{ color: var(--ink-soft); }}

    /* ---- table ---------------------------------------------------------- */
    .fkl-th {{
        font-size: 0.74rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        color: var(--ink-soft);
        padding: 0 0 0.65rem;
    }}
    .fkl-cell {{
        font-size: 0.92rem;
        line-height: 1.4;
        padding: 0.95rem 0;
        color: var(--ink);
    }}
    .fkl-num {{ font-variant-numeric: tabular-nums; letter-spacing: 0.01em; }}
    .fkl-dim {{ color: var(--ink-soft); }}
    /* Buttons sit in their own column block; this drops them onto the same
       baseline as the text cells beside them. */
    div.stButton {{ padding-top: 0.6rem; }}
    .fkl-rule {{ border-top: 1px solid var(--rule); }}
    .fkl-count {{
        font-size: 0.85rem;
        color: var(--ink-soft);
        padding: 1.75rem 0 1.25rem;
    }}

    .fkl-flag {{
        display: inline-block;
        font-size: 0.76rem;
        font-variant-numeric: tabular-nums;
        color: var(--flag);
        background: var(--flag-tint);
        border-radius: 3px;
        padding: 0.15rem 0.5rem;
        white-space: nowrap;
    }}

    /* ---- evidence -------------------------------------------------------- */
    .fkl-evidence {{
        border-left: 2px solid var(--rule);
        padding: 0.25rem 0 0.5rem 1.5rem;
        margin: 0.25rem 0 1rem;
        animation: fkl-reveal .16s ease-out both;
    }}
    .fkl-meta {{
        display: flex;
        align-items: baseline;
        gap: 1.25rem;
        font-size: 0.78rem;
        color: var(--ink-soft);
        margin-bottom: 0.5rem;
    }}
    .fkl-meta-doc {{ font-weight: 600; color: var(--ink); }}
    .fkl-meta-page {{ font-variant-numeric: tabular-nums; }}
    .fkl-side {{
        font-weight: 600;
        color: var(--ink-soft);
        border: 1px solid var(--rule);
        border-radius: 3px;
        padding: 0.05rem 0.4rem;
    }}

    /* inline-block so a short quotation is a short block. A quote stretched to
       the full column reads as an empty field rather than as a sentence. */
    .fkl-quote {{
        display: inline-block;
        font-family: 'Newsreader', Georgia, serif;
        font-size: 1.05rem;
        font-weight: 400;
        line-height: 1.6;
        color: var(--ink);
        background: var(--surface);
        border: 0;
        border-radius: 4px;
        padding: 0.85rem 1.1rem;
        margin: 0;
        max-width: 68ch;
    }}
    .fkl-quote::before {{ content: '\\201C'; }}
    .fkl-quote::after {{ content: '\\201D'; }}

    .fkl-note {{
        font-size: 0.85rem;
        line-height: 1.5;
        color: var(--flag);
        background: var(--flag-tint);
        border-radius: 4px;
        padding: 0.6rem 0.85rem;
        margin-top: 0.65rem;
        max-width: 68ch;
    }}
    .fkl-aside {{
        font-size: 0.82rem;
        line-height: 1.5;
        color: var(--ink-soft);
        margin-top: 0.55rem;
        max-width: 68ch;
    }}
    .fkl-aside-entity {{ color: var(--ink); font-weight: 500; }}
    .fkl-aside-value {{ color: var(--ink); }}

    /* ---- relationships ---------------------------------------------------- */
    .fkl-relation {{
        display: inline-flex;
        align-items: center;
        gap: 0.55rem;
        font-size: 0.82rem;
        font-weight: 600;
        letter-spacing: 0.01em;
    }}
    .fkl-relation-mark {{
        width: 14px;
        height: 2px;
        border-radius: 1px;
    }}
    .fkl-verdict {{
        font-size: 1rem;
        line-height: 1.6;
        color: var(--ink);
        max-width: 78ch;
        margin: 0.6rem 0 1.4rem;
    }}
    .fkl-card-gap {{ height: 1.75rem; }}

    /* ---- loading ---------------------------------------------------------- */
    .fkl-loading {{ padding: 0.5rem 0 0; }}
    .fkl-loading-track {{
        position: relative;
        height: 2px;
        background: var(--rule);
        overflow: hidden;
        border-radius: 1px;
    }}
    .fkl-loading-head {{
        position: absolute;
        top: 0;
        left: 0;
        height: 100%;
        width: 30%;
        background: var(--ink);
        border-radius: 1px;
        animation: fkl-sweep 1.15s cubic-bezier(.65,.05,.36,1) infinite;
    }}
    .fkl-loading-label {{
        font-size: 0.82rem;
        color: var(--ink-soft);
        margin-top: 0.55rem;
    }}
    @keyframes fkl-sweep {{
        0%   {{ transform: translateX(-105%); }}
        100% {{ transform: translateX(440%); }}
    }}
    @keyframes fkl-reveal {{
        from {{ opacity: 0; transform: translateY(-4px); }}
        to   {{ opacity: 1; transform: none; }}
    }}

    /* Streamlit's own spinner, used inside st.status, restyled into the same
       hairline language rather than a second competing indicator. */
    [data-testid="stSpinner"] i {{ border-top-color: var(--ink) !important; }}

    @media (prefers-reduced-motion: reduce) {{
        .fkl-loading-head {{ animation: none; width: 100%; opacity: .35; }}
        .fkl-evidence {{ animation: none; }}
        * {{ transition: none !important; }}
    }}

    /* ---- alerts ------------------------------------------------------------ */
    [data-testid="stAlert"] {{
        border-radius: 4px;
        font-size: 0.88rem;
    }}
    </style>
    """
