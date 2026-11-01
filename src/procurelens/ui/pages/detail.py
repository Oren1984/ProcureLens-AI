from __future__ import annotations

import pandas as pd
import streamlit as st

from procurelens.domain.lifecycle import Action, LifecycleError, allowed_actions
from procurelens.domain.models import ExceptionStatus
from procurelens.security.permissions import AuthorizationError, Permission
from procurelens.services.knowledge import KnowledgeError
from procurelens.ui import common

EVENT_LABEL = {
    "detected": "Detected",
    "facts_changed": "Source facts changed",
    "condition_cleared": "No longer detected",
    "condition_returned": "Detected again",
    "start_review": "Review started",
    "resolve": "Resolved",
    "reopen": "Reopened",
}


def _documents_table(docs) -> None:
    for d in docs:
        state = {0: "Draft", 1: "Submitted", 2: "Cancelled"}[d.docstatus]
        with st.expander(
            f"{d.doctype} {d.source_id} · {state} · {d.doc_date or '—'} · {d.currency}",
            expanded=False,
        ):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Line": ln.line_id,
                            "Item": ln.item_code,
                            "Qty": float(ln.qty),
                            **(
                                {
                                    "Received": float(ln.received_qty),
                                    "Rejected": float(ln.rejected_qty),
                                }
                                if d.doctype == "Purchase Receipt"
                                else {}
                            ),
                            "Rate": float(ln.rate),
                            "UOM": ln.uom,
                            "PO ref": f"{ln.po_id or '—'} / {ln.po_line_id or '—'}",
                        }
                        for ln in d.lines
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
            st.caption("Raw ERPNext snapshot (as synced)")
            st.json(d.raw_json, expanded=False)


