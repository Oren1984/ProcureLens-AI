from __future__ import annotations

import pandas as pd
import streamlit as st

from procurelens.ui import common

STATUS_OPTIONS = {"open": "Open", "in_review": "In review", "resolved": "Resolved"}


def render() -> None:
    p = common.require_principal()
    c = common.container()
    ds = common.selected_dataset(p)
    common.page_header(
        "Exceptions", f"{common.DATASET_LABEL[ds]} · sorted by triage score, then exposure"
    )
    suppliers = c.exceptions.supplier_names(p, ds)

    f1, f2, f3, f4 = st.columns([1.4, 1.6, 1.4, 1])
    statuses = f1.multiselect(
        "Status",
        list(STATUS_OPTIONS),
        default=["open", "in_review"],
        format_func=STATUS_OPTIONS.get,
    )
    types = f2.multiselect("Type", list(common.TYPE_LABEL), format_func=common.TYPE_LABEL.get)
    supplier = f3.selectbox(
        "Supplier",
        [""] + sorted(suppliers),
        format_func=lambda s: suppliers.get(s, "All suppliers"),
    )
    active_only = f4.toggle(
        "Still detected only",
        value=False,
        help="Hide exceptions whose discrepancy is no longer detected.",
    )
    rows = c.exceptions.list(
        p,
        ds,
        statuses=statuses or None,
        types=types or None,
        supplier=supplier or None,
        active_only=active_only,
    )
    if not rows:
        st.info("No exceptions match these filters.")
        common.help_link("rules", "How exceptions are detected")
        common.footer()
        return
    df = pd.DataFrame(
        [
            {
                "ID": e.id,
                "Score": e.priority_score,
                "Status": STATUS_OPTIONS[e.status],
                "Type": common.TYPE_LABEL[e.exception_type],
                "Summary": e.title,
                "Supplier": suppliers.get(e.supplier, e.supplier),
                "Order": e.po_id or "—",
                "Exposure": float(e.exposure),
                "Flags": ", ".join(
                    f
                    for f in [
                        "source changed" if e.source_changed else "",
                        "no longer detected" if not e.condition_active else "",
                        "critical item" if e.critical_item else "",
                    ]
                    if f
                )
                or "",
            }
            for e in rows
        ]
    )
    st.caption(f"{len(rows)} exception(s). Select a row to investigate.")
    event = st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key="exc_table",
        column_config={
            "ID": st.column_config.NumberColumn(width="small"),
            "Score": st.column_config.ProgressColumn(
                min_value=0,
                max_value=100,
                format="%d",
                width="small",
                help="Transparent triage score (exposure, overdue days, criticality).",
            ),
            "Exposure": st.column_config.NumberColumn(format="%.2f"),
            "Summary": st.column_config.TextColumn(width="large"),
        },
    )
    sel = event.selection.rows if event and event.selection else []
    if sel:
        st.session_state["selected_exception"] = int(df.iloc[sel[0]]["ID"])
        st.switch_page(st.session_state["_pl_pages"]["detail"])
    # Keyboard-friendly alternative to selecting a grid row.
    labels = {e.id: f"#{e.id} · {e.title}" for e in rows}
    o1, o2 = st.columns([4, 1], vertical_alignment="bottom")
    pick = o1.selectbox(
        "Open exception",
        list(labels),
        format_func=labels.get,
        index=None,
        placeholder="Choose an exception…",
    )
    if o2.button("Open details", disabled=pick is None, use_container_width=True):
        st.session_state["selected_exception"] = int(pick)
        st.switch_page(st.session_state["_pl_pages"]["detail"])
    common.help_link("rules", "Exception rules and tolerances")
    common.footer()
