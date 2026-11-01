from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select, text

from procurelens.adapters.persistence.models import AuditEvent
from procurelens.security.permissions import AuthorizationError, Permission
from procurelens.services.knowledge import KnowledgeError
from procurelens.ui import common

STATUS_ICON = {"succeeded": "✅", "succeeded_with_warnings": "⚠️", "failed": "❌", "running": "⏳"}


def _sync_panel(p, c, ds: str) -> None:
    fresh = c.sync.freshness(p, ds)
    last = fresh["last_success"]
    m1, m2, m3 = st.columns(3)
    m1.metric("Last successful sync", common.ago(last.finished_at) if last else "never")
    m2.metric("Failures since last success", fresh["failures_since_success"])
    m3.metric("Adapter", "Demo (synthetic)" if ds == "demo" else "ERPNext REST")
    if ds == "live":
        s = c.settings
        if not s.erpnext_configured:
            st.info(
                "ERPNext is not configured. Set ERPNEXT_BASE_URL, ERPNEXT_API_KEY and "
                "ERPNEXT_API_SECRET in the environment (see the ERPNext connection help)."
            )
        else:
            st.caption(
                f"ERPNext base URL: {common.safe_md(s.erpnext_base_url)} · credentials "
                "supplied via environment/secret file (never shown)"
            )
            if st.button("Check connection", disabled=not p.can(Permission.SYNC)):
                p.require(Permission.SYNC)
                src = c.source("live")
                status = src.check_connection()
                (st.success if status.ok else st.error)(
                    f"{status.message}" + (f" (user: {status.user})" if status.user else "")
                )
    can_sync = p.can(Permission.SYNC) and (ds == "demo" or c.settings.erpnext_configured)
    label = "Sync demo source" if ds == "demo" else "Sync from ERPNext"
    if st.button(
        label,
        type="primary",
        disabled=not can_sync,
        help=None if p.can(Permission.SYNC) else "Read-only account",
    ):
        with st.status("Synchronising…", expanded=True) as box:
            bar = st.progress(0.0)

            def progress(msg: str, frac: float) -> None:
                bar.progress(min(1.0, frac), text=msg)

            try:
                run = c.sync.run(p, c.source(ds), progress)
            except (AuthorizationError, RuntimeError) as err:
                box.update(label=str(err), state="error")
            else:
                state = "error" if run.status == "failed" else "complete"
                box.update(
                    label=f"Sync {run.status.replace('_', ' ')} · run {run.id[:8]}", state=state
                )
                if run.error:
                    st.error(run.error)
                st.json(run.counts, expanded=False)
    runs = c.sync.history(p, ds, limit=10)
    if runs:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Status": f"{STATUS_ICON.get(r.status, '')} {r.status}",
                        "Started (UTC)": r.started_at.strftime("%Y-%m-%d %H:%M:%S"),
                        "By": r.triggered_by,
                        "Run": r.id[:8],
                        "Documents": sum(
                            v for k, v in r.counts.items() if k.startswith("document_")
                        ),
                        "New exceptions": r.counts.get("exceptions_new", 0),
                        "Warnings": len(r.warnings),
                        "Error": r.error or "",
                    }
                    for r in runs
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
        warn = next((r for r in runs if r.warnings), None)
        if warn:
            with st.expander(f"Warnings from run {warn.id[:8]} (skipped or unsupported records)"):
                for w in warn.warnings:
                    st.markdown(f"- {common.safe_md(w)}")
    else:
        st.info("No synchronisation has run for this dataset yet.")


