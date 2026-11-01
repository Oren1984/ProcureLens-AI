from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from procurelens.ui import common

ARTICLES = {
    "overview": ("Product overview and limitations", "01-overview.md"),
    "demo": ("Demo walkthrough", "02-demo-walkthrough.md"),
    "rules": ("Exception rules and tolerances", "03-exception-rules.md"),
    "investigation": ("Investigation and resolution", "04-investigation.md"),
    "erpnext": ("ERPNext connection", "05-erpnext-connection.md"),
    "knowledge": ("Knowledge retrieval and optional AI", "06-knowledge-and-ai.md"),
    "troubleshooting": ("Troubleshooting and recovery", "07-troubleshooting.md"),
}


def _help_dir() -> Path:
    env = os.environ.get("PROCURELENS_HELP_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[4] / "docs" / "help"


def render() -> None:
    common.require_principal()
    common.page_header("Help Center")
    keys = list(ARTICLES)
    topic = st.session_state.pop("help_topic", None)
    if topic in ARTICLES:
        st.session_state["help_article"] = topic
    choice = st.radio(
        "Article",
        keys,
        format_func=lambda k: ARTICLES[k][0],
        horizontal=True,
        key="help_article",
        label_visibility="collapsed",
    )
    title, filename = ARTICLES[choice]
    path = (_help_dir() / filename).resolve()
    if path.parent != _help_dir().resolve() or not path.is_file():
        st.error("Help article not found.")
    else:
        with st.container(border=True):
            st.markdown(path.read_text(encoding="utf-8"))  # trusted, repository-owned content
    common.footer()
