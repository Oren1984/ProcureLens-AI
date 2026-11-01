"""Streamlit entry point: sign-in gate and navigation."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `streamlit run src/procurelens/ui/app.py` without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from procurelens import CREDIT  # noqa: E402
from procurelens.security.permissions import AuthenticationError  # noqa: E402
from procurelens.ui import common  # noqa: E402
from procurelens.ui.pages import (  # noqa: E402
    detail,
    exceptions,
    help_center,
    integrations,
    overview,
)

st.set_page_config(
    page_title="ProcureLens AI", page_icon="🔎", layout="wide", initial_sidebar_state="expanded"
)


def login_page() -> None:
    left, mid, right = st.columns([1, 1.2, 1])
    with mid:
        st.title("ProcureLens AI")
        st.caption("Procurement exception management for ERPNext · portfolio POC/MVP")
        flash = st.session_state.pop("_pl_flash", None)
        if flash:
            st.info(flash)
        with st.form("login", clear_on_submit=True, border=True):
            username = st.text_input("Username", autocomplete="username", max_chars=64)
            password = st.text_input(
                "Password", type="password", autocomplete="current-password", max_chars=256
            )
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            try:
                token, _principal = common.container().auth.login(username, password)
            except AuthenticationError as exc:
                st.error(str(exc))
            else:
                st.session_state.clear()
                st.session_state[common.TOKEN_KEY] = token
                st.rerun()
        st.caption(
            "Accounts are created by the one-time bootstrap. See the README for where "
            "the initial credentials file is written."
        )
        st.caption(CREDIT)


def logout() -> None:
    common.container().auth.logout(st.session_state.get(common.TOKEN_KEY))
    st.session_state.clear()
    st.session_state["_pl_flash"] = "You have been signed out."
    st.rerun()


principal = common.current_principal()
if principal is None:
    # Protected URLs resolve to the sign-in form (no "page not found" for deep links).
    aliases = [
        st.Page(login_page, title="Sign in", icon="🔐", url_path=path, default=(path == "login"))
        for path in ("login", "overview", "exceptions", "details", "integrations", "help")
    ]
    st.navigation(aliases, position="hidden").run()
    st.stop()

pages = {
    "overview": st.Page(
        overview.render, title="Overview", icon="📊", url_path="overview", default=True
    ),
    "exceptions": st.Page(exceptions.render, title="Exceptions", icon="📋", url_path="exceptions"),
    "detail": st.Page(detail.render, title="Exception details", icon="🔍", url_path="details"),
    "integrations": st.Page(
        integrations.render, title="Integrations & knowledge", icon="🔌", url_path="integrations"
    ),
    "help": st.Page(help_center.render, title="Help Center", icon="❓", url_path="help"),
}
st.session_state["_pl_pages"] = pages

with st.sidebar:
    st.markdown("### ProcureLens AI")
    role_label = "Administrator" if principal.role == "admin" else "Demo viewer (read-only)"
    st.caption(f"Signed in as **{common.safe_md(principal.username)}** · {role_label}")
    if len(principal.datasets) > 1:
        st.radio(
            "Dataset",
            principal.datasets,
            key="dataset",
            format_func=lambda d: common.DATASET_LABEL[d],
            horizontal=True,
        )
    else:
        st.session_state["dataset"] = "demo"
        st.caption("Dataset: Demo (synthetic) only")
    if st.button("Sign out", use_container_width=True):
        logout()
    st.caption(CREDIT)

nav = st.navigation(list(pages.values()))
nav.run()