def _knowledge_panel(p, c, ds: str) -> None:
    docs = c.knowledge.documents(p, ds)
    model_id = c.settings.embedding_model
    status = c.knowledge.index_status(p, model_id)
    k1, k2, k3 = st.columns(3)
    k1.metric("Documents (active)", sum(1 for d in docs if d.is_active))
    k2.metric("Embedding model", model_id.split("/")[-1], help=model_id)
    k3.metric("Chunks needing reindex", status["mismatched"])
    if status["mismatched"]:
        st.warning(
            "Some chunks were embedded with a different model and are excluded from "
            "search until you reindex."
        )
    if docs:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "File": d.filename,
                        "Title": d.title,
                        "Version": d.version,
                        "Type": d.doc_type,
                        "Scope": d.supplier_source_id or "global",
                        "Active": d.is_active,
                        "Imported by": d.uploaded_by,
                        "Imported (UTC)": d.created_at.strftime("%Y-%m-%d"),
                    }
                    for d in docs
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info("No knowledge documents in this dataset yet.")

    with st.form("kb-search"):
        q = st.text_input(
            "Search the knowledge base",
            max_chars=300,
            placeholder="e.g. may the supplier invoice rejected goods?",
        )
        sup = st.text_input("Supplier ID (optional scope filter)", max_chars=140)
        go = st.form_submit_button("Search")
    if go and q.strip():
        try:
            res = c.knowledge.search(p, ds, q, supplier=sup.strip() or None)
        except KnowledgeError as err:
            st.error(str(err))
        else:
            st.caption(common.safe_md(res.note))
            for e in res.evidence:
                with st.container(border=True):
                    st.markdown(
                        f"**{common.safe_md(e.ref)}** · {common.safe_md(e.section)} · "
                        f"similarity {e.similarity:.2f}"
                    )
                    st.markdown(common.quote_block(e.text))

    can_import = p.can(Permission.IMPORT_KNOWLEDGE)
    st.markdown(
        "**Import a document** (UTF-8 Markdown or plain text, max "
        f"{c.settings.max_upload_bytes // 1024} KB)"
    )
    with st.form("kb-import", clear_on_submit=True):
        up = st.file_uploader(
            "Document", type=["md", "txt"], disabled=not can_import, label_visibility="collapsed"
        )
        go_imp = st.form_submit_button("Import and index", disabled=not can_import)
    if go_imp and up is not None:
        try:
            outcome, row = c.knowledge.import_document(p, ds, up.name, up.getvalue())
        except (KnowledgeError, AuthorizationError) as err:
            st.error(str(err))
        else:
            st.success(f"{row.filename} v{row.version}: {outcome}.")
    with st.form("kb-reindex"):
        reason = st.text_input("Reason for reindex", max_chars=300, disabled=not can_import)
        go_re = st.form_submit_button("Reindex all embeddings", disabled=not can_import)
    if go_re:
        if len(reason.strip()) < 5:
            st.error("A reason of at least 5 characters is required.")
        else:
            with st.spinner("Re-embedding all chunks…"):
                n = c.knowledge.reindex(p, reason.strip())
            st.success(f"Re-embedded {n} chunks with {model_id}.")


def _settings_panel(p, c) -> None:
    bs = c.config.get(p)
    can = p.can(Permission.CONFIGURE)
    with st.form("settings"):
        a, b = st.columns(2)
        tol = a.text_input("Price tolerance (%)", str(bs.price_tolerance_pct), disabled=not can)
        grace = b.text_input("Overdue grace (days)", str(bs.overdue_grace_days), disabled=not can)
        crit = a.text_input(
            "Critical item codes (comma-separated)",
            ",".join(sorted(bs.critical_items)),
            disabled=not can,
        )
        sim = b.text_input(
            "Minimum retrieval similarity (0–1)", str(bs.retrieval_min_similarity), disabled=not can
        )
        reason = st.text_input("Reason for change", max_chars=300, disabled=not can)
        go = st.form_submit_button("Save and re-evaluate exceptions", disabled=not can)
    if go:
        try:
            c.config.update(
                p,
                {
                    "price_tolerance_pct": tol,
                    "overdue_grace_days": grace,
                    "critical_items": crit,
                    "retrieval_min_similarity": sim,
                },
                reason,
            )
            out = c.exceptions.reevaluate_all(p, f"Settings changed: {reason.strip()}")
        except (ValueError, AuthorizationError) as err:
            st.error(str(err))
        else:
            st.success(f"Saved. Re-evaluation: {out}")


def _diagnostics(p, c) -> None:
    with c.db.read() as s:
        version = s.scalar(text("SELECT version_num FROM alembic_version"))
        ext = s.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
        audit = (
            list(s.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(30)))
            if p.can(Permission.CONFIGURE)
            else []
        )
    d1, d2, d3 = st.columns(3)
    d1.metric("Database migration", version or "?")
    d2.metric("pgvector", ext or "missing")
    if c.generator is None:
        d3.metric("Local AI (Ollama)", "not configured")
    else:
        ok, msg = c.generator.status()
        d3.metric("Local AI (Ollama)", "ready" if ok else "unavailable", help=msg)
    if audit:
        st.markdown("**Recent audit events** (append-only for the application role)")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "When (UTC)": a.occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
                        "Actor": a.actor_name,
                        "Action": a.action,
                        "Entity": f"{a.entity_type} {a.entity_id or ''}".strip(),
                        "Dataset": a.dataset or "",
                        "Reason": a.reason or "",
                        "Run": (a.run_id or "")[:8],
                    }
                    for a in audit
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )


def render() -> None:
    p = common.require_principal()
    c = common.container()
    ds = common.selected_dataset(p)
    common.page_header("Integrations & knowledge", common.DATASET_LABEL[ds])
    if not p.can(Permission.SYNC):
        st.info(
            "Read-only demo account: synchronisation, import, reindex and settings are "
            "disabled and rejected by the server."
        )
    t1, t2, t3, t4 = st.tabs(["Synchronisation", "Knowledge", "Settings", "Diagnostics"])
    with t1:
        _sync_panel(p, c, ds)
        common.help_link("erpnext", "ERPNext connection")
    with t2:
        _knowledge_panel(p, c, ds)
        common.help_link("knowledge", "Knowledge retrieval and optional AI")
    with t3:
        _settings_panel(p, c)
        common.help_link("rules", "Exception rules and tolerances")
    with t4:
        _diagnostics(p, c)
        common.help_link("troubleshooting", "Troubleshooting and recovery")
    common.footer()
