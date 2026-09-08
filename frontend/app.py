"""Streamlit UI for the Fact Knowledge Layer (PRD.md section 6).

Talks to the FastAPI backend over real HTTP (see api_client.py) -- this module
never imports backend pipeline code directly, so API and UI stay independently
testable. Run the backend first (uvicorn backend.main:app --port 8000), then:
    streamlit run frontend/app.py
"""

import html

import requests
import streamlit as st

import api_client
from styles import (
    RELATION_LABELS,
    confidence_badge,
    evidence_meta,
    inject_base_styles,
    loading_bar,
    quote,
    relation_badge,
)

st.set_page_config(page_title="Fact Knowledge Layer", layout="wide")
st.markdown(inject_base_styles(), unsafe_allow_html=True)

MAX_ROWS_SHOWN = 150
FACT_COLUMNS = [2.3, 2.5, 1.4, 1.0, 0.7, 1.3, 1.1]
EM_DASH = "—"


def _write(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def _cell(text: str, *, numeric: bool = False, dim: bool = False) -> str:
    classes = "fkl-cell"
    if numeric:
        classes += " fkl-num"
    if dim:
        classes += " fkl-dim"
    return f'<div class="{classes}">{html.escape(str(text))}</div>'


def _masthead() -> None:
    st.sidebar.markdown(
        '<div class="fkl-mark">Fact Knowledge Layer</div>'
        '<div class="fkl-mark-sub">Grounded facts and the relationships between them, '
        'read straight out of the source PDFs.</div>',
        unsafe_allow_html=True,
    )


def _short_doc_label(document_id: str) -> str:
    # Document ids are already descriptive slugs (e.g. "03-imf-india-2025-article-iv-excerpt");
    # trim the numeric prefix only, keep the rest as-is -- no per-document special-casing.
    parts = document_id.split("-", 1)
    return parts[1] if len(parts) == 2 and parts[0].isdigit() else document_id


def _fetch(loader_label: str, call):
    """Every network wait in the app shows the same hairline indicator, then
    clears it. Returns None and renders the failure in place if the API is down."""
    slot = st.empty()
    slot.markdown(loading_bar(loader_label), unsafe_allow_html=True)
    try:
        result = call()
    except requests.exceptions.RequestException as e:
        slot.empty()
        st.error(
            f"Cannot reach the API at {api_client.API_BASE_URL}. "
            f"Start it with `uvicorn backend.main:app --port 8000`, then reload. ({e})"
        )
        return None
    slot.empty()
    return result


def _evidence_panel(fact: dict) -> None:
    """Provenance, then the quote it came from, then any caveat. Rendered as one
    markup block so the reveal animates as a single object."""
    parts = [
        '<div class="fkl-evidence">',
        evidence_meta(html.escape(_short_doc_label(fact["document_id"])), fact["page_number"]),
        quote(html.escape(fact["source_quote"])),
    ]
    if fact.get("skip_reason"):
        parts.append(
            f'<div class="fkl-note">Not fully grounded: {html.escape(fact["skip_reason"])}</div>'
        )
        candidates = (fact.get("attributes") or {}).get("candidate_values")
        if candidates:
            joined = ", ".join(html.escape(str(c)) for c in candidates)
            parts.append(f'<div class="fkl-aside">Values seen on this page: {joined}</div>')
    elif fact.get("attributes"):
        parts.append(
            f'<div class="fkl-aside">Attributes: {html.escape(str(fact["attributes"]))}</div>'
        )
    parts.append("</div>")
    _write("".join(parts))


def _facts_view() -> None:
    _write(
        '<div class="fkl-lede">Every row was read off a page of a source document and '
        'keeps the quote it came from. Open the evidence to see it.</div>'
    )

    payload = _fetch("Loading facts", api_client.get_facts)
    if payload is None:
        return
    all_facts = payload["facts"]

    document_ids = sorted({f["document_id"] for f in all_facts})

    col1, col2, col3 = st.columns([3, 2, 2], gap="large")
    with col1:
        search = st.text_input("Search entity or metric", placeholder="GDP, revenue, inflation")
    with col2:
        doc_filter = st.selectbox("Document", ["All documents"] + document_ids)
    with col3:
        min_confidence = st.slider("Minimum confidence", 0.0, 1.0, 0.0, 0.05)

    facts = all_facts
    if doc_filter != "All documents":
        facts = [f for f in facts if f["document_id"] == doc_filter]
    if search:
        needle = search.lower()
        facts = [
            f
            for f in facts
            if needle in (f.get("entity") or "").lower() or needle in (f.get("metric") or "").lower()
        ]
    facts = [f for f in facts if f.get("confidence", 0) >= min_confidence]

    if not facts:
        _write(
            '<div class="fkl-count">No facts match these filters. '
            'Clear the search or lower the confidence floor.</div>'
        )
        return

    shown = facts[:MAX_ROWS_SHOWN]
    unverified_count = sum(1 for f in facts if f.get("skip_reason"))
    noun = "fact" if len(facts) == 1 else "facts"
    summary = f"{len(facts)} {noun}, {unverified_count} flagged unverified"
    if len(facts) > MAX_ROWS_SHOWN:
        summary += f". Showing the first {MAX_ROWS_SHOWN} — narrow the search to see the rest"
    _write(f'<div class="fkl-count">{summary}</div>')

    st.session_state.setdefault("expanded_facts", set())

    header = st.columns(FACT_COLUMNS, gap="medium")
    labels = ["Entity", "Metric", "Value", "Period", "Page", "Confidence", ""]
    for col, label in zip(header, labels):
        col.markdown(f'<div class="fkl-th">{label}</div>', unsafe_allow_html=True)
    _write('<div class="fkl-rule"></div>')

    for fact in shown:
        is_open = fact["fact_id"] in st.session_state["expanded_facts"]
        cols = st.columns(FACT_COLUMNS, gap="medium")
        cols[0].markdown(_cell(fact.get("entity") or EM_DASH), unsafe_allow_html=True)
        cols[1].markdown(_cell(fact.get("metric") or EM_DASH), unsafe_allow_html=True)
        value_str = f"{fact.get('value')} {fact.get('unit') or ''}".strip()
        cols[2].markdown(_cell(value_str, numeric=True), unsafe_allow_html=True)
        cols[3].markdown(_cell(fact.get("time_period") or EM_DASH, dim=True), unsafe_allow_html=True)
        cols[4].markdown(_cell(fact["page_number"], numeric=True), unsafe_allow_html=True)
        cols[5].markdown(
            f'<div class="fkl-cell">'
            f'{confidence_badge(fact.get("confidence", 0), fact.get("skip_reason"))}</div>',
            unsafe_allow_html=True,
        )
        if cols[6].button(
            "Hide" if is_open else "Evidence",
            key=f"toggle_{fact['fact_id']}",
            help="Show the page and quote this fact was read from",
        ):
            expanded = st.session_state["expanded_facts"]
            if is_open:
                expanded.discard(fact["fact_id"])
            else:
                expanded.add(fact["fact_id"])
            st.rerun()

        if is_open:
            _evidence_panel(fact)
        _write('<div class="fkl-rule"></div>')


def _fact_line(fact: dict) -> str:
    """The extracted fact in one line, under the quote it was read from. The
    entity is set in ink and the reading in tabular figures so the eye can
    compare the two sides of a pair without re-reading the whole line."""
    value = f"{fact.get('value')} {fact.get('unit') or ''}".strip()
    return (
        f'<div class="fkl-aside">'
        f'<span class="fkl-aside-entity">{html.escape(str(fact.get("entity") or EM_DASH))}</span>, '
        f'{html.escape(str(fact.get("metric") or EM_DASH))} '
        f'<span class="fkl-num fkl-aside-value">{html.escape(value)}</span></div>'
    )


def _relationship_card(rel: dict) -> None:
    """Verdict first, then the reasoning, then the two quotes it rests on. The
    judgment is what the reader came for; the evidence is what backs it up."""
    _write(relation_badge(rel["relation"]))
    _write(f'<div class="fkl-verdict">{html.escape(rel["explanation"])}</div>')

    left, right = st.columns(2, gap="large")
    for col, side, fact in [(left, "Fact A", rel["fact_a"]), (right, "Fact B", rel["fact_b"])]:
        with col:
            _write(
                evidence_meta(
                    html.escape(_short_doc_label(fact["document_id"])),
                    fact["page_number"],
                    side=side,
                )
                + quote(html.escape(fact["source_quote"]))
                + _fact_line(fact)
            )
    _write('<div class="fkl-card-gap"></div><div class="fkl-rule"></div>'
           '<div class="fkl-card-gap"></div>')


def _relationships_view() -> None:
    _write(
        '<div class="fkl-lede">Facts from different documents, compared in pairs. Each '
        'verdict below was judged against both quotes, which are shown underneath it.</div>'
    )

    options = ["All"] + list(RELATION_LABELS.keys())
    choice = st.radio(
        "Filter by relationship",
        options,
        format_func=lambda r: "All" if r == "All" else RELATION_LABELS[r],
        horizontal=True,
    )
    relation_param = None if choice == "All" else choice

    payload = _fetch(
        "Loading relationships", lambda: api_client.get_relationships(relation=relation_param)
    )
    if payload is None:
        return
    relationships = payload["relationships"]

    if not relationships:
        _write('<div class="fkl-count">No relationships of this kind in the store yet.</div>')
        return

    count = len(relationships)
    _write(f'<div class="fkl-count">{count} judged pair{"" if count == 1 else "s"}</div>')
    for rel in relationships[:MAX_ROWS_SHOWN]:
        _relationship_card(rel)


def _ingest_result(result: dict) -> None:
    if result["status"] == "skipped":
        st.warning(
            f"'{result['document_id']}' is already in the store with "
            f"{result['existing_fact_count']} facts. Ingestion is incremental and never "
            f"reprocesses a document it has already read."
        )
        return

    st.success(f"Added '{result['document_id']}' with {result['fact_count']} facts.")
    _write(
        f'<div class="fkl-aside">Model: {html.escape(str(result["model"]))}<br>'
        f'Candidate pairs found against the existing store: '
        f'<span class="fkl-num">{result["candidate_pairs_found"]}</span><br>'
        f'Pairs judged: <span class="fkl-num">{result["relationships_judged"]}</span></div>'
    )
    if result["relationship_breakdown"]:
        st.write(result["relationship_breakdown"])
    st.info("Open Facts or Relationships in the sidebar to read the new data.")


def _upload_view() -> None:
    _write(
        '<div class="fkl-lede">Drop in a PDF the system has never seen. It is parsed page '
        'by page, grounded against its own quotes, then compared with everything already '
        'in the store.</div>'
    )
    uploaded = st.file_uploader("Choose a PDF", type=["pdf"])
    if uploaded is None:
        return

    if st.button("Ingest this document", type="primary"):
        slot = st.empty()
        slot.markdown(
            loading_bar(
                "Reading pages, extracting facts, embedding and comparing. "
                "A large PDF can take a few minutes."
            ),
            unsafe_allow_html=True,
        )
        try:
            result = api_client.post_ingest(uploaded.name, uploaded.getvalue())
        except requests.exceptions.RequestException as e:
            slot.empty()
            st.error(f"Ingest failed before it finished: {e}")
            return
        slot.empty()
        _ingest_result(result)


def main() -> None:
    _masthead()
    page = st.sidebar.radio("View", ["Facts", "Relationships", "Upload"], label_visibility="collapsed")

    if page == "Facts":
        st.title("Facts")
        _facts_view()
    elif page == "Relationships":
        st.title("Relationships")
        _relationships_view()
    else:
        st.title("Add a document")
        _upload_view()


if __name__ == "__main__":
    main()