def render() -> None:
    p = common.require_principal()
    c = common.container()
    exc_id = st.session_state.get("selected_exception")
    if not exc_id:
        common.page_header("Exception details")
        st.info("Select an exception on the **Exceptions** page to investigate it.")
        st.page_link(
            st.session_state["_pl_pages"]["exceptions"], label="Go to Exceptions", icon="📋"
        )
        common.footer()
        return
    try:
        exc = c.exceptions.get(p, int(exc_id))
    except (LookupError, AuthorizationError) as err:
        st.error(str(err))
        common.footer()
        return
    suppliers = c.exceptions.supplier_names(p, exc.dataset)
    supplier_name = suppliers.get(exc.supplier, exc.supplier)

    st.title(f"Exception #{exc.id}")
    st.markdown(
        f"{common.STATUS_BADGE[exc.status]} :gray-badge[{common.TYPE_LABEL[exc.exception_type]}] "
        f":blue-badge[{common.DATASET_LABEL[exc.dataset]}]  \n"
        f"**{common.safe_md(exc.title)}**  \nSupplier: {common.safe_md(supplier_name)} "
        f"({common.safe_md(exc.supplier)}) · Exposure **{common.money(exc.exposure)}**"
    )
    if exc.source_changed:
        st.warning(
            "The source data changed materially since this exception was first reviewed. "
            "The facts below are current; previous facts are kept in History."
        )
    if not exc.condition_active:
        st.info(
            "This discrepancy is no longer detected in the latest synced data. A reviewer "
            "decides whether to resolve it."
        )
    with st.expander(f"Triage score {exc.priority_score}/100 — how it is calculated"):
        st.caption("A transparent sorting aid, not a decision or a risk rating.")
        for r in exc.priority_reasons:
            st.markdown(f"- {common.safe_md(r)}")

    tab_calc, tab_ev, tab_hist = st.tabs(
        ["Calculation & source records", "Evidence & clarification draft", "History"]
    )
    with tab_calc:
        st.subheader("Discrepancy calculation")
        for step in exc.calculation:
            st.markdown(f"- {common.safe_md(step)}")
        for note in exc.notes:
            st.caption(f"Excluded: {note}")
        st.subheader("Linked source records")
        _documents_table(c.exceptions.source_documents(p, exc))
        common.help_link("rules", "Exception rules and tolerances")

    retrieval = None
    with tab_ev:
        try:
            from procurelens.services.explanation import retrieval_query

            with st.spinner("Retrieving agreement and policy evidence…"):
                retrieval = c.knowledge.search(
                    p, exc.dataset, retrieval_query(exc), supplier=exc.supplier
                )
        except (KnowledgeError, Exception) as err:  # noqa: BLE001 - evidence must not break page
            st.error(
                f"Evidence retrieval unavailable: {type(err).__name__}. The investigation "
                "can continue with the verified facts."
            )
        if retrieval is not None:
            ai_key = f"ai-{exc.id}"
            use_ai = st.session_state.get(ai_key, False)
            with st.spinner(
                "Drafting with local AI… this can take 1–2 minutes on CPU." if use_ai else ""
            ):
                expl = c.explanation.explain(p, exc, supplier_name, retrieval, use_ai=use_ai)
            st.subheader("1 · Verified procurement facts")
            st.caption("Deterministic calculation from synced ERP records.")
            for f in expl.facts:
                st.markdown(f"- {common.safe_md(f)}")
            st.subheader("2 · Retrieved agreement / policy evidence")
            st.caption(common.safe_md(expl.evidence_note))
            if not expl.evidence:
                st.warning("Insufficient evidence — no excerpt is relevant enough to show.")
            for e in expl.evidence:
                with st.container(border=True):
                    st.markdown(
                        f"**{common.safe_md(e['ref'])}** · {common.safe_md(e['section'])} "
                        f"· similarity {e['similarity']:.2f}"
                    )
                    st.markdown(common.quote_block(e["text"]))
            st.subheader("3 · Suggested next steps")
            st.caption(f"Source: {expl.source}")
            if expl.fallback_reason:
                st.info(expl.fallback_reason)
            st.markdown(common.safe_md(expl.summary))
            for s in expl.next_steps:
                st.markdown(f"- {common.safe_md(s)}")
            st.subheader("Supplier clarification draft")
            st.caption(
                "For a human to review and send outside ProcureLens. Nothing is sent automatically."
            )
            st.text_area(
                "Draft",
                expl.clarification_draft,
                height=260,
                key=f"draft-{exc.id}-{expl.source}",
                label_visibility="collapsed",
            )
            b1, b2, _ = st.columns([1, 1, 2])
            b1.download_button(
                "Export draft (.md)",
                expl.as_markdown(),
                file_name=f"exception-{exc.id}-clarification.md",
                mime="text/markdown",
            )
            ai_ok = p.can(Permission.GENERATE_AI) and c.generator is not None
            if b2.button(
                "Draft with local AI" if not use_ai else "Use template",
                disabled=not ai_ok,
                help=None
                if ai_ok
                else "Requires the administrator role and the optional Ollama service.",
            ):
                st.session_state[ai_key] = not use_ai
                st.rerun()
            st.session_state[f"evidence-{exc.id}"] = {
                "refs": [e["ref"] for e in expl.evidence],
                "draft_source": expl.source,
                "retrieval_note": expl.evidence_note,
            }
            common.help_link("knowledge", "Knowledge retrieval and optional AI")

    with tab_hist:
        rows = [
            {
                "When (UTC)": ev.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "Event": EVENT_LABEL.get(ev.event_type, ev.event_type),
                "From → To": f"{ev.from_status or ''} → {ev.to_status or ''}".strip(" →"),
                "Actor": ev.actor_name,
                "Reason": ev.reason or "",
            }
            for ev in exc.events
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        for ev in exc.events:
            with st.expander(
                f"Evidence snapshot · {EVENT_LABEL.get(ev.event_type, ev.event_type)}"
                f" · {ev.created_at:%Y-%m-%d %H:%M:%S}"
            ):
                st.json(ev.evidence_snapshot, expanded=False)

    st.subheader("Reviewer actions")
    can_write = p.can(Permission.INVESTIGATE)
    if not can_write:
        st.info(
            "Read-only demo account: review actions are disabled (and rejected by the "
            "server if attempted)."
        )
    actions = allowed_actions(ExceptionStatus(exc.status))
    with st.form(f"action-{exc.id}", clear_on_submit=True):
        labels = {
            Action.START_REVIEW: "Start review",
            Action.RESOLVE: "Resolve",
            Action.REOPEN: "Reopen",
        }
        action = st.radio(
            "Action", actions, format_func=labels.get, horizontal=True, disabled=not can_write
        )
        reason = st.text_area(
            "Reason (required to resolve or reopen)",
            max_chars=2000,
            disabled=not can_write,
            placeholder="e.g. Credit note CN-1042 received for 20 units",
        )
        go = st.form_submit_button("Apply", type="primary", disabled=not can_write)
    if go:
        try:
            c.exceptions.transition(
                p,
                exc.id,
                action,
                reason,
                evidence=st.session_state.get(f"evidence-{exc.id}"),
                expected_version=exc.version,
            )
        except (LifecycleError, AuthorizationError, LookupError) as err:
            st.error(str(err))
        else:
            st.toast(f"{labels[action]} recorded.")
            st.rerun()
    common.help_link("investigation", "Investigation and resolution")
    common.footer()
