"""Streamlit UI for the Fact Knowledge Layer (PRD.md section 6).

Talks to the FastAPI backend over real HTTP (see api_client.py) -- this module
never imports backend pipeline code directly, so API and UI stay independently
testable. Run the backend first (uvicorn backend.main:app --port 8000), then:
    streamlit run frontend/app.py
"""

import requests
import streamlit as st

import api_client
from styles import (
    RELATION_COLORS,
    RELATION_LABELS,
    confidence_badge,
    inject_base_styles,
    relation_badge,
)

st.set_page_config(page_title="Fact Knowledge Layer", layout="wide")
st.markdown(inject_base_styles(), unsafe_allow_html=True)

MAX_ROWS_SHOWN = 150


def _masthead() -> None:
    st.sidebar.markdown(
        '<div class="masthead">Fact Knowledge Layer</div>'
        '<div class="masthead-sub">grounded facts &middot; cross-document relationships</div>'
        '<div style="height:18px"></div>',
        unsafe_allow_html=True,
    )


def _short_doc_label(document_id: str) -> str:
    # Document ids are already descriptive slugs (e.g. "03-imf-india-2025-article-iv-excerpt");
    # trim the numeric prefix only, keep the rest as-is -- no per-document special-casing.
    parts = document_id.split("-", 1)
    return parts[1] if len(parts) == 2 and parts[0].isdigit() else document_id


def _facts_view() -> None:
    try:
        all_facts = api_client.get_facts()["facts"]
    except requests.exceptions.RequestException as e:
        st.error(f"Could not reach the API at {api_client.API_BASE_URL}: {e}")
        return

    document_ids = sorted({f["document_id"] for f in all_facts})

    col1, col2, col3 = st.columns([3, 2, 2])
    with col1:
        search = st.text_input("Search entity or metric", placeholder="e.g. GDP, revenue, inflation")
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

    unverified_count = sum(1 for f in facts if f.get("skip_reason"))
    st.caption(
        f"{len(facts)} fact(s) matching &middot; {unverified_count} flagged unverified "
        f"(low confidence or partial grounding)".replace("&middot;", "·")
    )

    shown = facts[:MAX_ROWS_SHOWN]
    if len(facts) > MAX_ROWS_SHOWN:
        st.info(f"Showing the first {MAX_ROWS_SHOWN} of {len(facts)} matches -- narrow your search to see more.")

    st.session_state.setdefault("expanded_facts", set())

    header = st.columns([2.2, 2.6, 1.3, 1.3, 0.7, 1.6, 1])
    for col, label in zip(header, ["Entity", "Metric", "Value", "Period", "Page", "Confidence", ""]):
        col.markdown(f"**{label}**")

    for fact in shown:
        cols = st.columns([2.2, 2.6, 1.3, 1.3, 0.7, 1.6, 1])
        cols[0].write(fact.get("entity") or "—")
        cols[1].write(fact.get("metric") or "—")
        value_str = f"{fact.get('value')} {fact.get('unit') or ''}".strip()
        cols[2].markdown(f"<span style='font-family:\"IBM Plex Mono\",monospace'>{value_str}</span>", unsafe_allow_html=True)
        cols[3].write(fact.get("time_period") or "—")
        cols[4].markdown(f"<span style='font-family:\"IBM Plex Mono\",monospace'>{fact['page_number']}</span>", unsafe_allow_html=True)
        cols[5].markdown(confidence_badge(fact.get("confidence", 0), fact.get("skip_reason")), unsafe_allow_html=True)
        if cols[6].button("Evidence", key=f"toggle_{fact['fact_id']}"):
            expanded = st.session_state["expanded_facts"]
            if fact["fact_id"] in expanded:
                expanded.discard(fact["fact_id"])
            else:
                expanded.add(fact["fact_id"])

        if fact["fact_id"] in st.session_state["expanded_facts"]:
            with st.container():
                st.markdown(
                    f'<div class="evidence-meta">{_short_doc_label(fact["document_id"])} '
                    f'&middot; page {fact["page_number"]}</div>'.replace("&middot;", "·"),
                    unsafe_allow_html=True,
                )
                st.markdown(f'<div class="quote-block">“{fact["source_quote"]}”</div>', unsafe_allow_html=True)
                if fact.get("skip_reason"):
                    st.markdown(
                        f'<div class="skip-reason-box">&#9888; Not fully grounded: {fact["skip_reason"]}</div>',
                        unsafe_allow_html=True,
                    )
                    candidates = (fact.get("attributes") or {}).get("candidate_values")
                    if candidates:
                        st.caption(f"Raw candidate values seen on the page: {', '.join(candidates)}")
                elif fact.get("attributes"):
                    st.caption(f"Additional attributes: {fact['attributes']}")
        st.markdown('<div class="fact-row"></div>', unsafe_allow_html=True)


