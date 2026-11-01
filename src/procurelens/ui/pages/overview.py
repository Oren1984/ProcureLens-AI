from __future__ import annotations

import pandas as pd
import streamlit as st

from procurelens.adapters.erpnext.demo import DEMO_AS_OF
from procurelens.ui import common


def render() -> None:
    p = common.require_principal()
    c = common.container()
    ds = common.selected_dataset(p)
    common.page_header(
        "Overview",
        f"{common.DATASET_LABEL[ds]} · exceptions are triage "
        "signals for a human reviewer, not decisions.",
    )
    ov = c.exceptions.overview(p, ds)
    fresh = c.sync.freshness(p, ds)
    by_status = ov["by_status"]

    cols = st.columns(5)
    cols[0].metric("Open", by_status.get("open", 0))
    cols[1].metric("In review", by_status.get("in_review", 0))
    cols[2].metric("Resolved", by_status.get("resolved", 0))
    cols[3].metric(
        f"Open exposure ({c.settings.base_currency})",
        f"{ov['open_exposure']:,.2f}",
        help="Sum of the calculated exposure of unresolved exceptions.",
    )
    cols[4].metric("Overdue cases", ov["overdue_open"])

    with st.container(border=True):
        last = fresh["last_success"]
        f1, f2, f3 = st.columns([2, 2, 1])
        f1.markdown(
            f"**Data freshness** — last successful sync: "
            f"{last.finished_at:%Y-%m-%d %H:%M} UTC ({common.ago(last.finished_at)})"
            if last
            else "**Data freshness** — no successful sync yet."
        )
        if fresh["failures_since_success"]:
            f2.warning(
                f"{fresh['failures_since_success']} failed sync(s) since the last "
                "success. Results may be stale."
            )
        elif ds == "demo":
            f2.caption(
                f"Demo data is evaluated as of {DEMO_AS_OF.isoformat()} so results are repeatable."
            )
        with f3:
            common.help_link("troubleshooting", "Freshness help")
    if ov["source_changed_open"]:
        st.warning(
            f"{ov['source_changed_open']} unresolved exception(s) changed after a source "
            "update. Review the new facts before deciding."
        )

    left, right = st.columns([1, 1.4])
    with left:
        st.subheader("Unresolved by type")
        if ov["open_by_type"]:
            df = pd.DataFrame(
                [
                    {"Type": common.TYPE_LABEL[k], "Count": v}
                    for k, v in sorted(ov["open_by_type"].items())
                ]
            )
            st.dataframe(df, hide_index=True, use_container_width=True)
        else:
            st.success("No unresolved exceptions in this dataset.")
    with right:
        st.subheader("Highest priority")
        top = [e for e in c.exceptions.list(p, ds, statuses=["open", "in_review"])][:5]
        if not top:
            st.info(
                "Nothing to review. Run a synchronisation from **Integrations & knowledge** "
                "to check for new discrepancies."
            )
        for e in top:
            with st.container(border=True):
                a, b = st.columns([4, 1])
                a.markdown(
                    f"{common.STATUS_BADGE[e.status]} **{common.safe_md(e.title)}**  \n"
                    f"{common.TYPE_LABEL[e.exception_type]} · "
                    f"{common.safe_md(e.supplier)} · {common.money(e.exposure)}"
                )
                if b.button(
                    f"Score {e.priority_score}",
                    key=f"ov-{e.id}",
                    help="Open details (score is a triage aid)",
                ):
                    st.session_state["selected_exception"] = e.id
                    st.switch_page(st.session_state["_pl_pages"]["detail"])
    common.help_link("overview", "What am I looking at?")
    common.footer()