def _relationship_card(rel: dict) -> None:
    fa, fb = rel["fact_a"], rel["fact_b"]
    st.markdown(relation_badge(rel["relation"]), unsafe_allow_html=True)
    left, right = st.columns(2)
    for col, fact in [(left, fa), (right, fb)]:
        with col:
            st.markdown(
                f'<div class="evidence-meta">{_short_doc_label(fact["document_id"])} '
                f'&middot; page {fact["page_number"]}</div>'.replace("&middot;", "·"),
                unsafe_allow_html=True,
            )
            st.markdown(f'<div class="quote-block">“{fact["source_quote"]}”</div>', unsafe_allow_html=True)
            st.caption(f"{fact.get('entity')} · {fact.get('metric')} = {fact.get('value')} {fact.get('unit') or ''}")
    st.markdown(f"**Reasoning:** {rel['explanation']}")
    st.markdown("<div class='fact-row'></div>", unsafe_allow_html=True)


def _relationships_view() -> None:
    options = ["All"] + list(RELATION_LABELS.keys())
    choice = st.radio(
        "Filter by relationship",
        options,
        format_func=lambda r: "All" if r == "All" else RELATION_LABELS[r],
        horizontal=True,
    )
    relation_param = None if choice == "All" else choice

    try:
        relationships = api_client.get_relationships(relation=relation_param)["relationships"]
    except requests.exceptions.RequestException as e:
        st.error(f"Could not reach the API at {api_client.API_BASE_URL}: {e}")
        return

    st.caption(f"{len(relationships)} relationship(s) found")
    for rel in relationships[:MAX_ROWS_SHOWN]:
        _relationship_card(rel)


def _upload_view() -> None:
    st.write(
        "Upload a new PDF to add it to the knowledge layer. The 5 starter documents "
        "are already ingested and judged -- this adds a document on top of them."
    )
    uploaded = st.file_uploader("Choose a PDF", type=["pdf"])
    if uploaded is None:
        return
    if st.button("Ingest this document"):
        with st.status("Ingesting document...", expanded=True) as status:
            st.write("Sending to the API -- extraction, grounding, embedding, and relationship judgment can take a few minutes on a large PDF.")
            try:
                result = api_client.post_ingest(uploaded.name, uploaded.getvalue())
            except requests.exceptions.RequestException as e:
                status.update(label="Ingest failed", state="error")
                st.error(f"Request to the API failed: {e}")
                return

            if result["status"] == "skipped":
                status.update(label="Already ingested", state="complete")
                st.warning(
                    f"'{result['document_id']}' is already in the store "
                    f"({result['existing_fact_count']} facts) -- incremental ingestion "
                    f"never reprocesses an existing document."
                )
            else:
                status.update(label="Ingest complete", state="complete")
                st.success(f"Ingested '{result['document_id']}' — {result['fact_count']} facts extracted.")
                st.write(f"Model used: `{result['model']}`")
                st.write(f"Candidate pairs found against the existing store: {result['candidate_pairs_found']}")
                st.write(f"Relationships judged: {result['relationships_judged']}")
                if result["relationship_breakdown"]:
                    st.write("Breakdown:", result["relationship_breakdown"])
                st.info("Switch to Facts or Relationships in the sidebar to see the new data.")


def main() -> None:
    _masthead()
    if not api_client.api_is_reachable():
        st.error(
            f"Cannot reach the API at {api_client.API_BASE_URL}. "
            f"Start it first: `uvicorn backend.main:app --port 8000`"
        )
        return

    page = st.sidebar.radio("View", ["Facts", "Relationships", "Upload"], label_visibility="collapsed")

    if page == "Facts":
        st.header("Facts")
        _facts_view()
    elif page == "Relationships":
        st.header("Relationships")
        _relationships_view()
    else:
        st.header("Upload a document")
        _upload_view()


if __name__ == "__main__":
    main()
